"""提交端点的权限、校验与落库.

权限那一组 (被授权人可提交 / `public` 可提交 / 非归属人 404) 验的是**一条已拍板的语义**: 可跑
集合与可见集合逐字相同——授权即可跑, 不区分 `read`/`run`. 三条各自成立, 才说明"跑权限检查就是
可见性检查"在实现里是真的, 而不是另写了一套判断恰好得出相同结论.

校验那一组拿的是 §3.2 的表. 每条规则都配一次"改坏即 400, 且库里无新行, 且运行根下无新目录":
只断言状态码的话, 一个"先落行再校验"的实现会让被拒的提交**照常排队跑起来**, 而接口回的是 400.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, Response

from app.catalog.database import PlatformDatabase
from app.catalog.enums import (
    GrantPermission,
    MarketDataType,
    RunStatus,
    StrategyVisibility,
)
from app.catalog.models import RunModel
from app.config import PlatformSettings
from app.scheduler.runner import MAXIMUM_ERROR_MESSAGE_LENGTH
from app.services.engine_probe import ENGINE_VERSION_FILENAME, read_engine_version
from app.services.run_submission import (
    MARKET_DATA_MISSING_MESSAGE,
    MATCH_MODE_NOT_SUBMITTABLE_MESSAGE,
    MISSING_PARAMETER_MESSAGE,
    NO_VERSION_MESSAGE,
    RUN_FIELD_NOT_MAPPED_MESSAGE,
    RUN_FIELD_REQUIRED_MESSAGE,
    SESSION_FILE_MISSING_MESSAGE,
    UNKNOWN_PARAMETER_MESSAGE,
    VERSION_NOT_FOUND_MESSAGE,
)

from .helpers import (
    SignedInAccount,
    bearer_headers,
    create_signed_in_account,
    create_strategy_grant_record,
    create_strategy_record,
)
from .run_helpers import (
    BEHAVIOR_PARAMETER_KEY,
    FLOOD_BYTES_PARAMETER_KEY,
    RESULT_FILENAME,
    RUNS_PATH,
    SLEEP_SECONDS_PARAMETER_KEY,
    STUB_CONFIG_FILENAME,
    await_run_status,
    build_run_request,
    build_stub_manifest,
    count_run_rows,
    create_runnable_strategy,
    post_run,
    read_job_json,
    read_run_record,
    run_directory_names,
    running_client,
    settings_with,
    submit_run,
    await_run_terminal,
)


STRATEGY_NAME = "桩策略"
OTHER_STRATEGY_NAME = "另一份桩策略"
GRANT_STRATEGY_NAME = "被授权人的桩策略"
PUBLIC_STRATEGY_NAME = "公开的桩策略"
RECORD_ONLY_STRATEGY_NAME = "还没上传源码的桩策略"

BLOCKING_SLEEP_SECONDS = 2
RUNNING_ELAPSED_LIMIT_SECONDS = 60

BEHAVIOR_PARAMETER_VALUE = "success"
DEFAULT_SLEEP_SECONDS_VALUE = 0
DEFAULT_FLOOD_BYTES_VALUE = 0

UNSUPPORTED_MATCH_MODE = MarketDataType.TICK
UNMAPPED_FIELD_VALUE = "SSE"
UNKNOWN_PARAMETER_KEY = "UndeclaredKnob"
UNKNOWN_PARAMETER_VALUE = "某个取值"
UNDECLARED_BEHAVIOR_VALUE = "not-a-declared-behavior"
ABSENT_MARKET_DATA_DIRECTORY_NAME = "absent-market-data"
ABSENT_SESSION_FILENAME = "AbsentSessions.json"

# 引擎包自己带的版本号长这样; 测试里给出一个具体取值, 好让"行里那一列究竟来自哪里"无可
# 抵赖——而非与一个同样恒为空串的取值相比.
ENGINE_VERSION_TEXT = "build-2026.09.27"

RESULT_LONG_TAIL_KEY = "MissingRateKeys"

# `exchange_id` 在下面的"未映射"用例里是被点名的字段: 报错文案该带**字段名**, 不该带取值.
EXCHANGE_ID_FIELD_NAME = "exchange_id"
INSTRUMENT_ID_FIELD_NAME = "instrument_id"


def assert_rejected(
    response: Response, expected_message: str, *rejected_values: str
) -> None:
    """一次被拒的提交: 400、文案相符, 且不原样回显调用方给出的取值.

    不回显是硬要求 (D.03): 文案进响应体, 带上调用方的输入就等于平台把一次错误请求放大成一次
    原样回显.
    """

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == expected_message

    for rejected_value in rejected_values:
        assert rejected_value not in response.text


async def test_a_missing_rate_key_array_is_not_in_the_response_but_is_on_disk(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """`MissingRateKeys` 是长尾数组: 留在结果文件里, 不进响应体.

    它按需读取 (详情页展开时才要), 塞进行宽会让每一行都拖着一条长度不可控的数组. 两面都断言:
    响应里连元素都不出现, 而盘上的结果文件里它非空——只断言"响应里没有"的话, 桩不写这个键也照样
    通过.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(client, run_owner.token, runnable.strategy.id)

    await await_run_terminal(database, submitted.id, RUNNING_ELAPSED_LIMIT_SECONDS)

    detail = await client.get(
        f"{RUNS_PATH}/{submitted.id}", headers=bearer_headers(run_owner.token)
    )

    assert detail.status_code == 200
    assert RESULT_LONG_TAIL_KEY not in detail.json()

    stored_result = read_job_json(platform_settings, submitted.id, RESULT_FILENAME)
    stored_rate_keys = stored_result[RESULT_LONG_TAIL_KEY]

    assert stored_rate_keys
    assert all(rate_key not in detail.text for rate_key in stored_rate_keys)

    # 长尾数组之外, 文案本身是有界的: `ErrorMsg` 列是 `String(1000)`, 而 SQLite 不强制长度.
    assert len(detail.json()["error_msg"]) <= MAXIMUM_ERROR_MESSAGE_LENGTH


async def test_submitting_someone_elses_strategy_is_a_404_with_no_new_row(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """非归属人提交 → 404, 且库里不留行.

    404 而不是 403: 跨租户一律"不存在", 403 会说漏"这个 id 是真的". 同时断言库里没有新行——
    只挡响应、行却已排队的实现, 会让这次调用在后台跑起来.
    """

    outsider = await create_signed_in_account(database, client, "submission-outsider")

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client, outsider.token, build_run_request(runnable.strategy.id)
    )

    assert response.status_code == 404
    assert await count_run_rows(database) == rows_before


@pytest.mark.parametrize(
    "permission_type", [GrantPermission.READ, GrantPermission.RUN]
)
async def test_a_grantee_may_submit_regardless_of_the_grant_permission(
    permission_type: GrantPermission,
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """被授权人可提交, 且 `read` 与 `run` **两档一样**可提交.

    两档参数化是这条用例的全部意义: 用户拍板的是"不区分 read/run, 授权即可跑". 只测一档的话,
    一个悄悄按 `permission_type == 'run'` 过滤的实现会照常通过——而那正是被拍掉的那个方案.
    """

    grantee = await create_signed_in_account(database, client, "submission-grantee")

    runnable = await create_runnable_strategy(
        database,
        platform_settings,
        run_owner.user,
        GRANT_STRATEGY_NAME,
        visibility_type=StrategyVisibility.SHARED,
    )

    await create_strategy_grant_record(
        database,
        runnable.strategy,
        grantee.user,
        run_owner.user,
        permission_type=permission_type,
    )

    submitted = await submit_run(client, grantee.token, runnable.strategy.id)

    assert submitted.status == RunStatus.QUEUED
    assert submitted.user_id == grantee.user_id


async def test_a_public_strategy_is_submittable_by_a_stranger(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """`public` 隐含可跑: 一条授权行都没有的人也提交得了.

    这是一次有意的权限放宽, 故值得钉住: 若哪天有人"顺手"给提交补一道授权表检查, public 策略会
    突然只有归属人跑得动, 而列表页上它仍对全体可见——症状是"看得见却点不动".
    """

    stranger = await create_signed_in_account(database, client, "submission-stranger")

    runnable = await create_runnable_strategy(
        database,
        platform_settings,
        run_owner.user,
        PUBLIC_STRATEGY_NAME,
        visibility_type=StrategyVisibility.PUBLIC,
    )

    submitted = await submit_run(client, stranger.token, runnable.strategy.id)

    assert submitted.status == RunStatus.QUEUED


async def test_a_match_mode_the_platform_cannot_submit_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """提交 Tick → 400, 库里无新行, 运行根下无新目录.

    平台只开 Bar: Tick 侧引擎有三档撮合方式 (OrderBook / LastPrice / OppositePrice), 而平台的
    `MarketDataType` 只有两值, 推不出该填哪一个. 收下的后果是**静默 0 成交**——回测照常跑完,
    结果里一个错误都没有.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)
    directories_before = run_directory_names(platform_settings)

    response = await post_run(
        client,
        run_owner.token,
        build_run_request(runnable.strategy.id, match_mode=UNSUPPORTED_MATCH_MODE),
    )

    assert_rejected(response, MATCH_MODE_NOT_SUBMITTABLE_MESSAGE)
    assert await count_run_rows(database) == rows_before
    assert run_directory_names(platform_settings) == directories_before


async def test_a_run_field_the_manifest_does_not_map_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """manifest 没映射 `exchange_id` 时提交它 → 400.

    该字段只有策略的 `subscribe_tick` 用, 引擎根本不认识它. 没映射而收下, 它无处落地: 用户以为
    自己换了个交易所, 而策略收到的配置里连这个键都没有.
    """

    runnable = await create_runnable_strategy(
        database,
        platform_settings,
        run_owner.user,
        STRATEGY_NAME,
        manifest=build_stub_manifest(run_field_keys={"bar_period": "BarPreces"}),
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client, run_owner.token, build_run_request(runnable.strategy.id)
    )

    assert_rejected(
        response,
        RUN_FIELD_NOT_MAPPED_MESSAGE.format(field=EXCHANGE_ID_FIELD_NAME),
        UNMAPPED_FIELD_VALUE,
    )
    assert await count_run_rows(database) == rows_before


async def test_a_mapped_run_field_left_empty_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """manifest 映射了 `instrument_id` 却提交空白 → 400.

    映射了就意味着策略一定会去读那个键; 空取值不会"保持默认", 它会以未捕获异常收场 (退出码 1).
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client,
        run_owner.token,
        build_run_request(runnable.strategy.id, instrument_id="   "),
    )

    assert_rejected(
        response,
        RUN_FIELD_REQUIRED_MESSAGE.format(field=INSTRUMENT_ID_FIELD_NAME),
    )
    assert await count_run_rows(database) == rows_before


async def test_an_undeclared_parameter_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """提交 manifest 未声明的参数 → 400, 文案列出参数名.

    未声明的键不会"原样透传"进策略配置: 平台按 manifest 渲染, 只写声明过的键. 静默收下等于让
    用户以为参数生效了, 而策略读到它时是 `KeyError`.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client,
        run_owner.token,
        build_run_request(
            runnable.strategy.id,
            params={UNKNOWN_PARAMETER_KEY: UNKNOWN_PARAMETER_VALUE},
        ),
    )

    assert_rejected(
        response,
        UNKNOWN_PARAMETER_MESSAGE.format(keys=UNKNOWN_PARAMETER_KEY),
        UNKNOWN_PARAMETER_VALUE,
    )
    assert await count_run_rows(database) == rows_before


async def test_a_parameter_value_outside_its_declared_options_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """参数取值不在 `options` 之内 → 400.

    与"未声明的参数"不同: 键是对的、类型也对, 坏的是取值. 这条若漏掉, 桩会以"未知的桩行为"抛
    异常收场——用户拿到的是一个与他的输入看起来毫无关联的 `failed`.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client,
        run_owner.token,
        build_run_request(
            runnable.strategy.id,
            params={BEHAVIOR_PARAMETER_KEY: UNDECLARED_BEHAVIOR_VALUE},
        ),
    )

    assert response.status_code == 400, response.text
    assert f"参数 {BEHAVIOR_PARAMETER_KEY}" in response.json()["detail"]
    assert UNDECLARED_BEHAVIOR_VALUE not in response.text
    assert await count_run_rows(database) == rows_before


async def test_a_parameter_under_its_declared_minimum_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """参数取值低于 `minimum` → 400.

    `minimum` 是 manifest 的声明, 不是平台对某个具体参数的偏好: 桩的 `FloodBytes` 声明了
    `minimum: 0`, 负值进不了配置.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client,
        run_owner.token,
        build_run_request(
            runnable.strategy.id, params={FLOOD_BYTES_PARAMETER_KEY: -1}
        ),
    )

    assert response.status_code == 400, response.text
    assert f"参数 {FLOOD_BYTES_PARAMETER_KEY}" in response.json()["detail"]
    assert await count_run_rows(database) == rows_before


async def test_a_required_parameter_left_out_is_rejected_naming_the_key(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """没有 `default` 的参数未提交 → 400, 文案列出缺失的 key.

    文案里的 key 取自 **manifest**, 不是调用方的输入. 这一条不设的话, 一次"缺参数"的提交会被
    策略以 `KeyError` 收场, 而用户看到的是退出码 1.
    """

    runnable = await create_runnable_strategy(
        database,
        platform_settings,
        run_owner.user,
        STRATEGY_NAME,
        manifest=build_stub_manifest(required_parameter_keys=frozenset({BEHAVIOR_PARAMETER_KEY})),
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client, run_owner.token, build_run_request(runnable.strategy.id)
    )

    assert_rejected(
        response, MISSING_PARAMETER_MESSAGE.format(keys=BEHAVIOR_PARAMETER_KEY)
    )
    assert await count_run_rows(database) == rows_before


async def test_the_declared_defaults_are_written_verbatim(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """只给必填项的一轮: 未提交的参数取 manifest 声明的默认值.

    与"缺必填参数被拒"是一对: 有 `default` 的参数不提交不算错, 没 `default` 的才算. 桩的五个
    参数默认全带默认值, 故连 `params` 都不给也应当成事——而"平台把默认值写成了 None"这类缺陷
    只在策略读到 `None` 时才以退出码 1 的面目出现.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(client, run_owner.token, runnable.strategy.id)

    await await_run_terminal(database, submitted.id, RUNNING_ELAPSED_LIMIT_SECONDS)

    stored_configuration = read_job_json(
        platform_settings, submitted.id, STUB_CONFIG_FILENAME
    )

    assert stored_configuration[BEHAVIOR_PARAMETER_KEY] == BEHAVIOR_PARAMETER_VALUE
    assert stored_configuration[SLEEP_SECONDS_PARAMETER_KEY] == DEFAULT_SLEEP_SECONDS_VALUE
    assert stored_configuration[FLOOD_BYTES_PARAMETER_KEY] == DEFAULT_FLOOD_BYTES_VALUE


async def test_a_strategy_without_any_version_cannot_be_submitted(
    client: AsyncClient,
    database: PlatformDatabase,
    run_owner: SignedInAccount,
) -> None:
    """还没有版本的策略 → 400, 而不是 500.

    这是"用户看得到但平台给不出答案"的一格: 策略建好了、源码还没传. 报 400 并说清原因, 否则
    详情页会把它显示成"平台坏了".
    """

    strategy = await create_strategy_record(
        database, run_owner.user, RECORD_ONLY_STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(client, run_owner.token, build_run_request(strategy.id))

    assert_rejected(response, NO_VERSION_MESSAGE)
    assert await count_run_rows(database) == rows_before


async def test_a_version_of_another_strategy_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """指定的版本不属于该策略 → 400.

    不校验归属的话, 一次提交就能让甲策略的作业跑乙策略的代码——而授权是按策略发的. 用的那份版本
    是**真实存在、就在盘上**的: 一个不存在的 id 会让这条用例在"版本根本没查到"上通过, 与归属
    校验无关.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )
    other_runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, OTHER_STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(
        client,
        run_owner.token,
        build_run_request(
            runnable.strategy.id, strategy_version_id=other_runnable.version.id
        ),
    )

    assert_rejected(response, VERSION_NOT_FOUND_MESSAGE)
    assert await count_run_rows(database) == rows_before


async def test_a_missing_market_data_root_is_rejected_pointing_at_the_input(
    platform_settings: PlatformSettings,
) -> None:
    """行情根不在位 → 400, 且点名缺的是哪一项 (不带路径).

    这两项缺失是**确定性**失败: 收下它只会让用户等一轮, 再收到一个与他的输入无关的 `failed`.
    文案不带路径——路径是服务端的目录布局.
    """

    settings = settings_with(
        platform_settings,
        market_data_root=(
            platform_settings.runs_root / ABSENT_MARKET_DATA_DIRECTORY_NAME
        ).resolve(),
    )

    response = await submit_under_settings(settings)

    assert_rejected(response, MARKET_DATA_MISSING_MESSAGE)


async def test_a_missing_session_file_is_rejected_pointing_at_the_input(
    platform_settings: PlatformSettings,
) -> None:
    """会话表文件不在位 → 400, 且点名缺的是哪一项.

    与行情根分开两条: 文案若把两者写成同一句, "点名缺哪一项"这条要求就落空了.
    """

    settings = settings_with(
        platform_settings,
        session_file_path=(
            platform_settings.engine_root / ABSENT_SESSION_FILENAME
        ).resolve(),
    )

    response = await submit_under_settings(settings)

    assert_rejected(response, SESSION_FILE_MISSING_MESSAGE)


async def submit_under_settings(settings: PlatformSettings) -> Response:
    """在一份自建配置下登号、建策略、发一次提交, 返回响应.

    单独起一个应用: 这两条要的正是"引擎输入不在位"的配置, 而共用夹具的配置必须是在位的——不然
    每一条提交用例都会死在"会话表文件不存在"上, 与它想测的东西毫无关系.
    """

    async with running_client(settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "engine-input-owner")

        runnable = await create_runnable_strategy(
            database, settings, owner.user, STRATEGY_NAME
        )

        return await post_run(
            client, owner.token, build_run_request(runnable.strategy.id)
        )


async def test_a_submitted_run_is_readable_from_another_session(
    platform_settings: PlatformSettings,
) -> None:
    """POST 返回时行已经落库且是 `queued`——另一个会话立刻读得到.

    只做 flush 不做 commit 的实现会让这一读落空: 行的可见性出不了请求所在的会话, 而调度器在
    另一个会话里扫库——"排上队了但调度器看不见"正是同一种症状.

    `queued` 靠一个 `sleep` 作业占住唯一的槽位来钉住: 上限为 1 时第二个作业必然停在 `queued`,
    这一条因此不靠时序碰运气.
    """

    limited_settings = settings_with(platform_settings, max_concurrent_runs=1)

    async with running_client(limited_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "commit-owner")

        runnable = await create_runnable_strategy(
            database, limited_settings, owner.user, STRATEGY_NAME
        )

        blocking = await submit_run(
            client,
            owner.token,
            runnable.strategy.id,
            params={SLEEP_SECONDS_PARAMETER_KEY: BLOCKING_SLEEP_SECONDS},
        )

        await await_run_status(
            database, blocking.id, RunStatus.RUNNING, RUNNING_ELAPSED_LIMIT_SECONDS
        )

        response = await post_run(
            client, owner.token, build_run_request(runnable.strategy.id)
        )

        assert response.status_code == 201

        # 另开一个会话去读: 与请求所在的会话无关.
        async with database.session_scope() as session:
            stored_run = await session.get(RunModel, response.json()["id"])

        assert stored_run is not None
        assert stored_run.status == RunStatus.QUEUED.value
        assert stored_run.workspace_path == stored_run.id


async def test_a_submitted_run_freezes_the_engine_version_it_will_launch(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """提交那一刻就把"用哪个引擎跑"冻进行里.

    不冻的话, 引擎原地换版之后历史轮与新轮在库里长得一模一样; `StrategyVersionId` 那条"供逐字
    复现"的承诺便少了引擎这一半——策略与参数都复现得出来, 跑它们的机器复现不出来. 这与 D.06
    是同一类事: 口径变了而库里没有线索, 旧数字就再也不能与新数字相减.

    断言有意走两路: 一路对着 `read_engine_version` 这份同一处真相, 一路对着写进引擎根的那个
    字面量. 只对前者的话, 若两边都恒返回空串, 这条用例照样是绿的.
    """

    (platform_settings.engine_root / ENGINE_VERSION_FILENAME).write_text(
        f"{ENGINE_VERSION_TEXT}\n", encoding="utf-8"
    )

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(client, run_owner.token, runnable.strategy.id)

    await await_run_terminal(database, submitted.id, RUNNING_ELAPSED_LIMIT_SECONDS)

    stored_run = await read_run_record(database, submitted.id)

    assert stored_run.engine_version == read_engine_version(
        platform_settings.engine_root
    )
    assert stored_run.engine_version == ENGINE_VERSION_TEXT


async def test_submitting_without_a_token_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """无令牌 → 401, 且库里无新行."""

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    rows_before = await count_run_rows(database)

    response = await post_run(client, None, build_run_request(runnable.strategy.id))

    assert response.status_code == 401
    assert await count_run_rows(database) == rows_before
