"""登录与当前用户.

本模块的两个端点中, 登录不需要令牌, 其余全部端点由各路由模块各自的依赖把关.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, status
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
from ..dependencies import (
    LoginThrottleDependency,
    SessionDependency,
    SettingsDependency,
)


router = APIRouter()

INVALID_CREDENTIALS_DETAIL = "用户名或密码不正确"

LOGIN_THROTTLED_DETAIL = "登录尝试过于频繁, 请稍后再试"

MINIMUM_RETRY_AFTER_SECONDS = 1

DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def throttled_login_error(remaining_lock_seconds: int) -> HTTPException:
    """构造 429, 并按 RFC 9110 给出客户端应等待的整数秒.

    秒数由节流器算好 (向上取整过), 这里只兜一个下限: `Retry-After: 0` 的语义是"立刻重试",
    会把客户端的重试变成忙等.
    """

    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=LOGIN_THROTTLED_DETAIL,
        headers={
            "Retry-After": str(max(MINIMUM_RETRY_AFTER_SECONDS, remaining_lock_seconds))
        },
    )


@router.post("/login", response_model=AccessTokenResponse)
async def login_handler(
    request: Request,
    request_body: LoginRequest,
    session: SessionDependency,
    settings: SettingsDependency,
    throttle: LoginThrottleDependency,
) -> AccessTokenResponse:
    """用户名口令换访问令牌.

    账号不存在时也照常走一遍口令校验, 并按**提交上来的那个用户名字符串**计数, 不论账号是否
    存在: 前者直接返回会让"用户不存在"明显更快, 后者只对存在的账号计数的话, 429 本身就变成了
    用户名枚举接口——两条都是同一个防枚举面.

    锁在验口令**之前**查, 省掉一次数据库查询与 260k 轮 PBKDF2: 被刷的时候正是这两样最该省.
    """

    client_address = throttle.client_address_for(
        forwarded_for=request.headers.get("x-forwarded-for"),
        peer_host=request.client.host if request.client is not None else None,
    )
    remaining_lock_seconds = throttle.remaining_lock_seconds(
        request_body.username, client_address
    )

    if remaining_lock_seconds > 0:
        raise throttled_login_error(remaining_lock_seconds)

    result = await session.execute(
        select(UserModel).where(UserModel.username == request_body.username)
    )
    user = result.scalar_one_or_none()

    if user is None:
        verify_password(request_body.password, DUMMY_PASSWORD_HASH)
        throttle.record_failure(request_body.username, client_address)
        raise unauthorized_error(INVALID_CREDENTIALS_DETAIL)

    if not verify_password(request_body.password, user.password_hash):
        throttle.record_failure(request_body.username, client_address)
        raise unauthorized_error(INVALID_CREDENTIALS_DETAIL)

    # 走到这里口令已经是对的, 凭据失败那套计数与"成功登录"都不适用: 停用账号既不记失败也不
    # 记成功, 原本累计的次数原样留到下次.
    if user.status != UserStatus.ACTIVE.value:
        raise unauthorized_error(DISABLED_ACCOUNT_DETAIL)

    throttle.record_successful_login(request_body.username)

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
