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
MAXIMUM_RUN_TEMPLATE_NAME_LENGTH = 64


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


class RunConfigurationRequest(BaseModel):
    """一次运行的取值集合: 提交与存模板**共用同一份字段定义**.

    两条路径收的字段逐字相同 (运行级取值 + 策略参数). 各写一份的话, "模板存得下的取值"与"提交
    收得下的取值"就成了两个集合, 而它们的差别只在特定取值上出现——症状是"存得下、提交时 400".
    共同的字段只声明一次, 这种漂移就没有可发生的缝隙.

    这里只声明"有哪些字段、是什么类型", **取值规则一概不写在这里**: 那些规则要回 400 并只说
    字段名与原因, 而 pydantic 的 422 会把出错的取值原样抄回响应体——这份请求体里装的是用户填的
    参数与标的, 不该进接入层日志. 规则全部落在 `services/run_configuration`.

    `extra="forbid"`: 多写一个字段名 (如 `param` 少了个 s) 会让参数整批静默落空, 而策略随后
    以"配置里没有这个键"的样子报错——把字段名写错这件事必须在提交这一步就拦住.

    `params` 的 key 不在 model 里逐一声明 (声明由 manifest 给, 是数据不是 schema), 故它收一个
    自由字典, 由提交侧按 manifest 校验.
    """

    model_config = ConfigDict(extra="forbid")

    match_mode: MarketDataType
    bar_period: str = ""
    exchange_id: str | None = None
    instrument_id: str | None = None
    start_trading_day: str = ""
    end_trading_day: str = ""
    initial_capital: float
    params: dict[str, object] = Field(default_factory=dict)


class RunSubmitRequest(RunConfigurationRequest):
    """提交一次回测: 取值集合 + 跑哪个策略的哪个版本."""

    strategy_id: str = Field(min_length=1, max_length=32)
    strategy_version_id: str | None = Field(default=None, min_length=1, max_length=32)


class RunTemplateCreateRequest(RunConfigurationRequest):
    """把一套取值命名存下.

    没有 `strategy_version_id`: 模板是**策略级**的, 不绑版本. 换了版本之后它仍该能用——绑了
    版本的话, 一次正常的版本迭代就会让所有旧模板失效, 而用户看不出那是"因为版本"。
    """

    name: str = Field(min_length=1, max_length=MAXIMUM_RUN_TEMPLATE_NAME_LENGTH)


class RunTemplateRenameRequest(BaseModel):
    """模板改名."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAXIMUM_RUN_TEMPLATE_NAME_LENGTH)


class RunTemplateResponse(BaseModel):
    """一份配置模板.

    字段与 `LastSubmittedParametersResponse` **刻意对齐** (`params` 是参数的原始取值, 运行级
    字段各有具名成员): 前端因此只有一条"把一套取值填进表单"的路径, 不必为模板另写一条——而
    两条路径并存时, 差别总会在某个取值类型上冒出来.
    """

    id: str
    strategy_id: str
    name: str
    match_mode: MarketDataType
    bar_period: str
    exchange_id: str | None
    instrument_id: str | None
    start_trading_day: str
    end_trading_day: str
    initial_capital: float
    params: dict[str, object]
    created_at: datetime
    updated_at: datetime


class RunTemplateListResponse(BaseModel):
    """某策略下本人保存的模板.

    不分页: 个人级小集合, 且有 `MAXIMUM_TEMPLATES_PER_STRATEGY` 的硬上限. 外面这层信封与
    `JobArtifactListResponse` 同形——让响应自带"这份列表属于哪个策略", 页面不必靠请求上下文
    去猜自己拿到的是不是刚才那一份.
    """

    strategy_id: str
    templates: list[RunTemplateResponse]


class UserCreateRequest(BaseModel):
    """由管理员建号.

    `display_name` 必填: 受限用户目录 (`GET /api/users/directory`) 只列显示名非空的账号,
    建号时留空等于建出一个**别人在授权表单里选不到**的账号.
    """

    username: str = Field(min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(
        min_length=MINIMUM_PASSWORD_LENGTH, max_length=MAXIMUM_PASSWORD_LENGTH
    )
    display_name: str = Field(min_length=1, max_length=128)
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


class UserDirectoryEntryResponse(BaseModel):
    """受限用户目录的一条: 只够用来在授权表单里认出一个人.

    只有 `id` 与 `display_name` 两个字段, 是**有意**的收窄, 详见
    `routers/users.py::list_user_directory_handler` 的泄漏面说明.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str


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
    """策略版本对外视图.

    `manifest_json` 是上传时那份文本的**原样透传** (字符串, 不在这里解析): 提交页要按它的
    `params` 生成参数控件, 故它必须能到前端; 而在读接口里解析会给存量行新增一条失败路径
    (一份坏 manifest 会让整个策略详情 500), 前端本来也要自己 `JSON.parse`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    strategy_id: str
    version_no: int
    entry_filename: str
    config_filename: str
    source_hash: str
    manifest_json: str
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

    `params_json` 在这里而不是只在详情里: 对比页要回答的是"这两轮差在哪", 而**参数就是那个
    差**——两轮同策略同参数、只差一个 `GridStep` 时, 指标列会不同却看不出因为什么. 它是纯增量
    (详情继承本类, 字段一个不少), 故不另立一个 20 列的扁平类型.
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
    params_json: str
    error_id: int | None
    error_msg: str | None


class RunDetailResponse(RunSummaryResponse):
    """运行详情: 列表列 + 工作目录、输出尾部与剩余结果镜像列."""

    runner_pid: int | None
    hostname: str
    engine_version: str
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


class JobArtifactResponse(BaseModel):
    """作业目录里的一个文件.

    `relative_path` 恒为 POSIX 形式且相对于作业目录 (`Dump/<RunId>/t_trade.csv` 这种嵌套也要
    能表达), 下载接口按原样接回路径参数——故它既是展示用的路径, 也是那条 URL 的构造依据.
    """

    relative_path: str
    size_bytes: int


class JobArtifactListResponse(BaseModel):
    """一次运行的产物清单.

    不分页: 作业目录的文件数由引擎决定 (实测一轮 20 余个), 且这里读的是文件系统而不是库,
    没有可加的 `offset`/`limit` 语义.
    """

    run_id: str
    artifacts: list[JobArtifactResponse]


class EquityPointResponse(BaseModel):
    """权益曲线上的一个点, 即 `Capital` 表某个交易日的结算权益.

    引擎的 `Capital` 首行是种子行 (`Deposit` = 初始资金), 故序列起点就是初始权益; 一天一点,
    `TradingDay` 是 8 字符 `YYYYMMDD` 串. **回撤不在后端算**: 它是派生数据, 由前端从这条
    序列推出来 (见 `frontend/src/domain/equity.ts`), 后端多加一列就等于把图表形状钉进接口.
    """

    trading_day: str
    balance: float
    available: float


class RunEquityResponse(BaseModel):
    """一次运行的逐日权益序列.

    不分页: 点数就是交易日数 (实测一轮 62 点, 15 年日线约 3600 点), 比一页明细表还小.
    """

    run_id: str
    points: list[EquityPointResponse]


class RunComparisonEntryResponse(BaseModel):
    """对比里的一轮: 它的列表列 (`summary`) 加上它自己的曲线.

    **指标一律取 `summary` 里的镜像列, 绝不重读 `result.json`**: 那些列的立项理由就是"列表与
    对比不逐行解文件"; 更要紧的是保留清理会移走旧轮的作业目录 (见 `services/run_retention`),
    那时历史轮的指标必须还在, 而只有镜像列做到了这一点.

    `equity_unavailable_reason` 与 `equity_points` 一空一满: 降级**只降这一轮**, 别的列照常
    出数——一条曲线画不出来不该让整页变白 (同详情页各面板的降级粒度). 文案里不带路径。
    """

    summary: RunSummaryResponse
    equity_points: list[EquityPointResponse]
    equity_unavailable_reason: str | None


class RunComparisonResponse(BaseModel):
    """多轮对比: 每轮一列.

    列表序 = 请求序 (去重后), 由服务端保证: 前端按 `ids` 里的次序渲染列, 而 `IN (...)` 的
    返回序是未定义的——不在这里定死, 界面上的列序就在驱动之间随机.
    """

    runs: list[RunComparisonEntryResponse]


class ResultTableResponse(BaseModel):
    """引擎结果表的一页.

    `total` / `offset` / `limit` / `records` 逐字沿用 `PageResponse` 的字段名 (前端的分页条
    只认这三个数, 原样复用), 但**不继承它**: 那个信封要求一个固定的记录模型, 而结果表的行
    没有固定形状——`columns` 由 `PRAGMA table_info` 给出, 顺序即权威顺序, 表头因此不必在
    界面上写死 (`Order` 有 33 列).
    """

    table: str
    columns: list[str]
    total: int
    offset: int
    limit: int
    records: list[dict[str, str | int | float | bool | None]]


class LastSubmittedParametersResponse(BaseModel):
    """该用户在该策略下最近一次提交的参数, 供提交页预填.

    字段全可空, 且**没有历史运行是正常状态** (返回 `run_id=None` + 空 `params`, 仍是 200):
    首次使用某个策略走的就是这条路, 用 404 表达会与"策略不存在/不可见"混为一谈.

    `params` 是策略参数的原始取值 (类型照旧), 不含运行级字段——后者已经拆成上面的具名字段,
    前端因此不必认识引擎 `BackTest.json` 的键名 (其中有 `BarPreces` 这种引擎侧拼写).
    """

    run_id: str | None = None
    submitted_at: datetime | None = None
    match_mode: MarketDataType | None = None
    bar_period: str | None = None
    exchange_id: str | None = None
    instrument_id: str | None = None
    start_trading_day: str | None = None
    end_trading_day: str | None = None
    initial_capital: float | None = None
    params: dict[str, object] = Field(default_factory=dict)
