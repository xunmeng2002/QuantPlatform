"""当前用户与管理员依赖.

认证失败一律 401 且不区分"令牌无效"与"用户不存在": 区分开就等于把账号是否存在透给未持令牌者.
"""

from __future__ import annotations

from typing import Annotated, TypeAlias

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..catalog.enums import UserStatus
from ..catalog.models import UserModel
from ..catalog.visibility import is_admin, load_user
from ..dependencies import SessionDependency, SettingsDependency
from ..errors import PermissionDeniedError, ResourceNotFoundError
from .tokens import decode_access_token


MISSING_TOKEN_DETAIL = "缺少访问令牌"
INVALID_TOKEN_DETAIL = "访问令牌无效或已过期"
DISABLED_ACCOUNT_DETAIL = "账号已被停用"
ADMIN_REQUIRED_DETAIL = "需要管理员权限"
BEARER_SCHEME = "Bearer"


BearerCredentials: TypeAlias = Annotated[
    HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
]


def unauthorized_error(detail: str) -> HTTPException:
    """构造 401, 并按 RFC 6750 附上质询头. 登录接口与令牌校验共用同一形态."""

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": BEARER_SCHEME},
    )


async def get_current_user(
    credentials: BearerCredentials,
    session: SessionDependency,
    settings: SettingsDependency,
) -> UserModel:
    """校验令牌并取出对应用户. 令牌非法、用户已删或账号停用一律 401."""

    if credentials is None or credentials.scheme.lower() != BEARER_SCHEME.lower():
        raise unauthorized_error(MISSING_TOKEN_DETAIL)

    claims = decode_access_token(credentials.credentials, settings.jwt_secret_key)

    if claims is None:
        raise unauthorized_error(INVALID_TOKEN_DETAIL)

    try:
        user = await load_user(session, claims.user_id)
    except ResourceNotFoundError:
        raise unauthorized_error(INVALID_TOKEN_DETAIL) from None

    if user.status != UserStatus.ACTIVE.value:
        raise unauthorized_error(DISABLED_ACCOUNT_DETAIL)

    return user


async def require_admin(
    current_user: Annotated[UserModel, Depends(get_current_user)],
) -> UserModel:
    """要求当前用户为管理员."""

    if not is_admin(current_user):
        raise PermissionDeniedError(ADMIN_REQUIRED_DETAIL)

    return current_user


CurrentUserDependency: TypeAlias = Annotated[UserModel, Depends(get_current_user)]

AdminUserDependency: TypeAlias = Annotated[UserModel, Depends(require_admin)]
