"""提交页预填: 从该用户在该策略下最近一次提交里读回参数.

「最近一次」= 最近一次**提交**, 与那一轮成没成功无关 (用户拍板): 失败后往往只改一两个参数重跑,
只认成功的那次反而会在最需要它的时候失效. 取数走 `build_owned_run_query`, 故共享与公开策略下
别人的运行不会成为你的预填值——粒度是 (用户, 策略).

正向那条走**真提交** (through-HTTP 层): "提交时落库的那两份配置文本能原样解回来"这句话, 只有
拿真提交写出来的文本才证得了; 手搓一份 JSON 只能证明手搓的那份能解. 其余边界走记录级层: 只有
"别人更新的轮次""同 SubmittedAt 的平局""坏 JSON"这几条需要伪造数据, 手搓更直接.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import MarketDataType, RunStatus, StrategyVisibility
from app.catalog.models import RunModel, StrategyModel, StrategyVersionModel, UserModel
from app.catalog.schemas import LastSubmittedParametersResponse
from app.clock import utc_now
from app.config import MARKET_DATA_PRECISION, PlatformSettings

from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    STRATEGIES_PATH,
    SignedInAccount,
    bearer_headers,
    create_run_record,
    create_signed_in_account,
    create_strategy_record,
    create_strategy_version_record,
    create_user_record,
    login,
    update_record_by_id,
)
from .run_helpers import (
    BAR_PERIOD_CONFIGURATION_KEY,
    BEHAVIOR_PARAMETER_KEY,
    EXCHANGE_ID_CONFIGURATION_KEY,
    EXIT_DELAY_SECONDS_PARAMETER_KEY,
    FLOOD_BYTES_PARAMETER_KEY,
    INSTRUMENT_ID_CONFIGURATION_KEY,
    SLEEP_SECONDS_PARAMETER_KEY,
    STDERR_FLOOD_BYTES_PARAMETER_KEY,
    create_runnable_strategy,
    submit_run,
)


PREFILL_STRATEGY_NAME = "预填用的桩策略"
RECORD_STRATEGY_NAME = "预填的记录级策略"
PUBLIC_STRATEGY_NAME = "预填的公开策略"

OWNER_USERNAME = "prefill-owner"
OUTSIDER_USERNAME = "prefill-outsider"

# 一律取与夹具默认值不同的取值: 填对了才区分得开"读回了上一次"与"回的是默认值".
RECORDED_BAR_PERIOD = "30m"
RECORDED_EXCHANGE_ID = "SZSE"
RECORDED_INSTRUMENT_ID = "000001"
RECORDED_START_TRADING_DAY = "20220104"
RECORDED_END_TRADING_DAY = "20221230"
RECORDED_INITIAL_CAPITAL = 250000.0
RECORDED_FLOOD_BYTES = 16

RECORDED_PARAMETER_KEY = "GridStep"
RECORDED_PARAMETER_VALUE = 0.02

# 策略配置: 三个运行级键名是**平台固定的那三个**, 外加一个用户参数.
RECORDED_STRATEGY_CONFIGURATION = json.dumps(
    {
        RECORDED_PARAMETER_KEY: RECORDED_PARAMETER_VALUE,
        EXCHANGE_ID_CONFIGURATION_KEY: RECORDED_EXCHANGE_ID,
        INSTRUMENT_ID_CONFIGURATION_KEY: RECORDED_INSTRUMENT_ID,
        BAR_PERIOD_CONFIGURATION_KEY: RECORDED_BAR_PERIOD,
    }
)

# 引擎配置: 只留这条读路径真正会碰的键 (`BarPreces` 是引擎侧既有拼写, 非笔误).
#
# 引擎那份 `BarPreces` 取**落盘精度**而不是 `RECORDED_BAR_PERIOD`: 两份配置里的 `BarPreces` 本来
# 就是两件事 (引擎按它选读哪一族 parquet, 策略按它说自己要聚合到多粗), 故真实数据里它们多半不同.
# 这里刻意让它们不等, "周期取自策略配置"这条才可分: 一个从引擎配置读的实现会读回 `5m`.
RECORDED_ENGINE_CONFIGURATION = json.dumps(
    {
        "RunId": "recorded-run",
        "MatchMode": 3,
        "BarPreces": MARKET_DATA_PRECISION,
        "StartTradingDay": RECORDED_START_TRADING_DAY,
        "EndTradingDay": RECORDED_END_TRADING_DAY,
        "InitialCapital": RECORDED_INITIAL_CAPITAL,
    }
)

PREFILL_PATH_TEMPLATE = f"{STRATEGIES_PATH}/{{strategy_id}}/last-submitted-parameters"


@dataclass(frozen=True)
class SubmittedBoard:
    """经真实提交路径造出的一轮运行."""

    token: str
    strategy_id: str
    run_id: str


@dataclass(frozen=True)
class RecordBoard:
    """记录级造出的一个账号、一个策略与它的一份版本."""

    token: str
    owner: UserModel
    strategy: StrategyModel
    version: StrategyVersionModel


def prefill_path(strategy_id: str) -> str:
    return PREFILL_PATH_TEMPLATE.format(strategy_id=strategy_id)


async def read_prefill(
    client: AsyncClient, token: str | None, strategy_id: str
) -> LastSubmittedParametersResponse:
    """请求预填端点并解出响应体; 非 200 直接失败."""

    headers = bearer_headers(token) if token is not None else {}
    response = await client.get(prefill_path(strategy_id), headers=headers)

    assert response.status_code == 200, response.text

    return LastSubmittedParametersResponse.model_validate(response.json())


async def submit_recording_run(
    database: PlatformDatabase,
    board: RecordBoard,
    owner: UserModel | None = None,
    **overrides: Any,
) -> RunModel:
    """记录级落一轮"跑过"的运行: 两份配置文本与状态都由调用方给."""

    arguments: dict[str, Any] = {
        "status": RunStatus.SUCCEEDED,
        "params_json": RECORDED_STRATEGY_CONFIGURATION,
        "backtest_config_json": RECORDED_ENGINE_CONFIGURATION,
    }
    arguments.update(overrides)

    return await create_run_record(
        database,
        owner if owner is not None else board.owner,
        board.strategy,
        board.version,
        **arguments,
    )


async def publish_strategy(database: PlatformDatabase, board: RecordBoard) -> None:
    """把夹具策略改成公开.

    "可见"与"有记忆"是两件事, 而后者才是归属过滤管的那一件: 要断言归属过滤, 先得让这个策略真的
    看得见——在一个看不见的策略上得到空结果, 可见性闸自己就会给出同样的结果.
    """

    await update_record_by_id(
        database,
        StrategyModel,
        board.strategy.id,
        lambda strategy: setattr(
            strategy, "visibility_type", StrategyVisibility.PUBLIC.value
        ),
    )


@pytest_asyncio.fixture
async def record_board(
    client: AsyncClient, database: PlatformDatabase
) -> RecordBoard:
    """记录级的一个账号、一个私有策略与它的一份版本.

    版本本身不参与预填 (取数只读那一轮的两份配置文本), 故用 `create_strategy_version_record` 的
    默认模板即可——预填路径根本不碰 `configuration_json`.
    """

    owner = await create_user_record(database, OWNER_USERNAME)
    strategy = await create_strategy_record(database, owner, RECORD_STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, owner)

    return RecordBoard(
        token=await login(client, OWNER_USERNAME, DEFAULT_MEMBER_PASSWORD),
        owner=owner,
        strategy=strategy,
        version=version,
    )


@pytest_asyncio.fixture
async def submitted_board(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> SubmittedBoard:
    """一个可提交的策略, 外加经接口真提交的一轮."""

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, PREFILL_STRATEGY_NAME
    )

    submitted = await submit_run(
        client,
        run_owner.token,
        runnable.strategy.id,
        bar_period=RECORDED_BAR_PERIOD,
        exchange_id=RECORDED_EXCHANGE_ID,
        instrument_id=RECORDED_INSTRUMENT_ID,
        start_trading_day=RECORDED_START_TRADING_DAY,
        end_trading_day=RECORDED_END_TRADING_DAY,
        initial_capital=RECORDED_INITIAL_CAPITAL,
        # 灌 16 字节 stdout 而非睡 7 秒: 要的是"一个非默认的、认得出的取值", 不是真让桩慢下来.
        params={FLOOD_BYTES_PARAMETER_KEY: RECORDED_FLOOD_BYTES},
    )

    return SubmittedBoard(
        token=run_owner.token,
        strategy_id=runnable.strategy.id,
        run_id=submitted.id,
    )


async def test_a_submitted_run_is_read_back_as_its_own_parameters(
    client: AsyncClient, submitted_board: SubmittedBoard
) -> None:
    """刚提交的那一轮就是"上一次": 运行级字段逐项相等, 参数按原类型带回来.

    这里读的是**提交自己写下的**两份文本, 故它同时是"写进去的与读出来的逐字一致"的证明. 那一轮
    跑成什么样与本条无关: 两份文本在提交那一刻就已落库.
    """

    prefill = await read_prefill(
        client, submitted_board.token, submitted_board.strategy_id
    )

    assert prefill.run_id == submitted_board.run_id
    assert prefill.submitted_at is not None
    assert prefill.match_mode == MarketDataType.BAR
    assert prefill.bar_period == RECORDED_BAR_PERIOD
    assert prefill.exchange_id == RECORDED_EXCHANGE_ID
    assert prefill.instrument_id == RECORDED_INSTRUMENT_ID
    assert prefill.start_trading_day == RECORDED_START_TRADING_DAY
    assert prefill.end_trading_day == RECORDED_END_TRADING_DAY
    assert prefill.initial_capital == RECORDED_INITIAL_CAPITAL
    assert prefill.params[FLOOD_BYTES_PARAMETER_KEY] == RECORDED_FLOOD_BYTES


async def test_run_level_key_names_are_not_reported_as_parameters(
    client: AsyncClient, submitted_board: SubmittedBoard
) -> None:
    """三个运行级键名不出现在 `params` 里, 参数**恰好**是模板里的那几个.

    它们已经在具名字段上; 再作为参数带回去, 前端就会给一个引擎键名 (`BarPreces` 这种) 渲染出一个
    参数控件. 断言取等而非"不含": 渲染结果恰好等于 `{平台那三个键} ∪ {模板的键}`, 没有第三类键
    ——"参数 = 策略配置减去平台那三个"是一条**常量差集**, 不再需要去查版本的 manifest.
    """

    prefill = await read_prefill(
        client, submitted_board.token, submitted_board.strategy_id
    )

    assert set(prefill.params) == {
        BEHAVIOR_PARAMETER_KEY,
        SLEEP_SECONDS_PARAMETER_KEY,
        EXIT_DELAY_SECONDS_PARAMETER_KEY,
        FLOOD_BYTES_PARAMETER_KEY,
        STDERR_FLOOD_BYTES_PARAMETER_KEY,
    }

    for run_field_key_name in (
        BAR_PERIOD_CONFIGURATION_KEY,
        EXCHANGE_ID_CONFIGURATION_KEY,
        INSTRUMENT_ID_CONFIGURATION_KEY,
    ):
        assert run_field_key_name not in prefill.params


async def test_a_strategy_without_history_reads_back_as_no_memory(
    client: AsyncClient, record_board: RecordBoard
) -> None:
    """没跑过 → 200 且字段全空, 不是 404.

    首次使用某个策略走的就是这条路, 而 404 已经表示"策略不存在或不可见": 两种含义挤在一个状态码
    上, 前端就分不清该报错还是该显示默认值.
    """

    prefill = await read_prefill(client, record_board.token, record_board.strategy.id)

    assert prefill.run_id is None
    assert prefill.submitted_at is None
    assert prefill.match_mode is None
    assert prefill.bar_period is None
    assert prefill.exchange_id is None
    assert prefill.instrument_id is None
    assert prefill.start_trading_day is None
    assert prefill.end_trading_day is None
    assert prefill.initial_capital is None
    assert prefill.params == {}


async def test_a_failed_run_is_read_back_like_any_other(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """"最近一次"不看成败: 失败那一轮照样是预填来源 (用户拍板).

    夹具造的就是一轮 `failed`——它单独成立, 才说明实现里没有一条 `Status == succeeded` 的过滤.
    """

    run = await submit_recording_run(
        database, record_board, status=RunStatus.FAILED
    )

    prefill = await read_prefill(client, record_board.token, record_board.strategy.id)

    assert prefill.run_id == run.id
    assert prefill.params[RECORDED_PARAMETER_KEY] == RECORDED_PARAMETER_VALUE


async def test_the_run_level_values_are_read_from_the_strategy_configuration(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """三项运行级取值都取自**策略配置**里的固定键名, 且那三个键不进参数.

    交换/标的两项只存在于策略配置里 (引擎不认识它们), 故这两项成立就说明读的确实是那一份.

    周期这一项是**两份配置取值不同**的那一格, 也是本条真正的位置: 引擎配置里也有一模一样的
    `BarPreces` 键 (引擎按它选读哪一族 parquet, 恒为落盘精度), 从那里读的实现会读回 `5m`.
    """

    await submit_recording_run(database, record_board)

    prefill = await read_prefill(client, record_board.token, record_board.strategy.id)

    assert prefill.exchange_id == RECORDED_EXCHANGE_ID
    assert prefill.instrument_id == RECORDED_INSTRUMENT_ID
    assert prefill.bar_period == RECORDED_BAR_PERIOD
    assert prefill.bar_period != MARKET_DATA_PRECISION
    assert prefill.params == {RECORDED_PARAMETER_KEY: RECORDED_PARAMETER_VALUE}


async def test_the_newest_submission_wins_over_an_earlier_one(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """多轮取最新, 与成败无关."""

    submitted_base = utc_now()

    await submit_recording_run(
        database, record_board, submitted_at=submitted_base
    )
    newest_run = await submit_recording_run(
        database,
        record_board,
        status=RunStatus.FAILED,
        submitted_at=submitted_base + timedelta(minutes=1),
    )

    prefill = await read_prefill(client, record_board.token, record_board.strategy.id)

    assert prefill.run_id == newest_run.id


async def test_a_submitted_at_tie_is_broken_by_the_run_id(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """同一 `SubmittedAt` 时按主键兜平局: 两条都排在最前, 没有兜法就无"最新"可言.

    Windows 上 `SubmittedAt` 只有毫秒精度, 同毫秒提交的两轮可以完全同值 (调度侧认领队列用了同一个
    兜法). 主键因此在这里是**捏在手里**的: 只按时间排序时"取到哪一条"不确定, 那样的实现过不了
    这一条.
    """

    tied_submitted_at = utc_now()
    smaller_id_run = await submit_recording_run(
        database, record_board, run_id="0" * 32, submitted_at=tied_submitted_at
    )
    larger_id_run = await submit_recording_run(
        database, record_board, run_id="f" * 32, submitted_at=tied_submitted_at
    )

    assert smaller_id_run.submitted_at == larger_id_run.submitted_at

    prefill = await read_prefill(client, record_board.token, record_board.strategy.id)

    assert prefill.run_id == larger_id_run.id


async def test_another_users_newer_run_is_not_adopted(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """归属人的轮次再新也不算你的: 粒度是 (用户, 策略), 不是策略.

    策略取 `public` 是为了让"可见"与"有记忆"分开——只要可见性对了就照归属人的参数填表, 是这条
    特性最容易踩的那一脚.
    """

    await publish_strategy(database, record_board)
    await submit_recording_run(database, record_board)

    outsider = await create_signed_in_account(database, client, OUTSIDER_USERNAME)

    prefill = await read_prefill(client, outsider.token, record_board.strategy.id)

    assert prefill.run_id is None
    assert prefill.params == {}


async def test_each_user_reads_back_their_own_latest_submission(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """同一策略下, 各自拿各自的那一份——两个方向都断言.

    只断言"别人的不算数"的话, 一个把归属过滤写成 `user_id != 当前用户` 的实现照样通过.
    """

    await publish_strategy(database, record_board)

    outsider = await create_signed_in_account(database, client, OUTSIDER_USERNAME)

    submitted_base = utc_now()

    outsider_run = await submit_recording_run(
        database,
        record_board,
        owner=outsider.user,
        submitted_at=submitted_base,
    )
    owner_run = await submit_recording_run(
        database, record_board, submitted_at=submitted_base + timedelta(minutes=1)
    )

    outsider_prefill = await read_prefill(
        client, outsider.token, record_board.strategy.id
    )
    owner_prefill = await read_prefill(
        client, record_board.token, record_board.strategy.id
    )

    assert outsider_prefill.run_id == outsider_run.id
    assert owner_prefill.run_id == owner_run.id


@pytest.mark.parametrize("broken_column", ["params_json", "backtest_config_json"])
@pytest.mark.parametrize("broken_text", ["{", "[]"])
async def test_an_unreadable_configuration_reads_back_as_no_memory(
    broken_column: str,
    broken_text: str,
    client: AsyncClient,
    database: PlatformDatabase,
    record_board: RecordBoard,
) -> None:
    """两份配置文本读不动 (坏 JSON 或不是对象) → 当作没有记忆, 不是 500.

    提交页是入口: 那里因为一条历史数据而报错, 用户既看不懂也无从修复. 两种坏法都试: 解析失败与
    形状不对是两条不同的分支.
    """

    await submit_recording_run(
        database, record_board, **{broken_column: broken_text}
    )

    prefill = await read_prefill(client, record_board.token, record_board.strategy.id)

    assert prefill.run_id is None
    assert prefill.params == {}


async def test_a_single_bad_value_only_costs_its_own_field(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """单项读不动时逐项回默认, 不把整轮预填作废.

    一个字段是坏数据, 没有理由让其余十来个字段的预填一起失效. `true` 冒充 `1` 这一档是显式的:
    `bool` 是 `IntEnum`/`int` 的子类, 不挡的话 `MatchMode: true` 会被当成 1 去查表.

    坏的是**引擎那一份**, 故策略配置侧那几项 (合约/标的/周期) 照常读回: 两份配置是两个来源, 一边
    坏掉不该把另一边也带下去.
    """

    await submit_recording_run(
        database,
        record_board,
        backtest_config_json=json.dumps(
            {
                "MatchMode": True,
                "StartTradingDay": RECORDED_START_TRADING_DAY,
                "EndTradingDay": RECORDED_END_TRADING_DAY,
                # 数值列给字符串: 转换是另一件事, 这里只降级不猜.
                "InitialCapital": "250000",
            }
        ),
    )

    prefill = await read_prefill(client, record_board.token, record_board.strategy.id)

    assert prefill.run_id is not None
    assert prefill.match_mode is None
    assert prefill.initial_capital is None
    assert prefill.bar_period == RECORDED_BAR_PERIOD
    assert prefill.start_trading_day == RECORDED_START_TRADING_DAY
    assert prefill.end_trading_day == RECORDED_END_TRADING_DAY


async def test_an_invisible_strategy_yields_404(
    client: AsyncClient, database: PlatformDatabase, record_board: RecordBoard
) -> None:
    """看不到的策略 → 404, 与详情接口同一条可见性收口.

    `private` 且未授权: 跨租户一律"不存在", 403 会说漏"这个 id 是真的".
    """

    outsider = await create_signed_in_account(database, client, OUTSIDER_USERNAME)

    response = await client.get(
        prefill_path(record_board.strategy.id),
        headers=bearer_headers(outsider.token),
    )

    assert response.status_code == 404


async def test_an_anonymous_request_yields_401(
    client: AsyncClient, record_board: RecordBoard
) -> None:
    """无令牌 → 401."""

    response = await client.get(prefill_path(record_board.strategy.id))

    assert response.status_code == 401
