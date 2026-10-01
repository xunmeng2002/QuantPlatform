/**
 * HTTP 出入口的失败归一化.
 *
 * 这里测的全是「后端回什么形状, 界面看到什么一句话」——四种失败 (字符串 detail / 数组 detail /
 * 没有 detail / 网络异常) 各自收敛成 `ApiError`, 视图因此只需要一条错误分支.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  NETWORK_FAILURE_STATUS,
  buildQueryString,
  configureUnauthorizedHandler,
  request,
  setAccessToken,
} from './client';

interface CapturedRequest {
  path: string;
  init: RequestInit | undefined;
}

let capturedRequests: CapturedRequest[] = [];

function stubFetchResponse(response: Response): void {
  vi.stubGlobal(
    'fetch',
    (path: string, init?: RequestInit): Promise<Response> => {
      capturedRequests.push({ path, init });

      return Promise.resolve(response);
    },
  );
}

function stubFetchFailure(error: Error): void {
  vi.stubGlobal('fetch', (): Promise<Response> => Promise.reject(error));
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function readAuthorizationHeader(captured: CapturedRequest): unknown {
  return (captured.init?.headers as Record<string, string> | undefined)?.Authorization;
}

async function readThrownApiError(action: () => Promise<unknown>): Promise<ApiError> {
  try {
    await action();
  } catch (error) {
    if (error instanceof ApiError) {
      return error;
    }

    throw error;
  }

  throw new Error('预期这里会抛 ApiError, 但它正常返回了');
}

beforeEach(() => {
  capturedRequests = [];
  setAccessToken(null);
  configureUnauthorizedHandler(() => {});
});

describe('request 的失败归一化', () => {
  it('400 的字符串 detail 原样透出', async () => {
    stubFetchResponse(jsonResponse({ detail: '策略名已被占用' }, 400));

    const apiError = await readThrownApiError(() => request('/users'));

    expect(apiError.status).toBe(400);
    expect(apiError.detail).toBe('策略名已被占用');
  });

  it('422 的数组 detail 压成「位置: 原因」, 且不带提交的取值', async () => {
    stubFetchResponse(
      jsonResponse(
        {
          detail: [
            {
              loc: ['body', 'name'],
              msg: 'Field required',
              input: '这是提交上去的取值',
              ctx: { note: '这是上下文' },
            },
            { loc: ['body', 'configuration'], msg: 'Field required' },
          ],
        },
        422,
      ),
    );

    const apiError = await readThrownApiError(() => request('/strategies'));

    expect(apiError.status).toBe(422);
    expect(apiError.detail).toBe(
      'body.name: Field required; body.configuration: Field required',
    );
    expect(apiError.detail).not.toContain('这是提交上去的取值');
    expect(apiError.detail).not.toContain('这是上下文');
  });

  it('401 没有响应体时回落到该状态码的通用文案', async () => {
    stubFetchResponse(new Response('', { status: 401 }));

    const apiError = await readThrownApiError(() => request('/runs'));

    expect(apiError.status).toBe(401);
    expect(apiError.detail).toBe('登录状态已失效, 请重新登录');
  });

  it('响应体不是 JSON 时也回落到通用文案, 不把原文抛给人看', async () => {
    stubFetchResponse(new Response('<html>502 Bad Gateway</html>', { status: 502 }));

    const apiError = await readThrownApiError(() => request('/runs'));

    expect(apiError.status).toBe(502);
    expect(apiError.detail).toBe('请求失败');
  });

  it('网络层异常归一化成 status 0', async () => {
    stubFetchFailure(new TypeError('fetch failed'));

    const apiError = await readThrownApiError(() => request('/runs'));

    expect(apiError.status).toBe(NETWORK_FAILURE_STATUS);
    expect(apiError.detail).toBe('无法连接到服务器, 请确认后端已启动');
  });

  it('204 一类的空响应体返回 undefined 而不是抛错', async () => {
    stubFetchResponse(new Response('', { status: 200 }));

    await expect(request('/runs')).resolves.toBeUndefined();
  });
});

describe('request 的请求组装', () => {
  it('有令牌时附 Bearer 头, 没有令牌时不附', async () => {
    // 每次请求都换一个响应体: Response 的 body 只能读一次, 复用同一个对象第二次就抛错.
    stubFetchResponse(new Response('{}', { status: 200 }));

    await request('/runs');
    expect(readAuthorizationHeader(capturedRequests[0])).toBeUndefined();

    setAccessToken('token-abc');
    stubFetchResponse(new Response('{}', { status: 200 }));

    await request('/runs');
    expect(readAuthorizationHeader(capturedRequests[1])).toBe('Bearer token-abc');
  });

  it('JSON 请求体补 Content-Type, multipart 不补 (boundary 要留给浏览器)', async () => {
    stubFetchResponse(new Response('{}', { status: 200 }));

    await request('/users', { method: 'POST', body: { username: 'alice' } });

    const jsonHeaders = capturedRequests[0].init?.headers as Record<string, string>;
    expect(jsonHeaders['Content-Type']).toBe('application/json');
    expect(capturedRequests[0].init?.body).toBe('{"username":"alice"}');

    const formData = new FormData();
    formData.append('name', '策略甲');
    stubFetchResponse(new Response('{}', { status: 200 }));

    await request('/strategies', { method: 'POST', formData });

    const formHeaders = capturedRequests[1].init?.headers as Record<string, string>;
    expect(formHeaders['Content-Type']).toBeUndefined();
    expect(capturedRequests[1].init?.body).toBe(formData);
  });

  it('查询串拼在路径末尾, 空串与 null 一律省略', async () => {
    stubFetchResponse(new Response('{}', { status: 200 }));

    await request('/runs', {
      query: { status: '', strategy_id: 'abc', descending: false, offset: 0 },
    });

    expect(capturedRequests[0].path).toBe(
      '/api/runs?strategy_id=abc&descending=false&offset=0',
    );
  });

  it('401 会清掉手里的令牌, 并触发全局处置回调', async () => {
    const onUnauthorized = vi.fn();
    configureUnauthorizedHandler(onUnauthorized);
    setAccessToken('token-expired');
    stubFetchResponse(new Response('', { status: 401 }));

    await readThrownApiError(() => request('/runs'));

    expect(onUnauthorized).toHaveBeenCalledTimes(1);

    stubFetchResponse(new Response('{}', { status: 200 }));

    await request('/runs');

    expect(readAuthorizationHeader(capturedRequests[1])).toBeUndefined();
  });
});

describe('buildQueryString', () => {
  it('省略空串 / null / undefined, 其余按值编码', () => {
    expect(
      buildQueryString({
        empty: '',
        missing: undefined,
        nothing: null,
        zero: 0,
        enabled: false,
        query: '甲 乙&丙',
      }),
    ).toBe('?zero=0&enabled=false&query=%E7%94%B2+%E4%B9%99%26%E4%B8%99');
  });

  it('没有任何有效参数时返回空串 (调用方据此不拼问号)', () => {
    expect(buildQueryString({ a: '', b: undefined })).toBe('');
    expect(buildQueryString({})).toBe('');
  });
});
