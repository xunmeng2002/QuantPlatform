"""可见性与归属的统一收口.

多租户过滤只在本模块实现, 路由层不得自行拼 where 条件: 条件散落各处时漏一处即越权,
且无处审计. 所有涉及归属的查询都必须经由这里的构造函数或取件函数.

策略可见范围 (见 StrategyVisibility): private 仅归属人; shared 另加授权表内的用户;
public 对全体登录用户. 运行只有提交人本人可见, 授权不延伸到他人的运行记录.

跨租户访问一律表现为 404 而非 403: 403 会泄漏"该 id 存在"这一事实, 多租户下应一律
表现为不存在.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ResourceNotFoundError
from .enums import StrategyVisibility, UserType
from .models import RunModel, RunTemplateModel, StrategyGrantModel, StrategyModel, UserModel


RecordType = TypeVar("RecordType")

STRATEGY_NOT_FOUND_MESSAGE = "策略不存在"
RUN_NOT_FOUND_MESSAGE = "运行不存在"
RUN_TEMPLATE_NOT_FOUND_MESSAGE = "配置模板不存在"
USER_NOT_FOUND_MESSAGE = "用户不存在"


def is_admin(user: UserModel) -> bool:
    """是否管理员."""

    return user.user_type == UserType.ADMIN.value


def build_visible_strategy_query(user: UserModel) -> Select[tuple[StrategyModel]]:
    """当前用户可见的策略查询, 已排除软删除的策略."""

    granted_strategy_ids = select(StrategyGrantModel.strategy_id).where(
        StrategyGrantModel.grantee_user_id == user.id
    )

    return (
        select(StrategyModel)
        .where(StrategyModel.deleted_at.is_(None))
        .where(
            or_(
                StrategyModel.owner_user_id == user.id,
                StrategyModel.visibility_type == StrategyVisibility.PUBLIC.value,
                and_(
                    StrategyModel.visibility_type == StrategyVisibility.SHARED.value,
                    StrategyModel.id.in_(granted_strategy_ids),
                ),
            )
        )
    )


def build_owned_strategy_query(user: UserModel) -> Select[tuple[StrategyModel]]:
    """当前用户拥有的策略查询, 用于上传新版本、授权与删除等写操作."""

    return (
        select(StrategyModel)
        .where(StrategyModel.deleted_at.is_(None))
        .where(StrategyModel.owner_user_id == user.id)
    )


def build_owned_run_query(user: UserModel) -> Select[tuple[RunModel]]:
    """当前用户提交的运行查询."""

    return select(RunModel).where(RunModel.user_id == user.id)


def build_owned_run_template_query(
    user: UserModel, strategy_id: str
) -> Select[tuple[RunTemplateModel]]:
    """当前用户在某个策略下拥有的配置模板查询.

    `strategy_id` 在这里而不是调用方: 归属过滤与策略过滤是**同一件事的两半**——模板的作用域是
    策略域, 只按 `OwnerUserId` 取件会让"用 B 策略的 URL 去改 A 策略的模板"成立, 而那正是这个
    作用域设计要挡的. 两半写在同一个函数里, 就没有"调用方漏加了一半"的位置.
    """

    return (
        select(RunTemplateModel)
        .where(RunTemplateModel.owner_user_id == user.id)
        .where(RunTemplateModel.strategy_id == strategy_id)
    )


async def _load_one_or_raise(
    session: AsyncSession,
    query: Select[tuple[RecordType]],
    not_found_message: str,
    populate_existing: bool = False,
) -> RecordType:
    """取单条记录, 取不到即抛"不存在".

    取件一律经此: 越权与真不存在必须给出同一种结果, 两处分开写就会分开判, 差别即泄漏.

    `populate_existing=True` 让本次查询**覆盖** identity map 里的那份取值, 供"重读一行"的重试
    循环用. 用 `session.expire_all()` 代替是不行的: 它会把同一个会话里认证时取到的用户对象也
    一并作废, 而下一次读它的属性会触发一次同步的惰性加载——在异步会话里那是 `MissingGreenlet`,
    表现为每个请求都 500.
    """

    if populate_existing:
        query = query.execution_options(populate_existing=True)

    row = (await session.execute(query)).scalar_one_or_none()

    if row is None:
        raise ResourceNotFoundError(not_found_message)

    return row


async def load_visible_strategy(
    session: AsyncSession, user: UserModel, strategy_id: str
) -> StrategyModel:
    """取当前用户可见的策略, 取不到即视为不存在."""

    return await _load_one_or_raise(
        session,
        build_visible_strategy_query(user).where(StrategyModel.id == strategy_id),
        STRATEGY_NOT_FOUND_MESSAGE,
    )


async def load_owned_strategy(
    session: AsyncSession, user: UserModel, strategy_id: str
) -> StrategyModel:
    """取当前用户拥有的策略, 取不到即视为不存在."""

    return await _load_one_or_raise(
        session,
        build_owned_strategy_query(user).where(StrategyModel.id == strategy_id),
        STRATEGY_NOT_FOUND_MESSAGE,
    )


async def load_owned_run(
    session: AsyncSession,
    user: UserModel,
    run_id: str,
    populate_existing: bool = False,
) -> RunModel:
    """取当前用户提交的运行, 取不到即视为不存在.

    `populate_existing` 供"重读一行"用: 取消端点的重试循环要读到**刚刚**被调度器或收尾写下的
    状态, 而不是会话里那份陈旧对象.
    """

    return await _load_one_or_raise(
        session,
        build_owned_run_query(user).where(RunModel.id == run_id),
        RUN_NOT_FOUND_MESSAGE,
        populate_existing=populate_existing,
    )


async def load_owned_run_template(
    session: AsyncSession, user: UserModel, strategy_id: str, template_id: str
) -> RunTemplateModel:
    """取当前用户在指定策略下拥有的配置模板, 取不到即视为不存在."""

    return await _load_one_or_raise(
        session,
        build_owned_run_template_query(user, strategy_id).where(
            RunTemplateModel.id == template_id
        ),
        RUN_TEMPLATE_NOT_FOUND_MESSAGE,
    )


async def load_user(session: AsyncSession, user_id: str) -> UserModel:
    """按主键取用户, 取不到即视为不存在."""

    user = await session.get(UserModel, user_id)

    if user is None:
        raise ResourceNotFoundError(USER_NOT_FOUND_MESSAGE)

    return user
