"""策略可见范围: private / shared / public 三种语义.

private 仅供归属人, 授权表对它不生效; shared 才吃授权表; public 对全体登录用户开放.
授权列表只回给归属人——被授权人若看得到, 就知道还有谁也被授权了.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import GrantPermission, StrategyVisibility
from app.catalog.visibility import STRATEGY_NOT_FOUND_MESSAGE

from .helpers import (
    SignedInAccount,
    create_signed_in_account,
    create_strategy_grant_record,
    create_strategy_record,
    create_user_record,
    get_strategy_response,
    list_visible_strategy_ids,
    read_strategy_detail,
    read_strategy_status_code,
    soft_delete_strategy_record,
)


OWNER_USERNAME = "visibility-owner"
VIEWER_USERNAME = "visibility-viewer"

ABSENT_STRATEGY_ID = "no-such-strategy-identifier"


@dataclass(frozen=True)
class AccountPair:
    """归属人账号与另一个账号.

    两者同为 `SignedInAccount`, 用元组装的话每个用例都得靠位置记"哪个是归属人"——而越权
    断言恰恰是把两者对调的产物, 记反了整条用例就测成相反的意思.

    留在本模块而不进 `helpers`: 只有这里用得着. `helpers` 放跨模块复用的基元,
    单一场景的"角色组"与用到它的用例放一起 (授权测试的 `GrantCast` 同理).
    """

    owner: SignedInAccount
    viewer: SignedInAccount


@pytest_asyncio.fixture
async def owner_and_viewer(
    database: PlatformDatabase, client: AsyncClient
) -> AccountPair:
    """归属人与旁观者两个账号."""

    return AccountPair(
        owner=await create_signed_in_account(database, client, OWNER_USERNAME),
        viewer=await create_signed_in_account(database, client, VIEWER_USERNAME),
    )


async def test_public_strategy_is_visible_to_another_account(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    strategy = await create_strategy_record(
        database,
        owner_and_viewer.owner.user,
        "public-grid",
        visibility_type=StrategyVisibility.PUBLIC,
    )

    viewer_token = owner_and_viewer.viewer.token

    assert strategy.id in await list_visible_strategy_ids(client, viewer_token)
    assert await read_strategy_status_code(client, viewer_token, strategy.id) == 200


async def test_shared_strategy_with_grant_is_visible(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    strategy = await create_strategy_record(
        database,
        owner_and_viewer.owner.user,
        "shared-grid",
        visibility_type=StrategyVisibility.SHARED,
    )
    await create_strategy_grant_record(
        database,
        strategy,
        grantee=owner_and_viewer.viewer.user,
        granted_by=owner_and_viewer.owner.user,
    )

    viewer_token = owner_and_viewer.viewer.token

    assert strategy.id in await list_visible_strategy_ids(client, viewer_token)
    assert await read_strategy_status_code(client, viewer_token, strategy.id) == 200


async def test_shared_strategy_without_grant_is_hidden(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    strategy = await create_strategy_record(
        database,
        owner_and_viewer.owner.user,
        "shared-grid",
        visibility_type=StrategyVisibility.SHARED,
    )

    viewer_token = owner_and_viewer.viewer.token

    assert strategy.id not in await list_visible_strategy_ids(client, viewer_token)
    assert await read_strategy_status_code(client, viewer_token, strategy.id) == 404


async def test_private_strategy_stays_hidden_even_when_granted(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    strategy = await create_strategy_record(
        database, owner_and_viewer.owner.user, "private-grid"
    )
    await create_strategy_grant_record(
        database,
        strategy,
        grantee=owner_and_viewer.viewer.user,
        granted_by=owner_and_viewer.owner.user,
    )

    viewer_token = owner_and_viewer.viewer.token

    assert strategy.id not in await list_visible_strategy_ids(client, viewer_token)
    assert await read_strategy_status_code(client, viewer_token, strategy.id) == 404


async def test_owner_always_sees_own_strategy_regardless_of_visibility(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    owner = owner_and_viewer.owner.user
    private_strategy = await create_strategy_record(database, owner, "private-grid")
    shared_strategy = await create_strategy_record(
        database, owner, "shared-grid", visibility_type=StrategyVisibility.SHARED
    )

    visible_ids = await list_visible_strategy_ids(client, owner_and_viewer.owner.token)

    assert private_strategy.id in visible_ids
    assert shared_strategy.id in visible_ids


async def test_grant_list_is_returned_only_to_the_owner(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    strategy = await create_strategy_record(
        database,
        owner_and_viewer.owner.user,
        "shared-grid",
        visibility_type=StrategyVisibility.SHARED,
    )
    await create_strategy_grant_record(
        database,
        strategy,
        grantee=owner_and_viewer.viewer.user,
        granted_by=owner_and_viewer.owner.user,
    )

    owner_grants = (
        await read_strategy_detail(client, owner_and_viewer.owner.token, strategy.id)
    ).grants
    viewer_grants = (
        await read_strategy_detail(client, owner_and_viewer.viewer.token, strategy.id)
    ).grants

    assert [grant.grantee_user_id for grant in owner_grants] == [
        owner_and_viewer.viewer.user_id
    ]
    assert owner_grants[0].permission_type is GrantPermission.READ
    assert viewer_grants == []


async def test_grantee_learning_one_grant_does_not_reveal_other_grantees(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    third_user = await create_user_record(database, "visibility-third")
    strategy = await create_strategy_record(
        database,
        owner_and_viewer.owner.user,
        "shared-grid",
        visibility_type=StrategyVisibility.SHARED,
    )
    await create_strategy_grant_record(
        database,
        strategy,
        grantee=owner_and_viewer.viewer.user,
        granted_by=owner_and_viewer.owner.user,
    )
    await create_strategy_grant_record(
        database,
        strategy,
        grantee=third_user,
        granted_by=owner_and_viewer.owner.user,
    )

    response = await get_strategy_response(
        client, owner_and_viewer.viewer.token, strategy.id
    )

    assert response.status_code == 200
    assert third_user.id not in response.text
    assert third_user.username not in response.text


async def test_soft_deleted_strategy_disappears_for_owner_and_others(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    strategy = await create_strategy_record(
        database,
        owner_and_viewer.owner.user,
        "public-grid",
        visibility_type=StrategyVisibility.PUBLIC,
    )

    await soft_delete_strategy_record(database, strategy.id)

    owner_token = owner_and_viewer.owner.token
    viewer_token = owner_and_viewer.viewer.token

    assert strategy.id not in await list_visible_strategy_ids(client, owner_token)
    assert strategy.id not in await list_visible_strategy_ids(client, viewer_token)
    assert await read_strategy_status_code(client, owner_token, strategy.id) == 404


async def test_deleted_strategy_reports_the_same_message_as_an_absent_one(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
) -> None:
    strategy = await create_strategy_record(
        database, owner_and_viewer.owner.user, "public-grid"
    )

    await soft_delete_strategy_record(database, strategy.id)

    owner_token = owner_and_viewer.owner.token

    deleted_response = await get_strategy_response(client, owner_token, strategy.id)
    absent_response = await get_strategy_response(client, owner_token, ABSENT_STRATEGY_ID)

    assert deleted_response.status_code == absent_response.status_code == 404
    assert deleted_response.json()["detail"] == STRATEGY_NOT_FOUND_MESSAGE
    assert deleted_response.json() == absent_response.json()


@pytest.mark.parametrize(
    "visibility_type",
    [StrategyVisibility.PRIVATE, StrategyVisibility.SHARED, StrategyVisibility.PUBLIC],
)
async def test_strategy_visibility_survives_a_round_trip(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: AccountPair,
    visibility_type: StrategyVisibility,
) -> None:
    strategy = await create_strategy_record(
        database,
        owner_and_viewer.owner.user,
        "round-trip-grid",
        visibility_type=visibility_type,
    )

    detail = await read_strategy_detail(
        client, owner_and_viewer.owner.token, strategy.id
    )

    assert detail.strategy.visibility_type is visibility_type
    assert detail.strategy.id == strategy.id
