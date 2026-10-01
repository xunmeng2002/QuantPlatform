"""真引擎验收: 桩策略覆盖不到的那一层.

桩是纯 Python, 它证明得了"平台把配置写对了" (桩把读到的 `RunId` / `MatchMode` / `DbHost` 回显进
结果文件), 但证明不了"引擎认这份配置". 只有真引擎才有的东西: `PYTHONPATH` 与 `.pyd`/DLL 的
解析, `result.json` 的真实键名与口径, `MatchMode`/`BarPreces` 的真实对应, `MdDataPath` 的正确
形态 (写错得到 `ErrorId 0x100F`), `DbHost` 的派生行为, 以及种子库缺失时引擎的降级.

**一条被实测推翻的旧记录** (此处留档, 免得后人又照抄): 平台启动引擎时传**裸文件名**, 但这**不是**
因为"带路径的 `argv[0]` 会在启动期被日志器 `fopen` 失败打死"——2026-09-25 在本机
`QuantTrading.cp314-win_amd64.pyd` 上实测, `./grid_strategy.py` 与
`C:\\...\\Temp\\<job>\\grid_strategy.py` 两种带路径形式都**跑完了整轮 Bar 回测、退出码 0**.
(**2026-10-01 注**: 本机 `bin/Release` 里只有 `QuantTrading.cp311-win_amd64.pyd`, 故这条记录
里的 `.pyd` 名字对不上这台机器——那次实测要么在别的机器上, 要么名字本就是照抄错的. 结论不受
影响, 它证的是"带路径的 `argv[0]` 能跑完整轮", 与解释器小版本无关.)
裸文件名因此是"够用且与 `result.json.DbPath` 派生口径一致"的选择, 而不是唯一可行的形式;
`job-workspace.md` §3.1 与 `PROGRESS.md` 的 D.01 已按实测订正.

故这里走一遍**完整链路**: 经上传接口落一份真策略 (QuantTrading 仓内**未做任何修改**的
`grid_strategy.py`) → 提交 → 调度侧构造作业目录 → 起真引擎 → 收尾 → 逐项比对引擎自己报的数.

**默认不跑** (标 `real_engine`, 见 pytest.ini): 一轮真回测会往作业目录写约 3.0 MB 产物 (876 KB 的
`.db`、549 KB 的 stdout、`Dump/<RunId>/` 下 17 个 CSV, 与 P0 记的 3.0 MB 相符), 且依赖两个仓之外
的东西——`QuantTrading/bin/Release` 下的扩展模块与仓内的 `market-data/` 行情. 两者都由环境变量
给出, 不在此处写死:

    set QUANT_REAL_ENGINE_ROOT=D:\\Gitee\\QuantTrading\\bin\\Release
    set QUANT_REAL_MARKET_DATA_ROOT=D:\\Gitee\\QuantPlatform\\market-data
    python -m pytest -m real_engine tests/test_real_engine_acceptance.py -v

`--basetemp` 落在仓内的 `_acc_tmp/` (已被 `.gitignore` 覆盖): 五个用例各留下一个作业目录,
共约 25 MB, 而 `%TEMP%` 下的临时根在排查时不好找——验收失败时第一件事就是进去看
`stdout.txt` 与 `result.json` 到底有没有写出来.

五个用例里有两个是**同一个场景的两档订阅周期** (`5m` 与 `15m`): 后者是"落盘精度与用户订阅周期
是两件事"这条契约在真引擎上的判据, 见 `test_a_subscription_period_finer_than_the_dataset_is_aggregated`.

    python -m pytest -m real_engine tests/test_real_engine_acceptance.py -v --basetemp=_acc_tmp

本机实测 (三个月的 5m 回测约 1.7 秒, 十五年 2010–2024 的 58176 根 bar 约 3.8 秒): **引擎比预期
快得多**, 故"制造一个跑得够久的作业"只能靠把下限压到它跑不完 (见
`test_a_real_run_that_exceeds_its_limit_is_killed`), 不能靠拉长时间范围.

⚠️ **上面这条实测原来记的是「Python 3.14.5 + `QuantTrading.cp314-win_amd64.pyd`」，那句已删**：
2026-10-01 查 `test_engine_probe.py` 的 4 项既有失败时发现，本机是 **Python 3.11.1**、引擎目录里
只有 `QuantTrading.cp311-win_amd64.pyd`，那句 cp314 不描述这台机器（详情见 `PROGRESS.md` 备注的
「环境锁定」段）。耗时那两个数保留 —— 它们与解释器版本无关。
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
from app.config import MARKET_DATA_PRECISION, SUBSCRIPTION_BAR_PERIODS, PlatformSettings

from .helpers import SignedInAccount, bearer_headers, create_signed_in_account
from .quote_hub_stub import build_covered_component
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
    os.environ.get("QUANT_REAL_MARKET_DATA_ROOT", "D:/Gitee/QuantPlatform/market-data")
)

#: 验收用的行情组件桩, 落在临时目录里.
#:
#: **必须是个桩**, 不能沿用默认值 `../QuoteHub`: 那几个用例提交的都映射了合约, 于是准备步骤要查
#: 组件库判"本地够不够" —— 真组件在位而本地没下过这个区间时, 一轮验收会**真的联网下载**(BaoStock,
#: 以小时计). 验收要证的是"平台把引擎包对了", 不是"组件会下载".
#:
#: 覆盖区间取到 1990–2099 而不是桩的默认区间: 宽区间那条用例从 2010 起跑, 若只覆盖到 2019, 准备
#: 步骤照样会去起那个桩脚本下载一次.
ACCEPTANCE_QUOTE_HUB_ROOT_NAME = "acceptance-quote-hub"
ACCEPTANCE_COVERED_START_DAY = "1990-01-01"
ACCEPTANCE_COVERED_END_DAY = "2099-12-31"

STRATEGIES_PATH = "/api/strategies"

REAL_ENTRY_FILENAME = "grid_strategy.py"
REAL_CONFIG_FILENAME = "TestStrategyGrid.json"
REAL_SESSION_FILENAME = "Sessions.json"
REAL_SEED_DATABASE_FILENAME = "BackTestInit.db"

STRATEGY_NAME = "成对网格 (真引擎验收)"

ACCOUNT_ID_PARAMETER_KEY = "AccountId"
LOG_LEVEL_PARAMETER_KEY = "LogLevel"
GRID_STEP_PARAMETER_KEY = "GridStep"
GRID_COUNT_PARAMETER_KEY = "GridCount"
VOLUME_PER_GRID_PARAMETER_KEY = "VolumePerGrid"

DEFAULT_LOG_LEVEL = 2
# 步长是**比例**（0.01 = 1%），不是绝对价格：档位与平仓价都按乘算（见 `grid_strategy.py`
# 的 `GridParams`）。旧口径的 10.0 在新口径下是越界值（10 × GridCount ≥ 1），策略构造期即拒启。
DEFAULT_GRID_STEP = 0.01
DEFAULT_GRID_COUNT = 5
DEFAULT_VOLUME_PER_GRID = 1

#: 上传配置模板里 `AccountId` 的占位值. **刻意不等于**引擎那份配置里的账号: 同值的话, "用户改的
#: 那个值真的落进了策略配置"这一条在真回测上就分不出来——模板默认值与提交值相同时, "覆写生效"与
#: "覆写被忽略"两种实现都断言得过.
TEMPLATE_ACCOUNT_ID_PLACEHOLDER = "模板占位账号"

# ── 两种「周期」是两件事 ─────────────────────────────────────────────────────
#
# 落盘行情只有 5m, 而引擎读哪一族 parquet 由 `BackTest.json.BarPreces` 决定, 聚合到多粗则由策略
# 订阅的目标周期 (`TestStrategyGrid.json.BarPreces`) 决定. 把一个值写进这两处, 正是本轮要修的
# 那个类别错误: 选 15m 时引擎按 `Preces = '15m'` 去过滤, 磁盘上没有这一族, 一行都读不到, 于是它
# 在**装载期**以 `ErrorMarketDataNotExist` 拒掉整轮 (见 `SimExchange.cpp`), 白跑一次.
#
#: 落盘精度, 平台常量. 引擎那份 `BackTest.json` 的 `BarPreces` **恒**为它, 与用户选什么无关.
DATASET_BAR_PERIOD = MARKET_DATA_PRECISION
#: 提交页选定的**订阅周期**, 即策略配置里 `BarPreces` 的取值. 取 5m 时与落盘精度同值 (不必聚合).
SUBSCRIPTION_BAR_PERIOD = "5m"
#: 与落盘精度**不同**的那一档, 供"用户选了要聚合的周期"那条用例用.
AGGREGATED_SUBSCRIPTION_BAR_PERIOD = "15m"

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

# ── 常量区: 只留**平台自己的**数字 ─────────────────────────────────────────────
#
# `BAR_MARKET_DATA_COUNT` 量的是"引擎**读到了**平台指的那批 bar", 是 `MdDataPath` + `BarPreces` +
# 起止交易日三者合起来的直接证据, 且**与策略无关** (只由行情范围决定, 不随步长动). 它变了, 就是
# 平台把递过去的输入写歪了.
#
# **这里有意不再记录成交 / 委托 / 余额 / 费用那几个数.** 它们是引擎拿那批 bar 算出来的**下游
# 结果**, 由策略与行情决定, 不是平台的产出: 换了策略、合约或区间, 它们本就该不一样, 记下来对
# 别的回测毫无参照价值. 而就这一个场景而言也不划算 —— 引擎二进制、种子库、行情 parquet、策略
# 文件全在**本仓之外**, 它们任何一个变了数字就变, 平台却没做错任何事. 履历也印证了这点: 那份
# 记录值 (成交 `34` / 委托 `629` / 两份余额) 自 `ebaaab1` 引入起只被手工重取过一次
# (`60a67e9`, 网格步长改比例 `84 → 34`)、又过期过一次 (2026-10-01 实跑 `435`, 原因在仓外),
# **没有一次指向平台缺陷**. 它当初是用来验 "(P3) 作业目录构造与配置渲染没有改变回测结果" 的
# 脚手架, 那个开发阶段早已结束; 它想验的东西现在由下面的断言**直接**钉着 —— 引擎那份 `BarPreces`
# 恒为落盘精度而策略那份是提交的订阅周期、三个运行级键按固定键名落进策略配置、`DbHost` →
# `DbPath` 的派生、预填往返 —— 那些断言的是**契约本身**, 不依赖任何记录值, 也永远不会过期.
BAR_MARKET_DATA_COUNT = 2928

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
def real_settings(platform_settings: PlatformSettings, tmp_path: Path) -> PlatformSettings:
    """把默认配置的三个引擎输入换成真货; 运行根、用户库与行情组件仍在临时目录里.

    运行根留在临时目录是刻意的: 一轮真回测写 3 MB 产物, 而"平台有没有把写路径关进作业目录"这条
    约束在临时目录里照样成立. 行情组件同理 (见 `ACCEPTANCE_QUOTE_HUB_ROOT_NAME` 的说明) —— 真组件
    在位时, 这一轮验收会先去联网把行情下下来.
    """

    return replace(
        platform_settings,
        engine_root=REAL_ENGINE_ROOT,
        market_data_root=REAL_MARKET_DATA_ROOT,
        quote_hub_root=build_covered_component(
            tmp_path / ACCEPTANCE_QUOTE_HUB_ROOT_NAME,
            start_day=ACCEPTANCE_COVERED_START_DAY,
            end_day=ACCEPTANCE_COVERED_END_DAY,
        ),
        session_file_path=REAL_ENGINE_ROOT / REAL_SESSION_FILENAME,
        seed_database_path=REAL_ENGINE_ROOT / REAL_SEED_DATABASE_FILENAME,
        run_timeout_seconds=RUN_TIMEOUT_SECONDS,
        max_concurrent_runs=1,
    )


def read_engine_account_id() -> str:
    """账号取自引擎目录里的那份配置, 不抄进平台仓库.

    这个值只对引擎有意义 (它按账号找资金与持仓), 与平台无关, 故它不是平台那三个运行级键之一,
    而是策略自己的一个参数——模板里给它一个占位值, 提交时由本用例换成引擎那份配置里的真账号.
    """

    configuration = json.loads(
        (REAL_ENGINE_ROOT / REAL_CONFIG_FILENAME).read_text(encoding="utf-8")
    )

    return str(configuration["AccountId"])


def build_real_configuration_template() -> dict[str, object]:
    """真策略那份配置 JSON 的内容, 也就是它的参数模板.

    **这份模板即上传的那份 `.json` 的正文**, 而它的键就是提交页要渲染的全部控件. `grid_strategy.py`
    在启动时读 `LogLevel` / `AccountId` / `GridStep` / `GridCount` / `VolumePerGrid` 五个键以及三个
    运行级键 (`ExchangeId` / `InstrumentId` / `BarPreces`); 少一个, 策略在启动期以 `KeyError` 收场
    ——而那一轮的 `stderr.txt` 里只有一行 traceback, 看起来像策略写坏了.

    三个运行级键**不在这里**: 平台渲染时按固定键名覆写 (模板里没有就新增), 模板自己声明它们反而
    会被覆盖掉. 两者的分工见 `app/strategy_configuration.py` 的模块 docstring.
    """

    return {
        LOG_LEVEL_PARAMETER_KEY: DEFAULT_LOG_LEVEL,
        ACCOUNT_ID_PARAMETER_KEY: TEMPLATE_ACCOUNT_ID_PLACEHOLDER,
        GRID_STEP_PARAMETER_KEY: DEFAULT_GRID_STEP,
        GRID_COUNT_PARAMETER_KEY: DEFAULT_GRID_COUNT,
        VOLUME_PER_GRID_PARAMETER_KEY: DEFAULT_VOLUME_PER_GRID,
    }


async def upload_real_strategy(
    client: AsyncClient, token: str
) -> tuple[str, str]:
    """经上传接口落一份真策略, 返回 (策略 id, 版本 id).

    上传的是**两份文件**, 而它们的文件名就是作业目录里的文件名——策略源码里 `open("TestStrategyGrid.json")`
    写死了那一个, 故这里的部件名必须用它本身, 另取一个 (如"浏览器叫它什么") 会让策略在启动期读不到
    配置. 这一条正是新契约与旧 manifest 的分界: 名字不再由平台另填一份元数据声明, 而是文件带的.
    """

    response = await client.post(
        STRATEGIES_PATH,
        data={"name": STRATEGY_NAME},
        files={
            "source": (
                REAL_ENTRY_FILENAME,
                (REAL_ENGINE_ROOT / REAL_ENTRY_FILENAME).read_bytes(),
                "text/x-python",
            ),
            "configuration": (
                REAL_CONFIG_FILENAME,
                json.dumps(
                    build_real_configuration_template(), ensure_ascii=False, indent=2
                ).encode("utf-8"),
                "application/json",
            ),
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
    """① 真引擎一轮 Bar 回测跑通, 平台递过去的输入逐件对得上.

    这是 P2「上传后能跑通」的结清点: 策略经**上传接口**落盘, 作业目录由**调度侧**构造, 引擎由
    **平台的 runner** 以裸文件名启动, 结果由**收尾**镜像进库. 任何一环错, 引擎都会在启动期或
    结果文件里留下一处对不上的数.

    **判据只落在平台的产出上**: 渲染出的两份配置、`DbHost` 派生、预填往返、读端点与镜像列互证.
    引擎的下游结果 (成交 / 委托 / 余额 / 费用) **一个都不判**——它们由策略与行情决定, 换了场景就
    该不一样, 记下来只会在仓外的输入变动时误报. 理由与履历见常量区.
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
        # 提交页的预填来源 (D.10): 同样是块内请求.
        prefill_response = await client.get(
            f"{STRATEGIES_PATH}/{strategy_id}/last-submitted-parameters",
            headers=bearer_headers(owner.token),
        )

    assert finished.status == RunStatus.SUCCEEDED.value, finished.error_msg
    assert finished.exit_code == 0
    assert finished.is_success is True
    assert finished.error_id == 0
    assert finished.market_data_type == "Bar"

    # 引擎读到的 bar 条数**是平台的收成**: `MdDataPath` + `BarPreces` + 起止交易日三者只要有一处
    # 被平台写歪, 这个数就动; 它也与策略无关, 故它不动就说明"引擎拿到的确实是平台指的那批数据".
    assert finished.bar_market_data_count == BAR_MARKET_DATA_COUNT

    # 成交 / 委托 / 余额 / 费用**不做任何断言**, 连"记录值对照"也不做 —— 理由见常量区.
    # 这几个数已由平台镜像进 `Runs` 并在运行详情页可见, 不复述.

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

    # 两份配置里的 `BarPreces` 是**两件事**, 而它们的取值差正是本轮修掉的类别错误 (见常量区):
    # 引擎那份决定读哪一族 parquet, 恒为落盘精度; 策略那份是策略订阅的目标周期, 取自提交值.
    assert engine_configuration["BarPreces"] == DATASET_BAR_PERIOD
    assert strategy_configuration["BarPreces"] == SUBSCRIPTION_BAR_PERIOD

    # 三个运行级键按**固定键名**落进策略配置 (引擎不认识它们, 只有策略订阅与下单用). 它们不在上传
    # 的模板里, 故这三条同时说明平台确实补上了这三个.
    assert strategy_configuration["ExchangeId"] == EXCHANGE_ID
    assert strategy_configuration["InstrumentId"] == INSTRUMENT_ID

    # `AccountId` 是**用户改过的**那个参数: 模板里是占位值, 提交时换成引擎那份配置里的真账号.
    # 少了这一条, "覆写真的写进了策略配置"就分不出来——模板值与提交值相同时两种实现都对得上.
    assert strategy_configuration[ACCOUNT_ID_PARAMETER_KEY] == read_engine_account_id()

    # 提交页预填 (D.10): 读回来的必须是**刚提交的那一份**, 逐字段对上. 这一条落在真回测上, 故它
    # 同时证明三件事: 提交写库的两份文本解得出原值; `BackTest.json` 里 `MatchMode` 那个 int 反查
    # 得回枚举; 参数**恰好**是模板声明的那五个——三个运行级键名 (`BarPreces` / `ExchangeId` /
    # `InstrumentId`) 已被按固定键名剔掉, 不会混进参数控件.
    assert prefill_response.status_code == 200, prefill_response.text

    prefill = prefill_response.json()

    assert prefill["run_id"] == submitted.id
    assert prefill["match_mode"] == "Bar"
    assert prefill["bar_period"] == SUBSCRIPTION_BAR_PERIOD
    assert prefill["exchange_id"] == EXCHANGE_ID
    assert prefill["instrument_id"] == INSTRUMENT_ID
    assert prefill["start_trading_day"] == START_TRADING_DAY
    assert prefill["end_trading_day"] == END_TRADING_DAY
    assert prefill["initial_capital"] == INITIAL_CAPITAL
    assert prefill["params"][ACCOUNT_ID_PARAMETER_KEY] == read_engine_account_id()
    assert prefill["params"][GRID_STEP_PARAMETER_KEY] == DEFAULT_GRID_STEP
    assert set(prefill["params"]) == {
        LOG_LEVEL_PARAMETER_KEY,
        ACCOUNT_ID_PARAMETER_KEY,
        GRID_STEP_PARAMETER_KEY,
        GRID_COUNT_PARAMETER_KEY,
        VOLUME_PER_GRID_PARAMETER_KEY,
    }

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
    # 总行数与收尾镜像进 `Runs` 的那一列**互证**: 两条路是分别算出来的 (一条数结果库的行, 一条
    # 是引擎回写时冻结的), 对不上就是其中一条写歪了. 这里有意不比一个常数——那个数由引擎的下游
    # 算术决定, 与平台无关 (见常量区).
    assert trade_page["total"] == finished.trade_count
    assert trade_page["offset"] == 0
    assert trade_page["limit"] == RESULT_PAGE_PROBE_LIMIT
    # 页大小小于总行数, 故"这一页装了几行"本身就在证明 `LIMIT` 真的绑上了 (取满 100 时看不出).
    assert len(trade_page["records"]) == RESULT_PAGE_PROBE_LIMIT
    assert "TradingDay" in trade_page["columns"]

    # `Order` 是 SQLite 保留字: 这条能回数据才说明表名真的被引号包住 (裸拼会 500).
    assert order_table_response.status_code == 200, order_table_response.text
    assert order_table_response.json()["total"] == finished.order_count


async def test_a_subscription_period_finer_than_the_dataset_is_aggregated(
    real_settings: PlatformSettings,
) -> None:
    """② 用户选 15m 时跑得通: 引擎那份 `BarPreces` 仍是落盘精度, 聚合发生在读之后.

    这一条直接钉住本轮修掉的类别错误. 磁盘上的行情只有 5m, 引擎按 `BackTest.json.BarPreces` 去
    过滤 parquet; 把用户选的周期写进那处时, 引擎按 `Preces = '15m'` 一行都读不到, 于是它在**装载
    期**即以 `ErrorMarketDataNotExist` 收场 (不是"安静地零成交"), 整轮白跑——而运行详情只会说
    "引擎报告本轮回测失败", 用户看不出是周期选错了.

    判据分两层, 缺一不可: "跑通了"只说明没报错; 而**读到的 bar 条数与 5m 那一档完全相同**才说明
    它读的确实是那批 5m 数据——聚合发生在读之后, 故 `BarMarketDataCount` 不随订阅周期动. 它若变
    了, 就是引擎真按 15m 去过滤了, 那正是"一行都读不到"的另一面.
    """

    assert AGGREGATED_SUBSCRIPTION_BAR_PERIOD in SUBSCRIPTION_BAR_PERIODS
    assert AGGREGATED_SUBSCRIPTION_BAR_PERIOD != DATASET_BAR_PERIOD

    async with running_client(real_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(
            database, client, "real-aggregation-owner"
        )

        strategy_id, version_id = await upload_real_strategy(client, owner.token)

        submitted = await submit_run(
            client,
            owner.token,
            strategy_id,
            strategy_version_id=version_id,
            bar_period=AGGREGATED_SUBSCRIPTION_BAR_PERIOD,
            params={ACCOUNT_ID_PARAMETER_KEY: read_engine_account_id()},
        )

        finished = await await_run_terminal(
            database, submitted.id, TERMINAL_TIMEOUT_SECONDS
        )

        engine_configuration = read_job_json(
            real_settings, submitted.id, "BackTest.json"
        )
        strategy_configuration = read_job_json(
            real_settings, submitted.id, REAL_CONFIG_FILENAME
        )

    assert finished.status == RunStatus.SUCCEEDED.value, finished.error_msg
    assert finished.bar_market_data_count == BAR_MARKET_DATA_COUNT

    assert engine_configuration["BarPreces"] == DATASET_BAR_PERIOD
    assert strategy_configuration["BarPreces"] == AGGREGATED_SUBSCRIPTION_BAR_PERIOD


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
        assert finished.db_path == f"./BackTest_{submitted.id}.db"
        assert engine_database_filenames(concurrent_settings, submitted.id) == {
            f"BackTest_{submitted.id}.db"
        }

    # 两轮**互比, 不比常数** —— 这才是上面那段 docstring 说的事: 撞库的表现是两轮的数**不一样**
    # (后写完的那轮把前一轮的结果掺进来). 至于那个共同的数该是多少, 是引擎的事, 不是平台的.
    assert first_finished.trade_count == second_finished.trade_count
    assert first_finished.balance == second_finished.balance

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


