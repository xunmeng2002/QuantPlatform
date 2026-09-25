/**
 * 运行状态与引擎判定怎么显示.
 *
 * 两者是**独立的信号**, 不能合成一个: `status` 是宿主对进程的观察 (退出码、是否被杀),
 * `is_success` 是引擎在自己那份 `result.json` 里给出的结论. 退出码为 0 只说明进程没崩,
 * 引擎完全可以自报失败; 反过来也一样.
 */

import type { RunStatus } from '../api/types';

/** 色调而不是颜色值: 具体色值只在 `StatusBadge.vue` 里出现一处. */
export type StatusTone = 'neutral' | 'progress' | 'success' | 'danger' | 'warning';

export interface StatusPresentation {
  label: string;
  tone: StatusTone;
}

/**
 * 终态 = 不会再变的状态; 非终态才值得轮询.
 *
 * 与 `catalog/enums.TERMINAL_RUN_STATUSES` 对应. 漏掉一个的后果是列表页对着一个已经结束的
 * 轮无限轮询, 故这里逐项列出而不是用「非 queued / running」反推.
 */
export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = [
  'succeeded',
  'failed',
  'interrupted',
  'timeout',
];

const RUN_STATUS_PRESENTATIONS: Record<RunStatus, StatusPresentation> = {
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'progress' },
  succeeded: { label: '已成功', tone: 'success' },
  failed: { label: '已失败', tone: 'danger' },
  interrupted: { label: '已中断', tone: 'warning' },
  timeout: { label: '已超时', tone: 'warning' },
};

export function describeRunStatus(status: RunStatus): StatusPresentation {
  return RUN_STATUS_PRESENTATIONS[status];
}

export function isTerminalRunStatus(status: RunStatus): boolean {
  return TERMINAL_RUN_STATUSES.includes(status);
}

const ENGINE_SUCCEEDED_PRESENTATION: StatusPresentation = {
  label: '引擎判定成功',
  tone: 'success',
};
const ENGINE_FAILED_PRESENTATION: StatusPresentation = {
  label: '引擎判定失败',
  tone: 'danger',
};
const ENGINE_SILENT_PRESENTATION: StatusPresentation = {
  label: '引擎未给出结论',
  tone: 'neutral',
};

/** `is_success` 为 `null` 是常态 (还没跑完, 或引擎没写出 `result.json`), 不是异常. */
export function describeEngineVerdict(isSuccess: boolean | null | undefined): StatusPresentation {
  if (isSuccess === true) {
    return ENGINE_SUCCEEDED_PRESENTATION;
  }

  if (isSuccess === false) {
    return ENGINE_FAILED_PRESENTATION;
  }

  return ENGINE_SILENT_PRESENTATION;
}
