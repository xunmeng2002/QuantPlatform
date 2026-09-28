/** 提交回测、查运行、取消, 以及作业产物的清单与下载. */

import { encodeArtifactPath } from '../domain/download';
import { request, requestBlob } from './client';
import type {
  JobArtifactList,
  MessageResponse,
  PageResponse,
  ResultTableName,
  ResultTableResponse,
  RunComparison,
  RunDetail,
  RunEquity,
  RunStatus,
  RunSubmitPayload,
  RunSummary,
} from './types';

/**
 * 一次对比的轮数上限, 与 `routers/runs.py:MAXIMUM_COMPARISON_RUNS` 一致.
 *
 * 界面上根本不给第 7 个勾选框, 是避免「请求 400、用户只看到一句报错」那种无声失败的唯一可靠
 * 办法 (同 `RUN_SORT_COLUMNS` / `RESULT_TABLE_NAMES` 的先例).
 */
export const MAXIMUM_COMPARISON_RUNS = 6;

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

/**
 * 多轮对比: 每轮一列, 附各自的权益曲线.
 *
 * `ids` 走在 query string 上 (`?ids=a,b,c`), 故它自带深链接——`/compare?ids=…` 是可以直接发给
 * 别人的地址, 而页面上那份勾选状态不过是这个地址的另一种写法.
 *
 * **归属判定是整请求的**: 有一个 id 不属于你 (或不存在) 就整个 404, 不会降级成"少一列"——多租户
 * 规则要求越权与不存在不可分辨. 而"某一轮还没有曲线"只降那一列 (见 `RunComparisonEntry`).
 */
export function fetchRunComparison(runIds: string[]): Promise<RunComparison> {
  return request<RunComparison>('/runs/compare', {
    query: { ids: runIds.join(',') },
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

/**
 * 删掉一轮: **连作业目录一起删**, 且这一动作不可撤销.
 *
 * 只能删终态的轮 (未结束的回 409, 想让它消失走取消端点); 重复删除回 404. 目录删不掉时后端
 * **保留行**并回失败——行是找到那个目录的唯一句柄 (Windows 上被孤儿进程占着的目录会走到这里),
 * 故调用方该把失败当作"过一会儿可以再试", 而不是"已经删干净了".
 */
export function deleteRun(runId: string): Promise<MessageResponse> {
  return request<MessageResponse>(`/runs/${encodeURIComponent(runId)}`, {
    method: 'DELETE',
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

/**
 * 逐日权益序列.
 *
 * 只有**终态**的轮能取: 引擎写库时没有 `busy_timeout`, 边写边读会拿到半份文件, 故后端对未结束的
 * 轮回 409 (`RESULT_NOT_READY_MESSAGE`); 没有结果库的轮 (失败/取消/超时) 回 404.
 */
export function fetchRunEquity(runId: string): Promise<RunEquity> {
  return request<RunEquity>(`/runs/${encodeURIComponent(runId)}/equity`);
}

/**
 * 结果表的一页.
 *
 * `table` 只取 `RESULT_TABLE_NAMES` 里的名字 (后端白名单的镜像), 其余一律 404. 页大小同样受
 * `MAXIMUM_PAGE_SIZE` 约束, 故界面只给 `PAGE_SIZE_OPTIONS` 那几档.
 */
export function fetchRunResultTable(
  runId: string,
  table: ResultTableName,
  page: { offset: number; limit: number },
): Promise<ResultTableResponse> {
  return request<ResultTableResponse>(
    `/runs/${encodeURIComponent(runId)}/tables/${encodeURIComponent(table)}`,
    { query: { offset: page.offset, limit: page.limit } },
  );
}
