/**
 * 枚举取值的界面文案与色调.
 *
 * 与 `run-status.ts` 分开: 那个文件讲的是「运行跑到哪一步了」, 是本项目的领域概念; 这里只是
 * 把后端的枚举值翻成中文, 换一套文案不影响任何逻辑.
 */

import type { RunSortColumn } from '../api/runs';
import type { GrantPermission, StrategyVisibility, UserStatus, UserType } from '../api/types';
import type { StatusPresentation } from './run-status';

const USER_TYPE_LABELS: Record<UserType, string> = {
  admin: '管理员',
  user: '普通用户',
};

export function describeUserType(userType: UserType): string {
  return USER_TYPE_LABELS[userType];
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
