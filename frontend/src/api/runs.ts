/** 提交回测、查运行、取消, 以及作业产物的清单与下载. */

import { encodeArtifactPath } from '../domain/download';
import { request, requestBlob } from './client';
import type {
  JobArtifactList,
  PageResponse,
  RunDetail,
  RunStatus,
  RunSubmitPayload,
  RunSummary,
} from './types';

/**
 * `sort_by` 的白名单, 与 `routers/runs.py:RUN_SORT_COLUMNS` 逐字对应.
 *
 * 后端对未收录的取值静默回落到默认列而非报错, 故这里放错取值的表现是「排序没生效」——
 * 界面上根本不给不合法的选项, 是避免那种无声失败的唯一可靠办法.
 */
export const RUN_SORT_COLUMNS = [
  'submitted_at',
  'finished_at',
  'duration_ms',
  'trade_count',
  'order_count',
  'balance',
  'total_commission',
] as const;
export type RunSortColumn = (typeof RUN_SORT_COLUMNS)[number];

export const DEFAULT_RUN_SORT_COLUMN: RunSortColumn = 'submitted_at';

export interface RunListQuery {
  status?: RunStatus | '';
  strategyId?: string;
  sortBy?: RunSortColumn;
  descending?: boolean;
  offset?: number;
  limit?: number;
}

export function fetchRuns(query: RunListQuery): Promise<PageResponse<RunSummary>> {
  return request<PageResponse<RunSummary>>('/runs', {
    query: {
      status: query.status,
      strategy_id: query.strategyId,
      sort_by: query.sortBy,
      descending: query.descending,
      offset: query.offset,
      limit: query.limit,
    },
  });
}

export function fetchRunDetail(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/runs/${encodeURIComponent(runId)}`);
}

export function submitRun(payload: RunSubmitPayload): Promise<RunDetail> {
  return request<RunDetail>('/runs', { method: 'POST', body: payload });
}

/** 只能取消非终态的轮; 已结束的轮后端回 409. */
export function cancelRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  });
}

export function fetchRunArtifacts(runId: string): Promise<JobArtifactList> {
  return request<JobArtifactList>(`/runs/${encodeURIComponent(runId)}/files`);
}

/** 产物一律是附件 (后端不按扩展名给类型), 故这里拿到的一定是字节流而不是可渲染的内容. */
export function fetchRunArtifactBlob(
  runId: string,
  relativePath: string,
): Promise<Blob> {
  return requestBlob(
    `/runs/${encodeURIComponent(runId)}/files/${encodeArtifactPath(relativePath)}`,
  );
}
