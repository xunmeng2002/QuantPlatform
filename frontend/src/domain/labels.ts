/**
 * 枚举取值的界面文案与色调.
 *
 * 与 `run-status.ts` 分开: 那个文件讲的是「运行跑到哪一步了」, 是本项目的领域概念; 这里只是
 * 把后端的枚举值翻成中文, 换一套文案不影响任何逻辑.
 */

import type { RunSortColumn } from '../api/runs';
import { BOTH_DIRECTIONS } from '../api/types';
import type {
  GrantPermission,
  ParameterValueType,
  StrategyVisibility,
  UserStatus,
  UserType,
} from '../api/types';
import type { StatusPresentation } from './run-status';

const USER_TYPE_LABELS: Record<UserType, string> = {
  admin: '管理员',
  user: '普通用户',
};

export function describeUserType(userType: UserType): string {
  return USER_TYPE_LABELS[userType];
}

/**
 * 引擎的 `ProductClassType` → 中文.
 *
 * 键是 `number` 而不是联合类型, 因为读侧收到的就是**裸数字**: 库里出现枚举外的取值时, 显示成
 * 原数字比显示"未知"有用 —— 那是个真取值 (从引擎侧 dump 灌进来的那套 `1` / `2` / `7` 迟早会
 * 出现), 认出来的人正是要看见它才知道该怎么办.
 */
const PRODUCT_CLASS_LABELS: Record<number, string> = {
  0: '期货',
  1: '期货期权',
  2: '组合',
  3: '现货',
  4: '期转现',
  5: '指数',
  6: '股票',
  7: '股票期权',
  8: 'ETF',
};

/**
 * 费率方向 → 中文.
 *
 * 「买」与「卖」而不是「多头」/「空头」: 这一列说的是**成交方向**, 与持仓方向是两件事 (卖出
 * 开仓也是"卖"). 它决定引擎取哪一行费率 —— 印花税只在卖出侧收, 卖出那一行没录, 那一笔的费用
 * 就按 0 算.
 *
 * 「双向」是平台自己的通配档 (`-1`): 买卖共用一套费率. 「双向」已经是完整说法了, 后面**不能再
 * 接「向」**去组"…向费率"那种短语 —— 故下面那个短语函数从这里派生, 而不是在调用点各拼一次.
 */
const COMMISSION_DIRECTION_LABELS: Record<number, string> = {
  [-1]: '双向',
  0: '买',
  1: '卖',
};

export function describeProductClass(productClass: number): string {
  return PRODUCT_CLASS_LABELS[productClass] ?? String(productClass);
}

export function describeCommissionDirection(direction: number): string {
  return COMMISSION_DIRECTION_LABELS[direction] ?? String(direction);
}

/**
 * 方向在「…向费率」这类短语里的说法: 「买向费率」「双向费率」, 而不是「双向向费率」.
 *
 * 单独一个函数而不在调用点拼后缀: 那句话在面板里有三处 (改单确认、删除确认、表单标题), 各拼
 * 一次就会各错一次 —— 通配档是后加的, 三处里漏改一处的那句话读起来才别扭得不明显.
 */
export function describeCommissionDirectionPhrase(direction: number): string {
  const label = describeCommissionDirection(direction);

  return direction === BOTH_DIRECTIONS ? label : `${label}向`;
}

const RUN_SORT_COLUMN_LABELS: Record<RunSortColumn, string> = {
  submitted_at: '提交时间',
  finished_at: '结束时间',
  duration_ms: '耗时',
  trade_count: '交易笔数',
  order_count: '订单笔数',
  balance: '余额',
  total_commission: '总手续费',
};

export function describeRunSortColumn(sortColumn: RunSortColumn): string {
  return RUN_SORT_COLUMN_LABELS[sortColumn];
}

const USER_STATUS_PRESENTATIONS: Record<UserStatus, StatusPresentation> = {
  active: { label: '启用中', tone: 'success' },
  disabled: { label: '已停用', tone: 'neutral' },
};

const STRATEGY_VISIBILITY_PRESENTATIONS: Record<StrategyVisibility, StatusPresentation> = {
  private: { label: '仅自己', tone: 'neutral' },
  shared: { label: '授权共享', tone: 'progress' },
  public: { label: '公开', tone: 'success' },
};

const GRANT_PERMISSION_LABELS: Record<GrantPermission, string> = {
  read: '可查看',
  run: '可查看并运行',
};

export function describeUserStatus(status: UserStatus): StatusPresentation {
  return USER_STATUS_PRESENTATIONS[status];
}

export function describeStrategyVisibility(
  visibility: StrategyVisibility,
): StatusPresentation {
  return STRATEGY_VISIBILITY_PRESENTATIONS[visibility];
}

export function describeGrantPermission(permission: GrantPermission): string {
  return GRANT_PERMISSION_LABELS[permission];
}

/**
 * 参数取值类型 → 它会渲染成什么控件.
 *
 * 说"控件"而不是"类型"是给用户看的: 版本预览里那一列回答的是"下一轮回测这个键长什么样", 而
 * `boolean` / `number` 这种字面量对他没有信息量.
 */
const PARAMETER_CONTROL_LABELS: Record<ParameterValueType, string> = {
  boolean: '复选框',
  number: '数字框',
  string: '文本框',
};

export function describeParameterControl(valueType: ParameterValueType): string {
  return PARAMETER_CONTROL_LABELS[valueType];
}
