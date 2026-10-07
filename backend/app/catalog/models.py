"""catalog 九张表的 ORM 定义.

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
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..clock import utc_now
from .enums import (
    GrantPermission,
    ProductClass,
    RunStatus,
    StrategyVisibility,
    UserStatus,
    UserType,
)


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

    名字的唯一性做成**部分**唯一索引 (只约束 DeletedAt IS NULL 的行), 不是整表唯一约束: 整表
    唯一会把已删策略的名字一起占住, 而列表页与详情页都不再显示这条记录——用户看见的现象是
    "名字明明没在用, 却被判重名", 且没有任何接口能释放它 (不提供硬删). 只约束未删行的意思
    正是"名字在**在用的**策略之间唯一".

    这个索引是 SQLite 方言的偏条件. 若日后换库, 必须把同样的条件带过去: 换个方言而条件丢失,
    索引会退化成整表唯一, 上面那个故障就原样回来, 且没有任何测试会因此变红.
    """

    __tablename__ = "Strategies"

    __table_args__ = (
        Index(
            "UidxStrategiesOwnerUserIdNameLive",
            "OwnerUserId",
            "Name",
            unique=True,
            sqlite_where=text("DeletedAt IS NULL"),
        ),
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

    同一策略下 (StrategyId, VersionNo) 唯一——版本号不得重复, 这是完整性.

    版本判重不在此处加约束: 判同的键是 (SourceHash, ConfigurationJson) 两列, 而 ConfigurationJson
    是 Text, 拿它做唯一索引不划算. 更要紧的是单看 SourceHash 会**判错**: 同一份源码配不同配置
    模板 (改了参数、或换了入口文件名) 是一份新版本, 只钉 SourceHash 的唯一约束会把这种上传挡成
    完整性冲突, 使"改配置必须连源码一起改"——荒谬. 故判重放在 services/strategy_store 内做,
    那里判错也只是多个目录, 不伤及完整性.

    `ManifestJson` 是**历史列**: 上传形态从"源码 + 手写 manifest"改成"源码 + 配置 JSON"之后,
    这份列不再承载任何含义, 只为兼容 SQLite 的既有表结构而保留 (`catalog/migrations.py` 只加列、
    不删列). 新写的一律是空串, 而"这个版本是旧形态"这件事由 `ConfigurationJson` 为 NULL 表达
    ——那是**结构性**的判据, 不是靠去猜旧 manifest 里的键长什么样.
    """

    __tablename__ = "StrategyVersions"

    __table_args__ = (
        UniqueConstraint("StrategyId", "VersionNo", name="UqStrategyVersionsStrategyIdVersionNo"),
    )

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    strategy_id: Mapped[str] = mapped_column(
        "StrategyId", String(32), ForeignKey("Strategies.Id"), index=True
    )

    version_no: Mapped[int] = mapped_column("VersionNo", Integer)

    entry_filename: Mapped[str] = mapped_column("EntryFilename", String(128))

    config_filename: Mapped[str] = mapped_column("ConfigFilename", String(128))

    configuration_json: Mapped[str | None] = mapped_column(
        "ConfigurationJson", Text, default=None
    )

    #: 历史列, 见类 docstring. Python 侧默认值是**必须**的: 这一列是 NOT NULL, 而新代码不再写它,
    #: 少了默认值 SQLAlchemy 会把它整个从 INSERT 里略去, 于是每一条新版本都撞 NOT NULL 冲突.
    manifest_json: Mapped[str] = mapped_column("ManifestJson", Text, default="")

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

    # "这一轮跑的是哪个引擎构建", 与 `Hostname` 同形: 提交时冻结, 取不到时为空串 (不是 NULL).
    # 它与 `StrategyVersionId` 一起才凑得齐"逐字复现"的两个前提——策略是哪个版本、引擎是哪个
    # 构建; 缺了后者, 引擎换版之后历史轮与新轮在库里不可分辨.
    engine_version: Mapped[str] = mapped_column("EngineVersion", String(64), default="")

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


class RunTemplateModel(Base):
    """配置模板: 一套命名的运行取值, 供同一策略重复套用.

    **作用域是策略域, 不是用户域**: 行上同时有 `OwnerUserId` 与 `StrategyId`. 参数只在一个策略
    的键命名空间里有意义, 纯用户域会让跨策略套用"只套一半"——运行级字段生效、参数被整批忽略,
    而那是本仓到处在防的静默失败.

    可见性仍只按归属人, 与运行同一条口径: 被授权人能用策略跑回测, 不等于能看见别人的模板.

    **硬删, 没有 `DeletedAt`**: 没有外键指向它, 删它不影响任何历史运行——运行那一侧在提交时就把
    取值烤进了 `Runs.ParamsJson`, 模板只是"下次照这样填"的一份草稿. 软删会引入"名字被占住又
    没有接口释放"的老问题 (与 `Strategies` 的软删不同, 那边是为了让历史运行的外键仍然有效).

    唯一键含 `StrategyId`: 同一个人在两个策略下各存一个叫 `默认` 的模板是正常的, 唯一性只该
    落在"同一策略下不重名". 重名以这条约束为准 (捕 `IntegrityError`), 不另做"先查后插".

    列的可空性与提交契约对齐: 提交时必填的列 (Name / MatchMode / BarPeriod / 两个交易日 /
    InitialCapital / CommissionGroupId) 一律非空不可省, 只有 `exchange_id` / `instrument_id`
    可空——不指名合约是正常的一轮, 它们此时存空串. `ParamsJson` 与 `Runs.ParamsJson` **同形同义** (都是策略配置的
    那个形状的 `{键: 取值}`, 只是这里不含平台那三个运行级键——它们在表上各有具名列), 故套用时
    可直接喂给参数控件, 不需要再译一遍.
    """

    __tablename__ = "RunTemplates"

    __table_args__ = (
        UniqueConstraint(
            "OwnerUserId",
            "StrategyId",
            "Name",
            name="UqRunTemplatesOwnerUserIdStrategyIdName",
        ),
    )

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    owner_user_id: Mapped[str] = mapped_column(
        "OwnerUserId", String(32), ForeignKey("Users.Id"), index=True
    )

    strategy_id: Mapped[str] = mapped_column(
        "StrategyId", String(32), ForeignKey("Strategies.Id"), index=True
    )

    name: Mapped[str] = mapped_column("Name", String(64))

    match_mode: Mapped[str] = mapped_column("MatchMode", String(20))

    bar_period: Mapped[str] = mapped_column("BarPeriod", String(64))

    exchange_id: Mapped[str | None] = mapped_column("ExchangeId", String(64), default=None)

    instrument_id: Mapped[str | None] = mapped_column(
        "InstrumentId", String(64), default=None
    )

    start_trading_day: Mapped[str] = mapped_column("StartTradingDay", String(8))

    end_trading_day: Mapped[str] = mapped_column("EndTradingDay", String(8))

    initial_capital: Mapped[float] = mapped_column("InitialCapital", Float)

    # `default=1` **只是迁移回填值**, 不是接口默认值. 既有模板行的组号在库里无从推断, 而它们此前
    # 实际跑的就是 1 号组 (那时平台侧只有那一个常量), 回填 1 与它们的真实行为一致.
    # `catalog/migrations.py` 要求 NOT NULL 的新列有一个可渲染的标量默认, 否则这一列根本不会被加上
    # —— 那个机制决定了这个默认值必须写在这里, 与 `Runs.EngineVersion` 的 `default=""` 同形.
    # 接口侧这一格**必填**: "没选"与"选了 1 号组"必须是两件能分辨的事, 而一个接口默认值会把前者
    # 抹成后者.
    commission_group_id: Mapped[int] = mapped_column(
        "CommissionGroupId", Integer, default=1
    )

    params_json: Mapped[str] = mapped_column("ParamsJson", Text, default="{}")

    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, default=utc_now)

    updated_at: Mapped[datetime] = mapped_column(
        "UpdatedAt", DateTime, default=utc_now, onupdate=utc_now
    )


class ProductModel(Base):
    """品种基本数据: 引擎 `Product` 表在平台侧的宿主.

    **列按引擎列序声明, 平台专有列排在最后**. 这不是排版的讲究: 引擎装载种子库时按列**下标**
    绑定 (`SELECT *` 的结果直接喂进字段描述数组), 少一列、多一列或换个次序都是静默错位 ——
    引擎不报错, 只是把 `ExchangeId` 读成 `ProductId`. 种子库文件本身的列序由
    `reference_data/seed_contract.py` 的显式 DDL 决定 (那里是权威), 这张表保持同序是为了让
    两处能一眼对上.

    主键是平台自造的 `Id` 而不是自然键: `PATCH /products/{record_id}` 必须允许改
    `ExchangeId` / `ProductId` (填错重填是常规动作), 而拿自然键当主键的话, 改一次品种代码
    就等于换掉一行主键, 路径参数得写成复合键. 自然键改由唯一约束守住.

    字段宽度逐列取自引擎的结构体声明 (char[n] → `String(n)`), 越界的字符串在请求层就会被
    422 拦下, 不会写进库再在装载时被引擎截断.
    """

    __tablename__ = "Products"

    __table_args__ = (
        UniqueConstraint(
            "ExchangeId", "ProductId", name="UqProductsExchangeIdProductId"
        ),
    )

    exchange_id: Mapped[str] = mapped_column("ExchangeId", String(8))
    product_id: Mapped[str] = mapped_column("ProductId", String(32))
    product_name: Mapped[str] = mapped_column("ProductName", String(32))
    product_class: Mapped[int] = mapped_column(
        "ProductClass", Integer, default=ProductClass.STOCK.value
    )
    volume_multiple: Mapped[int] = mapped_column("VolumeMultiple", Integer)
    price_tick: Mapped[float] = mapped_column("PriceTick", Float)
    max_market_order_volume: Mapped[int] = mapped_column("MaxMarketOrderVolume", Integer)
    min_market_order_volume: Mapped[int] = mapped_column("MinMarketOrderVolume", Integer)
    max_limit_order_volume: Mapped[int] = mapped_column("MaxLimitOrderVolume", Integer)
    min_limit_order_volume: Mapped[int] = mapped_column("MinLimitOrderVolume", Integer)
    session_name: Mapped[str] = mapped_column("SessionName", String(32), default="")

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, default=utc_now)

    updated_at: Mapped[datetime] = mapped_column(
        "UpdatedAt", DateTime, default=utc_now, onupdate=utc_now
    )


class CommissionGroupModel(Base):
    """手续费组: 引擎 `CommissionGroup` 表在平台侧的宿主.

    `CommissionGroupId` 是**人工指定的整数**, 不是自增主键 —— 引擎每轮的 `BackTest.json` 里
    `CommissionGroupId` 是一个具体的整数, 引擎按它去费率表里找组. 自增的话, 库里"1 号组"是哪
    一行就由插入次序决定, 而配置认的是数字, 认错了不会报错、只会用另一套费率算钱. 故这里让操作
    员自己填组号, 并由唯一约束保证不重复.

    建几个组就有几个组可用: 组号随每一轮提交**冻结**进该轮的引擎配置 (见
    `scheduler/engine_config.render_engine_config`), 提交页选哪个, 那一轮就按哪个组计费. 删组会
    被两道闸拦住——组下还有费率明细, 或有配置模板正引用它.
    """

    __tablename__ = "CommissionGroups"

    __table_args__ = (
        UniqueConstraint(
            "CommissionGroupId", name="UqCommissionGroupsCommissionGroupId"
        ),
    )

    commission_group_id: Mapped[int] = mapped_column("CommissionGroupId", Integer)
    commission_group_name: Mapped[str] = mapped_column("CommissionGroupName", String(64))

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, default=utc_now)

    updated_at: Mapped[datetime] = mapped_column(
        "UpdatedAt", DateTime, default=utc_now, onupdate=utc_now
    )


class BaseCommissionModel(Base):
    """费率明细: 引擎 `BaseCommission` 表在平台侧的宿主.

    一行 = 某个手续费组下的一档费率规则: 某个交易所、某一格合约代码、某一个方向上的费率与税额.
    后两格都允许写成通配 (见下).

    自然键是四列联合 (`CommissionGroupId` + `ExchangeId` + `InstrumentId` + `Direction`),
    与引擎的主键索引逐列相同, 而引擎**就是按这四列精确查这一行**的
    (`CommissionCalculator::Apply` → `PrimaryKey->Select(组号, 交易所, 合约, 方向)`): 没有
    通配、没有前缀匹配. 键里任一列对不上, 这笔成交就取不到费率, 三列费用按 0 算并计入
    `CommissionMissingCount` —— 回测照跑完, 只是手续费是零.

    **"没有通配"说的是引擎那一侧, 不是这张表**: 这张表收的是"规则", 它的键里有两处可以写成通配
    —— 合约格那三级 (合约 / 品种 / 交易所, 由 `InstrumentId` 的取值承载) 与买卖双向
    (`Direction = -1`). 写种子库之前 `reference_data.rate_expansion` 会把它们摊成具体合约、具体
    方向的行, 故引擎那条前提逐字不变, 而这两处通配不会漏到它那里去.

    故 `Direction` **是键的一部分, 不是"选哪一列"的开关**: 引擎拿成交方向去取行, 取到之后再
    按开平标志 (OffsetFlag) 决定用这一行的开仓列还是平仓列. 同一合约的买与卖要各占一行, 正是
    因为方向在键里 —— 印花税只在卖出侧收, 只写了一行的话另一个方向就整笔取不到费率. 引擎读到
    的每一行都必须是**具体方向**的那一行; 双向那一档只在这张表里存在.

    `MinCommission` / `MaxCommission` 的 0 (或负值) 表示该侧不设限, 不是"封到 0"; 封顶只作用
    于佣金, 印花税与过户费按法定费率实收. 见 `CommissionCalculator.cpp` 的 `ClampCommission`.

    `CommissionGroupId` 带**真外键**指向组表: 删掉一个仍被引用的组, 由 SQLite 抛
    `IntegrityError` 兜底 (逐连接开着 `PRAGMA foreign_keys=ON`), 而不是静默留下悬空引用 ——
    悬空行的效果是该合约完全匹配不到费率, 回测照跑, 只是手续费按 0 算.
    """

    __tablename__ = "BaseCommissions"

    __table_args__ = (
        UniqueConstraint(
            "CommissionGroupId",
            "ExchangeId",
            "InstrumentId",
            "Direction",
            name="UqBaseCommissionsCommissionGroupIdExchangeIdInstrumentIdDirection",
        ),
    )

    commission_group_id: Mapped[int] = mapped_column(
        "CommissionGroupId",
        Integer,
        ForeignKey("CommissionGroups.CommissionGroupId"),
        index=True,
    )
    exchange_id: Mapped[str] = mapped_column("ExchangeId", String(8))
    instrument_id: Mapped[str] = mapped_column("InstrumentId", String(32))
    direction: Mapped[int] = mapped_column("Direction", Integer)

    open_by_money: Mapped[float] = mapped_column("OpenByMoney", Float, default=0.0)
    close_by_money: Mapped[float] = mapped_column("CloseByMoney", Float, default=0.0)
    open_by_volume: Mapped[float] = mapped_column("OpenByVolume", Float, default=0.0)
    close_by_volume: Mapped[float] = mapped_column("CloseByVolume", Float, default=0.0)
    open_stamp_tax_by_money: Mapped[float] = mapped_column(
        "OpenStampTaxByMoney", Float, default=0.0
    )
    close_stamp_tax_by_money: Mapped[float] = mapped_column(
        "CloseStampTaxByMoney", Float, default=0.0
    )
    open_transfer_fee_by_money: Mapped[float] = mapped_column(
        "OpenTransferFeeByMoney", Float, default=0.0
    )
    close_transfer_fee_by_money: Mapped[float] = mapped_column(
        "CloseTransferFeeByMoney", Float, default=0.0
    )
    min_commission: Mapped[float] = mapped_column("MinCommission", Float, default=0.0)
    max_commission: Mapped[float] = mapped_column("MaxCommission", Float, default=0.0)

    id: Mapped[str] = mapped_column("Id", String(32), primary_key=True)

    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, default=utc_now)

    updated_at: Mapped[datetime] = mapped_column(
        "UpdatedAt", DateTime, default=utc_now, onupdate=utc_now
    )
