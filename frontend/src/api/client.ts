/**
 * 唯一的 HTTP 出入口.
 *
 * 全部视图都经这里发请求, 于是「附令牌 / 序列化请求体 / 解析响应 / 归一化失败」各只有一处实现.
 *
 * 失败的归一化是这里的核心职责. 后端有三种形状, 视图不该各写一套分支:
 *   1. 400/401/403/404/409/413/500 的 `detail` 是**字符串**;
 *   2. **422 的 `detail` 是数组** (pydantic 的逐字段报错项);
 *   3. **401 可能没有 `detail`** —— 缺令牌时 Starlette 层就挡了, 还没进到应用的路由.
 * 再叠上 `fetch` 自身抛异常 (后端没起、网线断了) 这一种, 四种最终都落成一个 `ApiError`:
 * 网络层失败用 `status === 0` 标记, 视图因此只需要一条错误分支.
 *
 * 令牌存模块变量而不是 localStorage: 持久化是 session store 的事, 这里只管「这次请求带什么」.
 * 401 的全局动作 (清会话 + 跳登录) 以回调注入, 本模块**不** import router / store —— 那会造出
 * `client → store → client` 的循环依赖.
 */

export const API_BASE_PATH = '/api';

export const NETWORK_FAILURE_STATUS = 0;

export type QueryValue = string | number | boolean | null | undefined;

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

/**
 * 归一化后的失败.
 *
 * `detail` 恒为可直接展示给人看的中文, 且**绝不含**后端堆栈、文件路径或提交的取值: 422 的
 * pydantic 报错项里同时带着 `msg` 与 `input`, 这里只取前者.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string, options?: ErrorOptions) {
    super(detail, options);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export interface RequestOptions {
  method?: HttpMethod;
  /** JSON 请求体, 由本模块序列化并补 `Content-Type`. 与 `formData` 互斥. */
  body?: unknown;
  /** multipart 请求体. `Content-Type` 必须留给浏览器填 (它要带 boundary), 故不设该头. */
  formData?: FormData;
  query?: Record<string, QueryValue>;
}

const STATUS_FALLBACK_DETAILS: Record<number, string> = {
  [NETWORK_FAILURE_STATUS]: '无法连接到服务器, 请确认后端已启动',
  400: '请求不合法',
  401: '登录状态已失效, 请重新登录',
  403: '没有权限执行该操作',
  404: '资源不存在',
  409: '与现有数据冲突',
  413: '提交的内容过大',
  422: '提交的内容不合法',
  500: '服务器内部错误',
};

const GENERIC_FALLBACK_DETAIL = '请求失败';

let accessToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

/** 由 session store 驱动: 令牌一变化就同步过来, 本模块不自行持久化. */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** 由组装根 (main.ts) 注入: 401 时清会话并跳登录页. 没有 refresh 端点, 重新登录是唯一恢复路径. */
export function configureUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

export function describeStatusFallback(status: number): string {
  return STATUS_FALLBACK_DETAILS[status] ?? GENERIC_FALLBACK_DETAIL;
}

/**
 * 把查询参数序列化成 `?a=1&b=2`.
 *
 * 空串与 `null` / `undefined` 一律省略: 后端把空串当作「未提供」(如
 * `run_submission._normalize_run_field_value`), 显式带上等号只会让 URL 更长更难读.
 * 编码交给 `URLSearchParams`, 不手拼——手工转义是拼 SQL 的近亲.
 */
export function buildQueryString(query: Record<string, QueryValue>): string {
  const searchParameters = new URLSearchParams();

  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') {
      continue;
    }

    searchParameters.append(name, String(value));
  }

  const serialized = searchParameters.toString();

  return serialized ? `?${serialized}` : '';
}

/** 发一个请求并返回响应体; 失败一律抛 `ApiError`. 无响应体 (204) 时返回 `undefined`. */
export async function request<ResponseBody>(
  path: string,
  options: RequestOptions = {},
): Promise<ResponseBody> {
  const response = await performRequest(path, options);
  const responseText = await response.text();

  if (!responseText.trim()) {
    return undefined as ResponseBody;
  }

  try {
    return JSON.parse(responseText) as ResponseBody;
  } catch (error) {
    throw new ApiError(response.status, '响应格式无法解析', { cause: error });
  }
}

/** 下载二进制产物走这条路: 响应体不进 JSON 解析, 也不驻留内存成一串文本. */
export async function requestBlob(
  path: string,
  options: RequestOptions = {},
): Promise<Blob> {
  const response = await performRequest(path, options);

  return await response.blob();
}

async function performRequest(
  path: string,
  options: RequestOptions,
): Promise<Response> {
  const { method = 'GET', body, formData, query } = options;
  const headers: Record<string, string> = {};

  if (accessToken !== null) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  let requestBody: BodyInit | undefined;

  if (formData !== undefined) {
    requestBody = formData;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    requestBody = JSON.stringify(body);
  }

  const requestPath = `${API_BASE_PATH}${path}${
    query === undefined ? '' : buildQueryString(query)
  }`;

  let response: Response;

  try {
    response = await fetch(requestPath, { method, headers, body: requestBody });
  } catch (error) {
    throw new ApiError(
      NETWORK_FAILURE_STATUS,
      describeStatusFallback(NETWORK_FAILURE_STATUS),
      { cause: error },
    );
  }

  if (response.status === 401) {
    accessToken = null;
    unauthorizedHandler?.();
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readFailureDetail(response));
  }

  return response;
}

/** 从失败响应里读出可展示的原因, 读不出就回落到该状态码的通用文案. */
async function readFailureDetail(response: Response): Promise<string> {
  let parsedBody: unknown;

  try {
    parsedBody = JSON.parse(await response.text());
  } catch {
    return describeStatusFallback(response.status);
  }

  return readErrorDetail(parsedBody) ?? describeStatusFallback(response.status);
}

/**
 * 从已解析的失败响应体里取 `detail`, 字符串与数组两种形状都收敛成一句话.
 *
 * 递归是有用的: `{"detail": [...]}` 与裸数组 (Starlette 层直接抛出的 422) 都会命中.
 */
function readErrorDetail(parsedBody: unknown): string | null {
  if (typeof parsedBody === 'string') {
    return parsedBody.trim() || null;
  }

  if (Array.isArray(parsedBody)) {
    return describeValidationErrorItems(parsedBody);
  }

  if (isRecord(parsedBody) && 'detail' in parsedBody) {
    return readErrorDetail(parsedBody.detail);
  }

  return null;
}

function describeValidationErrorItems(items: unknown[]): string | null {
  const descriptions = items
    .map(describeValidationErrorItem)
    .filter((description): description is string => description !== null);

  return descriptions.length > 0 ? descriptions.join('; ') : null;
}

/** 一个 pydantic 报错项压成 `位置: 原因`. **不读** `input` / `ctx`: 那里装着提交的取值. */
function describeValidationErrorItem(item: unknown): string | null {
  if (!isRecord(item) || typeof item.msg !== 'string') {
    return null;
  }

  const reason = item.msg.trim();

  if (!reason) {
    return null;
  }

  const location = Array.isArray(item.loc) ? item.loc.map(String).join('.') : '';

  return location ? `${location}: ${reason}` : reason;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
