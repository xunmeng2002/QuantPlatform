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
  /** 上传时那份 manifest 文本的原样透传 (字符串). 提交页按它生成参数控件, 故须自行解析. */
  manifest_json: string;
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

/* ── manifest (策略作者与平台的契约, 经 manifest_json 到达前端) ── */

export const PARAMETER_TYPES = ['integer', 'number', 'string', 'boolean'] as const;
export type ParameterType = (typeof PARAMETER_TYPES)[number];

export interface ManifestParameterOption {
  value: unknown;
  /** 缺省空串 (`StrategyParameterOption`), 界面上回落成取值的字面量. */
  label?: string;
}

/**
 * manifest 里的一个参数.
 *
 * 除 `key` 外**全部可省**: 后端 `StrategyParameter` 给 `label` / `type` / `options` / `group`
 * 都写了默认值, 故「只声明 key 与 default」是一份完全合法的 manifest. 这里跟着放宽类型, 读取侧
 * 才能按后端的默认值补全 (`domain/manifest.ts` 的 `toParameterDescriptor`).
 */
export interface ManifestParameter {
  key: string;
  label?: string;
  type?: ParameterType;
  default?: unknown;
  minimum?: number | null;
  maximum?: number | null;
  options?: ManifestParameterOption[];
  group?: string;
}

/** 运行级字段在策略配置里的键名; 未映射的字段不该出现在提交表单上. */
export interface ManifestRunFieldKeys {
  exchange_id?: string | null;
  instrument_id?: string | null;
  bar_period?: string | null;
}

export interface StrategyManifest {
  entry_filename: string;
  config_filename: string;
  supported_match_modes: MarketDataType[];
  run_field_keys?: ManifestRunFieldKeys;
  params?: ManifestParameter[];
}

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
  error_id: number | null;
  error_msg: string | null;
}

export interface RunDetail extends RunSummary {
  runner_pid: number | null;
  hostname: string;
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
 * 后端是 `extra="forbid"`: 多一个键即 422, 键名写错不会被静默忽略. 而 `exchange_id` /
 * `instrument_id` 只在 manifest 声明了映射时才该出现在这里——没映射而提交, 后端回 400
 * 「该策略未映射 {field}」(见 `run_submission.RUN_FIELD_NOT_MAPPED_MESSAGE`).
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
