/**
 * 配置模板: 命名的取值集合, 供提交页重复套用.
 *
 * 模板挂在**策略**下 (路径里的 `strategy_id`), 而可见性只按归属人: 共享或公开的策略下列出来的
 * 仍是你自己那几份. 权限与不存在都回 404, 前端无从分辨, 故也不必分辨.
 *
 * **没有 apply 端点**: "套用"要与当前配置模板派生出的控件、当前表单已填的值一起决定, 是纯前端
 * 动作 (见 `domain/run-form` 与 `domain/strategy-configuration`). 服务端只负责存与取.
 */

import { request } from './client';
import type {
  MessageResponse,
  RunTemplate,
  RunTemplateCreatePayload,
  RunTemplateList,
} from './types';

function templatePath(strategyId: string): string {
  return `/strategies/${encodeURIComponent(strategyId)}/run-templates`;
}

/**
 * 该策略下本人保存的模板, 最近创建的在前.
 *
 * 不分页: 个人级小集合, 且有 `MAXIMUM_TEMPLATES_PER_STRATEGY` 的硬上限. 策略本身不可见时也是
 * 404 (与提交权限同一口径).
 */
export function fetchRunTemplates(strategyId: string): Promise<RunTemplateList> {
  return request<RunTemplateList>(templatePath(strategyId));
}

/** 同名模板回 409——判据是库里的唯一约束, 不是前端先查一次. */
export function createRunTemplate(
  strategyId: string,
  payload: RunTemplateCreatePayload,
): Promise<RunTemplate> {
  return request<RunTemplate>(templatePath(strategyId), {
    method: 'POST',
    body: payload,
  });
}

/** 只改名: 改取值等于存一个新模板. */
export function renameRunTemplate(
  strategyId: string,
  templateId: string,
  name: string,
): Promise<RunTemplate> {
  return request<RunTemplate>(
    `${templatePath(strategyId)}/${encodeURIComponent(templateId)}`,
    { method: 'PATCH', body: { name } },
  );
}

/** 硬删; 已删的再删即 404. 删掉的名字立刻可以再用. */
export function deleteRunTemplate(
  strategyId: string,
  templateId: string,
): Promise<MessageResponse> {
  return request<MessageResponse>(
    `${templatePath(strategyId)}/${encodeURIComponent(templateId)}`,
    { method: 'DELETE' },
  );
}
