"""真引擎验收: 桩策略覆盖不到的那一层.

桩是纯 Python, 它证明得了"平台把配置写对了" (桩把读到的 `RunId` / `MatchMode` / `DbHost` 回显进
结果文件), 但证明不了"引擎认这份配置". 只有真引擎才有的东西: `PYTHONPATH` 与 `.pyd`/DLL 的
解析, `result.json` 的真实键名与口径, `MatchMode`/`BarPreces` 的真实对应, `MdDataPath` 的正确
形态 (写错得到 `ErrorId 0x100F`), `DbHost` 的派生行为, 以及种子库缺失时引擎的降级.

**一条被实测推翻的旧记录** (此处留档, 免得后人又照抄): 平台启动引擎时传**裸文件名**, 但这**不是**
因为"带路径的 `argv[0]` 会在启动期被日志器 `fopen` 失败打死"——2026-09-25 在本机
`QuantTrading.cp314-win_amd64.pyd` 上实测, `./grid_strategy.py` 与
`C:\\...\\Temp\\<job>\\grid_strategy.py` 两种带路径形式都**跑完了整轮 Bar 回测、退出码 0**.
裸文件名因此是"够用且与 `result.json.DbPath` 派生口径一致"的选择, 而不是唯一可行的形式;
`job-workspace.md` §3.1 与 `PROGRESS.md` 的 D.01 已按实测订正.

故这里走一遍**完整链路**: 经上传接口落一份真策略 (QuantTrading 仓内**未做任何修改**的
`grid_strategy.py`) → 提交 → 调度侧构造作业目录 → 起真引擎 → 收尾 → 逐项比对引擎自己报的数.

**默认不跑** (标 `real_engine`, 见 pytest.ini): 一轮真回测会往作业目录写约 3.0 MB 产物 (876 KB 的
`.db`、549 KB 的 stdout、`Dump/<RunId>/` 下 17 个 CSV, 与 P0 记的 3.0 MB 相符), 且依赖两个仓之外
的东西——`QuantTrading/bin/Release` 下的扩展模块与 `D:/MdBaoStock` 的行情. 两者都由环境变量给出,
不在此处写死:

    set QUANT_REAL_ENGINE_ROOT=D:\\Gitee\\QuantTrading\\bin\\Release
    set QUANT_REAL_MARKET_DATA_ROOT=D:\\MdBaoStock
    python -m pytest -m real_engine tests/test_real_engine_acceptance.py -v

`--basetemp` 落在仓内的 `_acc_tmp/` (已被 `.gitignore` 覆盖): 四个用例各留下一个作业目录,
共约 22 MB, 而 `%TEMP%` 下的临时根在排查时不好找——验收失败时第一件事就是进去看
`stdout.txt` 与 `result.json` 到底有没有写出来.

    python -m pytest -m real_engine tests/test_real_engine_acceptance.py -v --basetemp=_acc_tmp

本机实测 (Python 3.14.5 + `QuantTrading.cp314-win_amd64.pyd`): 三个月的 5m 回测约 1.7 秒,
十五年 (2010–2024, 58176 根 bar) 约 3.8 秒——**引擎比预期快得多**, 故"制造一个跑得够久的作业"
只能靠把下限压到它跑不完 (见 `test_a_real_run_that_exceeds_its_limit_is_killed`), 不能靠拉长
时间范围.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.config import PlatformSettings
from app.manifest import StrategyManifest

from .helpers import SignedInAccount, bearer_headers, create_signed_in_account
from .run_helpers import (
    RESULT_FILENAME,
    RUNS_PATH,
    STATUS_POLL_INTERVAL_SECONDS,
    await_run_terminal,
    job_directory,
    read_job_json,
    read_job_text,
    read_run_record,
    running_client,
    submit_run,
)


REAL_ENGINE_ROOT = Path(
    os.environ.get("QUANT_REAL_ENGINE_ROOT", "D:/Gitee/QuantTrading/bin/Release")
)
REAL_MARKET_DATA_ROOT = Path(
    os.environ.get("QUANT_REAL_MARKET_DATA_ROOT", "D:/MdBaoStock")
)

STRATEGIES_PATH = "/api/strategies"

REAL_ENTRY_FILENAME = "grid_strategy.py"
REAL_CONFIG_FILENAME = "TestStrategyGrid.json"
REAL_SESSION_FILENAME = "Sessions.json"
REAL_SEED_DATABASE_FILENAME = "BackTestInit.db"

STRATEGY_NAME = "成对网格 (真引擎验收)"
CLIENT_SIDE_PART_FILENAME = "whatever-the-browser-called-it.py"

ACCOUNT_ID_PARAMETER_KEY = "AccountId"
LOG_LEVEL_PARAMETER_KEY = "LogLevel"
GRID_STEP_PARAMETER_KEY = "GridStep"
GRID_COUNT_PARAMETER_KEY = "GridCount"
VOLUME_PER_GRID_PARAMETER_KEY = "VolumePerGrid"

DEFAULT_LOG_LEVEL = 2
DEFAULT_GRID_STEP = 10.0
DEFAULT_GRID_COUNT = 5
DEFAULT_VOLUME_PER_GRID = 1

BAR_PERIOD = "5m"
EXCHANGE_ID = "SSE"
INSTRUMENT_ID = "600519"
START_TRADING_DAY = "20241001"
END_TRADING_DAY = "20241231"
INITIAL_CAPITAL = 1000000.0

RUN_TIMEOUT_SECONDS = 600
# 等终态的上界必须比作业级时限宽: 时限到了平台自己会收尾, 这里等的是收尾后的行.
TERMINAL_TIMEOUT_SECONDS = 900

# 制造"跑得够久"的作业只能压时限: 引擎跑三个月的 5m 回测约 1.7 秒, 故时限取 1 秒时它**必然**
# 跑不完 (不依赖机器快慢), 才谈得上"真的杀掉了".
IMPATIENT_RUN_TIMEOUT_SECONDS = 1
IMPATIENT_RUN_DURATION_LIMIT_MS = 20_000
ENGINE_STARTUP_ELAPSED_LIMIT_SECONDS = 30.0
# 被杀之后等这么久再看一眼: "此刻还没有 result.json"证明不了进程死了 (一个还在跑的引擎同样没有
# 它), 而等过"若它还活着早该写完"的那一刻再看, 标志的缺席才等价于死亡.
KILLED_RUN_QUIET_SECONDS = 8.0

# 十五年那一档只用于"让引擎有得跑"的取消用例: 它有 58176 根 bar, 约 3.8 秒, 给取消留出足够宽的
# 窗口. 那一轮的时限仍是 `RUN_TIMEOUT_SECONDS` (要压时限的是超时用例, 它自己带设置).
WIDE_START_TRADING_DAY = "20100101"

STDOUT_FILENAME = "stdout.txt"
STDERR_FILENAME = "stderr.txt"
# 引擎跑完一轮会在 stdout 末尾写下这一行 (`RunResult Written, Path:result.json, Success:…`).
# 它的缺席是"这一轮没跑完"的直接证据——来自引擎自己, 不是平台的推断.
ENGINE_FINISHED_MARKER = "RunResult Written"

# 引擎在**同一批输入**下的量: 成交/委托/行情条数与"种子库在不在"无关, 故这两份基线共用它们.
# P0 基线取自 `QuantTrading/bin/Release/result.json` (docs/job-workspace.md §6), 本轮基线取自
# 同一目录下、同一策略的另一次实测 (见下 `BASELINE_BALANCE_*` 的分工). 相等说明作业目录构造与
# 配置渲染没有改变回测结果.
BASELINE_TRADE_COUNT = 84
BASELINE_ORDER_COUNT = 654
BASELINE_BAR_MARKET_DATA_COUNT = 2928

# **两份基线并存, 各自注明前提**——不要用新的盖掉旧的: 差值恰好是费用三项, 是"引擎行为未变、
# 只是缺了费率表"这个判断的依据.
#
# 种子库 `BackTestInit.db` 在盘上时 (P0): 引擎按它带的费率表收费, 费用三项非 0, 余额是扣费后的.
BASELINE_BALANCE_WITH_SEED_DATABASE = 998951.4506464996
# 种子库不在盘上时 (本轮): 引擎对它的缺失是优雅降级 (`SimExchange.cpp` 只做 `exists` 检查,
# 缺失仅 Warning + `BasicDataLoaded=false`, 继续跑完), 费用三项退化成 0, 余额因此**高出**
# 费用的总和 420.0 + 4.297395 + 1.3419585 = 425.6393535——与两份基线的差 425.6393535003 相符
# (末位差异来自浮点求和次序, 不是行为差异).
BASELINE_BALANCE_WITHOUT_SEED_DATABASE = 999377.0899999999

# 故费用三项与"缺费率"的条数是**输入缺失**的证据, 钉成断言: 日后重建种子库时, 这四条会一起
# 转红, 正好提醒把 `BASELINE_BALANCE_*` 换回带种子库的那一份.
MISSING_SEED_COMMISSION = 0.0
MISSING_SEED_COMMISSION_MISSING_COUNT = 84

# 结果表那一页故意取一个**小于**行数的页大小: 取 100 (上界) 时 `total` 与 `len(records)` 恰好
# 相等, 于是"`LIMIT` 到底有没有生效"这件事在响应里看不出来——一页装得下全表时, 少绑一个参数
# 也一样是对的.
RESULT_PAGE_PROBE_LIMIT = 5

pytestmark = pytest.mark.real_engine


def _engine_inputs_are_present() -> bool:
    """真引擎的几个输入是否都在本机.

    缺任何一个都只能跳过而不是失败: 这套用例的用途是"在这台机器上验一次真引擎", 换台机器跑整套
    测试时它不该变成一个红叉.
    """

    return (
        (REAL_ENGINE_ROOT / REAL_ENTRY_FILENAME).is_file()
        and any(REAL_ENGINE_ROOT.glob("QuantTrading.*.pyd"))
        and (REAL_ENGINE_ROOT / REAL_SESSION_FILENAME).is_file()
        and REAL_MARKET_DATA_ROOT.is_dir()
    )


pytestmark = [
    pytest.mark.real_engine,
    pytest.mark.skipif(
        not _engine_inputs_are_present(),
        reason=f"本机没有真引擎输入: {REAL_ENGINE_ROOT} 或 {REAL_MARKET_DATA_ROOT}",
    ),
]


@pytest.fixture
def real_settings(platform_settings: PlatformSettings) -> PlatformSettings:
    """把默认配置的三个引擎输入换成真货; 运行根与用户库仍在临时目录里.

    运行根留在临时目录是刻意的: 一轮真回测写 3 MB 产物, 而"平台有没有把写路径关进作业目录"这条
    约束在临时目录里照样成立.
    """

    return replace(
        platform_settings,
        engine_root=REAL_ENGINE_ROOT,
        market_data_root=REAL_MARKET_DATA_ROOT,
        session_file_path=REAL_ENGINE_ROOT / REAL_SESSION_FILENAME,
        seed_database_path=REAL_ENGINE_ROOT / REAL_SEED_DATABASE_FILENAME,
        run_timeout_seconds=RUN_TIMEOUT_SECONDS,
        max_concurrent_runs=1,
    )


def read_engine_account_id() -> str:
    """账号取自引擎目录里的那份配置, 不抄进平台仓库.

    这个值只对引擎有意义 (它按账号找资金与持仓), 与平台无关, 故平台的 manifest 把它声明成"无
    默认值的参数"、由提交方给出——本用例的提交方是这里, 取值直接读引擎那份配置.
    """

    configuration = json.loads(
        (REAL_ENGINE_ROOT / REAL_CONFIG_FILENAME).read_text(encoding="utf-8")
    )

    return str(configuration["AccountId"])


def build_real_manifest() -> StrategyManifest:
    """真策略的 manifest: 声明它从策略配置里读的每一个键.

    `grid_strategy.py` 读 `LogLevel` / `AccountId` / `GridStep` / `GridCount` /
    `VolumePerGrid` 以及三个运行级字段 (`ExchangeId` / `InstrumentId` / `BarPreces`).
    少声明一个, 平台渲染出来的配置里就少一个键, 策略在启动期以 `KeyError` 收场——而那一轮的
    `stderr.txt` 里只有一行 traceback, 看起来像策略写坏了.

    三个运行级字段经 `run_field_keys` 映射: 平台于是把**同一个** `BarPreces` 既写进
    `BackTest.json` (引擎实际聚合周期) 又写进策略配置 (策略 `declare_bar_period` 的期望周期),
    §1.2 那条"静默 0 成交"的失效路径因此是结构性关闭的.
    """

    return StrategyManifest.model_validate(
        {
            "entry_filename": REAL_ENTRY_FILENAME,
            "config_filename": REAL_CONFIG_FILENAME,
            "supported_match_modes": ["Bar"],
            "run_field_keys": {
                "exchange_id": "ExchangeId",
                "instrument_id": "InstrumentId",
                "bar_period": "BarPreces",
            },
            "params": [
                {
                    "key": LOG_LEVEL_PARAMETER_KEY,
                    "label": "日志级别",
                    "type": "integer",
                    "default": DEFAULT_LOG_LEVEL,
                    "minimum": 0,
                },
                {
                    "key": ACCOUNT_ID_PARAMETER_KEY,
                    "label": "引擎账号",
                    "type": "string",
                },
                {
                    "key": GRID_STEP_PARAMETER_KEY,
                    "label": "网格步长",
                    "type": "number",
                    "default": DEFAULT_GRID_STEP,
                    "minimum": 0,
                },
                {
                    "key": GRID_COUNT_PARAMETER_KEY,
                    "label": "单向格数",
                    "type": "integer",
                    "default": DEFAULT_GRID_COUNT,
                    "minimum": 1,
                },
                {
                    "key": VOLUME_PER_GRID_PARAMETER_KEY,
                    "label": "每格手数",
                    "type": "integer",
                    "default": DEFAULT_VOLUME_PER_GRID,
                    "minimum": 1,
                },
            ],
        }
    )


async def upload_real_strategy(
    client: AsyncClient, token: str
) -> tuple[str, str]:
    """经上传接口落一份真策略, 返回 (策略 id, 版本 id)."""

    manifest_text = build_real_manifest().model_dump_json()

    response = await client.post(
        STRATEGIES_PATH,
        data={"manifest": manifest_text, "name": STRATEGY_NAME},
        files={
            "source": (
                CLIENT_SIDE_PART_FILENAME,
                (REAL_ENGINE_ROOT / REAL_ENTRY_FILENAME).read_bytes(),
                "text/x-python",
            )
        },
        headers=bearer_headers(token),
    )

    assert response.status_code == 201, response.text

    uploaded = response.json()

    return uploaded["strategy"]["id"], uploaded["versions"][0]["id"]


def engine_database_filenames(settings: PlatformSettings, run_id: str) -> set[str]:
    """作业目录里的 `.db` 文件名.

    作业目录里**只应**有引擎自己派生出来的那一个库: 种子库是按绝对路径引用的、不会被复制进来,
    而渲染器在类型上就传不进绝对写路径. 于是多出一个 `.db` 就是"某个写入者跑到了作业目录之外"
    或"两个作业写了同一个库"的证据——那正是相对路径失效时的样子.
    """

    return {path.name for path in job_directory(settings, run_id).glob("*.db")}


def engine_report_exists(settings: PlatformSettings, run_id: str) -> bool:
    """`result.json` 在不在.

    它在收尾之前**一定**不存在: 宿主启动时先删掉上一轮的那一份. 故"被取消/超时的作业目录里没有
    它"等价于"本轮没走到写结果那一步".
    """

    return (job_directory(settings, run_id) / RESULT_FILENAME).is_file()


async def await_engine_started(settings: PlatformSettings, run_id: str) -> None:
    """等到引擎**真的在跑**: 它自己往 `stdout.txt` 写下了第一行.

    桩用 `span.txt` 自证活着, 引擎不写那个文件——它的等价物是 stdout, 而捕获建立时文件先被建成
    空的, 故"非空"才等价于"进程起来了". 行的状态是 `running` 挡不住这一条: 认领之后、起进程之前
    有一段真实的窗口, 取消落在那段窗口里时平台**根本不会起进程**.
    """

    stdout_path = job_directory(settings, run_id) / STDOUT_FILENAME
    deadline = (
        asyncio.get_running_loop().time() + ENGINE_STARTUP_ELAPSED_LIMIT_SECONDS
    )

    while asyncio.get_running_loop().time() < deadline:
        if stdout_path.is_file() and stdout_path.stat().st_size > 0:
            return

        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"引擎在 {ENGINE_STARTUP_ELAPSED_LIMIT_SECONDS} 秒内没有往 {STDOUT_FILENAME} 写下任何东西"
    )


async def test_a_real_bar_backtest_runs_through_the_platform(
    real_settings: PlatformSettings,
) -> None:
    """① 真引擎一轮 Bar 回测跑通, 指标与 P0 基线逐位一致.

    这是 P2「上传后能跑通」的结清点: 策略经**上传接口**落盘, 作业目录由**调度侧**构造, 引擎由
    **平台的 runner** 以裸文件名启动, 结果由**收尾**镜像进库. 任何一环错, 引擎都会在启动期或
    结果文件里留下一处对不上的数.
    """

    async with running_client(real_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "real-engine-owner")

        strategy_id, version_id = await upload_real_strategy(client, owner.token)

        submitted = await submit_run(
            client,
            owner.token,
            strategy_id,
            strategy_version_id=version_id,
            params={ACCOUNT_ID_PARAMETER_KEY: read_engine_account_id()},
        )

        finished = await await_run_terminal(
            database, submitted.id, TERMINAL_TIMEOUT_SECONDS
        )

        # 结果库那两条端点在真产物上的样子 (P5): 引擎写的表名/列名/条数, 只有真库才检得出.
        # 请求必须在 `async with` 块内发——客户端与后台任务都随块退出而收摊.
        equity_response = await client.get(
            f"{RUNS_PATH}/{submitted.id}/equity", headers=bearer_headers(owner.token)
        )
        trade_table_response = await client.get(
            f"{RUNS_PATH}/{submitted.id}/tables/Trade",
            params={"limit": RESULT_PAGE_PROBE_LIMIT},
            headers=bearer_headers(owner.token),
        )
        # `Order` 是 SQLite 保留字: 这条请求走通了才说明表名真的被引号包住 (裸拼会 500).
        order_table_response = await client.get(
            f"{RUNS_PATH}/{submitted.id}/tables/Order",
            params={"limit": RESULT_PAGE_PROBE_LIMIT},
            headers=bearer_headers(owner.token),
        )

    assert finished.status == RunStatus.SUCCEEDED.value, finished.error_msg
    assert finished.exit_code == 0
    assert finished.is_success is True
    assert finished.error_id == 0
    assert finished.market_data_type == "Bar"

    assert finished.trade_count == BASELINE_TRADE_COUNT
    assert finished.order_count == BASELINE_ORDER_COUNT
    assert finished.bar_market_data_count == BASELINE_BAR_MARKET_DATA_COUNT
    assert finished.balance == BASELINE_BALANCE_WITHOUT_SEED_DATABASE

    # 种子库缺失的降级: 费用三项为 0, 且引擎自己报了"缺费率"的条数.
    assert finished.total_commission == MISSING_SEED_COMMISSION
    assert finished.total_stamp_tax == MISSING_SEED_COMMISSION
    assert finished.total_transfer_fee == MISSING_SEED_COMMISSION
    assert finished.commission_missing_count == MISSING_SEED_COMMISSION_MISSING_COUNT
    assert finished.basic_data_loaded is False

    # `DbHost` 写的是 `./BackTest.db`, 由引擎自己派生成 `BackTest_<RunId>.db`——平台若先拼一份,
    # 这里会变成 `BackTest_<RunId>_<RunId>.db`.
    assert finished.db_path == f"./BackTest_{submitted.id}.db"

    directory = job_directory(real_settings, submitted.id)

    assert (directory / finished.db_path.removeprefix("./")).is_file()
    assert (directory / "Dump" / submitted.id).is_dir()

    engine_configuration = read_job_json(real_settings, submitted.id, "BackTest.json")
    strategy_configuration = read_job_json(
        real_settings, submitted.id, REAL_CONFIG_FILENAME
    )

    assert engine_configuration["RunId"] == submitted.id

    # 「静默 0 成交」那条失效路径 (D.01): 引擎实际聚合周期 (`BackTest.json.BarPreces`) 与策略自己
    # 期望的周期 (`grid_strategy.py:declare_bar_period`) 必须同值. 不一致时策略收不到 bar、**不
    # 报错、只 0 成交**, 故这里直接比两份配置——它们同值才是"平台写的是同一个值"。
    assert engine_configuration["BarPreces"] == BAR_PERIOD
    assert strategy_configuration["BarPreces"] == BAR_PERIOD

    # 三个运行级字段经 `run_field_keys` 落到了策略配置里 (引擎不认识它们, 只有策略订阅用).
    assert strategy_configuration["ExchangeId"] == EXCHANGE_ID
    assert strategy_configuration["InstrumentId"] == INSTRUMENT_ID

    # 结果库的两条读端点 (P5). 判据是"图上的曲线"与"列表里的指标"互相对得上, 而不是钉一个常数:
    # 首点 = 引擎的种子行 (初始资金), 末点 = 最后一个交易日的结算权益, 后者与收尾镜像进库的
    # `Balance` 同值; 首末两个交易日又与 `result.json` 的起止日一致.
    assert equity_response.status_code == 200, equity_response.text

    equity_points = equity_response.json()["points"]

    assert equity_points[0]["balance"] == INITIAL_CAPITAL
    assert equity_points[0]["trading_day"] == finished.start_trading_day
    assert equity_points[-1]["balance"] == finished.balance
    assert equity_points[-1]["trading_day"] == finished.last_trading_day
    assert equity_points[0]["trading_day"] < equity_points[-1]["trading_day"]

    assert trade_table_response.status_code == 200, trade_table_response.text

    trade_page = trade_table_response.json()

    assert trade_page["table"] == "Trade"
    assert trade_page["total"] == BASELINE_TRADE_COUNT
    assert trade_page["offset"] == 0
    assert trade_page["limit"] == RESULT_PAGE_PROBE_LIMIT
    # 页大小小于总行数, 故"这一页装了几行"本身就在证明 `LIMIT` 真的绑上了 (取满 100 时看不出).
    assert len(trade_page["records"]) == RESULT_PAGE_PROBE_LIMIT
    assert "TradingDay" in trade_page["columns"]

    # `Order` 是 SQLite 保留字: 这条能回数据才说明表名真的被引号包住 (裸拼会 500).
    assert order_table_response.status_code == 200, order_table_response.text
    assert order_table_response.json()["total"] == BASELINE_ORDER_COUNT


async def test_two_real_runs_at_once_keep_their_databases_apart(
    real_settings: PlatformSettings,
) -> None:
    """③ 两轮真回测并发跑: 每个作业目录里**只有一个**库, 且那个库的名字嵌着自己的 RunId.

    这是"写路径必须相对"在真引擎上的那一面: 若渲染器写出绝对路径 (或写出同一个固定位置), 两个
    引擎会打开同一个库——单作业时看不出来, 并发时才表现为互相踩写. 判据不是"两轮都成功" (撞库
    时它也可能成立), 而是**库文件名与作业一一对应**, 且两轮报出的指标逐位相同.

    用例另配一条并发证据: 两行的 `[started_at, finished_at]` 区间**必须相交**. 调度器串行跑完
    两轮时隔离断言同样成立, 但那样测的就不是并发——区间相交是"两个作业真的同时在跑"留痕.
    """

    concurrent_settings = replace(real_settings, max_concurrent_runs=2)

    async with running_client(concurrent_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(
            database, client, "real-concurrency-owner"
        )

        strategy_id, version_id = await upload_real_strategy(client, owner.token)
        account_id = read_engine_account_id()

        first_submitted = await submit_run(
            client,
            owner.token,
            strategy_id,
            strategy_version_id=version_id,
            params={ACCOUNT_ID_PARAMETER_KEY: account_id},
        )
        second_submitted = await submit_run(
            client,
            owner.token,
            strategy_id,
            strategy_version_id=version_id,
            params={ACCOUNT_ID_PARAMETER_KEY: account_id},
        )

        first_finished = await await_run_terminal(
            database, first_submitted.id, TERMINAL_TIMEOUT_SECONDS
        )
        second_finished = await await_run_terminal(
            database, second_submitted.id, TERMINAL_TIMEOUT_SECONDS
        )

    for submitted, finished in (
        (first_submitted, first_finished),
        (second_submitted, second_finished),
    ):
        assert finished.status == RunStatus.SUCCEEDED.value, finished.error_msg
        assert finished.trade_count == BASELINE_TRADE_COUNT
        assert finished.balance == BASELINE_BALANCE_WITHOUT_SEED_DATABASE
        assert finished.db_path == f"./BackTest_{submitted.id}.db"
        assert engine_database_filenames(concurrent_settings, submitted.id) == {
            f"BackTest_{submitted.id}.db"
        }

    assert first_finished.db_path != second_finished.db_path

    assert first_finished.started_at < second_finished.finished_at
    assert second_finished.started_at < first_finished.finished_at


async def test_a_real_run_that_exceeds_its_limit_is_killed(
    real_settings: PlatformSettings,
) -> None:
    """④ 真引擎跑不完时限时被真的杀掉: 没有 `result.json`, 也没有引擎自己那句"结果已写出".

    超时路径在桩上已测过, 这里换真引擎是要证**`TerminateProcess` 停得住那个 C++ 宿主**——桩是纯
    Python, 它被杀只说明 CPython 能被杀. 判据取引擎自己的终局标志: 跑完一轮它会往 stdout 写
    `RunResult Written`, 被打死的引擎写不出来. 那一行的缺席是"它没跑完"的直接证据; 少了它, 一条
    `timeout` 的行也可能是"引擎其实跑完了、收尾判错了".

    时限取 1 秒是**必杀**而不是碰运气: 引擎跑这一档实测 3.8 秒起.
    """

    impatient_settings = replace(
        real_settings, run_timeout_seconds=IMPATIENT_RUN_TIMEOUT_SECONDS
    )

    async with running_client(impatient_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "real-timeout-owner")

        strategy_id, version_id = await upload_real_strategy(client, owner.token)

        submitted = await submit_run(
            client,
            owner.token,
            strategy_id,
            strategy_version_id=version_id,
            start_trading_day=WIDE_START_TRADING_DAY,
            end_trading_day=END_TRADING_DAY,
            params={ACCOUNT_ID_PARAMETER_KEY: read_engine_account_id()},
        )

        finished = await await_run_terminal(
            database, submitted.id, TERMINAL_TIMEOUT_SECONDS
        )

        await asyncio.sleep(KILLED_RUN_QUIET_SECONDS)

        observed_later = await read_run_record(database, submitted.id)

    assert finished.status == RunStatus.TIMEOUT.value, finished.error_msg
    assert finished.error_msg
    assert finished.is_success is None
    assert finished.exit_code is not None

    # 耗时算的是"从开始跑到收尾", 故下界是时限本身, 上界留给 terminate → kill 那两步的宽限.
    assert (
        IMPATIENT_RUN_TIMEOUT_SECONDS * 1_000
        <= finished.duration_ms
        < IMPATIENT_RUN_DURATION_LIMIT_MS
    )

    # 超时不是"引擎报告失败": 指标一列都不镜像 (见 §4.3 的终态仲裁), 故两列都停在 INSERT 时的样子.
    # 空判据分两种写法, 取决于列的默认值: 可空的 (`TradeCount`) 是 NULL, 而 `DbPath` 是
    # `String(500), default=""`——它"没有被写过"的证据是空串, 不是 None.
    assert finished.trade_count is None
    assert finished.db_path == ""

    # 那一轮若还活着, 到这里早该跑完并写下终局标志了.
    assert observed_later.status == RunStatus.TIMEOUT.value
    assert not engine_report_exists(impatient_settings, submitted.id)

    engine_output = read_job_text(
        impatient_settings, submitted.id, STDOUT_FILENAME
    )

    assert ENGINE_FINISHED_MARKER not in engine_output
    assert (job_directory(impatient_settings, submitted.id) / STDERR_FILENAME).is_file()


async def test_cancelling_a_running_real_engine_job_stops_it(
    real_settings: PlatformSettings,
) -> None:
    """⑤ 取消一个正在跑的真引擎作业: 进程停住, 且**没有** `result.json`.

    `result.json` 的缺席是陈旧结果防护生效的证据: 作业目录里那一份是宿主在启动时删掉的, 故"本轮
    没走到写结果那一步"与"文件不存在"是同一件事. 少了这一步, 一个被取消的作业会带着上一轮的
    结果被判成功——而那正是用户刚取消掉的那一轮.

    "取消落在已认领但进程未起那一格"的语义在桩用例里测过 (那里能构造出那一格); 这里先等引擎自己
    在 stdout 上出声再取消, 于是**一定**是"取消一个正在跑的进程"这一档.
    """

    async with running_client(real_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "real-cancel-owner")

        strategy_id, version_id = await upload_real_strategy(client, owner.token)

        submitted = await submit_run(
            client,
            owner.token,
            strategy_id,
            strategy_version_id=version_id,
            start_trading_day=WIDE_START_TRADING_DAY,
            end_trading_day=END_TRADING_DAY,
            params={ACCOUNT_ID_PARAMETER_KEY: read_engine_account_id()},
        )

        await await_engine_started(real_settings, submitted.id)

        response = await client.post(
            f"{RUNS_PATH}/{submitted.id}/cancel",
            headers=bearer_headers(owner.token),
        )

        assert response.status_code == 200, response.text

        finished = await await_run_terminal(
            database, submitted.id, TERMINAL_TIMEOUT_SECONDS
        )

        await asyncio.sleep(KILLED_RUN_QUIET_SECONDS)

        observed_later = await read_run_record(database, submitted.id)

    assert finished.status == RunStatus.INTERRUPTED.value, finished.error_msg
    assert finished.is_success is None
    # 退出码有值而指标全空, 正是"起了进程、被杀、没跑完"这一格的形状 (Windows 的 terminate 给 1,
    # 与"宿主启动失败"同码——所以判据只能看标记, 不能看码).
    assert finished.exit_code is not None
    # 指标一列都不镜像: 可空的那几列是 NULL, 而 `DbPath` 的列默认值是空串 (见 ④ 的同一条说明).
    assert finished.trade_count is None
    assert finished.db_path == ""
    assert finished.duration_ms is not None

    # 那一轮若还活着, 到这里早该跑完并写下终局标志了.
    assert observed_later.status == RunStatus.INTERRUPTED.value
    assert not engine_report_exists(real_settings, submitted.id)

    engine_output = read_job_text(real_settings, submitted.id, STDOUT_FILENAME)

    assert engine_output
    assert ENGINE_FINISHED_MARKER not in engine_output


