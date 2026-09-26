/**
 * 会话 store 与 `api/client` 之间的令牌传播.
 *
 * 这条用例锁的是一个**真跑出来的 bug**: 把令牌推给 client 的是 `watch(accessToken, …)`, 而
 * Vue 的 watch 默认 `flush: 'pre'` —— 回调排在微任务里跑. 于是 `login()` 里紧跟的那次
 * `/auth/me` 在 `setAccessToken` **之前**就发了出去, 不带 `Authorization` 头; 后端回 401
 * 「缺少访问令牌」, 而 `client` 拿到 401 会顺手清会话, 于是**怎么登都进不去**.
 *
 * 断言"请求离开时带了什么头"而不是"事后 store 里是什么": 后者在两种实现下都对——`flush: 'pre'`
 * 只是晚一个微任务, `await login()` 之后再查一定是好的. 只有抓请求那一刻才看得见差别.
 *
 * 本文件是全仓第一个 store 用例 (`environment: node`), 故 `window` 与 `fetch` 都现桩:
 * Pinia 与 Vue 的响应式在 node 下无需 DOM, store 只用到 `localStorage` 这一样浏览器 API.
 */

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchCurrentUser, login } from '../api/auth';
import { setAccessToken } from '../api/client';
import type { User } from '../api/types';
import { ACCESS_TOKEN_STORAGE_KEY, useSessionStore } from './session';

const TEST_TOKEN = 'test-access-token';
/** 口令只是个占位串: 端点被打了桩, 没有任何一处会去校验它. */
const TEST_PASSWORD = 'placeholder-password';
const ADMIN_USER: User = {
  id: '11111111-1111-1111-1111-111111111111',
  username: 'admin',
  display_name: '管理员',
  user_type: 'admin',
  status: 'active',
  created_at: '2026-09-26T00:00:00',
};

let storedValues: Map<string, string>;
let sentAuthorizations: (string | undefined)[];

function stubBrowserStorage(): void {
  storedValues = new Map();

  vi.stubGlobal('window', {
    localStorage: {
      getItem: (key: string): string | null => storedValues.get(key) ?? null,
      setItem: (key: string, value: string): void => void storedValues.set(key, value),
      removeItem: (key: string): void => void storedValues.delete(key),
    },
  });
}

/** 记下每个请求实际带出去的 `Authorization`, 并把 `/auth/login` 与 `/auth/me` 都答成 200. */
function stubFetch(): void {
  sentAuthorizations = [];

  vi.stubGlobal('fetch', (input: string, init?: RequestInit): Promise<Response> => {
    const headers = init?.headers as Record<string, string> | undefined;
    sentAuthorizations.push(headers?.Authorization);

    const responseBody = input.endsWith('/auth/login')
      ? { access_token: TEST_TOKEN, token_type: 'bearer' }
      : ADMIN_USER;

    return Promise.resolve(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
}

beforeEach(() => {
  stubBrowserStorage();
  stubFetch();
  setAccessToken(null);
  setActivePinia(createPinia());
});

describe('login', () => {
  it('把令牌带到紧随其后的 /auth/me 上', async () => {
    const session = useSessionStore();

    await session.login('admin', TEST_PASSWORD);

    expect(sentAuthorizations[0]).toBeUndefined();
    expect(sentAuthorizations[1]).toBe(`Bearer ${TEST_TOKEN}`);
    expect(session.currentUser?.username).toBe('admin');
  });

  it('把令牌落进 localStorage, 刷新页面后才还登着', async () => {
    const session = useSessionStore();

    await session.login('admin', TEST_PASSWORD);

    expect(storedValues.get(ACCESS_TOKEN_STORAGE_KEY)).toBe(TEST_TOKEN);
    expect(session.isAuthenticated).toBe(true);
  });
});

describe('从 localStorage 恢复会话', () => {
  it('令牌在 store 建好那一刻就已推给 client', async () => {
    storedValues.set(ACCESS_TOKEN_STORAGE_KEY, TEST_TOKEN);

    const session = useSessionStore();

    expect(session.isAuthenticated).toBe(true);

    // 恢复路径本是同步的 (`immediate: true` 当场就跑), 故刷新页面不中招——这正是上面那个
    // bug 只在"刚点完登录"时露头的原因.
    await fetchCurrentUser();

    expect(sentAuthorizations[0]).toBe(`Bearer ${TEST_TOKEN}`);
  });

  it('退出登录后请求不再带令牌', async () => {
    storedValues.set(ACCESS_TOKEN_STORAGE_KEY, TEST_TOKEN);

    const session = useSessionStore();

    session.clear();
    await fetchCurrentUser();

    expect(sentAuthorizations[0]).toBeUndefined();
    expect(storedValues.has(ACCESS_TOKEN_STORAGE_KEY)).toBe(false);
  });
});

describe('login 与 api/auth 的关系', () => {
  it('直接调 api/auth.login 不会自己带上令牌 (它不该知道令牌)', async () => {
    await login('admin', TEST_PASSWORD);

    expect(sentAuthorizations[0]).toBeUndefined();
  });
});
