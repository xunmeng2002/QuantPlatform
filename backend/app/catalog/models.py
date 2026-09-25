"""catalog 五张表的 ORM 定义.

列名一律 PascalCase (按 database-style.md), Python 属性名 snake_case, 两者由
mapped_column 的列名参数映射. 约束名不逐个手写, 由 MetaData 的命名约定统一生成.

result.json 共 29 个键, 其中 25 个镜像成 Runs 的列——列表页与对比页要按指标排序与
筛选, 逐行去解文件不可行. RunId / DbPath / DumpPath 分别对应本表的 Id / DbPath /
DumpPath, MissingRateKeys 是长尾数组, 留在结果文件里按需读取.

金额与指标列用 Float 而非 Numeric: 引擎报的是 float64 (Balance 实测
998951.45064649964), 若存 Numeric(18,6) 会被舍入成 ...450646, 直接破坏与引擎逐位
一致的验收判据.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..clock import utc_now
from .enums import GrantPermission, RunStatus, StrategyVisibility, UserStatus, UserType


NAMING_CONVENTION = {
    "ck": "Ck%(table_name)s%(constraint_name)s",
    "fk": "Fk%(table_name)s%(referred_table_name)s",
    "ix": "Idx%(table_name)s%(column_0_name)s",
    "pk": "Pk%(table_name)s",
    "uq": "Uq%(table_name)s%(column_0_name)s",
}


class Base(DeclarativeBase):
    """声明式基类, 承载统一约束命名约定."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserModel(Base):
    """用户账号."""

    __tablename__ = "Users"

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    username: Mapped[str] = mapped_column("Username", String(64), unique=True)

    password_hash: Mapped[str] = mapped_column("PasswordHash", String(255))

    display_name: Mapped[str] = mapped_column("DisplayName", String(128), default="")

    user_type: Mapped[str] = mapped_column(
        "UserType", String(20), default=UserType.USER.value
    )

    status: Mapped[str] = mapped_column(
        "Status", String(20), default=UserStatus.ACTIVE.value
    )

    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, default=utc_now)


class StrategyModel(Base):
    """策略逻辑实体: 一个名字对应一个归属人.

    DeletedAt 供软删除使用. 硬删会与历史运行的外键冲突, 而计划要求删策略不影响历史运行.
    """

    __tablename__ = "Strategies"

    __table_args__ = (
        UniqueConstraint("OwnerUserId", "Name", name="UqStrategiesOwnerUserIdName"),
    )

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    owner_user_id: Mapped[str] = mapped_column(
        "OwnerUserId", String(32), ForeignKey("Users.Id"), index=True
    )

    name: Mapped[str] = mapped_column("Name", String(128))

    description: Mapped[str] = mapped_column("Description", String(500), default="")

    visibility_type: Mapped[str] = mapped_column(
        "VisibilityType", String(20), default=StrategyVisibility.PRIVATE.value
    )

    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, default=utc_now)

    updated_at: Mapped[datetime] = mapped_column(
        "UpdatedAt", DateTime, default=utc_now, onupdate=utc_now
    )

    deleted_at: Mapped[datetime | None] = mapped_column("DeletedAt", DateTime, default=None)


class StrategyVersionModel(Base):
    """策略版本: 只追加, 永不改写.

    同一策略下 (StrategyId, SourceHash) 唯一, 使内容未变时不产生新版本号.
    """

    __tablename__ = "StrategyVersions"

    __table_args__ = (
        UniqueConstraint("StrategyId", "VersionNo", name="UqStrategyVersionsStrategyIdVersionNo"),
        UniqueConstraint(
            "StrategyId", "SourceHash", name="UqStrategyVersionsStrategyIdSourceHash"
        ),
    )

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    strategy_id: Mapped[str] = mapped_column(
        "StrategyId", String(32), ForeignKey("Strategies.Id"), index=True
    )

    version_no: Mapped[int] = mapped_column("VersionNo", Integer)

    entry_filename: Mapped[str] = mapped_column("EntryFilename", String(128))

    config_filename: Mapped[str] = mapped_column("ConfigFilename", String(128))

    manifest_json: Mapped[str] = mapped_column("ManifestJson", Text)

    source_hash: Mapped[str] = mapped_column("SourceHash", String(64), index=True)

    storage_path: Mapped[str] = mapped_column("StoragePath", String(500))

    uploaded_at: Mapped[datetime] = mapped_column("UploadedAt", DateTime, default=utc_now)

    uploaded_by_user_id: Mapped[str] = mapped_column(
        "UploadedByUserId", String(32), ForeignKey("Users.Id"), index=True
    )


class StrategyGrantModel(Base):
    """策略授权: 多对多的共享关系.

    归属不是表 (那是 Strategies.OwnerUserId 一列), 需要表的只有共享.
    """

    __tablename__ = "StrategyGrants"

    __table_args__ = (
        PrimaryKeyConstraint("StrategyId", "GranteeUserId", name="PkStrategyGrants"),
    )

    strategy_id: Mapped[str] = mapped_column(
        "StrategyId", String(32), ForeignKey("Strategies.Id")
    )

    grantee_user_id: Mapped[str] = mapped_column(
        "GranteeUserId",
        String(32),
        ForeignKey("Users.Id", name="FkStrategyGrantsGranteeUser"),
    )

    permission_type: Mapped[str] = mapped_column(
        "PermissionType", String(20), default=GrantPermission.READ.value
    )

    granted_by_user_id: Mapped[str] = mapped_column(
        "GrantedByUserId",
        String(32),
        ForeignKey("Users.Id", name="FkStrategyGrantsGrantedByUser"),
    )

    granted_at: Mapped[datetime] = mapped_column("GrantedAt", DateTime, default=utc_now)


class RunModel(Base):
    """回测作业及其结果快照.

    StrategyId 供按策略分组看历史, StrategyVersionId 供逐字复现. 两者职责不同, 不是冗余:
    只钉策略的话, 策略改动后历史运行的参数与结果就对不上, 对比不可信.
    """

    __tablename__ = "Runs"

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    user_id: Mapped[str] = mapped_column(
        "UserId", String(32), ForeignKey("Users.Id"), index=True
    )

    strategy_id: Mapped[str] = mapped_column(
        "StrategyId", String(32), ForeignKey("Strategies.Id"), index=True
    )

    strategy_version_id: Mapped[str] = mapped_column(
        "StrategyVersionId", String(32), ForeignKey("StrategyVersions.Id")
    )

    status: Mapped[str] = mapped_column(
        "Status", String(20), default=RunStatus.QUEUED.value, index=True
    )

    submitted_at: Mapped[datetime] = mapped_column("SubmittedAt", DateTime, default=utc_now)

    started_at: Mapped[datetime | None] = mapped_column("StartedAt", DateTime, default=None)

    finished_at: Mapped[datetime | None] = mapped_column(
        "FinishedAt", DateTime, default=None
    )

    duration_ms: Mapped[int | None] = mapped_column("DurationMs", Integer, default=None)

    runner_pid: Mapped[int | None] = mapped_column("RunnerPid", Integer, default=None)

    hostname: Mapped[str] = mapped_column("Hostname", String(128), default="")

    exit_code: Mapped[int | None] = mapped_column("ExitCode", Integer, default=None)

    params_json: Mapped[str] = mapped_column("ParamsJson", Text, default="{}")

    backtest_config_json: Mapped[str] = mapped_column("BacktestConfigJson", Text, default="{}")

    workspace_path: Mapped[str] = mapped_column("WorkspacePath", String(500), default="")

    db_path: Mapped[str] = mapped_column("DbPath", String(500), default="")

    dump_path: Mapped[str] = mapped_column("DumpPath", String(500), default="")

    stdout_tail: Mapped[str] = mapped_column("StdoutTail", Text, default="")

    stderr_tail: Mapped[str] = mapped_column("StderrTail", Text, default="")

    is_success: Mapped[bool | None] = mapped_column("IsSuccess", Boolean, default=None)

    error_id: Mapped[int | None] = mapped_column("ErrorId", Integer, default=None)

    error_msg: Mapped[str | None] = mapped_column("ErrorMsg", String(1000), default=None)

    schema_version: Mapped[int | None] = mapped_column("SchemaVersion", Integer, default=None)

    market_data_type: Mapped[str | None] = mapped_column(
        "MarketDataType", String(20), default=None
    )

    start_trading_day: Mapped[str | None] = mapped_column(
        "StartTradingDay", String(8), default=None
    )

    end_trading_day: Mapped[str | None] = mapped_column("EndTradingDay", String(8), default=None)

    last_trading_day: Mapped[str | None] = mapped_column(
        "LastTradingDay", String(8), default=None
    )

    account_id: Mapped[str | None] = mapped_column("AccountId", String(32), default=None)

    balance: Mapped[float | None] = mapped_column("Balance", Float, default=None)

    available: Mapped[float | None] = mapped_column("Available", Float, default=None)

    total_commission: Mapped[float | None] = mapped_column(
        "TotalCommission", Float, default=None
    )

    total_stamp_tax: Mapped[float | None] = mapped_column(
        "TotalStampTax", Float, default=None
    )

    total_transfer_fee: Mapped[float | None] = mapped_column(
        "TotalTransferFee", Float, default=None
    )

    order_count: Mapped[int | None] = mapped_column("OrderCount", Integer, default=None)

    trade_count: Mapped[int | None] = mapped_column("TradeCount", Integer, default=None)

    md_subscribe_count: Mapped[int | None] = mapped_column(
        "MdSubscribeCount", Integer, default=None
    )

    bar_market_data_count: Mapped[int | None] = mapped_column(
        "BarMarketDataCount", Integer, default=None
    )

    depth_market_data_count: Mapped[int | None] = mapped_column(
        "DepthMarketDataCount", Integer, default=None
    )

    instrument_count: Mapped[int | None] = mapped_column(
        "InstrumentCount", Integer, default=None
    )

    commission_missing_count: Mapped[int | None] = mapped_column(
        "CommissionMissingCount", Integer, default=None
    )

    commission_zero_rate_key_count: Mapped[int | None] = mapped_column(
        "CommissionZeroRateKeyCount", Integer, default=None
    )

    volume_multiple_fallback_product_count: Mapped[int | None] = mapped_column(
        "VolumeMultipleFallbackProductCount", Integer, default=None
    )

    basic_data_loaded: Mapped[bool | None] = mapped_column(
        "BasicDataLoaded", Boolean, default=None
    )

    has_capital: Mapped[bool | None] = mapped_column("HasCapital", Boolean, default=None)
