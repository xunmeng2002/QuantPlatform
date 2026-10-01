/**
 * 策略本体、版本与授权.
 *
 * 上传是 multipart, 且是**两个文件字段**: 源码 `.py` 与配置 `.json`. 配置必须是文件而不是文本
 * 字段, 因为它要带着自己的文件名落到作业目录里 —— 策略启动时按那个名字读它 (见
 * `docs/job-workspace.md`), 平台替它编一个名字就等于改掉了策略本来就认的那份配置名. 那份 JSON 同
 * 时是提交页的参数模板 (见 `domain/strategy-configuration`).
 */

import { request } from './client';
import type {
  LastSubmittedParameters,
  MessageResponse,
  PageResponse,
  Strategy,
  StrategyDetail,
  StrategyGrantPayload,
  StrategyVersion,
  StrategyVisibility,
} from './types';

export interface StrategyCreateInput {
  name: string;
  description: string;
  visibilityType: StrategyVisibility;
  sourceFile: File;
  configurationFile: File;
}

export function fetchStrategies(
  offset: number,
  limit: number,
): Promise<PageResponse<Strategy>> {
  return request<PageResponse<Strategy>>('/strategies', { query: { offset, limit } });
}

export function fetchStrategyDetail(strategyId: string): Promise<StrategyDetail> {
  return request<StrategyDetail>(`/strategies/${encodeURIComponent(strategyId)}`);
}

export function createStrategy(input: StrategyCreateInput): Promise<StrategyDetail> {
  const formData = new FormData();
  formData.append('source', input.sourceFile);
  formData.append('configuration', input.configurationFile);
  formData.append('name', input.name);
  formData.append('description', input.description);
  formData.append('visibility_type', input.visibilityType);

  return request<StrategyDetail>('/strategies', { method: 'POST', formData });
}

export function uploadStrategyVersion(
  strategyId: string,
  sourceFile: File,
  configurationFile: File,
): Promise<StrategyVersion> {
  const formData = new FormData();
  formData.append('source', sourceFile);
  formData.append('configuration', configurationFile);

  return request<StrategyVersion>(
    `/strategies/${encodeURIComponent(strategyId)}/versions`,
    { method: 'POST', formData },
  );
}

/**
 * 该用户在该策略下最近一次提交的参数, 供提交页预填.
 *
 * 没有历史运行时也是 200 (字段全空), 故调用方不必区分"没跑过"与"请求成功". 取的是**本人**的
 * 轮次——共享与公开策略下拿到的不是归属人的参数.
 */
export function fetchLastSubmittedParameters(
  strategyId: string,
): Promise<LastSubmittedParameters> {
  return request<LastSubmittedParameters>(
    `/strategies/${encodeURIComponent(strategyId)}/last-submitted-parameters`,
  );
}

/** 整体替换授权集合: 空列表即撤销全部授权, 重复提交同一份名单结果不变. */
export function replaceStrategyGrants(
  strategyId: string,
  grants: StrategyGrantPayload[],
): Promise<StrategyDetail> {
  return request<StrategyDetail>(
    `/strategies/${encodeURIComponent(strategyId)}/grants`,
    { method: 'PUT', body: { grants } },
  );
}

/** 软删: 行留着, 运行记录仍指得到它, 故历史运行不会因此变成孤儿. */
export function deleteStrategy(strategyId: string): Promise<MessageResponse> {
  return request<MessageResponse>(`/strategies/${encodeURIComponent(strategyId)}`, {
    method: 'DELETE',
  });
}
