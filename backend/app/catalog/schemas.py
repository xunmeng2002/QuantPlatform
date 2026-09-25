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


class RunSubmitRequest(BaseModel):
    """提交一次回测.

    这里只声明"有哪些字段、是什么类型", **取值规则一概不写在这里**: 那些规则要回 400 并只说
    字段名与原因, 而 pydantic 的 422 会把出错的取值原样抄回响应体——这份请求体里装的是用户填的
    参数与标的, 不该进接入层日志. 规则全部落在 `services/run_submission`.

    `extra="forbid"`: 多写一个字段名 (如 `param` 少了个 s) 会让参数整批静默落空, 而策略随后
    以"配置里没有这个键"的样子报错——把字段名写错这件事必须在提交这一步就拦住.

    `params` 的 key 不在 model 里逐一声明 (声明由 manifest 给, 是数据不是 schema), 故它收一个
    自由字典, 由提交侧按 manifest 校验.
    """

    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(min_length=1, max_length=32)
    strategy_version_id: str | None = Field(default=None, min_length=1, max_length=32)
    match_mode: MarketDataType
    bar_period: str = ""
    exchange_id: str | None = None
    instrument_id: str | None = None
    start_trading_day: str = ""
    end_trading_day: str = ""
    initial_capital: float
    params: dict[str, object] = Field(default_factory=dict)


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


class StrategyGrantRequest(BaseModel):
    """一条待写入的授权: 被授权人与权限粒度."""

    grantee_user_id: str = Field(min_length=1, max_length=32)
    permission_type: GrantPermission = GrantPermission.READ


class StrategyGrantReplaceRequest(BaseModel):
    """整体替换某策略的授权集合. 空列表即撤销全部授权.

    整体替换而非增量: 增量下"撤销谁"要另设一条删除路径, 而调用方手上的本来就是一份完整
    名单. 传全集只表达一个意图, 且重复提交同一份名单结果不变.
    """

    grants: list[StrategyGrantRequest] = Field(default_factory=list)


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
