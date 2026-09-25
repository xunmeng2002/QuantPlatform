"""用户管理.

除 `GET /directory` 外全部端点要求管理员: 用户清单本身即租户列表, 开放给普通用户等于泄漏
平台上有谁. `/directory` 是一处**有意的、收了口的**放宽, 边界写在那个处理器的 docstring 里.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import AdminUserDependency, CurrentUserDependency
from ..auth.passwords import hash_password
from ..catalog.enums import UserStatus, UserType
from ..catalog.models import UserModel
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import (
    MessageResponse,
    PageResponse,
    UserCreateRequest,
    UserDirectoryEntryResponse,
    UserResponse,
    UserStatusUpdateRequest,
)
from ..catalog.visibility import load_user
from ..dependencies import SessionDependency
from ..errors import ConflictError, InvalidRequestError
from ..ids import generate_identifier


router = APIRouter()

USERNAME_TAKEN_MESSAGE = "用户名已被占用"
USER_STATUS_UPDATED_MESSAGE = "用户状态已更新"
LAST_ACTIVE_ADMIN_MESSAGE = "至少需保留一个启用状态的管理员账号"
BLANK_DISPLAY_NAME_MESSAGE = "显示名不能为空白"
MAXIMUM_DIRECTORY_QUERY_LENGTH = 128


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


@router.get("/directory", response_model=PageResponse[UserDirectoryEntryResponse])
async def list_user_directory_handler(
    session: SessionDependency,
    current_user: CurrentUserDependency,
    query: str | None = Query(None, max_length=MAXIMUM_DIRECTORY_QUERY_LENGTH),
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[UserDirectoryEntryResponse]:
    """受限用户目录: 给授权表单选人用, 只回 id 与显示名.

    **为什么这是一处有意的放宽.** 授权端点按 `user_id` 指定被授权人, 而平台没有别的路径让
    普通用户知道同事的 `user_id`——授权功能于是写完了却无法在界面上操作. 放宽的收口如下:

    - 只列**启用中**的账号: 停用账号登录不了 (令牌里记的账号状态, 见 `PATCH /status`),
      授权给它是给人一条必然无效的选项.
    - 只列**显示名非空**的账号: 与建号时"显示名必填"配套, 否则管理员随手建的账号会从目录里
      消失, 表现是"表单里选不到人".
    - 只回 `id` 与 `display_name`. `username` **绝不出现**: 登录接口特意为未知用户名跑一遍
      假校验 (见 `auth.passwords` 的 `DUMMY_PASSWORD_HASH`) 防的就是用户名枚举, 目录里回退
      `username` 等于把那笔代价还回去. `user_type` 与 `status` 也不出现——谁是管理员、谁被
      停用了, 都不该由这条端点透露.
    - 排除自己: 授权给策略归属人会被 `PUT /grants` 以 400 挡住, 让表单能选中一个必然失败的
      选项是白白造一条死路.

    由此, 泄漏面就是"登录用户可枚举**启用中账号的显示名**"这一条, 且它不含用户名.
    显示名可以重名, 故它**不是**唯一标识——选人时以 `id` 为准 (见 `PROGRESS.md` 的已知缺口).

    `query` 按显示名做子串过滤, 走 `contains(..., autoescape=True)`: 手工转义 `%` 与 `_`
    是拼 SQL 的近亲, 交给 SQLAlchemy.

    排序键除显示名外**必须**带上 `Id`: 显示名没有唯一约束, 并列行的顺序在 SQLite 里未定义,
    翻页时同一条会出现两次、另一条一次都不出现 (`runs` 列表的 `SubmittedAt, Id` 同此理由).
    """

    directory_query = (
        select(UserModel)
        .where(UserModel.status == UserStatus.ACTIVE.value)
        .where(UserModel.display_name != "")
        .where(UserModel.id != current_user.id)
        .order_by(UserModel.display_name.asc(), UserModel.id.asc())
    )

    if query is not None and query.strip():
        directory_query = directory_query.where(
            UserModel.display_name.contains(query.strip(), autoescape=True)
        )

    return await fetch_page(
        session, directory_query, UserDirectoryEntryResponse, offset, limit
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user_handler(
    request_body: UserCreateRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> UserModel:
    """建号. 用户名唯一, 重复即拒; 显示名必填.

    先查重再插入只为给出干净的报错; 真正的保证来自唯一约束, 故仍接住并发下的完整性冲突.

    显示名先 `strip` 再存, 空即 400 (与策略名同一条做法): 受限用户目录只列显示名非空的账号,
    一个只由空白字符组成的显示名会在目录里显示成空白行——看得见却认不出是谁.
    """

    existing_user_id = await session.scalar(
        select(UserModel.id).where(UserModel.username == request_body.username)
    )

    if existing_user_id is not None:
        raise ConflictError(USERNAME_TAKEN_MESSAGE)

    display_name = request_body.display_name.strip()

    if not display_name:
        raise InvalidRequestError(BLANK_DISPLAY_NAME_MESSAGE)

    user = UserModel(
        id=generate_identifier(),
        username=request_body.username,
        password_hash=hash_password(request_body.password),
        display_name=display_name,
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
