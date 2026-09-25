"""策略可见范围: private / shared / public 三种语义.

private 仅供归属人, 授权表对它不生效; shared 才吃授权表; public 对全体登录用户开放.
授权列表只回给归属人——被授权人若看得到, 就知道还有谁也被授权了.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import GrantPermission, StrategyVisibility
from app.catalog.models import UserModel
from app.catalog.schemas import StrategyDetailResponse, StrategyResponse
from app.catalog.visibility import STRATEGY_NOT_FOUND_MESSAGE

from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    bearer_headers,
    create_strategy_grant_record,
    create_strategy_record,
    create_user_record,
    fetch_page,
    login,
    record_ids,
    soft_delete_strategy_record,
)


STRATEGIES_PATH = "/api/strategies"
OWNER_USERNAME = "visibility-owner"
VIEWER_USERNAME = "visibility-viewer"


@pytest_asyncio.fixture
async def owner_and_viewer(
    database: PlatformDatabase, client: AsyncClient
) -> tuple[UserModel, str, UserModel, str]:
    """归属人与旁观者两个账号, 及各自的访问令牌."""

    owner = await create_user_record(database, OWNER_USERNAME)
    viewer = await create_user_record(database, VIEWER_USERNAME)

    return (
        owner,
        await login(client, OWNER_USERNAME, DEFAULT_MEMBER_PASSWORD),
        viewer,
        await login(client, VIEWER_USERNAME, DEFAULT_MEMBER_PASSWORD),
    )


async def _list_visible_strategy_ids(client: AsyncClient, token: str) -> list[str]:
    return record_ids(
        await fetch_page(client, STRATEGIES_PATH, token, StrategyResponse)
    )


async def _read_strategy_status_code(client: AsyncClient, token: str, strategy_id: str) -> int:
    response = await client.get(
        f"{STRATEGIES_PATH}/{strategy_id}", headers=bearer_headers(token)
    )

    return response.status_code


async def test_public_strategy_is_visible_to_another_account(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, _, _, viewer_token = owner_and_viewer
    strategy = await create_strategy_record(
        database, owner, "public-grid", visibility_type=StrategyVisibility.PUBLIC
    )

    assert strategy.id in await _list_visible_strategy_ids(client, viewer_token)
    assert await _read_strategy_status_code(client, viewer_token, strategy.id) == 200


async def test_shared_strategy_with_grant_is_visible(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, _, viewer, viewer_token = owner_and_viewer
    strategy = await create_strategy_record(
        database, owner, "shared-grid", visibility_type=StrategyVisibility.SHARED
    )
    await create_strategy_grant_record(
        database, strategy, grantee=viewer, granted_by=owner
    )

    assert strategy.id in await _list_visible_strategy_ids(client, viewer_token)
    assert await _read_strategy_status_code(client, viewer_token, strategy.id) == 200


async def test_shared_strategy_without_grant_is_hidden(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, _, _, viewer_token = owner_and_viewer
    strategy = await create_strategy_record(
        database, owner, "shared-grid", visibility_type=StrategyVisibility.SHARED
    )

    assert strategy.id not in await _list_visible_strategy_ids(client, viewer_token)
    assert await _read_strategy_status_code(client, viewer_token, strategy.id) == 404


async def test_private_strategy_stays_hidden_even_when_granted(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, _, viewer, viewer_token = owner_and_viewer
    strategy = await create_strategy_record(database, owner, "private-grid")
    await create_strategy_grant_record(
        database, strategy, grantee=viewer, granted_by=owner
    )

    assert strategy.id not in await _list_visible_strategy_ids(client, viewer_token)
    assert await _read_strategy_status_code(client, viewer_token, strategy.id) == 404


async def test_owner_always_sees_own_strategy_regardless_of_visibility(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, owner_token, _, _ = owner_and_viewer
    private_strategy = await create_strategy_record(database, owner, "private-grid")
    shared_strategy = await create_strategy_record(
        database, owner, "shared-grid", visibility_type=StrategyVisibility.SHARED
    )

    visible_ids = await _list_visible_strategy_ids(client, owner_token)

    assert private_strategy.id in visible_ids
    assert shared_strategy.id in visible_ids


async def test_grant_list_is_returned_only_to_the_owner(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, owner_token, viewer, viewer_token = owner_and_viewer
    strategy = await create_strategy_record(
        database, owner, "shared-grid", visibility_type=StrategyVisibility.SHARED
    )
    await create_strategy_grant_record(
        database, strategy, grantee=viewer, granted_by=owner
    )

    owner_detail = await client.get(
        f"{STRATEGIES_PATH}/{strategy.id}", headers=bearer_headers(owner_token)
    )
    viewer_detail = await client.get(
        f"{STRATEGIES_PATH}/{strategy.id}", headers=bearer_headers(viewer_token)
    )

    owner_grants = StrategyDetailResponse.model_validate(owner_detail.json()).grants
    viewer_grants = StrategyDetailResponse.model_validate(viewer_detail.json()).grants

    assert [grant.grantee_user_id for grant in owner_grants] == [viewer.id]
    assert owner_grants[0].permission_type is GrantPermission.READ
    assert viewer_grants == []


async def test_grantee_learning_one_grant_does_not_reveal_other_grantees(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, _, viewer, viewer_token = owner_and_viewer
    third_user = await create_user_record(database, "visibility-third")
    strategy = await create_strategy_record(
        database, owner, "shared-grid", visibility_type=StrategyVisibility.SHARED
    )
    await create_strategy_grant_record(
        database, strategy, grantee=viewer, granted_by=owner
    )
    await create_strategy_grant_record(
        database, strategy, grantee=third_user, granted_by=owner
    )

    response = await client.get(
        f"{STRATEGIES_PATH}/{strategy.id}", headers=bearer_headers(viewer_token)
    )

    assert response.status_code == 200
    assert third_user.id not in response.text
    assert third_user.username not in response.text


async def test_soft_deleted_strategy_disappears_for_owner_and_others(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, owner_token, _, viewer_token = owner_and_viewer
    strategy = await create_strategy_record(
        database, owner, "public-grid", visibility_type=StrategyVisibility.PUBLIC
    )

    await soft_delete_strategy_record(database, strategy.id)

    assert strategy.id not in await _list_visible_strategy_ids(client, owner_token)
    assert strategy.id not in await _list_visible_strategy_ids(client, viewer_token)
    assert await _read_strategy_status_code(client, owner_token, strategy.id) == 404


async def test_deleted_strategy_reports_the_same_message_as_an_absent_one(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
) -> None:
    owner, owner_token, _, _ = owner_and_viewer
    strategy = await create_strategy_record(database, owner, "public-grid")

    await soft_delete_strategy_record(database, strategy.id)

    deleted_response = await client.get(
        f"{STRATEGIES_PATH}/{strategy.id}", headers=bearer_headers(owner_token)
    )
    absent_response = await client.get(
        f"{STRATEGIES_PATH}/no-such-strategy-identifier",
        headers=bearer_headers(owner_token),
    )

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
    owner_and_viewer: tuple[UserModel, str, UserModel, str],
    visibility_type: StrategyVisibility,
) -> None:
    owner, owner_token, _, _ = owner_and_viewer
    strategy = await create_strategy_record(
        database, owner, "round-trip-grid", visibility_type=visibility_type
    )

    response = await client.get(
        f"{STRATEGIES_PATH}/{strategy.id}", headers=bearer_headers(owner_token)
    )

    detail = StrategyDetailResponse.model_validate(response.json())

    assert detail.strategy.visibility_type is visibility_type
    assert detail.strategy.id == strategy.id
