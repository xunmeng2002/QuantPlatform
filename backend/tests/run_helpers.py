"""运行侧夹具: 桩策略的落盘与上传、可跑的作业、以及读作业产物.

与 `helpers.py` 的分工: 那边造的是"经接口能取到的租户数据", 这边造的是"能被调度器真的跑起来
的东西". 差别在**版本目录里有没有文件**——`create_strategy_version_record` 只落库, 走不完真实
作业路径 (作业目录构造要从版本目录复制入口文件), 故这里必须真的调 `store_strategy_version`.

作业产物 (span.txt / argv0.txt / result.json) 一律经**文件系统**读, 不走接口: 断言的是"进程
真实做了什么", 而接口会把结果按平台的判据重新解释一遍.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.database import PlatformDatabase
from app.catalog.enums import (
    TERMINAL_RUN_STATUSES,
    MarketDataType,
    RunStatus,
    StrategyVisibility,
)
from app.catalog.models import RunModel, StrategyModel, StrategyVersionModel, UserModel
from app.catalog.schemas import RunDetailResponse
from app.config import PlatformSettings
from app.ids import generate_identifier
from app.main import create_application
from app.manifest import StrategyManifest
from app.services.strategy_store import store_strategy_version

from .helpers import bearer_headers


STUB_SOURCE_PATH = Path(__file__).resolve().parent / "strategies" / "stub_strategy.py"

CONFIG_FILENAME_TOKEN = '"<<CONFIG_FILENAME>>"'

STUB_ENTRY_FILENAME = "stub_strategy.py"
STUB_CONFIG_FILENAME = "StubStrategy.json"
STUB_SPACE_ENTRY_FILENAME = "stub strategy.py"

RUNS_PATH = "/api/runs"

ENGINE_CONFIGURATION_FILENAME = "BackTest.json"
RESULT_FILENAME = "result.json"
ARGV0_FILENAME = "argv0.txt"
SPAN_FILENAME = "span.txt"

# 一份"目录里有一棵带嵌套的树"用到的名字. 只求像真的, 不求与引擎逐字一致——用它写的作业目录
# 不参与任何端到端的断言, 断言的是"整棵树被移走 / 整棵树还在".
DUMP_DIRECTORY_NAME = "Dump"
STDOUT_FILENAME = "stdout.log"
TRADE_FILENAME = "t_trade.csv"

BEHAVIOR_PARAMETER_KEY = "Behavior"
SLEEP_SECONDS_PARAMETER_KEY = "SleepSeconds"
EXIT_DELAY_SECONDS_PARAMETER_KEY = "ExitDelaySeconds"
FLOOD_BYTES_PARAMETER_KEY = "FloodBytes"
STDERR_FLOOD_BYTES_PARAMETER_KEY = "StderrFloodBytes"

BAR_PERIOD_CONFIGURATION_KEY = "BarPreces"
EXCHANGE_ID_CONFIGURATION_KEY = "ExchangeId"
INSTRUMENT_ID_CONFIGURATION_KEY = "InstrumentId"

DEFAULT_BEHAVIOR = "success"
DEFAULT_SLEEP_SECONDS = 0
DEFAULT_EXIT_DELAY_SECONDS = 0
DEFAULT_FLOOD_BYTES = 0
DEFAULT_STDERR_FLOOD_BYTES = 0

DEFAULT_BAR_PERIOD = "5m"
DEFAULT_EXCHANGE_ID = "SSE"
DEFAULT_INSTRUMENT_ID = "600519"
DEFAULT_START_TRADING_DAY = "20241001"
DEFAULT_END_TRADING_DAY = "20241231"
DEFAULT_INITIAL_CAPITAL = 1000000.0

STATUS_POLL_INTERVAL_SECONDS = 0.02
DEFAULT_TERMINAL_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class RunnableStrategy:
    """一份真的落在盘上、可以提交回测的策略."""

    strategy: StrategyModel
    version: StrategyVersionModel
    manifest: StrategyManifest


def build_stub_source(config_filename: str = STUB_CONFIG_FILENAME) -> bytes:
    """按目标配置文件名生成桩策略源码.

    桩只能按硬编码的名字读自己的配置 (真策略 `grid_strategy.py` 同样把
    `TestStrategyGrid.json` 写死在源码里), 故文件名在**上传前**替换进去.
    """

    template = STUB_SOURCE_PATH.read_text(encoding="utf-8")

    if CONFIG_FILENAME_TOKEN not in template:
        raise LookupError(f"桩策略模板里找不到配置文件名占位符: {STUB_SOURCE_PATH}")

    return template.replace(
        CONFIG_FILENAME_TOKEN, json.dumps(config_filename)
    ).encode("utf-8")


def build_stub_manifest(
    entry_filename: str = STUB_ENTRY_FILENAME,
    config_filename: str = STUB_CONFIG_FILENAME,
    supported_match_modes: Sequence[MarketDataType] = (MarketDataType.BAR,),
    run_field_keys: dict[str, str] | None = None,
    required_parameter_keys: frozenset[str] = frozenset(),
) -> StrategyManifest:
    """桩策略的 manifest.

    三个运行级字段全映射: 平台因此会把 `BarPreces` / `ExchangeId` / `InstrumentId` 写进策略
    配置, 而**同一份 `BackTest.json` 里也有一份 `BarPreces`**——用例 19/20 就是拿这两处去对.

    `required_parameter_keys` 里的参数**不带 `default`**, 于是提交方必须给出: §1.1 用"缺省即
    必填"替代 `required` 标志, 这一条路径因此只能这样表达.
    """

    if run_field_keys is None:
        run_field_keys = {
            "exchange_id": EXCHANGE_ID_CONFIGURATION_KEY,
            "instrument_id": INSTRUMENT_ID_CONFIGURATION_KEY,
            "bar_period": BAR_PERIOD_CONFIGURATION_KEY,
        }

    parameter_entries = [
        {
            "key": BEHAVIOR_PARAMETER_KEY,
            "label": "桩行为",
            "type": "string",
            "default": DEFAULT_BEHAVIOR,
            "options": [{"value": behavior} for behavior in _stub_behaviors()],
        },
        {
            "key": SLEEP_SECONDS_PARAMETER_KEY,
            "label": "启动后先睡多少秒",
            "type": "number",
            "default": DEFAULT_SLEEP_SECONDS,
            "minimum": 0,
        },
        {
            "key": EXIT_DELAY_SECONDS_PARAMETER_KEY,
            "label": "写完结果再等多少秒才退",
            "type": "number",
            "default": DEFAULT_EXIT_DELAY_SECONDS,
            "minimum": 0,
        },
        {
            "key": FLOOD_BYTES_PARAMETER_KEY,
            "label": "往标准输出灌多少字节",
            "type": "integer",
            "default": DEFAULT_FLOOD_BYTES,
            "minimum": 0,
        },
        {
            "key": STDERR_FLOOD_BYTES_PARAMETER_KEY,
            "label": "往标准错误灌多少字节",
            "type": "integer",
            "default": DEFAULT_STDERR_FLOOD_BYTES,
            "minimum": 0,
        },
    ]

    for parameter_entry in parameter_entries:
        if parameter_entry["key"] in required_parameter_keys:
            parameter_entry.pop("default")

    return StrategyManifest.model_validate(
        {
            "entry_filename": entry_filename,
            "config_filename": config_filename,
            "supported_match_modes": [
                match_mode.value for match_mode in supported_match_modes
            ],
            "run_field_keys": run_field_keys,
            # 五个旋钮默认全给默认值, 于是每条用例只提交它真正关心的那一个; 需要"必填"那条路径的
            # 用例经 `required_parameter_keys` 单独摘掉一个.
            "params": parameter_entries,
        }
    )


def _stub_behaviors() -> tuple[str, ...]:
    """桩认识的全部行为名, 从桩源码的常量定义里读出来.

    在这里另抄一份的话, 桩新增一个行为而这里没跟上, 用例提交的取值会先被 manifest 的 `options`
    拒掉——报错指向参数校验, 与真正的原因隔了两层.
    """

    behaviors = []

    for line in STUB_SOURCE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("BEHAVIOR_") or " = " not in line:
            continue

        declared_value = line.split(" = ", 1)[1].strip()

        if not (declared_value.startswith('"') and declared_value.endswith('"')):
            raise LookupError(f"桩行为常量不再是字符串字面量: {line}")

        behaviors.append(declared_value.strip('"'))

    if not behaviors:
        raise LookupError(f"桩源码里一个行为常量都没读到: {STUB_SOURCE_PATH}")

    return tuple(sorted(behaviors))


async def create_runnable_strategy(
    database: PlatformDatabase,
    settings: PlatformSettings,
    owner: UserModel,
    name: str,
    entry_filename: str = STUB_ENTRY_FILENAME,
    config_filename: str = STUB_CONFIG_FILENAME,
    visibility_type: StrategyVisibility = StrategyVisibility.PRIVATE,
    manifest: StrategyManifest | None = None,
) -> RunnableStrategy:
    """建一个策略并把它的一份版本**真的写到盘上**.

    版本走 `store_strategy_version`, 与上传接口同一条代码路径: 作业目录构造要从版本目录复制入口
    文件, 只落库的版本会让每一个作业在"复制入口文件"那一步失败, 而那种失败看起来像调度器的缺陷.
    """

    resolved_manifest = manifest or build_stub_manifest(entry_filename, config_filename)

    strategy = StrategyModel(
        id=generate_identifier(),
        owner_user_id=owner.id,
        name=name,
        description=f"{name} 的说明",
        visibility_type=visibility_type.value,
    )

    async with database.session_scope() as session:
        session.add(strategy)
        await session.commit()

        version = await store_strategy_version(
            session,
            settings,
            strategy,
            owner,
            build_stub_source(config_filename),
            resolved_manifest,
        )

    return RunnableStrategy(strategy=strategy, version=version, manifest=resolved_manifest)


def build_run_request(
    strategy_id: str,
    strategy_version_id: str | None = None,
    match_mode: MarketDataType = MarketDataType.BAR,
    bar_period: str = DEFAULT_BAR_PERIOD,
    exchange_id: str | None = DEFAULT_EXCHANGE_ID,
    instrument_id: str | None = DEFAULT_INSTRUMENT_ID,
    start_trading_day: str = DEFAULT_START_TRADING_DAY,
    end_trading_day: str = DEFAULT_END_TRADING_DAY,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    """一份合法的提交请求体.

    与 `submit_run` 分开: 校验用例要的是"把某一个字段改坏之后再发出去", 若请求体只能由
    `submit_run` 内部拼出来, 那些用例就得各抄一份完整请求体——而漏抄一个字段会让它测的东西
    悄悄换掉 (少一个必填字段, 报错就与想测的那条规则无关了).
    """

    request_body: dict[str, object] = {
        "strategy_id": strategy_id,
        "match_mode": match_mode.value,
        "bar_period": bar_period,
        "start_trading_day": start_trading_day,
        "end_trading_day": end_trading_day,
        "initial_capital": initial_capital,
        "params": dict(params or {}),
    }

    if strategy_version_id is not None:
        request_body["strategy_version_id"] = strategy_version_id

    if exchange_id is not None:
        request_body["exchange_id"] = exchange_id

    if instrument_id is not None:
        request_body["instrument_id"] = instrument_id

    return request_body


async def post_run(
    client: AsyncClient, token: str | None, request_body: dict[str, object]
) -> Response:
    """把一份请求体发出去, 原样返回响应.

    `token` 可为 None: 无令牌那一档要的正是"连请求头都不带"的响应.
    """

    headers = bearer_headers(token) if token is not None else {}

    return await client.post(RUNS_PATH, json=request_body, headers=headers)


async def submit_run(
    client: AsyncClient,
    token: str,
    strategy_id: str,
    strategy_version_id: str | None = None,
    match_mode: MarketDataType = MarketDataType.BAR,
    bar_period: str = DEFAULT_BAR_PERIOD,
    exchange_id: str | None = DEFAULT_EXCHANGE_ID,
    instrument_id: str | None = DEFAULT_INSTRUMENT_ID,
    start_trading_day: str = DEFAULT_START_TRADING_DAY,
    end_trading_day: str = DEFAULT_END_TRADING_DAY,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    params: dict[str, object] | None = None,
) -> RunDetailResponse:
    """经接口提交一次回测, 断言 201 再解出响应体."""

    response = await post_run(
        client,
        token,
        build_run_request(
            strategy_id,
            strategy_version_id=strategy_version_id,
            match_mode=match_mode,
            bar_period=bar_period,
            exchange_id=exchange_id,
            instrument_id=instrument_id,
            start_trading_day=start_trading_day,
            end_trading_day=end_trading_day,
            initial_capital=initial_capital,
            params=params,
        ),
    )

    assert response.status_code == 201, response.text

    return RunDetailResponse.model_validate(response.json())


async def read_run_record(database: PlatformDatabase, run_id: str) -> RunModel:
    """直接从库里读一行运行, 不经接口.

    接口会把行按响应模型重新组装一遍, 而用例关心的是入库的**列** (比如 `runner_pid` 留没留、
    `duration_ms` 是 NULL 还是 0), 那些列在响应体里未必有.
    """

    async with database.session_scope() as session:
        run = await session.get(RunModel, run_id)

        if run is None:
            raise LookupError(f"运行记录不存在: {run_id}")

        return run


async def read_run_or_none(
    database: PlatformDatabase, run_id: str
) -> RunModel | None:
    """同上, 但行不在时返回 None.

    "行真的没了"是一整类用例的主结果 (删除、保留清理都是), 而拿一个会抛错的读取器去问它, 那些
    用例会死在与结论无关的 LookupError 上.
    """

    async with database.session_scope() as session:
        return await session.get(RunModel, run_id)


async def await_run_status(
    database: PlatformDatabase,
    run_id: str,
    expected_status: RunStatus,
    timeout_seconds: float = DEFAULT_TERMINAL_TIMEOUT_SECONDS,
) -> RunModel:
    """轮询到行处于指定状态为止.

    轮询而不是等 `scheduler.wait_until_idle`: 后者判的是"作业任务集合空", 而提交之后、调度循环
    认领之前那一瞬它**也是空的**, 于是它会在作业还没开始跑时就返回 True. 库里的状态没有这个
    问题——行是持久记录.
    """

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    observed_status = RunStatus.QUEUED.value

    while asyncio.get_running_loop().time() < deadline:
        run = await read_run_record(database, run_id)
        observed_status = run.status

        if observed_status == expected_status.value:
            return run

        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"运行在 {timeout_seconds} 秒内未进入 {expected_status.value}, "
        f"当前为 {observed_status}"
    )


async def await_run_terminal(
    database: PlatformDatabase,
    run_id: str,
    timeout_seconds: float = DEFAULT_TERMINAL_TIMEOUT_SECONDS,
) -> RunModel:
    """轮询到终态为止."""

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    observed_status = RunStatus.QUEUED.value

    while asyncio.get_running_loop().time() < deadline:
        run = await read_run_record(database, run_id)
        observed_status = run.status

        if observed_status in TERMINAL_RUN_STATUSES:
            return run

        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"运行在 {timeout_seconds} 秒内未落终态, 当前为 {observed_status}")


def job_directory(settings: PlatformSettings, run_id: str) -> Path:
    """一个作业的工作目录 (可能不存在)."""

    return settings.runs_root / run_id


def write_job_directory(settings: PlatformSettings, run_id: str) -> Path:
    """写一份作业目录: 顶层两个文件 + `Dump/<RunId>/` 一层嵌套, 返回那个目录.

    与 `test_run_artifacts.write_job_files` 的分工: 那边按引擎的真实布局写, 断言的是"产物清单与
    下载取到什么"; 这里的契约只是"目录里有一棵**带嵌套**的树", 用于断言"整棵树被移走"或"整棵树
    还在"——不带嵌套的话, `rmtree` 与"只删顶层文件"这两种实现分不开.
    """

    directory = job_directory(settings, run_id)
    (directory / DUMP_DIRECTORY_NAME / run_id).mkdir(parents=True)
    (directory / RESULT_FILENAME).write_text(
        f'{{"RunId": "{run_id}"}}', encoding="utf-8"
    )
    (directory / STDOUT_FILENAME).write_text("engine started\n", encoding="utf-8")
    (directory / DUMP_DIRECTORY_NAME / run_id / TRADE_FILENAME).write_text(
        "index,price\n1,10.5\n", encoding="utf-8"
    )

    return directory


@contextmanager
def open_result_database_for_writing(
    database_file: Path,
) -> Iterator[sqlite3.Connection]:
    """以可写方式打开一份结果库, 出块即**关闭并提交**.

    直接用 `with sqlite3.connect(...)` 是不行的: `Connection.__exit__` 只提交或回滚, **不关闭
    连接**. 连接不关, Windows 上那个文件就被本进程占着——用例随后要移走它 (对比端点的曲线降级、
    保留清理) 会拿到 `WinError 32`, 而报错指向"删不掉", 与真正的原因隔着两层.

    出块时才提交: 块里抛异常就整段回滚, 不留一份半建的库.
    """

    with closing(sqlite3.connect(database_file)) as connection:
        yield connection
        connection.commit()


async def await_process_started(
    settings: PlatformSettings,
    run_id: str,
    timeout_seconds: float = DEFAULT_TERMINAL_TIMEOUT_SECONDS,
) -> None:
    """等到桩**自己**写下 `span.txt` 为止, 即"进程确实开始执行了".

    行的状态是 `running` 挡不住这一条: 认领之后、起进程之前有一段真实的窗口 (读上下文、构造作业
    目录), 取消落在那段窗口里时平台**根本不会起进程**, 于是作业目录里连 `span.txt` 都没有. 要
    测"取消掉一个正在跑的进程", 就得先等到进程真的在跑——而这件事只有进程自己能证明.
    """

    span_path = job_directory(settings, run_id) / SPAN_FILENAME
    deadline = asyncio.get_running_loop().time() + timeout_seconds

    while asyncio.get_running_loop().time() < deadline:
        if span_path.exists():
            return

        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"作业在 {timeout_seconds} 秒内没有写出 {SPAN_FILENAME}")


def read_job_json(
    settings: PlatformSettings, run_id: str, filename: str
) -> dict[str, object]:
    """读作业目录里的一个 JSON 文件."""

    return json.loads(
        (job_directory(settings, run_id) / filename).read_text(encoding="utf-8")
    )


def read_job_text(settings: PlatformSettings, run_id: str, filename: str) -> str:
    """读作业目录里的一个文本文件.

    `errors="replace"` 是**必需的**, 不是省事: 引擎宿主是 C++, 它的 stdout 不保证是 UTF-8
    (本机实测混有本地码页的字节), 而且进程被 terminate/kill 时最后一个多字节字符可能**只写了一半**
    ——实测复现 (6 次里 1 次): 严格解码会在非法续字节上抛 `UnicodeDecodeError`. 断言看的是
    ASCII 哨兵行, 替换字符只会落在坏字节处, 不影响这些判据.
    """

    return (job_directory(settings, run_id) / filename).read_text(
        encoding="utf-8", errors="replace"
    )


def read_reported_argv0(settings: PlatformSettings, run_id: str) -> str:
    """桩自己写下的 `sys.argv[0]`."""

    return read_job_text(settings, run_id, ARGV0_FILENAME)


def read_span_intervals(
    settings: PlatformSettings, run_id: str
) -> list[tuple[float, float]]:
    """一个作业的 `[start, end]` 区间; 没有 end 行时区间到正无穷.

    桩被杀时不会写 end 行, 于是"它到底跑没跑完"是可判的——超时用例正是靠这一点断言进程真的死了.
    """

    start_moments: dict[int, float] = {}
    intervals: list[tuple[float, float]] = []

    for line in read_job_text(settings, run_id, SPAN_FILENAME).splitlines():
        phase, raw_pid, raw_moment = line.split()
        process_id = int(raw_pid)

        if phase == "start":
            start_moments[process_id] = float(raw_moment)
        else:
            intervals.append(
                (start_moments.get(process_id, float("-inf")), float(raw_moment))
            )

    return intervals


def count_span_end_lines(settings: PlatformSettings, run_id: str) -> int:
    """`span.txt` 里 end 行的条数."""

    return sum(
        1
        for line in read_job_text(settings, run_id, SPAN_FILENAME).splitlines()
        if line.startswith("end ")
    )


def maximum_overlap(intervals: Sequence[tuple[float, float]]) -> int:
    """一组区间的最大重叠数.

    按端点排序扫描, 而不是"轮询采样当前 running 数": 采样在偏差下几乎恒真 (作业跑完了才采到
    两个), 杀不掉任何变异; 区间是进程自己落的证据, 与采样时刻无关.
    """

    boundaries = sorted(
        [(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals]
    )

    running_count = 0
    highest_count = 0

    for _, delta in boundaries:
        running_count += delta
        highest_count = max(highest_count, running_count)

    return highest_count


def settings_with(
    settings: PlatformSettings, **overrides: object
) -> PlatformSettings:
    """派生一份配置副本; `__post_init__` 会重新跑一遍校验."""

    return dataclasses.replace(settings, **overrides)


@asynccontextmanager
async def running_application(settings: PlatformSettings) -> AsyncIterator[FastAPI]:
    """按给定配置起一个应用 (走完整 lifespan).

    供需要非默认配置的用例自建实例 (并发上限、运行时限). 这些用例**不得**同时用 `client` 夹具:
    那会起第二个调度器去扫同一个库, 两个循环各认领一半, 断言就失去了意义.
    """

    application = create_application(settings)

    async with application.router.lifespan_context(application):
        yield application


@asynccontextmanager
async def running_client(
    settings: PlatformSettings,
) -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    """自建应用 + 经 ASGI 直连的客户端, 库与应用都从返回值里取."""

    async with running_application(settings) as application:
        transport = ASGITransport(app=application)

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield application, client


def database_of(application: FastAPI) -> PlatformDatabase:
    """应用持有的数据库门面."""

    return application.state.database


async def count_runs_with_status(session: AsyncSession, status: RunStatus) -> int:
    """库里处于某状态的运行条数, 供"提交被拒则库里无新行"这类断言用."""

    total = await session.scalar(
        select(func.count()).select_from(RunModel).where(RunModel.status == status.value)
    )

    return int(total or 0)


async def count_run_rows(database: PlatformDatabase) -> int:
    """运行表的总行数.

    "被拒的提交什么都不许落"必须按**总行数**判, 不能只看 `queued` 的条数: 一个把行写成别的状态
    的实现同样是在库里留了东西.
    """

    async with database.session_scope() as session:
        total = await session.scalar(select(func.count()).select_from(RunModel))

        return int(total or 0)


def run_directory_names(settings: PlatformSettings) -> set[str]:
    """运行根下现有的目录名 (运行根不存在时为空集)."""

    if not settings.runs_root.exists():
        return set()

    return {entry.name for entry in settings.runs_root.iterdir() if entry.is_dir()}
