"""测试辅助: 经接口登录与取列表, 经 ORM 造策略与运行.

造租户数据一律直落库而不过管理员接口: 测越权时若还要管理员在场, 就分不清拒绝到底是因为
归属校验生效, 还是因为调用者本来就不是管理员.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from httpx import AsyncClient, Response
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
from app.catalog.schemas import PageResponse, StrategyDetailResponse, StrategyResponse
from app.clock import utc_now
from app.ids import generate_identifier


ModelType = TypeVar("ModelType", bound=DeclarativeBase)
RecordType = TypeVar("RecordType", bound=BaseModel)

DEFAULT_MEMBER_PASSWORD = "member-table-password"
LOGIN_PATH = "/api/auth/login"
STRATEGIES_PATH = "/api/strategies"

UNUSABLE_PASSWORD_HASH = "not-a-password-hash"

BASELINE_TRADE_COUNT = 84
BASELINE_ORDER_COUNT = 654
BASELINE_BALANCE = 998951.4506464996


@dataclass(frozen=True)
class SignedInAccount:
    """一个已登录账号.

    连 ORM 记录一起留下, 不只是 id: 造策略、造运行这些夹具要的是记录本身, 只留 id 的话
    每个用例都得再按主键把记录取回来一次.
    """

    user: UserModel
    token: str

    @property
    def user_id(self) -> str:
        """账号主键."""

        return self.user.id


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


async def list_visible_strategy_ids(client: AsyncClient, token: str) -> list[str]:
    """当前账号可见的策略 id, 保持列表接口给出的顺序."""

    return record_ids(await fetch_page(client, STRATEGIES_PATH, token, StrategyResponse))


async def get_strategy_response(
    client: AsyncClient, token: str, strategy_id: str
) -> Response:
    """请求策略详情, 原样返回响应.

    详情请求散在多个测试模块里, 有的要状态码、有的要响应体、有的要在整段响应文本上找
    有没有泄漏别的 id——三条路只差怎么用这个响应, 不该各抄一遍 URL 与请求头.
    """

    return await client.get(
        f"{STRATEGIES_PATH}/{strategy_id}", headers=bearer_headers(token)
    )


async def read_strategy_detail(
    client: AsyncClient, token: str, strategy_id: str
) -> StrategyDetailResponse:
    """读策略详情, 断言取得到再解出响应体."""

    response = await get_strategy_response(client, token, strategy_id)

    assert response.status_code == 200, response.text

    return StrategyDetailResponse.model_validate(response.json())


async def read_strategy_status_code(
    client: AsyncClient, token: str, strategy_id: str
) -> int:
    """读策略详情, 只要状态码.

    越权用例要的就是这个: 把响应体也解出来反而会写死在"取不到"上, 而取不到与不存在本就
    该给出同一种结果.
    """

    return (await get_strategy_response(client, token, strategy_id)).status_code


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


async def bulk_create_user_records(
    database: PlatformDatabase, user_count: int, username_prefix: str
) -> list[str]:
    """批量落库建号, 只回主键, 建出来的账号不用于登录.

    口令散列一律用同一个按格式就通不过的占位串: 这些账号只为"在册"而存在, 而
    `create_user_record` 会为每个号付一次 26 万次迭代的 PBKDF2——为凑一个数量上限
    付一千次, 不值得. `verify_password` 先查格式, 所以这些账号谁也登不进来.
    """

    user_ids = [generate_identifier() for _ in range(user_count)]

    async with database.session_scope() as session:
        session.add_all(
            UserModel(
                id=user_id,
                username=f"{username_prefix}-{user_id[:12]}",
                password_hash=UNUSABLE_PASSWORD_HASH,
            )
            for user_id in user_ids
        )

        await session.commit()

    return user_ids


async def create_signed_in_account(
    database: PlatformDatabase, client: AsyncClient, username: str
) -> SignedInAccount:
    """造一个账号并经登录接口取到令牌.

    令牌一律走登录接口取, 不直接签发: 造出来的令牌若与线上签发路径不是同一条, 鉴权用例
    测的就不是线上那套.
    """

    user = await create_user_record(database, username)

    return SignedInAccount(
        user=user,
        token=await login(client, username, DEFAULT_MEMBER_PASSWORD),
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
    manifest_json: str = "{}",
) -> StrategyVersionModel:
    """直接落库建一个策略版本.

    `manifest_json` 默认是空对象 (读它的路径都当"没有声明"处理); 需要按 manifest 做判据的用例
    自己给一份文本, 不必为了拿一个声明走一遍上传接口.
    """

    return await persist_record(
        database,
        StrategyVersionModel(
            id=generate_identifier(),
            strategy_id=strategy.id,
            version_no=1,
            entry_filename=entry_filename,
            config_filename="StrategyConfig.json",
            manifest_json=manifest_json,
            source_hash=generate_identifier(),
            storage_path=f"{uploaded_by.id}/strategies/{strategy.id}/1",
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
    params_json: str = "{}",
    backtest_config_json: str = "{}",
    run_id: str | None = None,
) -> RunModel:
    """直接落库建一个运行.

    指标列可取非基线值: 排序与筛选的断言要靠互不相同的取值才区分得开.

    两份配置文本默认为空对象 (与列默认值一致), 需要断言"读回提交时那份配置"的用例自己给文本.
    `run_id` 只在断言次序时给: 排序若以主键兜平局, 就得先把主键捏在手里.
    """

    resolved_run_id = run_id if run_id is not None else generate_identifier()

    run = RunModel(
        id=resolved_run_id,
        user_id=user.id,
        strategy_id=strategy.id,
        strategy_version_id=version.id,
        status=status.value,
        trade_count=trade_count,
        order_count=order_count,
        balance=balance,
        params_json=params_json,
        backtest_config_json=backtest_config_json,
        # 工作目录名就是主键, 二者是同一件事: 调度侧按 `runs_root / <WorkspacePath>` 找它, 而
        # 提交侧写的正是 `RunId`. 这里写成 `runs/<id>` 会让作业目录嵌进 `runs/runs/<id>`, 与
        # 真实作业对不上——造出来的行于是走不完"目录已存在"以外的任何一条真实路径.
        workspace_path=resolved_run_id,
    )

    if submitted_at is not None:
        run.submitted_at = submitted_at

    return await persist_record(database, run)
