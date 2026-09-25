"""登录与当前用户.

本模块的两个端点中, 登录不需要令牌, 其余全部端点由各路由模块各自的依赖把关.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter
from sqlalchemy import select

from ..auth.dependencies import (
    DISABLED_ACCOUNT_DETAIL,
    CurrentUserDependency,
    unauthorized_error,
)
from ..auth.passwords import hash_password, verify_password
from ..auth.tokens import create_access_token
from ..catalog.enums import UserStatus
from ..catalog.models import UserModel
from ..catalog.schemas import AccessTokenResponse, LoginRequest, UserResponse
from ..dependencies import SessionDependency, SettingsDependency


router = APIRouter()

INVALID_CREDENTIALS_DETAIL = "用户名或密码不正确"

DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


@router.post("/login", response_model=AccessTokenResponse)
async def login_handler(
    request_body: LoginRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> AccessTokenResponse:
    """用户名口令换访问令牌.

    账号不存在时也照常走一遍口令校验: 直接返回会让"用户不存在"明显更快, 据此可枚举用户名.
    """

    result = await session.execute(
        select(UserModel).where(UserModel.username == request_body.username)
    )
    user = result.scalar_one_or_none()

    if user is None:
        verify_password(request_body.password, DUMMY_PASSWORD_HASH)
        raise unauthorized_error(INVALID_CREDENTIALS_DETAIL)

    if not verify_password(request_body.password, user.password_hash):
        raise unauthorized_error(INVALID_CREDENTIALS_DETAIL)

    if user.status != UserStatus.ACTIVE.value:
        raise unauthorized_error(DISABLED_ACCOUNT_DETAIL)

    return AccessTokenResponse(
        access_token=create_access_token(
            user_id=user.id,
            username=user.username,
            user_type=user.user_type,
            secret_key=settings.jwt_secret_key,
            expires_in_minutes=settings.access_token_expire_minutes,
        ),
        expires_in_minutes=settings.access_token_expire_minutes,
    )


@router.get("/me", response_model=UserResponse)
async def read_current_user_handler(current_user: CurrentUserDependency) -> UserModel:
    """当前登录用户."""

    return current_user
