/**
 * 后端契约的 TypeScript 视图.
 *
 * 字段名与 JSON **逐字一致**, 不做驼峰转换: 接口上本来就叫 `initial_capital` 与
 * `relative_path`, 加一层转换就是加一处会漂移的真相来源.
 *
 * 枚举用 `as const` 数组 + 派生联合, 而不是 TS 的 `enum`: tsconfig 开了
 * `erasableSyntaxOnly`, `enum` 不是可擦除语法, 会直接编译失败; 而数组本身还能当运行时取值
 * 清单用 (下拉框选项). 取值与 `backend/app/catalog/enums.py` 逐字对应.
 */

/* ── 通用信封 ─────────────────────────────────────────────── */

export interface PageResponse<RecordShape> {
  total: number;
  offset: number;
  limit: number;
  records: RecordShape[];
}

/** 与 `catalog/pagination.py` 一致. 超过上限的 `limit` 后端回 422, 故界面只给这几档. */
export const DEFAULT_PAGE_SIZE = 20;
export const MAXIMUM_PAGE_SIZE = 100;
export const PAGE_SIZE_OPTIONS = [20, 50, 100] as const;

/**
 * 录入框的 `maxlength`, 与 `catalog/schemas.py` 一致.
 *
 * 放在客户端是为了让超长在**打字时**就被拦住: 等后端回 422 时用户已经写了几百字, 而报错文案里
 * 只有字段位置, 看不出该砍掉多少.
 */
export const MAXIMUM_STRATEGY_NAME_LENGTH = 128;
export const MAXIMUM_STRATEGY_DESCRIPTION_LENGTH = 500;
export const MAXIMUM_USERNAME_LENGTH = 64;
export const MINIMUM_USERNAME_LENGTH = 3;
export const MINIMUM_PASSWORD_LENGTH = 8;
export const MAXIMUM_PASSWORD_LENGTH = 256;
export const MAXIMUM_DISPLAY_NAME_LENGTH = 128;

export interface MessageResponse {
  message: string;
  success: boolean;
}

/* ── 枚举 ─────────────────────────────────────────────────── */

export const USER_TYPES = ['admin', 'user'] as const;
export type UserType = (typeof USER_TYPES)[number];

export const USER_STATUSES = ['active', 'disabled'] as const;
export type UserStatus = (typeof USER_STATUSES)[number];

export const STRATEGY_VISIBILITIES = ['private', 'shared', 'public'] as const;
export type StrategyVisibility = (typeof STRATEGY_VISIBILITIES)[number];

export const GRANT_PERMISSIONS = ['read', 'run'] as const;
export type GrantPermission = (typeof GRANT_PERMISSIONS)[number];

/** 取值即引擎字面量, **大小写不可改** (`Bar` / `Tick`, 不是 `bar` / `tick`). */
export const MARKET_DATA_TYPES = ['Bar', 'Tick'] as const;
export type MarketDataType = (typeof MARKET_DATA_TYPES)[number];

/** 提交侧只收这一档 (`run_submission.MATCH_MODE_NOT_SUBMITTABLE_MESSAGE`). */
export const SUBMITTABLE_MATCH_MODE: MarketDataType = 'Bar';

export const RUN_STATUSES = [
  'queued',
  'running',
  'succeeded',
  'failed',
  'interrupted',
  'timeout',
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/* ── 鉴权 ─────────────────────────────────────────────────── */

export interface AccessTokenResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
}

/* ── 用户 ─────────────────────────────────────────────────── */

export interface User {
  id: string;
  username: string;
  display_name: string;
  user_type: UserType;
  status: UserStatus;
  /** UTC 朴素时间串, **无时区标记**: 必须经 `domain/format` 的解析函数处理. */
  created_at: string;
}

/**
 * 受限用户目录的一条.
 *
 * 只有 `id` 与 `display_name`: 这是后端**有意**的收窄 (`username` 绝不出现), 故它不能当
 * 登录名用, 也不能用来区分同名的人.
 */
export interface UserDirectoryEntry {
  id: string;
  display_name: string;
}

export interface UserCreatePayload {
  username: string;
  password: string;
  /** 必填: 目录只列显示名非空的账号, 留空等于建出一个别人选不到的号. */
  display_name: string;
  user_type: UserType;
}

/* ── 策略 ─────────────────────────────────────────────────── */

export interface Strategy {
  id: string;
  owner_user_id: string;
  name: string;
  description: string;
  visibility_type: StrategyVisibility;
  created_at: string;
  updated_at: string;
}

export interface StrategyVersion {
  id: string;
  strategy_id: string;
  version_no: number;
  entry_filename: string;
  config_filename: string;
  source_hash: string;
  /**
   * 上传的那份配置 JSON 的**原样透传** (字符串, 不在这里解析): 提交页按它的键生成参数控件, 故
   * 必须能到前端; 而在读接口里解析会给存量行新增一条失败路径 (一份坏配置会让整个策略详情 500),
   * 前端本来也要自己 `JSON.parse`.
   *
   * `null` 表示**改形态之前**落的版本 (那时存的是 manifest), 它没有模板, 提交时后端会回 400 让
   * 用户重传. 前端据此把"这份版本不能用来提交"说在前面.
   */
  configuration_json: string | null;
  uploaded_at: string;
}

export interface StrategyGrant {
  strategy_id: string;
  grantee_user_id: string;
  permission_type: GrantPermission;
  granted_by_user_id: string;
  granted_at: string;
}

export interface StrategyDetail {
  strategy: Strategy;
  /** 版本号倒序, 最新在前. */
  versions: StrategyVersion[];
  /** 只对归属人非空: 被授权人拿到的是空数组, 不是 403. */
  grants: StrategyGrant[];
}

export interface StrategyGrantPayload {
  grantee_user_id: string;
  permission_type: GrantPermission;
}

/* ── 策略配置模板 (上传的那份 JSON, 经 configuration_json 到达前端) ── */

/**
 * 参数取值的 JSON 类型, 也就是**控件形态的判据**.
 *
 * 平台不看取值范围、不看标题、更不认识类型声明——它只看这个键当前取值的 JSON 类型. 故这里没有
 * `integer` 与 `number` 之分 (JSON 只有一种数), `integer` 那个名字留给"用户手填时必须是个整数"
 * 的输入约束.
 */
export const PARAMETER_VALUE_TYPES = ['boolean', 'number', 'string'] as const;
export type ParameterValueType = (typeof PARAMETER_VALUE_TYPES)[number];

/**
 * 平台按固定键名**覆写**的三个键; 它们不在参数区渲染 (提交页各有自己的控件).
 *
 * 与后端 `strategy_configuration.PLATFORM_KEY_NAMES` 逐字对应, 含 `BarPreces` 这个引擎侧既有
 * 拼写. 模板里若本来就有它们, 用户改的值不作数 (平台覆写), 故把它们留在参数区只会误导.
 */
export const PLATFORM_CONFIGURATION_KEY_NAMES = [
  'ExchangeId',
  'InstrumentId',
  'BarPreces',
] as const;

/** 上传的配置模板: 键即参数, 值即默认值. 键集由这份文件固定, 提交只有"改值"这一种权利. */
export type StrategyConfigurationTemplate = Record<string, unknown>;

/* ── 运行 ─────────────────────────────────────────────────── */

export interface RunSummary {
  id: string;
  user_id: string;
  strategy_id: string;
  strategy_version_id: string;
  status: RunStatus;
  submitted_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  exit_code: number | null;
  /**
   * 引擎自报的成败.
   *
   * **不能拿它替代 `status`**: 进程退出码为 0 只说明宿主没崩, 引擎可以自己报失败; 反过来
   * `status` 为 `succeeded` 而 `is_success` 为 false 是完全可能的. 两个信号分开显示.
   */
  is_success: boolean | null;
  market_data_type: MarketDataType | null;
  start_trading_day: string | null;
  end_trading_day: string | null;
  trade_count: number | null;
  order_count: number | null;
  balance: number | null;
  available: number | null;
  total_commission: number | null;
  /**
   * 该轮提交的策略参数 (JSON 文本), 与详情页那一份**同一个字段**.
   *
   * 列表视图也带着它, 因为对比页要回答的是"这两轮差在哪", 而**参数就是那个差**: 两轮同策略同参数、
   * 只差一个 `GridStep` 时, 指标列会不同却看不出因为什么.
   */
  params_json: string;
  error_id: number | null;
  error_msg: string | null;
}

export interface RunDetail extends RunSummary {
  runner_pid: number | null;
  hostname: string;
  /**
   * 提交时冻结的引擎标识: 引擎包自带版本号时是它, 否则是 `.pyd` 与运行时库的内容摘要
   * (`sha256:` 前缀), 读不出时是空串.
   *
   * 两种形态**不可比较**: 一个是人写的版本号, 一个是内容哈希. 换版之后新旧两轮在这一列上
   * 长得一样的话, 说明引擎的标识口径没变——而不是引擎没换.
   */
  engine_version: string;
  params_json: string;
  backtest_config_json: string;
  workspace_path: string;
  db_path: string;
  dump_path: string;
  stdout_tail: string;
  stderr_tail: string;
  schema_version: number | null;
  last_trading_day: string | null;
  account_id: string | null;
  md_subscribe_count: number | null;
  bar_market_data_count: number | null;
  depth_market_data_count: number | null;
  instrument_count: number | null;
  commission_missing_count: number | null;
  commission_zero_rate_key_count: number | null;
  volume_multiple_fallback_product_count: number | null;
  basic_data_loaded: boolean | null;
  has_capital: boolean | null;
  total_stamp_tax: number | null;
  total_transfer_fee: number | null;
}

/**
 * 提交请求体.
 *
 * 后端是 `extra="forbid"`: 多一个键即 422, 键名写错不会被静默忽略.
 *
 * `bar_period` 是**策略的订阅周期** (`5m` / `15m` / `30m` / `60m`, 即落盘精度的整数倍), 不是数据源
 * 精度: 引擎那份 `BackTest.json.BarPreces` 恒为落盘精度, 由平台写死, 用户选的值只进策略配置.
 *
 * `params` 只需给**用户改过的**键; 没给的键取那份上传的配置里的值 (见
 * `run_configuration.build_strategy_configuration`). 三个运行级键不许出现在这里: 后端回 400.
 */
export interface RunSubmitPayload {
  strategy_id: string;
  strategy_version_id?: string | null;
  match_mode: MarketDataType;
  bar_period: string;
  exchange_id?: string | null;
  instrument_id?: string | null;
  start_trading_day: string;
  end_trading_day: string;
  initial_capital: number;
  params: Record<string, unknown>;
}

/**
 * 一份配置模板: 命名的取值集合, 供提交页重复套用.
 *
 * 字段与 `LastSubmittedParameters` **刻意对齐** (`params` 是参数的原始取值, 运行级字段各有具名
 * 成员), 故前端只有一条"把一套取值填进表单"的路径. 与后端 `catalog.schemas.RunTemplateResponse`
 * 逐字对应: `strategy_id` 也带回来, 免得页面靠自己的请求上下文去猜这份列表属于哪个策略.
 */
export interface RunTemplate {
  id: string;
  strategy_id: string;
  name: string;
  match_mode: MarketDataType;
  bar_period: string;
  exchange_id: string | null;
  instrument_id: string | null;
  start_trading_day: string;
  end_trading_day: string;
  initial_capital: number;
  params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface RunTemplateList {
  strategy_id: string;
  templates: RunTemplate[];
}

/**
 * 存一份配置模板的请求体.
 *
 * 与 `RunSubmitPayload` 是**同一套取值**少两项: 策略在 URL 上 (模板挂在那个策略下), 版本刻意
 * 不存 (模板是策略级的, 换版本后仍该能用). 与后端 `catalog.schemas.RunTemplateCreateRequest`
 * 逐字对应, 参数的取值口径见 `domain/run-form.buildTemplateDraft`.
 */
export interface RunTemplateCreatePayload {
  name: string;
  match_mode: MarketDataType;
  bar_period: string;
  exchange_id: string | null;
  instrument_id: string | null;
  start_trading_day: string;
  end_trading_day: string;
  initial_capital: number;
  params: Record<string, unknown>;
}

/**
 * 该用户在该策略下最近一次提交的参数, 供提交页预填.
 *
 * 与 `catalog/schemas.py:LastSubmittedParametersResponse` 逐字对应, 字段全可空: `run_id` 为
 * `null` 即**没有历史运行** (正常状态, 不是错误). 取值一律照原样带回来, 不在这里判能不能用——
 * 判据是当前那份配置模板给的控件, 见 `domain/strategy-configuration.createInitialParameterInputs`.
 *
 * `params` 里只有策略参数: 运行级字段已拆成下面的具名字段, 故前端不必认识引擎 `BackTest.json`
 * 的键名 (其中有 `BarPreces` 这种引擎侧拼写).
 */
export interface LastSubmittedParameters {
  run_id: string | null;
  submitted_at: string | null;
  match_mode: MarketDataType | null;
  bar_period: string | null;
  exchange_id: string | null;
  instrument_id: string | null;
  start_trading_day: string | null;
  end_trading_day: string | null;
  initial_capital: number | null;
  params: Record<string, unknown>;
}

export interface JobArtifact {
  /** POSIX 形式, 相对于作业目录, 可含 `/` (`Dump/<RunId>/t_trade.csv`). */
  relative_path: string;
  size_bytes: number;
}
export interface JobArtifactList {
  run_id: string;
  artifacts: JobArtifact[];
}

/* ── 结果库 (引擎写的 BackTest_<RunId>.db) ─────────────────── */

/**
 * 界面上开放的结果表, 与 `backend/app/services/result_database.py:RESULT_TABLE_NAMES` 逐字对应.
 *
 * 这是那份白名单的**镜像**. 引擎的结果库有 17 张表, 平台只开这 5 张, 其余表名一律 404; 而清单
 * 在前端又抄了一份, 是因为没有列表端点 (表名不是从后端拉的). 于是**两边必须一起改**——界面上
 * 根本不给不合法的选项, 是避免「请求 404、用户只看到一句报错」那种无声失败的唯一可靠办法
 * (同 `RUN_SORT_COLUMNS` 的先例).
 */
export const RESULT_TABLE_NAMES = [
  'Capital',
  'Trade',
  'Order',
  'Position',
  'PositionDetail',
] as const;
export type ResultTableName = (typeof RESULT_TABLE_NAMES)[number];

/** 页签文案. 引擎的表名是英文的, 中文只出现在这一处, 不散到模板里. */
export const RESULT_TABLE_LABELS: Record<ResultTableName, string> = {
  Capital: '资金',
  Trade: '成交',
  Order: '委托',
  Position: '持仓',
  PositionDetail: '持仓明细',
};

export interface EquityPoint {
  /** 8 字符 `YYYYMMDD` 交易日; 展示前过 `formatTradingDay`. */
  trading_day: string;
  balance: number;
  available: number;
}

export interface RunEquity {
  run_id: string;
  /**
   * 逐日升序, 一天一点.
   *
   * **首点是引擎的种子行** (`Deposit` = 初始资金, `Balance` = 初始权益), 故序列起点就是初始
   * 权益而不是第一个交易日结束时的权益; 末点与运行详情里的 `balance` 同值.
   */
  points: EquityPoint[];
}

/**
 * 对比里的一轮: 它的列表列加上它自己的曲线.
 *
 * `summary` 而不是整份 `RunDetail`: 对比页只展示列表页与详情页共有的那些指标, 而 `Runs` 的镜像列
 * 恰好就是它们——对比**绝不重读结果文件** (`docs/platform-plan.md` §12.8).
 */
export interface RunComparisonEntry {
  summary: RunSummary;
  equity_points: EquityPoint[];
  /**
   * 这一轮的曲线为什么没有; `null` 即有曲线.
   *
   * 与 `summary` 是**两个独立的可用性**: 结果库文件被磁盘清理删掉之后, 指标仍在 (它们在 `Runs`
   * 表上), 只有曲线没了. 故降级粒度是"这一轮少一条线", 而不是整页报错.
   */
  equity_unavailable_reason: string | null;
}

/** 多轮对比. `runs` 的次序 = 请求里 `ids` 的次序 (去重后), 由服务端保证. */
export interface RunComparison {
  runs: RunComparisonEntry[];
}

/** 结果表的单元格值. 引擎的列只有 str / int / float 三种标量 (无 BLOB), `null` 是防御性允许. */
export type ResultTableValue = string | number | boolean | null;

export type ResultTableRecord = Record<string, ResultTableValue>;

/**
 * 结果表的一页.
 *
 * `total` / `offset` / `limit` 与 `PageResponse` 逐字同形 (`PaginationBar` 只认这三个数, 原样
 * 复用), 但它**不是** `PageResponse`: 结果表的行没有固定形状, 表头由响应里的 `columns` 给出
 * (顺序即权威顺序), 故列名不在前端写死——`Order` 有 33 列, 写死就是 33 处会漂移的真相.
 */
export interface ResultTableResponse {
  table: string;
  columns: string[];
  total: number;
  offset: number;
  limit: number;
  records: ResultTableRecord[];
}

/* ── 行情组件 (../QuoteHub) ─────────────────────────────────── */

/**
 * 下拉框里的一项.
 *
 * `code` 是组件侧的原生主键 (`sh.600519`), 也是下拉框的取值; `exchange_id` / `instrument_id` 是
 * 后端**已经拆好**的平台侧两个字段. 拆写因此不靠前端解析 `code` —— 前缀到交易所的映射是组件与
 * 引擎之间的约定, 本仓只镜像一份 (`domain/market-data.ts`), 再散一处就是会漂移的第二处.
 */
export interface MarketDataContract {
  code: string;
  exchange_id: string;
  instrument_id: string;
  display_name: string;
}

/**
 * 合约清单与 K 线周期.
 *
 * `available` 为 `false` 是**一种状态, 不是错误**: 行情组件是另一个仓的组件, 没装就没有它. 那时
 * `contracts` 为空、`reason` 是一句可以直接显示的中文; 界面据此禁用下拉框并给出原因, **不退回
 * 自由文本**——自由文本会让用户填出一个看着正常、跑起来零成交的取值.
 *
 * 后端对此因此回 200 而不是 503: 用户该看到"现在不能选合约"这件确定的事, 而不是一个要他去猜的
 * 加载失败.
 */
export interface MarketDataContractList {
  available: boolean;
  reason: string;
  contracts: MarketDataContract[];
}

/**
 * 一轮提交的行情预检结果. 它只**告知**, 放行与否不归它管 (真下载发生在调度器里).
 *
 * `available` 为 `false` 时 `sufficient` 没有意义: 那是"没法判", 不是"不够" —— 界面必须按前者
 * 显示, 否则会把"组件不在位"说成"本地缺行情".
 */
export interface MarketDataCoverage {
  available: boolean;
  reason: string;
  sufficient: boolean;
  expected_day_count: number;
  missing_day_count: number;
}

/**
 * 预检查询的参数, 与 `routers/market_data.read_coverage_handler` 的查询参数逐字对应.
 *
 * **没有 `bar_period`**: 覆盖判据问的是"本地有没有那段日期的数据", 而落盘只有 5m 一档, 用户的订阅
 * 周期不影响"数据在不在". 四个字段都取自表单本身 (`domain/run-form.buildCoverageQuery`), 故"预检
 * 说缺数据"与"提交被拒"用的是同一批判据, 不会各说各话.
 */
export interface MarketDataCoverageQuery {
  exchange_id: string;
  instrument_id: string;
  start_trading_day: string;
  end_trading_day: string;
  /** 它整份交给 `request` 的 `query`, 那边收的是开放键集; 四个字段拼错名字是这里唯一的防线. */
  [parameterName: string]: string;
}
