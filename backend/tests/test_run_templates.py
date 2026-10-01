"""配置模板: 命名保存一套运行取值, 供提交页重复套用.

**判据与提交侧共用**是这组用例的主线: 存模板的每一条拒绝都拿提交侧那句文案去对, 而不是断言
"400 就算过". 两边各写一份校验的话, 症状是"存得下、提交时 400"——只在特定取值上出现, 且用户
看不出该改哪一边. 故这里逐条复用 `services/run_configuration` 的文案.

**作用域是策略域**: 同名可以跨策略共存 (唯一键含 `StrategyId`), 但同一策略下不许重名; 可见性
只按归属人, 共享与公开策略下别人存不进你的、也读不到你的模板.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.catalog.database import PlatformDatabase
from app.catalog.enums import MarketDataType, StrategyVisibility
from app.catalog.models import RunTemplateModel, StrategyModel
from app.config import PlatformSettings
from app.ids import generate_identifier
from app.routers.run_templates import MAXIMUM_TEMPLATES_PER_STRATEGY
from app.services.run_configuration import (
    BAR_PERIOD_FIELD_NAME,
    BAR_PERIOD_INVALID_MESSAGE,
    INITIAL_CAPITAL_INVALID_MESSAGE,
    MATCH_MODE_NOT_SUBMITTABLE_MESSAGE,
    PLATFORM_PARAMETER_MESSAGE,
    RUN_FIELD_REQUIRED_MESSAGE,
    TRADING_DAY_INVALID_MESSAGE,
    TRADING_DAY_ORDER_MESSAGE,
    UNKNOWN_PARAMETER_MESSAGE,
)
from app.strategy_configuration import BAR_PERIOD_KEY_NAME

from .helpers import (
    STRATEGIES_PATH,
    SignedInAccount,
    assert_rejected,
    bearer_headers,
    create_signed_in_account,
    persist_record,
)
from .run_helpers import (
    DEFAULT_BAR_PERIOD,
    DEFAULT_END_TRADING_DAY,
    DEFAULT_EXCHANGE_ID,
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_INSTRUMENT_ID,
    DEFAULT_START_TRADING_DAY,
    FLOOD_BYTES_PARAMETER_KEY,
    build_run_request,
    create_runnable_strategy,
)


TEMPLATES_PATH_TEMPLATE = f"{STRATEGIES_PATH}/{{strategy_id}}/run-templates"

OWNER_USERNAME = "template-owner"
OUTSIDER_USERNAME = "template-outsider"

STRATEGY_NAME = "模板用的桩策略"
OTHER_STRATEGY_NAME = "模板用的第二份桩策略"
OUTSIDER_STRATEGY_NAME = "外人的桩策略"

TEMPLATE_NAME = "默认参数"
RENAMED_TEMPLATE_NAME = "改名之后"
FIFTIETH_TEMPLATE_NAME = "第五十个"
FIFTY_FIRST_TEMPLATE_NAME = "第五十一个"

UNKNOWN_PARAMETER_KEY = "NotDeclared"

#: 一个非默认的参数取值, 用来证"存进去的是提交的那一份、且保持原类型".
RECORDED_FLOOD_BYTES = 16

BROKEN_PARAMS_JSON = "{ 这不是 JSON"

# 参数块里**不许**出现的键: 平台那三个运行级键在表上各有具名列, 再进 `ParamsJson` 就有了两份
# 真相. 按策略配置里的键名去找, 而不是按字段名——参数块用的就是策略那一侧的词汇.
RUN_FIELD_CONFIGURATION_KEYS = frozenset({"BarPreces", "ExchangeId", "InstrumentId"})


def templates_path(strategy_id: str) -> str:
    return TEMPLATES_PATH_TEMPLATE.format(strategy_id=strategy_id)


def template_item_path(strategy_id: str, template_id: str) -> str:
    return f"{templates_path(strategy_id)}/{template_id}"


@dataclass(frozen=True)
class TemplateBoard:
    """两个账号; owner 名下有**两份**可提交的桩策略, outsider 名下有一份."""

    owner: SignedInAccount
    outsider: SignedInAccount
    strategy: StrategyModel
    other_strategy: StrategyModel


def build_template_request(
    strategy_id: str, name: str = TEMPLATE_NAME, **overrides: object
) -> dict[str, object]:
    """一份合法的存模板请求体, 从**提交请求体**派生.

    派生而不另抄一遍: 两份请求体的取值字段是同一份产品契约, 抄一遍就等于在测试里多留一份会与
    实现漂移的副本. 顺带它也是一条断言——`name` 之外多出来的字段会被 `extra="forbid"` 拒掉,
    故这份请求能通, 就说明两边的字段集确实对得上.
    """

    request_body = build_run_request(strategy_id, **overrides)
    request_body.pop("strategy_id")
    request_body["name"] = name

    return request_body


async def post_template(
    client: AsyncClient,
    token: str | None,
    strategy_id: str,
    request_body: dict[str, object],
):
    """把一份存模板请求发出去, 原样返回响应; `token` 可为 None."""

    headers = bearer_headers(token) if token is not None else {}

    return await client.post(
        templates_path(strategy_id), json=request_body, headers=headers
    )


async def create_template(
    client: AsyncClient,
    token: str,
    strategy_id: str,
    name: str = TEMPLATE_NAME,
    **overrides: object,
) -> dict[str, object]:
    """经接口存一个模板, 断言 201 再解出响应体."""

    response = await post_template(
        client,
        token,
        strategy_id,
        build_template_request(strategy_id, name, **overrides),
    )

    assert response.status_code == 201, response.text

    return response.json()


async def list_templates(
    client: AsyncClient, token: str, strategy_id: str
) -> list[dict[str, object]]:
    """列出某策略下本人的模板, 非 200 直接失败."""

    response = await client.get(
        templates_path(strategy_id), headers=bearer_headers(token)
    )

    assert response.status_code == 200, response.text

    return response.json()["templates"]


async def read_template_rows(
    database: PlatformDatabase, strategy_id: str
) -> list[RunTemplateModel]:
    """直接从库里读模板行: 用例关心的是**入库的列** (比如 `ParamsJson` 里到底存了什么)."""

    async with database.session_scope() as session:
        rows = await session.execute(
            select(RunTemplateModel).where(RunTemplateModel.strategy_id == strategy_id)
        )

        return list(rows.scalars().all())


async def rename_template(
    client: AsyncClient, token: str, strategy_id: str, template_id: str, name: str
):
    return await client.patch(
        template_item_path(strategy_id, template_id),
        json={"name": name},
        headers=bearer_headers(token),
    )


async def delete_template(
    client: AsyncClient, token: str, strategy_id: str, template_id: str
):
    return await client.delete(
        template_item_path(strategy_id, template_id), headers=bearer_headers(token)
    )


async def publish_strategy(database: PlatformDatabase, strategy_id: str) -> None:
    """把策略改成公开."""

    async with database.session_scope() as session:
        strategy = await session.get(StrategyModel, strategy_id)
        strategy.visibility_type = StrategyVisibility.PUBLIC.value
        await session.commit()


async def soft_delete_strategy(
    client: AsyncClient, token: str, strategy_id: str
) -> None:
    response = await client.delete(
        f"{STRATEGIES_PATH}/{strategy_id}", headers=bearer_headers(token)
    )

    assert response.status_code == 200, response.text


async def persist_template_rows(
    database: PlatformDatabase,
    owner_id: str,
    strategy_id: str,
    count: int,
    params_json: str = "{}",
) -> None:
    """直落库造若干行模板, 供配额与坏数据两条用例用.

    不走接口造: 五十次 HTTP 往返只为把配额喂满, 而配额那道闸判的是库里**已有多少行**, 与它们
    怎么来的无关.
    """

    for index in range(count):
        await persist_record(
            database,
            RunTemplateModel(
                id=generate_identifier(),
                owner_user_id=owner_id,
                strategy_id=strategy_id,
                name=f"{TEMPLATE_NAME}-{index}",
                match_mode=MarketDataType.BAR.value,
                bar_period=DEFAULT_BAR_PERIOD,
                exchange_id=DEFAULT_EXCHANGE_ID,
                instrument_id=DEFAULT_INSTRUMENT_ID,
                start_trading_day=DEFAULT_START_TRADING_DAY,
                end_trading_day=DEFAULT_END_TRADING_DAY,
                initial_capital=DEFAULT_INITIAL_CAPITAL,
                params_json=params_json,
            ),
        )


@pytest_asyncio.fixture
async def template_board(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> TemplateBoard:
    """两个已登录账号, 三份可提交的桩策略."""

    owner = await create_signed_in_account(database, client, OWNER_USERNAME)
    outsider = await create_signed_in_account(database, client, OUTSIDER_USERNAME)

    strategy = await create_runnable_strategy(
        database, platform_settings, owner.user, STRATEGY_NAME
    )
    other_strategy = await create_runnable_strategy(
        database, platform_settings, owner.user, OTHER_STRATEGY_NAME
    )
    await create_runnable_strategy(
        database, platform_settings, outsider.user, OUTSIDER_STRATEGY_NAME
    )

    return TemplateBoard(
        owner=owner,
        outsider=outsider,
        strategy=strategy.strategy,
        other_strategy=other_strategy.strategy,
    )


async def test_a_saved_template_comes_back_whole(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """正向全链: 存下 → 读回 → 改名 → 删掉 → 再读什么都没有."""

    board = template_board
    strategy_id = board.strategy.id

    created = await create_template(client, board.owner.token, strategy_id)

    assert created["name"] == TEMPLATE_NAME
    assert created["strategy_id"] == strategy_id
    assert created["match_mode"] == MarketDataType.BAR.value
    assert created["bar_period"] == DEFAULT_BAR_PERIOD
    assert created["exchange_id"] == DEFAULT_EXCHANGE_ID
    assert created["instrument_id"] == DEFAULT_INSTRUMENT_ID
    assert created["start_trading_day"] == DEFAULT_START_TRADING_DAY
    assert created["end_trading_day"] == DEFAULT_END_TRADING_DAY
    assert created["initial_capital"] == DEFAULT_INITIAL_CAPITAL

    listed = await list_templates(client, board.owner.token, strategy_id)

    assert [template["id"] for template in listed] == [created["id"]]

    renamed = await rename_template(
        client, board.owner.token, strategy_id, created["id"], RENAMED_TEMPLATE_NAME
    )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == RENAMED_TEMPLATE_NAME
    assert renamed.json()["id"] == created["id"]

    removed = await delete_template(
        client, board.owner.token, strategy_id, created["id"]
    )

    assert removed.status_code == 200, removed.text
    assert await list_templates(client, board.owner.token, strategy_id) == []
    assert await read_template_rows(database, strategy_id) == []

    removed_again = await delete_template(
        client, board.owner.token, strategy_id, created["id"]
    )

    assert removed_again.status_code == 404


async def test_submitted_parameters_are_stored_in_the_engine_value_form(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """`ParamsJson` 与 `Runs.ParamsJson` 同形同义: **只存用户改过的键**、原类型, 不含运行级字段.

    不含运行级字段是关键: 那三个在这张表上各有具名列, 再写进参数块就有了两份真相, 而两份真相迟早
    不一致——届时套用模板读到的是哪一份, 取决于实现细节而不是设计.

    **只存改过的键**也是这版契约的一部分: 没提交的键由"模板 + 那份上传的配置"补上. 平台在这里替
    策略抄一份默认值反而有害——那份抄本会盖住模板里后来改过的值, 于是"上传的 JSON 即所见"在套用
    旧模板时不再成立.
    """

    board = template_board

    created = await create_template(
        client,
        board.owner.token,
        board.strategy.id,
        name=TEMPLATE_NAME,
        params={FLOOD_BYTES_PARAMETER_KEY: RECORDED_FLOOD_BYTES},
    )

    rows = await read_template_rows(database, board.strategy.id)

    assert len(rows) == 1

    stored_parameters = json.loads(rows[0].params_json)

    assert stored_parameters == {FLOOD_BYTES_PARAMETER_KEY: RECORDED_FLOOD_BYTES}
    assert stored_parameters == created["params"]
    # 原类型, 不是字符串化的 `"16"`: 策略读到的必须是它当初写进那份文件里的数.
    assert type(stored_parameters[FLOOD_BYTES_PARAMETER_KEY]) is int
    assert RUN_FIELD_CONFIGURATION_KEYS.isdisjoint(stored_parameters)
    assert DEFAULT_BAR_PERIOD not in stored_parameters.values()


async def test_a_run_field_override_reaches_the_named_column(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """改一个运行级取值, 落到的是它自己的列——不是参数块."""

    board = template_board

    created = await create_template(
        client,
        board.owner.token,
        board.strategy.id,
        name=TEMPLATE_NAME,
    )

    assert created["bar_period"] == DEFAULT_BAR_PERIOD

    rows = await read_template_rows(database, board.strategy.id)

    assert rows[0].bar_period == DEFAULT_BAR_PERIOD
    assert DEFAULT_BAR_PERIOD not in json.loads(rows[0].params_json).values()


async def test_the_same_name_may_live_under_two_strategies(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """唯一键含 `StrategyId`: 同一个人在两个策略下各存一个同名模板是正常的."""

    board = template_board

    first = await create_template(client, board.owner.token, board.strategy.id)
    second = await create_template(
        client, board.owner.token, board.other_strategy.id, name=TEMPLATE_NAME
    )

    assert first["id"] != second["id"]
    assert first["name"] == second["name"] == TEMPLATE_NAME


async def test_a_repeated_name_under_one_strategy_is_a_conflict(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """同一策略下重名即 409, 且以唯一约束为准——被拒的那次没在库里留东西."""

    board = template_board
    strategy_id = board.strategy.id

    await create_template(client, board.owner.token, strategy_id)

    response = await post_template(
        client, board.owner.token, strategy_id, build_template_request(strategy_id)
    )

    assert response.status_code == 409, response.text
    assert len(await read_template_rows(database, strategy_id)) == 1


async def test_a_whitespace_only_name_is_rejected(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """全是空白的名字拒在 400: pydantic 的 `min_length` 拦不住它 (`"  "` 长度为 2)."""

    board = template_board

    response = await post_template(
        client,
        board.owner.token,
        board.strategy.id,
        build_template_request(board.strategy.id, name="   "),
    )

    assert response.status_code == 400, response.text


async def test_a_trailing_blank_is_stripped_rather_than_stored(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """名字先去空白: 不去的话 `默认参数` 与 `默认参数 ` 会各占一个, 而界面上看是同一个."""

    board = template_board
    strategy_id = board.strategy.id

    created = await create_template(
        client, board.owner.token, strategy_id, name=f"{TEMPLATE_NAME} "
    )

    assert created["name"] == TEMPLATE_NAME

    response = await post_template(
        client, board.owner.token, strategy_id, build_template_request(strategy_id)
    )

    assert response.status_code == 409, response.text


async def test_the_run_field_and_parameter_judgements_are_the_submission_ones(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """逐条拿提交侧的文案去对: 模式 / 必填 / 交易日 / 倒置 / 资金 / 未声明参数."""

    board = template_board
    strategy_id = board.strategy.id

    forbidden_match_mode = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(strategy_id, match_mode=MarketDataType.TICK),
    )
    assert_rejected(forbidden_match_mode, MATCH_MODE_NOT_SUBMITTABLE_MESSAGE)

    missing_required_field = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(strategy_id, bar_period=""),
    )
    assert_rejected(
        missing_required_field,
        RUN_FIELD_REQUIRED_MESSAGE.format(field=BAR_PERIOD_FIELD_NAME),
    )

    unsupported_period = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(strategy_id, bar_period="7m"),
    )
    assert_rejected(unsupported_period, BAR_PERIOD_INVALID_MESSAGE)

    invalid_trading_day = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(strategy_id, start_trading_day="2024-10-01"),
    )
    assert_rejected(
        invalid_trading_day,
        TRADING_DAY_INVALID_MESSAGE.format(field="start_trading_day"),
        "2024-10-01",
    )

    reversed_trading_days = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(
            strategy_id,
            start_trading_day=DEFAULT_END_TRADING_DAY,
            end_trading_day="20240101",
        ),
    )
    assert_rejected(reversed_trading_days, TRADING_DAY_ORDER_MESSAGE)

    invalid_capital = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(strategy_id, initial_capital=0),
    )
    assert_rejected(invalid_capital, INITIAL_CAPITAL_INVALID_MESSAGE)

    unknown_parameter = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(strategy_id, params={UNKNOWN_PARAMETER_KEY: 1}),
    )
    assert_rejected(
        unknown_parameter, UNKNOWN_PARAMETER_MESSAGE.format(keys=UNKNOWN_PARAMETER_KEY)
    )


async def test_a_parameter_key_of_the_platform_is_rejected(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """参数里写平台那三个键 → 400, 且文案说的是"别写它", 不是"没有这个键".

    两者在模板里通常都成立 (模板一般不写那三个), 故**报哪一句**是要紧的: 说"键不存在", 用户的
    动作是去改自己那份配置文件; 而正确的动作是什么都不做——用户选的是那三个控件, 模板里写什么
    都不作数. 这条用例钉的正是这个先后次序.
    """

    board = template_board

    response = await post_template(
        client,
        board.owner.token,
        board.strategy.id,
        build_template_request(
            board.strategy.id, params={BAR_PERIOD_KEY_NAME: "60m"}
        ),
    )

    assert_rejected(
        response, PLATFORM_PARAMETER_MESSAGE.format(keys=BAR_PERIOD_KEY_NAME)
    )


async def test_another_tenant_cannot_read_or_write(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """跨租户一律 404: 取不到策略是 404, 拿得到模板 id 但不是自己的也是 404."""

    board = template_board
    strategy_id = board.strategy.id

    created = await create_template(client, board.owner.token, strategy_id)

    assert (
        await client.get(
            templates_path(strategy_id), headers=bearer_headers(board.outsider.token)
        )
    ).status_code == 404

    assert (
        await post_template(
            client,
            board.outsider.token,
            strategy_id,
            build_template_request(strategy_id, name=TEMPLATE_NAME),
        )
    ).status_code == 404

    assert (
        await rename_template(
            client,
            board.outsider.token,
            strategy_id,
            created["id"],
            RENAMED_TEMPLATE_NAME,
        )
    ).status_code == 404

    assert (
        await delete_template(
            client, board.outsider.token, strategy_id, created["id"]
        )
    ).status_code == 404


async def test_a_visible_strategy_lets_you_keep_your_own_templates(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """共享/公开策略下也能存模板, 但列出来的**只有自己的那几份**."""

    board = template_board
    strategy_id = board.strategy.id

    await create_template(client, board.owner.token, strategy_id)
    await publish_strategy(database, strategy_id)

    mine = await create_template(
        client, board.outsider.token, strategy_id, name="我自己的模板"
    )

    outsider_templates = await list_templates(client, board.outsider.token, strategy_id)

    assert [template["id"] for template in outsider_templates] == [mine["id"]]

    owner_templates = await list_templates(client, board.owner.token, strategy_id)

    assert len(owner_templates) == 1
    assert owner_templates[0]["id"] != mine["id"]


async def test_templates_stay_manageable_after_the_strategy_goes_away(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """策略被软删之后, 我自己的模板仍读得到、改得动、删得掉.

    这条是"归属判定只按归属人"的可测形式: 若改名与删除也要求策略可见, 这些模板会在库里留成
    只有 DBA 能收拾的孤儿.
    """

    board = template_board
    strategy_id = board.strategy.id

    created = await create_template(client, board.owner.token, strategy_id)

    await soft_delete_strategy(client, board.owner.token, strategy_id)

    assert (
        await client.get(
            templates_path(strategy_id), headers=bearer_headers(board.owner.token)
        )
    ).status_code == 404

    renamed = await rename_template(
        client, board.owner.token, strategy_id, created["id"], RENAMED_TEMPLATE_NAME
    )

    assert renamed.status_code == 200, renamed.text

    removed = await delete_template(
        client, board.owner.token, strategy_id, created["id"]
    )

    assert removed.status_code == 200, removed.text


async def test_the_fifty_first_template_is_rejected(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """护栏: 单个策略下第 51 个模板 400, 而第 50 个仍能存下."""

    board = template_board
    strategy_id = board.strategy.id

    await persist_template_rows(
        database, board.owner.user_id, strategy_id, MAXIMUM_TEMPLATES_PER_STRATEGY - 1
    )

    fiftieth = await create_template(
        client, board.owner.token, strategy_id, name=FIFTIETH_TEMPLATE_NAME
    )

    assert fiftieth["name"] == FIFTIETH_TEMPLATE_NAME

    rejected = await post_template(
        client,
        board.owner.token,
        strategy_id,
        build_template_request(strategy_id, name=FIFTY_FIRST_TEMPLATE_NAME),
    )

    assert rejected.status_code == 400, rejected.text
    assert len(await read_template_rows(database, strategy_id)) == (
        MAXIMUM_TEMPLATES_PER_STRATEGY
    )


async def test_the_quota_is_counted_per_strategy(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """配额按 (归属人, 策略) 算: 一个策略满了不影响另一个策略."""

    board = template_board
    strategy_id = board.strategy.id

    await persist_template_rows(
        database, board.owner.user_id, strategy_id, MAXIMUM_TEMPLATES_PER_STRATEGY
    )

    assert (
        await post_template(
            client, board.owner.token, strategy_id, build_template_request(strategy_id)
        )
    ).status_code == 400

    assert (
        await create_template(client, board.owner.token, board.other_strategy.id)
    )["name"] == TEMPLATE_NAME


async def test_a_corrupt_parameter_blob_degrades_to_an_empty_set(
    client: AsyncClient, database: PlatformDatabase, template_board: TemplateBoard
) -> None:
    """`ParamsJson` 读不动时只降级成空参数集, 不 500 也不让整页报错."""

    board = template_board
    strategy_id = board.strategy.id

    await persist_template_rows(
        database, board.owner.user_id, strategy_id, 1, params_json=BROKEN_PARAMS_JSON
    )

    listed = await list_templates(client, board.owner.token, strategy_id)

    assert len(listed) == 1
    assert listed[0]["params"] == {}
    assert listed[0]["name"] == f"{TEMPLATE_NAME}-0"


async def test_an_anonymous_caller_is_rejected(
    client: AsyncClient, template_board: TemplateBoard
) -> None:
    """不带令牌即 401——四个端点一个都不例外."""

    strategy_id = template_board.strategy.id

    assert (await client.get(templates_path(strategy_id))).status_code == 401
    assert (
        await post_template(
            client, None, strategy_id, build_template_request(strategy_id)
        )
    ).status_code == 401
