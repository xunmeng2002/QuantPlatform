"""测试辅助: 经接口登录与取列表, 经 ORM 造策略与运行.

造租户数据一律直落库而不过管理员接口: 测越权时若还要管理员在场, 就分不清拒绝到底是因为
归属校验生效, 还是因为调用者本来就不是管理员.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy.orm import DeclarativeBase

from app.auth.passwords import hash_password
from app.catalog.database import PlatformDatabase
from app.catalog.enums import (
    GrantPermission,
    RunStatus,
    StrategyVisibility,
    UserStatus,
    UserType,
)
from app.catalog.models import (
    RunModel,
    StrategyGrantModel,
    StrategyModel,
    StrategyVersionModel,
    UserModel,
)
from app.catalog.schemas import PageResponse
from app.clock import utc_now
from app.ids import generate_identifier


ModelType = TypeVar("ModelType", bound=DeclarativeBase)
RecordType = TypeVar("RecordType", bound=BaseModel)

DEFAULT_MEMBER_PASSWORD = "member-table-password"
LOGIN_PATH = "/api/auth/login"

BASELINE_TRADE_COUNT = 84
BASELINE_ORDER_COUNT = 654
BASELINE_BALANCE = 998951.4506464996


async def login(client: AsyncClient, username: str, password: str) -> str:
    """登录并返回访问令牌."""

    response = await client.post(
        LOGIN_PATH, json={"username": username, "password": password}
    )

    assert response.status_code == 200, response.text

    return response.json()["access_token"]


def bearer_headers(token: str) -> dict[str, str]:
    """构造携带令牌的请求头."""

    return {"Authorization": f"Bearer {token}"}


async def fetch_page(
    client: AsyncClient,
    path: str,
    token: str,
    response_model: type[RecordType],
    **parameters: object,
) -> PageResponse[RecordType]:
    """请求一个列表端点并解出分页信封."""

    response = await client.get(path, params=parameters, headers=bearer_headers(token))

    assert response.status_code == 200, response.text

    return PageResponse[response_model].model_validate(response.json())


def record_ids(page: PageResponse[RecordType]) -> list[str]:
    """当页各记录的 id, 保持既有顺序."""

    return [record.id for record in page.records]


async def persist_record(database: PlatformDatabase, record: ModelType) -> ModelType:
    """直接落库一条新记录."""

    async with database.session_scope() as session:
        session.add(record)
        await session.commit()

    return record


async def update_record_by_id(
    database: PlatformDatabase,
    model_type: type[ModelType],
    record_id: str,
    apply_change: Callable[[ModelType], None],
) -> None:
    """按主键取一条记录, 施加改动后落库.

    取不到时直接报错: 夹具对象缺失会让后续断言以"属性为 None"这类面目出现, 追因很费时.
    """

    async with database.session_scope() as session:
        record = await session.get(model_type, record_id)

        if record is None:
            raise LookupError(f"测试夹具记录不存在: {model_type.__tablename__}")

        apply_change(record)

        await session.commit()


async def create_user_record(
    database: PlatformDatabase,
    username: str,
    password: str = DEFAULT_MEMBER_PASSWORD,
    user_type: UserType = UserType.USER,
    status: UserStatus = UserStatus.ACTIVE,
) -> UserModel:
    """直接落库建一个账号."""

    return await persist_record(
        database,
        UserModel(
            id=generate_identifier(),
            username=username,
            password_hash=hash_password(password),
            display_name=username,
            user_type=user_type.value,
            status=status.value,
        ),
    )


async def update_user_status_record(
    database: PlatformDatabase, user_id: str, status: UserStatus
) -> None:
    """直接落库改账号状态."""

    await update_record_by_id(
        database, UserModel, user_id, lambda user: setattr(user, "status", status.value)
    )


async def create_strategy_record(
    database: PlatformDatabase,
    owner: UserModel,
    name: str,
    visibility_type: StrategyVisibility = StrategyVisibility.PRIVATE,
) -> StrategyModel:
    """直接落库建一个策略."""

    return await persist_record(
        database,
        StrategyModel(
            id=generate_identifier(),
            owner_user_id=owner.id,
            name=name,
            description=f"{name} 的说明",
            visibility_type=visibility_type.value,
        ),
    )


async def soft_delete_strategy_record(database: PlatformDatabase, strategy_id: str) -> None:
    """直接落库把策略标记为已删."""

    await update_record_by_id(
        database,
        StrategyModel,
        strategy_id,
        lambda strategy: setattr(strategy, "deleted_at", utc_now()),
    )


async def create_strategy_grant_record(
    database: PlatformDatabase,
    strategy: StrategyModel,
    grantee: UserModel,
    granted_by: UserModel,
    permission_type: GrantPermission = GrantPermission.READ,
) -> None:
    """直接落库建一条授权."""

    await persist_record(
        database,
        StrategyGrantModel(
            strategy_id=strategy.id,
            grantee_user_id=grantee.id,
            permission_type=permission_type.value,
            granted_by_user_id=granted_by.id,
        ),
    )


async def create_strategy_version_record(
    database: PlatformDatabase,
    strategy: StrategyModel,
    uploaded_by: UserModel,
    entry_filename: str = "entry.py",
) -> StrategyVersionModel:
    """直接落库建一个策略版本."""

    return await persist_record(
        database,
        StrategyVersionModel(
            id=generate_identifier(),
            strategy_id=strategy.id,
            version_no=1,
            entry_filename=entry_filename,
            config_filename="StrategyConfig.json",
            manifest_json="{}",
            source_hash=generate_identifier(),
            storage_path=f"users/{uploaded_by.id}/strategies/{strategy.id}/1",
            uploaded_by_user_id=uploaded_by.id,
        ),
    )


async def create_run_record(
    database: PlatformDatabase,
    user: UserModel,
    strategy: StrategyModel,
    version: StrategyVersionModel,
    status: RunStatus = RunStatus.SUCCEEDED,
    trade_count: int = BASELINE_TRADE_COUNT,
    order_count: int = BASELINE_ORDER_COUNT,
    balance: float = BASELINE_BALANCE,
    submitted_at: datetime | None = None,
) -> RunModel:
    """直接落库建一个运行.

    指标列可取非基线值: 排序与筛选的断言要靠互不相同的取值才区分得开.
    """

    run = RunModel(
        id=generate_identifier(),
        user_id=user.id,
        strategy_id=strategy.id,
        strategy_version_id=version.id,
        status=status.value,
        trade_count=trade_count,
        order_count=order_count,
        balance=balance,
        workspace_path=f"runs/{generate_identifier()}",
    )

    if submitted_at is not None:
        run.submitted_at = submitted_at

    return await persist_record(database, run)
