"""策略授权的写入路径: `PUT /api/strategies/{id}/grants`.

`StrategyGrants` 本身只是张多对多关系表, 要钉的是它与可见性的耦合: `private` 下授权表
不生效, 于是"授权写成功"必须同时意味着"对方能看见了"——否则调用方看到的成败与事实相反,
而整条接口清单里没有第二处能把可见性改成 `shared`, 用户无从自救. 另一半是写入的原子性:
未经校验的请求一条都不该落库, 否则会留下一个既非旧名单也非新名单的授权集合.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response

from app.catalog.database import PlatformDatabase
from app.catalog.enums import GrantPermission, StrategyVisibility
from app.catalog.models import StrategyModel
from app.catalog.schemas import StrategyDetailResponse
from app.catalog.visibility import STRATEGY_NOT_FOUND_MESSAGE
from app.routers.strategies import (
    GRANT_TO_OWNER_MESSAGE,
    MAXIMUM_GRANTS_PER_STRATEGY,
    REPEATED_GRANTEE_MESSAGE,
    TOO_MANY_GRANTS_MESSAGE,
    UNKNOWN_GRANTEE_MESSAGE,
)

from .helpers import (
    STRATEGIES_PATH,
    SignedInAccount,
    bearer_headers,
    bulk_create_user_records,
    create_signed_in_account,
    create_strategy_record,
    list_visible_strategy_ids,
    read_strategy_detail,
    read_strategy_status_code,
    soft_delete_strategy_record,
)


OWNER_USERNAME = "grant-owner"
GRANTEE_USERNAME = "grant-grantee"
OTHER_USERNAME = "grant-other"

GRANT_STRATEGY_NAME = "授权用网格"

VALUE_MARKER = "marker-that-must-not-be-echoed"

ABSENT_USER_ID = "0" * 32


@dataclass(frozen=True)
class GrantCast:
    """一次授权用例要用的三个账号.

    `other` 一身两职: 在"换一批被授权人"里充当新的被授权人, 在"非授权人一律 404"里充当
    那个没被授权的人. 两处的判据不同, 不必为它们各造一个账号.

    留在本模块而不进 `helpers`: 只有这里用得着. `helpers` 放跨模块复用的基元,
    单一场景的"角色组"与用到它的用例放一起 (可见性测试的 `AccountPair` 同理).
    """

    owner: SignedInAccount
    grantee: SignedInAccount
    other: SignedInAccount


def grants_path(strategy_id: str) -> str:
    """授权端点路径."""

    return f"{STRATEGIES_PATH}/{strategy_id}/grants"


def grant_entry(
    grantee_user_id: str, permission_type: GrantPermission = GrantPermission.READ
) -> dict[str, str]:
    """一条授权项的请求体形态."""

    return {
        "grantee_user_id": grantee_user_id,
        "permission_type": permission_type.value,
    }


def granted_user_ids(detail: StrategyDetailResponse) -> list[str]:
    """授权列表里的被授权人 id, 排序后返回.

    排序只为断言稳定: 列表按授权时间倒序, 而同一批写入的时间戳可能落在同一刻度上,
    先后不该成为判据.
    """

    return sorted(grant.grantee_user_id for grant in detail.grants)


async def put_grants(
    client: AsyncClient,
    account: SignedInAccount,
    strategy_id: str,
    grants: list[dict[str, str]],
) -> Response:
    """整体替换授权集合."""

    return await client.put(
        grants_path(strategy_id),
        json={"grants": grants},
        headers=bearer_headers(account.token),
    )


async def read_owner_view(
    client: AsyncClient, grant_cast: GrantCast, strategy: StrategyModel
) -> StrategyDetailResponse:
    """归属人眼中这条策略的状态: 可见性与完整授权列表."""

    return await read_strategy_detail(client, grant_cast.owner.token, strategy.id)


@pytest_asyncio.fixture
async def grant_cast(client: AsyncClient, database: PlatformDatabase) -> GrantCast:
    return GrantCast(
        owner=await create_signed_in_account(database, client, OWNER_USERNAME),
        grantee=await create_signed_in_account(database, client, GRANTEE_USERNAME),
        other=await create_signed_in_account(database, client, OTHER_USERNAME),
    )


async def test_granting_makes_a_private_strategy_shared(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    assert strategy.visibility_type == StrategyVisibility.PRIVATE.value

    response = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)]
    )

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.strategy.visibility_type is StrategyVisibility.SHARED
    assert granted_user_ids(owner_view) == [grant_cast.grantee.user_id]
    assert owner_view.grants[0].permission_type is GrantPermission.READ
    assert owner_view.grants[0].granted_by_user_id == grant_cast.owner.user_id


async def test_the_grantee_gains_visibility_and_sees_no_grant_list(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await put_grants(client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)])

    assert strategy.id in await list_visible_strategy_ids(client, grant_cast.grantee.token)

    grantee_view = await read_strategy_detail(client, grant_cast.grantee.token, strategy.id)

    assert grantee_view.grants == []


async def test_an_ungranted_account_sees_neither_the_strategy_nor_a_hint(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    granted = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)]
    )

    assert granted.status_code == 200, granted.text

    assert strategy.id not in await list_visible_strategy_ids(client, grant_cast.other.token)
    assert await read_strategy_status_code(client, grant_cast.other.token, strategy.id) == 404


async def test_replacing_the_grant_list_revokes_the_previous_grantee(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await put_grants(client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)])

    response = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.other.user_id)]
    )

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert granted_user_ids(owner_view) == [grant_cast.other.user_id]
    assert await read_strategy_status_code(client, grant_cast.grantee.token, strategy.id) == 404
    assert await read_strategy_status_code(client, grant_cast.other.token, strategy.id) == 200


async def test_an_empty_grant_list_revokes_everything_and_returns_to_private(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await put_grants(
        client,
        grant_cast.owner,
        strategy.id,
        [grant_entry(grant_cast.grantee.user_id), grant_entry(grant_cast.other.user_id)],
    )

    response = await put_grants(client, grant_cast.owner, strategy.id, [])

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.grants == []
    assert owner_view.strategy.visibility_type is StrategyVisibility.PRIVATE
    assert await read_strategy_status_code(client, grant_cast.grantee.token, strategy.id) == 404


async def test_a_public_strategy_stays_public_and_keeps_the_grants(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(
        database,
        grant_cast.owner.user,
        GRANT_STRATEGY_NAME,
        visibility_type=StrategyVisibility.PUBLIC,
    )

    response = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)]
    )

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.strategy.visibility_type is StrategyVisibility.PUBLIC
    assert granted_user_ids(owner_view) == [grant_cast.grantee.user_id]
    assert await read_strategy_status_code(client, grant_cast.other.token, strategy.id) == 200


async def test_empty_grants_on_a_public_strategy_leave_it_public(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(
        database,
        grant_cast.owner.user,
        GRANT_STRATEGY_NAME,
        visibility_type=StrategyVisibility.PUBLIC,
    )

    response = await put_grants(client, grant_cast.owner, strategy.id, [])

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.strategy.visibility_type is StrategyVisibility.PUBLIC
    assert await read_strategy_status_code(client, grant_cast.other.token, strategy.id) == 200


async def test_a_shared_strategy_without_grants_returns_to_private_on_an_empty_list(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    """空名单配 `shared` 也要落到 `private`.

    这一格只有本用例覆盖: 上传时可指定 `shared` 而不带任何授权, 此后"没有名单"与
    `private` 该是同一件事——留着 `shared` 就成了一个谁也看不见、却谁也没被挡在门外的
    空壳状态, 而授权表对它同样不生效.
    """

    strategy = await create_strategy_record(
        database,
        grant_cast.owner.user,
        GRANT_STRATEGY_NAME,
        visibility_type=StrategyVisibility.SHARED,
    )

    response = await put_grants(client, grant_cast.owner, strategy.id, [])

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.strategy.visibility_type is StrategyVisibility.PRIVATE


async def test_only_the_owner_may_replace_the_grant_list(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    response = await put_grants(
        client, grant_cast.grantee, strategy.id, [grant_entry(grant_cast.grantee.user_id)]
    )

    assert response.status_code == 404
    assert response.json()["detail"] == STRATEGY_NOT_FOUND_MESSAGE

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.grants == []
    assert owner_view.strategy.visibility_type is StrategyVisibility.PRIVATE


async def test_a_grantee_may_not_widen_its_own_access(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    granted = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)]
    )

    assert granted.status_code == 200, granted.text

    response = await put_grants(
        client,
        grant_cast.grantee,
        strategy.id,
        [grant_entry(grant_cast.grantee.user_id), grant_entry(grant_cast.other.user_id)],
    )

    assert response.status_code == 404
    assert await read_strategy_status_code(client, grant_cast.other.token, strategy.id) == 404


async def test_a_non_owner_may_not_replace_grants_even_on_a_public_strategy(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    """非归属人对一条**它看得见**的策略改授权, 仍是 404.

    上面那条"非归属人"用的是 private 策略, 于是 404 也可能只是没通过可见性这一关; 换成
    public, 可见性对非归属人是敞开的, 此时还能拒就只剩归属校验这一个原因.
    """

    strategy = await create_strategy_record(
        database,
        grant_cast.owner.user,
        GRANT_STRATEGY_NAME,
        visibility_type=StrategyVisibility.PUBLIC,
    )

    assert await read_strategy_status_code(client, grant_cast.other.token, strategy.id) == 200

    response = await put_grants(
        client, grant_cast.other, strategy.id, [grant_entry(grant_cast.other.user_id)]
    )

    assert response.status_code == 404
    assert response.json()["detail"] == STRATEGY_NOT_FOUND_MESSAGE

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.grants == []
    assert owner_view.strategy.visibility_type is StrategyVisibility.PUBLIC


async def test_replacing_grants_on_an_absent_strategy_is_not_found(
    client: AsyncClient, grant_cast: GrantCast
) -> None:
    response = await put_grants(
        client,
        grant_cast.owner,
        "no-such-strategy-identifier",
        [grant_entry(grant_cast.grantee.user_id)],
    )

    assert response.status_code == 404
    assert response.json()["detail"] == STRATEGY_NOT_FOUND_MESSAGE


async def test_replacing_grants_on_a_soft_deleted_strategy_is_not_found(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await soft_delete_strategy_record(database, strategy.id)

    response = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)]
    )

    assert response.status_code == 404
    assert response.json()["detail"] == STRATEGY_NOT_FOUND_MESSAGE


async def test_granting_to_the_owner_is_rejected(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    response = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.owner.user_id)]
    )

    assert response.status_code == 400
    assert response.json()["detail"] == GRANT_TO_OWNER_MESSAGE

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.grants == []
    assert owner_view.strategy.visibility_type is StrategyVisibility.PRIVATE


async def test_an_unknown_grantee_is_rejected_without_writing_anything(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    response = await put_grants(
        client,
        grant_cast.owner,
        strategy.id,
        [grant_entry(grant_cast.grantee.user_id), grant_entry(ABSENT_USER_ID)],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == UNKNOWN_GRANTEE_MESSAGE

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.grants == []
    assert owner_view.strategy.visibility_type is StrategyVisibility.PRIVATE


async def test_a_failed_replacement_leaves_the_previous_grants_intact(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await put_grants(client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)])

    response = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(ABSENT_USER_ID)]
    )

    assert response.status_code == 400

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert granted_user_ids(owner_view) == [grant_cast.grantee.user_id]
    assert owner_view.strategy.visibility_type is StrategyVisibility.SHARED
    assert await read_strategy_status_code(client, grant_cast.grantee.token, strategy.id) == 200


async def test_a_repeated_grantee_is_rejected(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    """重复名单被拒时, 盘上那份旧名单要原样留着.

    先写入一份合法名单再发非法请求: 从空名单起步的话, "旧名单没被改动"与"压根没写过"是
    同一个观测结果, 断言落在后者上也照样绿.
    """

    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await put_grants(client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)])

    response = await put_grants(
        client,
        grant_cast.owner,
        strategy.id,
        [grant_entry(grant_cast.other.user_id), grant_entry(grant_cast.other.user_id)],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == REPEATED_GRANTEE_MESSAGE

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert granted_user_ids(owner_view) == [grant_cast.grantee.user_id]
    assert await read_strategy_status_code(client, grant_cast.other.token, strategy.id) == 404


async def test_a_grant_list_beyond_the_cap_is_rejected(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    """同上: 超限被拒之后, 旧名单与旧可见性都该原样不动."""

    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await put_grants(client, grant_cast.owner, strategy.id, [grant_entry(grant_cast.grantee.user_id)])

    oversized_grants = [
        grant_entry(f"{index:032x}")
        for index in range(MAXIMUM_GRANTS_PER_STRATEGY + 1)
    ]

    response = await put_grants(client, grant_cast.owner, strategy.id, oversized_grants)

    assert response.status_code == 400
    assert response.json()["detail"] == TOO_MANY_GRANTS_MESSAGE

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert granted_user_ids(owner_view) == [grant_cast.grantee.user_id]
    assert owner_view.strategy.visibility_type is StrategyVisibility.SHARED


def test_the_grant_cap_keeps_its_agreed_value() -> None:
    """上限的**取值**本身也钉住.

    这个数不只是业务尺寸: 它同时是挡住 `IN` 越界那道 500 的闸 (见
    `_ensure_each_grantee_exists` 的 docstring), 而它又经错误文案对外可见——
    「不得超过 N」就是接口契约的一部分. 上下限两条用例都从常量派生, 常量改了它们
    照样绿, 于是这里写死字面值: 改这个数应当是有人**专门决定**过的事, 不该混在
    某次顺手调整里悄悄发生.
    """

    assert MAXIMUM_GRANTS_PER_STRATEGY == 1000
    assert "1000" in TOO_MANY_GRANTS_MESSAGE


async def test_a_grant_list_exactly_at_the_cap_is_accepted(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    """恰好等于上限的一份名单要被收下.

    上限用例只测"超了被拒"的话, 把判据写成 `>=` 也照样绿——那会让上限比声明的少一个,
    而调用方无从知道. 这里钉住的是边界本身, 不是拒与收.
    """

    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    grantee_user_ids = await bulk_create_user_records(
        database, MAXIMUM_GRANTS_PER_STRATEGY, "cap-grantee"
    )

    response = await put_grants(
        client,
        grant_cast.owner,
        strategy.id,
        [grant_entry(user_id) for user_id in grantee_user_ids],
    )

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert granted_user_ids(owner_view) == sorted(grantee_user_ids)
    assert owner_view.strategy.visibility_type is StrategyVisibility.SHARED


@pytest.mark.parametrize("permission_type", [GrantPermission.READ, GrantPermission.RUN])
async def test_the_permission_type_round_trips(
    client: AsyncClient,
    database: PlatformDatabase,
    grant_cast: GrantCast,
    permission_type: GrantPermission,
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    await put_grants(
        client,
        grant_cast.owner,
        strategy.id,
        [grant_entry(grant_cast.grantee.user_id, permission_type)],
    )

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert owner_view.grants[0].permission_type is permission_type


async def test_repeating_the_same_list_changes_nothing(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)
    grants = [grant_entry(grant_cast.grantee.user_id, GrantPermission.RUN)]

    await put_grants(client, grant_cast.owner, strategy.id, grants)

    response = await put_grants(client, grant_cast.owner, strategy.id, grants)

    assert response.status_code == 200, response.text

    owner_view = await read_owner_view(client, grant_cast, strategy)

    assert granted_user_ids(owner_view) == [grant_cast.grantee.user_id]
    assert owner_view.grants[0].permission_type is GrantPermission.RUN


async def test_an_unknown_permission_type_is_rejected(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    response = await client.put(
        grants_path(strategy.id),
        json={
            "grants": [
                {
                    "grantee_user_id": grant_cast.grantee.user_id,
                    "permission_type": "admin",
                }
            ]
        },
        headers=bearer_headers(grant_cast.owner.token),
    )

    assert response.status_code == 422


async def test_rejections_never_echo_the_submitted_grantee_id(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    unknown_response = await put_grants(
        client, grant_cast.owner, strategy.id, [grant_entry(VALUE_MARKER)]
    )
    repeated_response = await put_grants(
        client,
        grant_cast.owner,
        strategy.id,
        [grant_entry(VALUE_MARKER), grant_entry(VALUE_MARKER)],
    )

    assert unknown_response.status_code == 400
    assert repeated_response.status_code == 400
    assert VALUE_MARKER not in unknown_response.text
    assert VALUE_MARKER not in repeated_response.text


async def test_replacing_grants_requires_a_token(
    client: AsyncClient, database: PlatformDatabase, grant_cast: GrantCast
) -> None:
    strategy = await create_strategy_record(database, grant_cast.owner.user, GRANT_STRATEGY_NAME)

    response = await client.put(
        grants_path(strategy.id), json={"grants": [grant_entry(grant_cast.grantee.user_id)]}
    )

    assert response.status_code == 401
