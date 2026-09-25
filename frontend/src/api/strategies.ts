/**
 * 策略本体、版本与授权.
 *
 * 上传是 multipart 而不是 JSON: 源码是文件, 而 manifest 是**同一个请求里的一个文本字段**
 * (见 `routers/strategies.py` 的模块 docstring). 前端两条录入路径 (手填 / 读入一份
 * manifest.json) 最终落到同一个字段, 故服务端只有一条路径.
 */

import { request } from './client';
import type {
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
  manifestText: string;
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
  formData.append('manifest', input.manifestText);
  formData.append('name', input.name);
  formData.append('description', input.description);
  formData.append('visibility_type', input.visibilityType);

  return request<StrategyDetail>('/strategies', { method: 'POST', formData });
}

export function uploadStrategyVersion(
  strategyId: string,
  sourceFile: File,
  manifestText: string,
): Promise<StrategyVersion> {
  const formData = new FormData();
  formData.append('source', sourceFile);
  formData.append('manifest', manifestText);

  return request<StrategyVersion>(
    `/strategies/${encodeURIComponent(strategyId)}/versions`,
    { method: 'POST', formData },
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
