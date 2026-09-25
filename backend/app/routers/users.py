"""用户管理.

全部端点要求管理员: 用户清单本身即租户列表, 开放给普通用户等于泄漏平台上有谁.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import AdminUserDependency
from ..auth.passwords import hash_password
from ..catalog.enums import UserStatus, UserType
from ..catalog.models import UserModel
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import (
    MessageResponse,
    PageResponse,
    UserCreateRequest,
    UserResponse,
    UserStatusUpdateRequest,
)
from ..catalog.visibility import load_user
from ..dependencies import SessionDependency
from ..errors import ConflictError
from ..ids import generate_identifier


router = APIRouter()

USERNAME_TAKEN_MESSAGE = "用户名已被占用"
USER_STATUS_UPDATED_MESSAGE = "用户状态已更新"
LAST_ACTIVE_ADMIN_MESSAGE = "至少需保留一个启用状态的管理员账号"


async def _ensure_another_active_admin_remains(
    session: AsyncSession, target_user: UserModel, target_status: UserStatus
) -> None:
    """停用管理员前确认还有人能管.

    最后一个启用中的管理员被停用后, 播种逻辑按"库中是否存在管理员"判断, 不区分状态,
    重启也不会重建, 平台将失去全部管理入口且无任何接口可恢复.
    """

    if target_user.user_type != UserType.ADMIN.value:
        return

    is_active_to_disabled = (
        target_user.status == UserStatus.ACTIVE.value
        and target_status is UserStatus.DISABLED
    )

    if not is_active_to_disabled:
        return

    remaining_active_admin_count = await session.scalar(
        select(func.count())
        .select_from(UserModel)
        .where(UserModel.user_type == UserType.ADMIN.value)
        .where(UserModel.status == UserStatus.ACTIVE.value)
    )

    if (remaining_active_admin_count or 0) <= 1:
        raise ConflictError(LAST_ACTIVE_ADMIN_MESSAGE)


@router.get("", response_model=PageResponse[UserResponse])
async def list_users_handler(
    session: SessionDependency,
    admin_user: AdminUserDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[UserResponse]:
    """用户列表, 按建号时间倒序."""

    return await fetch_page(
        session,
        select(UserModel),
        UserResponse,
        offset,
        limit,
        order_by=UserModel.created_at.desc(),
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user_handler(
    request_body: UserCreateRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> UserModel:
    """建号. 用户名唯一, 重复即拒.

    先查重再插入只为给出干净的报错; 真正的保证来自唯一约束, 故仍接住并发下的完整性冲突.
    """

    existing_user_id = await session.scalar(
        select(UserModel.id).where(UserModel.username == request_body.username)
    )

    if existing_user_id is not None:
        raise ConflictError(USERNAME_TAKEN_MESSAGE)

    user = UserModel(
        id=generate_identifier(),
        username=request_body.username,
        password_hash=hash_password(request_body.password),
        display_name=request_body.display_name,
        user_type=request_body.user_type.value,
    )
    session.add(user)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(USERNAME_TAKEN_MESSAGE) from error

    return user


@router.patch("/{user_id}/status", response_model=MessageResponse)
async def update_user_status_handler(
    user_id: str,
    request_body: UserStatusUpdateRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> MessageResponse:
    """启用或停用账号.

    停用后既有令牌立即失效, 因为每个请求都会重查账号状态, 不依赖令牌里的快照.
    """

    user = await load_user(session, user_id)

    await _ensure_another_active_admin_remains(session, user, request_body.status)

    user.status = request_body.status.value

    await session.commit()

    return MessageResponse(message=USER_STATUS_UPDATED_MESSAGE)
