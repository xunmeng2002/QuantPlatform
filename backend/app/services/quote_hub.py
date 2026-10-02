"""与行情组件 QuoteHub 的全部接触面.

平台**读它的 SQLite 库、跑它的命令行**, 但**不 import 它、也不写它的任何文件**——它是个独立仓,
两个界面 (库 schema 与 CLI 参数) 就是全部耦合. 本模块是这两个界面的**唯一**出入口, 于是组件
改 schema 时只需要改这一处, 且能在一处显式报错而不是让判据静默失效.

三条硬约束收在这一层:

  - **只读开库**: 连接走 `?mode=ro`, 由 SQLite 自己强制. 组件可能正持有这个库, 平台绝不去写它.
  - **cwd 必须是组件仓**: 组件用自己的**相对**文件名 (`stock_data.db`) 开库, 换个 cwd 它会安静地
    读/建另一个位置的库, 现象是"跑成功但没数据".
  - **命令行一律参数列表**: 绝不 `shell=True`; 代码全集来自组件库的查询结果, 拼接即注入面.

它**不是**判据: 「本地行情够不够」的判定逻辑在 `market_data_coverage`, 编排在
`market_data_preparation`. 本模块只负责"把事实读出来"与"把命令跑起来".
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..config import (
    QUOTE_HUB_CLI_FILENAME,
    QUOTE_HUB_DATABASE_FILENAME,
)


# 组件的表名与列名. 集中在此是为了让"组件改了 schema"这件事只需要改一个地方, 且由下面的
# `_assert_expected_schema` 在读到不符合预期的结构时**显式报错**——把 schema 漂移当作"没数据"
# 会让判据安静地判错, 而那正是本模块要防的事.
SECURITIES_TABLE_NAME = "Securities"
TRADE_DATES_TABLE_NAME = "TradeDates"
MINUTE_BARS_TABLE_NAME = "MinuteBars"
QUERIED_BAR_SPANS_TABLE_NAME = "QueriedBarSpans"

CONTRACT_CODE_COLUMN = "Code"
CONTRACT_NAME_COLUMN = "CodeName"
CONTRACT_STOCK_TYPE_COLUMN = "StockType"
CONTRACT_EXCHANGE_COLUMN = "Exchange"
CONTRACT_TRADE_STATUS_COLUMN = "CurrentTradeStatus"

CALENDAR_DATE_COLUMN = "CalendarDate"
IS_TRADING_DAY_COLUMN = "IsTradingDay"

BAR_FREQUENCY_COLUMN = "Frequency"
BAR_TRADING_DAY_COLUMN = "TradingDay"

QUERIED_SPAN_START_COLUMN = "StartDay"
QUERIED_SPAN_END_COLUMN = "EndDay"

# 每个真表必须有的列; 少一列即视为 schema 漂移.
EXPECTED_SCHEMA_COLUMNS: dict[str, frozenset[str]] = {
    SECURITIES_TABLE_NAME: frozenset(
        {
            CONTRACT_CODE_COLUMN,
            CONTRACT_NAME_COLUMN,
            CONTRACT_STOCK_TYPE_COLUMN,
            CONTRACT_EXCHANGE_COLUMN,
            CONTRACT_TRADE_STATUS_COLUMN,
        }
    ),
    TRADE_DATES_TABLE_NAME: frozenset({CALENDAR_DATE_COLUMN, IS_TRADING_DAY_COLUMN}),
    MINUTE_BARS_TABLE_NAME: frozenset(
        {CONTRACT_CODE_COLUMN, BAR_FREQUENCY_COLUMN, BAR_TRADING_DAY_COLUMN}
    ),
}

# 可以不在, 但**在就必须列齐**的表. `QueriedBarSpans` 是组件 v2.3.0 才有的账: 组件还没升过级
# 时它不存在, 那时账为空、判据退化成"一段都没问过, 于是每次都取"——这是**有意**的降级方向,
# 多取只是慢, 少取才是错. 但表在而列漂了是另一回事, 那种 schema 漂移必须显式报错, 不能当成
# "账是空的"。
OPTIONAL_SCHEMA_COLUMNS: dict[str, frozenset[str]] = {
    QUERIED_BAR_SPANS_TABLE_NAME: frozenset(
        {
            CONTRACT_CODE_COLUMN,
            BAR_FREQUENCY_COLUMN,
            QUERIED_SPAN_START_COLUMN,
            QUERIED_SPAN_END_COLUMN,
        }
    ),
}

# 只收股票. 指数 (`'2'`) 被组件自己的文档写明"无分钟线", 收进下拉框就是给用户一个永远 0 成交的
# 选项; ETF (`'5'`) 的分钟线供给未经实测, 一并排除. 放宽就是往这个元组里加取值.
TRADABLE_STOCK_TYPE_VALUES = ("1",)

# 组件用 `1`/`0` 表示当前交易状态 (而不是布尔). 历史上停牌、但历史区间内有数据的合约会被这一条
# 误伤, 是**有意接受**的: 下拉框的用途是挑一个现在还能跑的标的.
TRADING_TRADE_STATUS_VALUE = "1"

# 组件主键 `sh.600519` 的前缀与引擎侧的交易所字面量. 与 `BaoStockParquet.py` 的拆分函数、以及
# 行情目录名 `Identity=SSE.Stock` 三处必须一致, 故这里只镜像、不推导.
EXCHANGE_PREFIX_TO_IDENTIFIER = {"sh": "SSE", "sz": "SZSE"}
EXCHANGE_IDENTIFIER_TO_PREFIX = {
    identifier: prefix for prefix, identifier in EXCHANGE_PREFIX_TO_IDENTIFIER.items()
}

CONTRACT_CODE_SEPARATOR = "."

# 组件打印的登录失败标记. 它 `raise SystemExit("BaoStock 登录失败")`, 类别只能从 stderr 文字认.
LOGIN_FAILURE_STDERR_MARKER = "登录失败"

STREAM_TAIL_CHARACTER_LIMIT = 4096
STREAM_READ_CHUNK_BYTES = 4096
PROCESS_TERMINATE_GRACE_SECONDS = 10
PUMP_DRAIN_GRACE_SECONDS = 5


class QuoteHubUnavailableError(Exception):
    """组件或其数据不在位. 文案必须是固定中文字符串, 且不带路径 (同 `UnrunnableJob` 的纪律)."""


def _read_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    """表存在时回它的列名集合; 表不在回空集."""

    return {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _assert_expected_schema(connection: sqlite3.Connection) -> None:
    """必有的表都要在且列齐, 可选的表在才要求列齐. 缺什么就报什么, 不静默降级成"没有数据"."""

    for table_name, expected_columns in EXPECTED_SCHEMA_COLUMNS.items():
        actual_columns = _read_table_columns(connection, table_name)

        if not actual_columns:
            raise QuoteHubUnavailableError("行情组件的数据库缺少预期的表")

        if expected_columns - actual_columns:
            raise QuoteHubUnavailableError("行情组件的数据库表结构与预期不符")

    for table_name, expected_columns in OPTIONAL_SCHEMA_COLUMNS.items():
        actual_columns = _read_table_columns(connection, table_name)

        if actual_columns and expected_columns - actual_columns:
            raise QuoteHubUnavailableError("行情组件的数据库表结构与预期不符")


@contextmanager
def open_quote_hub_database_readonly(quote_hub_root: Path) -> Iterator[sqlite3.Connection]:
    """只读打开组件的库, 出块即关闭.

    收目录而不是收连接: sqlite3 的连接不能跨线程用, 调用方要把它整个丢进 `asyncio.to_thread`,
    于是"开连接 → 查询 → 关连接"必须在同一个 worker 里完成.
    """

    database_file = quote_hub_root / QUOTE_HUB_DATABASE_FILENAME

    if not database_file.is_file():
        raise QuoteHubUnavailableError("行情组件的数据库不在位")

    connection = sqlite3.connect(f"{database_file.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    try:
        _assert_expected_schema(connection)
        yield connection
    finally:
        connection.close()


@dataclass(frozen=True)
class QuoteHubContract:
    """组件 `Securities` 表里一只可回测的合约, 已拆成平台侧的两个字段."""

    code: str
    exchange_id: str
    instrument_id: str
    display_name: str


def split_contract_code(code: str) -> tuple[str, str] | None:
    """`sh.600519` → `("SSE", "600519")`; 认不出前缀回 None."""

    prefix, separator, instrument_id = code.partition(CONTRACT_CODE_SEPARATOR)

    if not separator or not instrument_id:
        return None

    exchange_id = EXCHANGE_PREFIX_TO_IDENTIFIER.get(prefix)

    if exchange_id is None:
        return None

    return exchange_id, instrument_id


def build_contract_code(exchange_id: str, instrument_id: str) -> str | None:
    """`("SSE", "600519")` → `sh.600519`; 认不出交易所回 None."""

    prefix = EXCHANGE_IDENTIFIER_TO_PREFIX.get(exchange_id)

    if prefix is None or not instrument_id:
        return None

    return f"{prefix}{CONTRACT_CODE_SEPARATOR}{instrument_id}"


def read_tradable_contracts(quote_hub_root: Path) -> list[QuoteHubContract]:
    """可回测的合约清单, 按 (交易所, 代码) 升序.

    界面上的下拉框直接吃这一份. 拆不出交易所字面量的行**跳过**: 组件里存在平台认不出的前缀,
    把它带进界面只会得到一只提交就被拒的合约.
    """

    stock_type_placeholders = ", ".join("?" for _ in TRADABLE_STOCK_TYPE_VALUES)

    with open_quote_hub_database_readonly(quote_hub_root) as connection:
        rows = connection.execute(
            f"SELECT {CONTRACT_CODE_COLUMN}, {CONTRACT_NAME_COLUMN}"
            f" FROM {SECURITIES_TABLE_NAME}"
            f" WHERE {CONTRACT_STOCK_TYPE_COLUMN} IN ({stock_type_placeholders})"
            f" AND {CONTRACT_TRADE_STATUS_COLUMN} = ?"
            f" ORDER BY {CONTRACT_EXCHANGE_COLUMN}, {CONTRACT_CODE_COLUMN}",
            (*TRADABLE_STOCK_TYPE_VALUES, TRADING_TRADE_STATUS_VALUE),
        ).fetchall()

    contracts: list[QuoteHubContract] = []

    for row in rows:
        split_code = split_contract_code(row[CONTRACT_CODE_COLUMN])

        if split_code is None:
            continue

        exchange_id, instrument_id = split_code
        contracts.append(
            QuoteHubContract(
                code=row[CONTRACT_CODE_COLUMN],
                exchange_id=exchange_id,
                instrument_id=instrument_id,
                display_name=row[CONTRACT_NAME_COLUMN] or row[CONTRACT_CODE_COLUMN],
            )
        )

    return contracts


def read_ingested_codes(quote_hub_root: Path) -> list[str]:
    """组件库里**任何频率**下已经有过数据的代码.

    这是"整所刷新"要传的代码全集的基准. 组件只把传进去的代码写进年度文件, 少传一个就把那个
    合约从文件里**静默挤掉** (它只记一条 warning), 故这个集合只能多不能少.
    """

    with open_quote_hub_database_readonly(quote_hub_root) as connection:
        rows = connection.execute(
            f"SELECT DISTINCT {CONTRACT_CODE_COLUMN} FROM {MINUTE_BARS_TABLE_NAME}"
        ).fetchall()

    return sorted(row[0] for row in rows)


@dataclass(frozen=True)
class CoverageFacts:
    """判定"够不够"所需的全部事实, 一次连接读完.

    `asked_days` 是**落在区间内、且已被账记过**的交易日; `has_bars` 是该频率在区间内是否至少
    有一天真有 bar. 两者问的不是一件事, 故不能互相替代——见 `market_data_coverage` 的模块说明.
    """

    expected_days: list[str]
    asked_days: list[str]
    has_bars: bool
    calendar_last_day: str | None


def read_coverage_facts(
    quote_hub_root: Path, code: str, frequency: str, start_day: str, end_day: str
) -> CoverageFacts:
    """四张表一次连接读完: 分开读要开四次 40 MB 的库, 而它们本来就是同一次判断的输入."""

    with open_quote_hub_database_readonly(quote_hub_root) as connection:
        expected_rows = connection.execute(
            f"SELECT {CALENDAR_DATE_COLUMN} FROM {TRADE_DATES_TABLE_NAME}"
            f" WHERE {IS_TRADING_DAY_COLUMN} = 1"
            f" AND {CALENDAR_DATE_COLUMN} >= ? AND {CALENDAR_DATE_COLUMN} <= ?"
            f" ORDER BY {CALENDAR_DATE_COLUMN}",
            (start_day, end_day),
        ).fetchall()

        queried_day_spans = _read_queried_day_spans(
            connection, code, frequency, start_day, end_day
        )

        bar_row = connection.execute(
            f"SELECT 1 FROM {MINUTE_BARS_TABLE_NAME}"
            f" WHERE {CONTRACT_CODE_COLUMN} = ? AND {BAR_FREQUENCY_COLUMN} = ?"
            f" AND {BAR_TRADING_DAY_COLUMN} >= ? AND {BAR_TRADING_DAY_COLUMN} <= ?"
            f" LIMIT 1",
            (code, frequency, start_day, end_day),
        ).fetchone()

        calendar_row = connection.execute(
            f"SELECT MAX({CALENDAR_DATE_COLUMN}) FROM {TRADE_DATES_TABLE_NAME}"
            f" WHERE {IS_TRADING_DAY_COLUMN} = 1"
        ).fetchone()

    expected_days = [row[0] for row in expected_rows]

    return CoverageFacts(
        expected_days=expected_days,
        asked_days=[
            day
            for day in expected_days
            if _is_day_within_queried_spans(day, queried_day_spans)
        ],
        has_bars=bar_row is not None,
        calendar_last_day=None if calendar_row is None else calendar_row[0],
    )


def _read_queried_day_spans(
    connection: sqlite3.Connection, code: str, frequency: str, start_day: str, end_day: str
) -> list[tuple[str, str]]:
    """该合约该频率下与区间相交的已问日区间; **账表不在时回空**.

    表不在 = 组件还停在 v2.3.0 之前, 账还没开始记, 于是退化成"一段都没问过、每次都得取".
    这是有意的降级方向: 多取只是慢, 少取才是错.
    """

    if not _read_table_columns(connection, QUERIED_BAR_SPANS_TABLE_NAME):
        return []

    rows = connection.execute(
        f"SELECT {QUERIED_SPAN_START_COLUMN}, {QUERIED_SPAN_END_COLUMN}"
        f" FROM {QUERIED_BAR_SPANS_TABLE_NAME}"
        f" WHERE {CONTRACT_CODE_COLUMN} = ? AND {BAR_FREQUENCY_COLUMN} = ?"
        f" AND {QUERIED_SPAN_END_COLUMN} >= ? AND {QUERIED_SPAN_START_COLUMN} <= ?"
        f" ORDER BY {QUERIED_SPAN_START_COLUMN}",
        (code, frequency, start_day, end_day),
    ).fetchall()

    return [(row[0], row[1]) for row in rows]


def _is_day_within_queried_spans(day: str, day_spans: list[tuple[str, str]]) -> bool:
    """`2024-01-05` 是否落在某段已问区间内. 段是闭的, 两端都算 (ISO 日期串可直接比大小)."""

    return any(span_start <= day <= span_end for span_start, span_end in day_spans)


def format_platform_day(component_day: str) -> str:
    """`2024-12-31` → `20241231` (平台内部一律 8 位)."""

    return component_day.replace("-", "")


def format_component_day(platform_day: str) -> str:
    """`20241231` → `2024-12-31` (组件库一律带连字符)."""

    return f"{platform_day[:4]}-{platform_day[4:6]}-{platform_day[6:8]}"


def build_download_command(
    frequency: str, start_day: str, end_day: str, codes: list[str], output_root: Path
) -> list[str]:
    """拼出 `backfill` 的命令行. 日期是组件格式 (`YYYY-MM-DD`).

    用 `backfill` 而不是 `update`: 前者按 argparse 的帮助原文"区间入库并**直接写出年度 Parquet**
    (不产生日度文件)", 与行情根现有的年度文件同构; 后者写日度文件.

    `--output-root` **必须显式给**: 组件自己的默认值就是旧的行情根, 不传它仍会往那里写.
    `--codes` 一并显式给, 见 `read_ingested_codes` 关于"静默挤掉"的说明.
    """

    return [
        sys.executable,
        QUOTE_HUB_CLI_FILENAME,
        "backfill",
        "--start",
        start_day,
        "--end",
        end_day,
        "--frequency",
        frequency,
        "--codes",
        ",".join(codes),
        "--output-root",
        str(output_root),
    ]


@dataclass(frozen=True)
class DownloadOutcome:
    """一次下载子进程的收场.

    `exit_code` 为 None 表示它**不是自然退出的**: 或被取消, 或到了时限, 平台把它收掉了.
    """

    exit_code: int | None
    stderr_tail: str
    cancelled: bool
    timed_out: bool


async def run_download_command(
    command: list[str],
    quote_hub_root: Path,
    cancel_signal: asyncio.Event,
    timeout_seconds: int,
) -> DownloadOutcome:
    """跑一次下载, 直到它退出、被取消、或到时限.

    三件事同时竞速 (退出 / 取消信号 / 时限), **不用"取消这个协程"来实现超时**: 那样子进程会
    留在盘上继续往行情根里写, 而平台已经认为这一轮结束了. 这里改成普通 `await`, 谁先到都走
    同一条"收尸再定论"的路.

    两条输出流**并发读**: Windows 管道缓冲区约 64 KB, 串行读会在被填满时互相死锁. 只保留有界
    尾部——它是用来**分类失败原因**的 (认登录失败要翻 stderr), 不是拿来展示的, 故不进任何
    给用户的文案.
    """

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(quote_hub_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise QuoteHubUnavailableError("行情组件无法启动") from error

    stderr_sink = _StreamTailSink()
    # stdout 只读不存: 失败分类只看 stderr, 但这条流同样必须被读走, 否则管道填满后双方互等.
    stdout_reader = asyncio.ensure_future(_pump_stream_tail(process.stdout, _StreamTailSink()))
    stderr_reader = asyncio.ensure_future(_pump_stream_tail(process.stderr, stderr_sink))
    exit_waiter = asyncio.ensure_future(process.wait())
    cancel_waiter = asyncio.ensure_future(cancel_signal.wait())
    deadline_waiter = asyncio.ensure_future(asyncio.sleep(timeout_seconds))

    try:
        await asyncio.wait(
            {exit_waiter, cancel_waiter, deadline_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        cancelled = cancel_waiter.done() and not exit_waiter.done()
        timed_out = deadline_waiter.done() and not exit_waiter.done() and not cancelled

        if cancelled or timed_out:
            await _stop_process(process)

        exit_code = await exit_waiter
    finally:
        for unfinished_waiter in (cancel_waiter, deadline_waiter):
            unfinished_waiter.cancel()

        await asyncio.gather(cancel_waiter, deadline_waiter, return_exceptions=True)

        # 兜底: 走到这里进程还活着, 说明本协程是被外层取消的 (平台关停一类), 上面那条收尸路
        # 没赶上. 不再 await——此刻 await 什么都可能立刻再被取消一次; 只保证它不会继续跑.
        if process.returncode is None:
            process.kill()

        await _drain_readers(stdout_reader, stderr_reader)

    return DownloadOutcome(
        exit_code=exit_code,
        stderr_tail=stderr_sink.text,
        cancelled=cancelled,
        timed_out=timed_out,
    )


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """先请它自己退, 有界等待后再强杀. 不留活着的下载进程继续往行情根里写."""

    if process.returncode is not None:
        return

    process.terminate()

    try:
        async with asyncio.timeout(PROCESS_TERMINATE_GRACE_SECONDS):
            await process.wait()
    except TimeoutError:
        process.kill()
        await process.wait()


async def _drain_readers(
    stdout_reader: asyncio.Task[None], stderr_reader: asyncio.Task[None]
) -> None:
    """等两条读泵收拢, 但有界.

    进程退出 ≠ 输出读完, 退出瞬间仍在管道里的最后几行往往正是失败原因, 故要等一下; 而子进程
    留下的孙进程可能一直握着管道不放, 无界等就把这一轮的槽位永久占住了.
    """

    try:
        async with asyncio.timeout(PUMP_DRAIN_GRACE_SECONDS):
            await asyncio.gather(stdout_reader, stderr_reader, return_exceptions=True)
    except TimeoutError:
        for reader in (stdout_reader, stderr_reader):
            reader.cancel()


class _StreamTailSink:
    """读泵与调用方之间那格可变状态: 尾部文本.

    用持有者而不是用任务的返回值, 是因为**读泵可能被取消** (管道那侧还有孙进程握着不放),
    而被取消的任务没有返回值——那时"已经读到的那些"恰恰是唯一能说明失败原因的线索.
    """

    __slots__ = ("text",)

    def __init__(self) -> None:
        self.text = ""


async def _pump_stream_tail(stream: asyncio.StreamReader | None, sink: _StreamTailSink) -> None:
    """把一条输出流读到 EOF, 只往 `sink` 里留字符数不超过上限的尾部."""

    if stream is None:
        return

    tail: deque[str] = deque()
    retained_characters = 0

    while True:
        chunk = await stream.read(STREAM_READ_CHUNK_BYTES)

        if not chunk:
            break

        text = chunk.decode("utf-8", errors="replace")
        tail.append(text)
        retained_characters += len(text)

        while retained_characters > STREAM_TAIL_CHARACTER_LIMIT and len(tail) > 1:
            retained_characters -= len(tail.popleft())

        sink.text = "".join(tail)


def indicates_login_failure(stderr_tail: str) -> bool:
    """组件登录不上时 `raise SystemExit("BaoStock 登录失败")`, 只能从文字认. 实测退出码为 1,
    与它的其它致命错误同码, 故这一步是消歧的**唯一**依据."""

    return LOGIN_FAILURE_STDERR_MARKER in stderr_tail
