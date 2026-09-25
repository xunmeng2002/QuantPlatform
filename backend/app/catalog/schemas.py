"""请求与响应模型.

响应模型统一开 from_attributes, 直接读 ORM 对象属性, 不做逐字段手工搬运.
枚举字段一律标注为枚举类型, 使合法取值只有一处来源 (catalog.enums).
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    GrantPermission,
    MarketDataType,
    RunStatus,
    StrategyVisibility,
    UserStatus,
    UserType,
)


T = TypeVar("T")

USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"
MINIMUM_PASSWORD_LENGTH = 8
MAXIMUM_PASSWORD_LENGTH = 256
MAXIMUM_STRATEGY_NAME_LENGTH = 128
MAXIMUM_STRATEGY_DESCRIPTION_LENGTH = 500


class PageResponse(BaseModel, Generic[T]):
    """分页信封, 各列表接口共用."""

    total: int
    offset: int
    limit: int
    records: list[T]


class MessageResponse(BaseModel):
    """操作结果回执."""

    message: str
    success: bool = True


class LoginRequest(BaseModel):
    """登录请求."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAXIMUM_PASSWORD_LENGTH)


class AccessTokenResponse(BaseModel):
    """登录成功返回的访问令牌."""

    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class UserCreateRequest(BaseModel):
    """由管理员建号."""

    username: str = Field(min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(
        min_length=MINIMUM_PASSWORD_LENGTH, max_length=MAXIMUM_PASSWORD_LENGTH
    )
    display_name: str = Field(default="", max_length=128)
    user_type: UserType = UserType.USER


class UserStatusUpdateRequest(BaseModel):
    """启用或停用账号."""

    status: UserStatus


class UserResponse(BaseModel):
    """用户对外视图, 绝不含口令散列."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    user_type: UserType
    status: UserStatus
    created_at: datetime


class StrategyResponse(BaseModel):
    """策略对外视图."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_user_id: str
    name: str
    description: str
    visibility_type: StrategyVisibility
    created_at: datetime
    updated_at: datetime


class StrategyVersionResponse(BaseModel):
    """策略版本对外视图."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    strategy_id: str
    version_no: int
    entry_filename: str
    config_filename: str
    source_hash: str
    uploaded_at: datetime


class StrategyGrantResponse(BaseModel):
    """授权对外视图."""

    model_config = ConfigDict(from_attributes=True)

    strategy_id: str
    grantee_user_id: str
    permission_type: GrantPermission
    granted_by_user_id: str
    granted_at: datetime


class StrategyDetailResponse(BaseModel):
    """策略详情: 本体 + 版本列表 + 授权列表."""

    strategy: StrategyResponse
    versions: list[StrategyVersionResponse]
    grants: list[StrategyGrantResponse]


class RunSummaryResponse(BaseModel):
    """运行列表视图: 只含列表页要展示与排序的列.

    全集见 RunDetailResponse; 列表页按指标排序与筛选, 逐行去解结果文件不可行, 故这些列
    由提交完成时从 result.json 镜像入库.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    strategy_id: str
    strategy_version_id: str
    status: RunStatus
    submitted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    exit_code: int | None
    is_success: bool | None
    market_data_type: MarketDataType | None
    start_trading_day: str | None
    end_trading_day: str | None
    trade_count: int | None
    order_count: int | None
    balance: float | None
    available: float | None
    total_commission: float | None
    error_id: int | None
    error_msg: str | None


class RunDetailResponse(RunSummaryResponse):
    """运行详情: 列表列 + 工作目录、输出尾部与剩余结果镜像列."""

    runner_pid: int | None
    hostname: str
    params_json: str
    backtest_config_json: str
    workspace_path: str
    db_path: str
    dump_path: str
    stdout_tail: str
    stderr_tail: str
    schema_version: int | None
    last_trading_day: str | None
    account_id: str | None
    md_subscribe_count: int | None
    bar_market_data_count: int | None
    depth_market_data_count: int | None
    instrument_count: int | None
    commission_missing_count: int | None
    commission_zero_rate_key_count: int | None
    volume_multiple_fallback_product_count: int | None
    basic_data_loaded: bool | None
    has_capital: bool | None
    total_stamp_tax: float | None
    total_transfer_fee: float | None
