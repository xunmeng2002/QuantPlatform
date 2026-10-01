"""行情组件的测试替身.

契约只有两条 (见 `app/services/quote_hub.py`): 以 `quote_hub_root` 为 cwd 跑该目录下的
`BaoStockParquet.py`, 以及一张三张表齐备的 `stock_data.db`. 于是替身就是"一个目录 + 一个脚本",
生产代码里不必开任何注入缝.

脚本的行为由**同目录下的文件**驱动, 而不是环境变量: 环境变量会漏进同一个进程里的其它用例,
而文件是隔离的, 且"这一轮开了哪些开关"在失败现场一眼可见.

桩库的列名一律取自 `quote_hub` 的常量, 不另抄一份——抄一份就意味着 schema 改了这里不跟着改,
而"这里的表结构与真组件一致"正是替身唯一能提供的东西.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from app.services import quote_hub


STUB_COMPONENT_FILENAME = quote_hub.QUOTE_HUB_CLI_FILENAME

STUB_ARGV_FILENAME = "stub-argv.json"
STUB_STARTED_MARKER_FILENAME = "stub-started.marker"
STUB_EXIT_CODE_FILENAME = "stub-exit-code.txt"
STUB_DELAY_SECONDS_FILENAME = "stub-delay-seconds.txt"
STUB_STDERR_FILENAME = "stub-stderr.txt"
STUB_INGEST_BARS_FILENAME = "stub-ingest-bars.txt"

DEFAULT_FREQUENCY = "5"

STUB_CONTRACT_NAME = "贵州茅台"
STUB_STOCK_TYPE = "1"
STUB_EXCHANGE = "sh"

#: 共享夹具用的契约: 与 `tests/run_helpers.py` 提交的默认取值同一只, 于是调度器用例都在"已
#: 覆盖"这一侧收场, 谁也不去碰下载那条路.
DEFAULT_CONTRACT_CODE = "sh.600519"
DEFAULT_COVERED_START_DAY = "2019-01-01"
DEFAULT_COVERED_END_DAY = "2031-12-31"


# 桩脚本: 落到 `argv.json`、按文件决定退出码与延迟、可选地"入库"。
#
# "入库"那一段并非画蛇添足: 组件退出码为 0 **不等于**有数据 (真组件在库中无数据时只记一条
# warning 仍以 0 退出), 故平台一定会复判一次。桩要能造出"复判通过"的真实路径, 否则那条路
# 只有靠 mock 才能覆盖, 而 mock 掉的恰恰是要验的东西。
STUB_COMPONENT_SOURCE = '''
import json
import sqlite3
import sys
import time
from pathlib import Path


root = Path(__file__).parent

(root / "stub-argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
(root / "stub-started.marker").open("a", encoding="utf-8").write("1\\n")

exit_code_file = root / "stub-exit-code.txt"

if exit_code_file.is_file():
    exit_code = int(exit_code_file.read_text(encoding="utf-8").strip())

    if exit_code != 0:
        stderr_file = root / "stub-stderr.txt"

        if stderr_file.is_file():
            sys.stderr.write(stderr_file.read_text(encoding="utf-8"))

        raise SystemExit(exit_code)

delay_file = root / "stub-delay-seconds.txt"

if delay_file.is_file():
    time.sleep(float(delay_file.read_text(encoding="utf-8").strip()))

ingest_file = root / "stub-ingest-bars.txt"

if ingest_file.is_file():
    arguments = sys.argv[1:]
    options = dict(zip(arguments[1::2], arguments[2::2]))

    connection = sqlite3.connect(root / "stock_data.db")
    trading_days = [
        row[0]
        for row in connection.execute(
            "SELECT CalendarDate FROM TradeDates"
            " WHERE IsTradingDay = 1 AND CalendarDate >= ? AND CalendarDate <= ?",
            (options["--start"], options["--end"]),
        ).fetchall()
    ]

    for code in options["--codes"].split(","):
        connection.executemany(
            "INSERT OR REPLACE INTO MinuteBars (Code, Frequency, Time, TradingDay)"
            " VALUES (?, ?, ?, ?)",
            [
                (code, options["--frequency"], day + " 09:35:00", day)
                for day in trading_days
            ],
        )

    connection.commit()
    connection.close()
'''


def write_stub_component(quote_hub_root: Path) -> Path:
    """落一个桩 `BaoStockParquet.py`. 文件在 = 组件在位."""

    quote_hub_root.mkdir(parents=True, exist_ok=True)
    component_file = quote_hub_root / STUB_COMPONENT_FILENAME
    component_file.write_text(STUB_COMPONENT_SOURCE, encoding="utf-8")

    return component_file


def enable_bar_ingestion(quote_hub_root: Path) -> None:
    """让桩脚本在下一次运行时把请求的区间"入库", 从而复判能通过."""

    (quote_hub_root / STUB_INGEST_BARS_FILENAME).write_text("", encoding="utf-8")


def set_exit_code(quote_hub_root: Path, exit_code: int, stderr_text: str = "") -> None:
    """让桩脚本以指定退出码收场, 并可选地往 stderr 写一段文字 (登录失败一类靠它消歧)."""

    (quote_hub_root / STUB_EXIT_CODE_FILENAME).write_text(
        str(exit_code), encoding="utf-8"
    )

    if stderr_text:
        (quote_hub_root / STUB_STDERR_FILENAME).write_text(
            stderr_text, encoding="utf-8"
        )


def set_delay_seconds(quote_hub_root: Path, delay_seconds: float) -> None:
    """让桩脚本运行前先睡一会儿, 用来造出"正在下载"这个窗口."""

    (quote_hub_root / STUB_DELAY_SECONDS_FILENAME).write_text(
        str(delay_seconds), encoding="utf-8"
    )


def read_recorded_arguments(quote_hub_root: Path) -> list[str] | None:
    """桩脚本这次收到的参数; 没跑过时回 `None`."""

    argument_file = quote_hub_root / STUB_ARGV_FILENAME

    if not argument_file.is_file():
        return None

    return json.loads(argument_file.read_text(encoding="utf-8"))


def count_starts(quote_hub_root: Path) -> int:
    """桩脚本被启动过几次.

    这是"够用时零子进程"与"单飞锁真的只放一个进去"两条**唯一**的硬证据: 命令拼得再对, 起了
    两次也是错的.
    """

    marker_file = quote_hub_root / STUB_STARTED_MARKER_FILENAME

    if not marker_file.is_file():
        return 0

    return len(marker_file.read_text(encoding="utf-8").splitlines())


def enumerate_days(start_day: str, end_day: str) -> list[str]:
    """`2019-01-01` ~ `2019-01-05` 之间**每一天** (含周末).

    桩库把每一天都当交易日: 判定只比对"日历里的交易日 − 有 bar 的交易日", 两者都由这里灌入,
    于是取值相等, 判据因此与真实的周末/节假日排列无关.
    """

    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)

    return [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]


def write_catalog_database(
    quote_hub_root: Path,
    *,
    covered_contracts: dict[str, tuple[str, ...]],
    calendar_days: tuple[str, ...],
    contracts: tuple[tuple[str, str, str, str], ...],
) -> None:
    """写一张只够判据用的桩库.

    `covered_contracts` 是 `{频率: (代码, ...)}`, 决定哪只合约在哪个频率下"有 bar";
    `calendar_days` 同时灌进 `TradeDates` 与那些合约的 `MinuteBars` —— 两者相等即"已覆盖".
    `contracts` 是 `(Code, CodeName, StockType, Exchange)` 四元组.
    """

    quote_hub_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(quote_hub_root / quote_hub.QUOTE_HUB_DATABASE_FILENAME)

    connection.executescript(
        f"""
        CREATE TABLE {quote_hub.SECURITIES_TABLE_NAME} (
            {quote_hub.CONTRACT_CODE_COLUMN} TEXT PRIMARY KEY,
            {quote_hub.CONTRACT_NAME_COLUMN} TEXT,
            {quote_hub.CONTRACT_STOCK_TYPE_COLUMN} TEXT,
            {quote_hub.CONTRACT_EXCHANGE_COLUMN} TEXT,
            {quote_hub.CONTRACT_TRADE_STATUS_COLUMN} TEXT
        );
        CREATE TABLE {quote_hub.TRADE_DATES_TABLE_NAME} (
            {quote_hub.CALENDAR_DATE_COLUMN} TEXT,
            {quote_hub.IS_TRADING_DAY_COLUMN} INTEGER
        );
        CREATE TABLE {quote_hub.MINUTE_BARS_TABLE_NAME} (
            {quote_hub.CONTRACT_CODE_COLUMN} TEXT,
            {quote_hub.BAR_FREQUENCY_COLUMN} TEXT,
            Time TEXT,
            {quote_hub.BAR_TRADING_DAY_COLUMN} TEXT,
            PRIMARY KEY ({quote_hub.CONTRACT_CODE_COLUMN},
                         {quote_hub.BAR_FREQUENCY_COLUMN}, Time)
        );
        """
    )

    connection.executemany(
        f"INSERT INTO {quote_hub.SECURITIES_TABLE_NAME} VALUES (?, ?, ?, ?, ?)",
        [
            (code, name, stock_type, exchange, "1")
            for code, name, stock_type, exchange in contracts
        ],
    )

    connection.executemany(
        f"INSERT INTO {quote_hub.TRADE_DATES_TABLE_NAME} VALUES (?, 1)",
        [(day,) for day in calendar_days],
    )

    for frequency, codes in covered_contracts.items():
        connection.executemany(
            f"INSERT INTO {quote_hub.MINUTE_BARS_TABLE_NAME}"
            f" ({quote_hub.CONTRACT_CODE_COLUMN}, {quote_hub.BAR_FREQUENCY_COLUMN},"
            f" {quote_hub.BAR_TRADING_DAY_COLUMN}, Time) VALUES (?, ?, ?, ?)",
            [
                (code, frequency, day, f"{day} 09:35:00")
                for code in codes
                for day in calendar_days
            ],
        )

    connection.commit()
    connection.close()


def build_covered_component(
    quote_hub_root: Path,
    contract_code: str = DEFAULT_CONTRACT_CODE,
    frequency: str = DEFAULT_FREQUENCY,
    start_day: str = DEFAULT_COVERED_START_DAY,
    end_day: str = DEFAULT_COVERED_END_DAY,
) -> Path:
    """一个"该合约的行情早已落地"且在位的组件.

    共享夹具与"够用则零子进程"那条用例都用它: 库里既有日历又有 bar, 于是判据直接回够用, 一次
    子进程都不起.
    """

    write_stub_component(quote_hub_root)
    write_catalog_database(
        quote_hub_root,
        covered_contracts={frequency: (contract_code,)},
        calendar_days=tuple(enumerate_days(start_day, end_day)),
        contracts=((contract_code, STUB_CONTRACT_NAME, STUB_STOCK_TYPE, STUB_EXCHANGE),),
    )

    return quote_hub_root
