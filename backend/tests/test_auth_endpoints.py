"""登录与当前用户端点.

登录失败一律返回同一条文案: 账号不存在与口令错误若可分, 就等于对外提供了用户名枚举接口.
账号停用是例外——那要先通过口令校验才谈得上, 不构成枚举面.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.auth.dependencies import (
    DISABLED_ACCOUNT_DETAIL,
    INVALID_TOKEN_DETAIL,
    MISSING_TOKEN_DETAIL,
    unauthorized_error,
)
from app.catalog.database import PlatformDatabase
from app.catalog.enums import UserStatus, UserType
from app.catalog.models import UserModel
from app.catalog.schemas import AccessTokenResponse, UserResponse
from app.routers.auth import INVALID_CREDENTIALS_DETAIL

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME
from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    LOGIN_PATH,
    bearer_headers,
    create_user_record,
    login,
    update_user_status_record,
)


CURRENT_USER_PATH = "/api/auth/me"
GARBAGE_TOKEN = "not-a-real-token"
MEMBER_USERNAME = "member-auth"


async def _sign_in_admin(client: AsyncClient) -> str:
    return await login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)


async def test_startup_seeds_exactly_one_admin_account(
    client: AsyncClient, database: PlatformDatabase
) -> None:
    async with database.session_scope() as session:
        total = await session.scalar(select(func.count()).select_from(UserModel))
        seeded = await session.scalar(select(UserModel))

    assert total == 1
    assert seeded.username == TEST_ADMIN_USERNAME
    assert seeded.user_type == UserType.ADMIN.value


async def test_login_returns_access_token_for_seeded_admin(client: AsyncClient) -> None:
    response = await client.post(
        LOGIN_PATH, json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD}
    )

    assert response.status_code == 200

    payload = AccessTokenResponse.model_validate(response.json())

    assert payload.access_token
    assert payload.token_type == "bearer"


async def test_login_rejects_wrong_password(client: AsyncClient) -> None:
    response = await client.post(
        LOGIN_PATH,
        json={"username": TEST_ADMIN_USERNAME, "password": "wrong-admin-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == INVALID_CREDENTIALS_DETAIL


async def test_login_rejects_unknown_username_with_same_message(
    client: AsyncClient,
) -> None:
    unknown_response = await client.post(
        LOGIN_PATH,
        json={"username": "never-created-account", "password": DEFAULT_MEMBER_PASSWORD},
    )
    wrong_password_response = await client.post(
        LOGIN_PATH,
        json={"username": TEST_ADMIN_USERNAME, "password": "wrong-admin-password"},
    )

    assert unknown_response.status_code == 401
    assert unknown_response.json() == wrong_password_response.json()


async def test_login_rejects_disabled_account(
    client: AsyncClient, database: PlatformDatabase
) -> None:
    await create_user_record(database, MEMBER_USERNAME, status=UserStatus.DISABLED)

    response = await client.post(
        LOGIN_PATH,
        json={"username": MEMBER_USERNAME, "password": DEFAULT_MEMBER_PASSWORD},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == DISABLED_ACCOUNT_DETAIL


async def test_disabled_account_with_wrong_password_reports_credentials_not_status(
    client: AsyncClient, database: PlatformDatabase
) -> None:
    await create_user_record(database, MEMBER_USERNAME, status=UserStatus.DISABLED)

    response = await client.post(
        LOGIN_PATH,
        json={"username": MEMBER_USERNAME, "password": "wrong-member-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == INVALID_CREDENTIALS_DETAIL


async def test_read_current_user_returns_signed_in_account(client: AsyncClient) -> None:
    token = await _sign_in_admin(client)

    response = await client.get(CURRENT_USER_PATH, headers=bearer_headers(token))

    assert response.status_code == 200

    payload = UserResponse.model_validate(response.json())

    assert payload.username == TEST_ADMIN_USERNAME
    assert payload.status is UserStatus.ACTIVE


async def test_read_current_user_never_exposes_password_hash(client: AsyncClient) -> None:
    token = await _sign_in_admin(client)

    response = await client.get(CURRENT_USER_PATH, headers=bearer_headers(token))

    assert "password" not in response.text.lower()


async def test_read_current_user_without_token_is_unauthorized(client: AsyncClient) -> None:
    response = await client.get(CURRENT_USER_PATH)

    assert response.status_code == 401
    assert response.json()["detail"] == MISSING_TOKEN_DETAIL
    assert response.headers["www-authenticate"] == "Bearer"


async def test_read_current_user_with_malformed_token_is_unauthorized(
    client: AsyncClient,
) -> None:
    response = await client.get(CURRENT_USER_PATH, headers=bearer_headers(GARBAGE_TOKEN))

    assert response.status_code == 401
    assert response.json()["detail"] == INVALID_TOKEN_DETAIL
    assert response.headers["www-authenticate"] == "Bearer"


async def test_read_current_user_with_non_bearer_scheme_is_unauthorized(
    client: AsyncClient,
) -> None:
    token = await _sign_in_admin(client)

    response = await client.get(
        CURRENT_USER_PATH, headers={"Authorization": f"Basic {token}"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == MISSING_TOKEN_DETAIL


async def test_token_issued_before_disable_stops_working(
    client: AsyncClient, database: PlatformDatabase
) -> None:
    member = await create_user_record(database, MEMBER_USERNAME)
    token = await login(client, MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD)

    await update_user_status_record(database, member.id, UserStatus.DISABLED)

    response = await client.get(CURRENT_USER_PATH, headers=bearer_headers(token))

    assert response.status_code == 401
    assert response.json()["detail"] == DISABLED_ACCOUNT_DETAIL


async def test_admin_and_member_are_distinguishable_by_user_type(
    client: AsyncClient, database: PlatformDatabase
) -> None:
    await create_user_record(database, MEMBER_USERNAME)

    member_response = await client.get(
        CURRENT_USER_PATH,
        headers=bearer_headers(await login(client, MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD)),
    )
    admin_response = await client.get(
        CURRENT_USER_PATH, headers=bearer_headers(await _sign_in_admin(client))
    )

    assert member_response.json()["user_type"] == UserType.USER.value
    assert admin_response.json()["user_type"] == UserType.ADMIN.value


@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"username": TEST_ADMIN_USERNAME},
        {"password": TEST_ADMIN_PASSWORD},
        {"username": "", "password": TEST_ADMIN_PASSWORD},
        {"username": TEST_ADMIN_USERNAME, "password": ""},
    ],
)
async def test_login_rejects_incomplete_request_body(
    client: AsyncClient, request_body: dict[str, str]
) -> None:
    response = await client.post(LOGIN_PATH, json=request_body)

    assert response.status_code == 422


def test_unauthorized_error_carries_bearer_challenge() -> None:
    error = unauthorized_error(INVALID_TOKEN_DETAIL)

    assert error.status_code == 401
    assert error.detail == INVALID_TOKEN_DETAIL
    assert error.headers == {"WWW-Authenticate": "Bearer"}
