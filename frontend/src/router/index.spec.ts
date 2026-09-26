// @vitest-environment jsdom
/**
 * 路由表与登录守卫.
 *
 * 用**真的路由单例** (`import { router } from './index'`), 不在用例里另抄一份最小路由表: 手抄的那份
 * 是第二个真相, 路由表一改就漂, 而它恰好是这里唯一要钉的东西.
 *
 * 两条装配上的坑, 都在这里踩实了:
 *
 * 1. `setActivePinia` 必须先于任何导航 —— 守卫第一句就是 `useSessionStore()`, 没有 active pinia
 *    时它当场抛异常, 而异常会被当成"导航失败"而不是"用例写错了".
 * 2. 每个用例都先 `replace('/__reset')` 再 push 目标. `push` 到**当前所在地址**时 vue-router 直接
 *    返回 `NAVIGATION_DUPLICATED`, **守卫根本不跑**, 断言于是全绿 —— 这是本仓最贵的一种假绿.
 *    `/__reset` 落在通配的 404 上 (公开且不挂外壳), 因此这次热身不会发请求、也不会被弹走.
 *
 * 令牌写入 localStorage 必须在 store 建好**之前**: `accessToken = ref(readStoredAccessToken())` 是
 * store 创建那一刻读的, 而 store 是守卫在导航中创建的.
 *
 * 这里刻意**不**照抄 `stores/session.spec.ts` 里那段 `vi.stubGlobal('window', {...})`: 那是为了让
 * node 环境长出 localStorage, 而 jsdom 下有真的 `window.localStorage`, 照抄会把真的换掉.
 */

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { User } from '../api/types';
import { ACCESS_TOKEN_STORAGE_KEY, useSessionStore } from '../stores/session';
import { router } from './index';

const TEST_TOKEN = 'test-access-token';
const ADMIN_USER: User = {
  id: '11111111-1111-1111-1111-111111111111',
  username: 'admin',
  display_name: '管理员',
  user_type: 'admin',
  status: 'active',
  created_at: '2026-09-26T00:00:00',
};

const JSON_HEADERS = { 'Content-Type': 'application/json' };

let requestedPaths: string[];
/** `null` 表示 `/auth/me` 回 401 (令牌过期或后端不可达), 否则回这个用户. */
let currentUserResponse: User | null;

function stubFetch(): void {
  requestedPaths = [];

  vi.stubGlobal('fetch', (input: string): Promise<Response> => {
    requestedPaths.push(String(input));

    if (currentUserResponse === null) {
      return Promise.resolve(
        new Response(JSON.stringify({ detail: '登录状态已失效, 请重新登录' }), {
          status: 401,
          headers: JSON_HEADERS,
        }),
      );
    }

    return Promise.resolve(
      new Response(JSON.stringify(currentUserResponse), { status: 200, headers: JSON_HEADERS }),
    );
  });
}

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  currentUserResponse = ADMIN_USER;
  stubFetch();
  // jsdom 没实现 window.scrollTo (路由的 scrollBehavior 会调它), 打桩只为不让它刷一行 stderr.
  vi.stubGlobal('scrollTo', () => undefined);
});

describe('路由表', () => {
  it('根路径是主页, 且不需要登录', () => {
    const resolved = router.resolve('/');

    expect(resolved.name).toBe('home');
    expect(resolved.meta.isPublic).toBe(true);
    // 主页**要**顶栏: 访客从那里登录. `isPublic` 与"不挂外壳"是两件事.
    expect(resolved.meta.hidesHeader).toBeUndefined();
  });

  it('乱地址仍落在 404 上 (通配没有被主页的静态段吃掉)', () => {
    expect(router.resolve('/nope').name).toBe('not-found');
    expect(router.resolve('/nope').meta.hidesHeader).toBe(true);
  });

  it('登录页与 404 不挂外壳, 其余页面都不带这个标志', () => {
    expect(router.resolve('/login').meta.hidesHeader).toBe(true);

    for (const path of ['/', '/runs', '/strategies', '/users']) {
      expect(router.resolve(path).meta.hidesHeader).toBeUndefined();
    }
  });
});

describe('未登录时的守卫', () => {
  beforeEach(async () => {
    await router.replace('/__reset');
  });

  it('公开的主页放行, 且不为它去取身份', async () => {
    await router.push({ name: 'home' });

    expect(router.currentRoute.value.name).toBe('home');
    expect(requestedPaths).toEqual([]);
  });

  it('受保护页弹到登录页, 并带上原地址', async () => {
    await router.push({ name: 'runs' });

    expect(router.currentRoute.value.name).toBe('login');
    expect(router.currentRoute.value.query.redirect).toBe('/runs');
  });
});

describe('有令牌但还没取到用户时的守卫', () => {
  it('停在主页会补一次身份 (否则顶栏是空的, 主页还在问要不要登录)', async () => {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, TEST_TOKEN);
    await router.replace('/__reset');

    await router.push({ name: 'home' });

    expect(requestedPaths).toEqual(['/api/auth/me']);
    expect(useSessionStore().hasCurrentUser).toBe(true);
  });

  it('登录页与 404 不补身份 (它们不挂外壳, 没有消费者)', async () => {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, TEST_TOKEN);

    await router.replace('/__reset');
    await router.push({ name: 'login' });

    expect(requestedPaths).toEqual([]);
  });

  it('补身份失败时仍停在主页, 并清掉已经不作数的令牌', async () => {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, TEST_TOKEN);
    currentUserResponse = null;
    await router.replace('/__reset');

    await router.push({ name: 'home' });

    expect(requestedPaths).toEqual(['/api/auth/me']);
    expect(router.currentRoute.value.name).toBe('home');
    expect(useSessionStore().isAuthenticated).toBe(false);
  });

  it('补身份失败时受保护页仍回登录页', async () => {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, TEST_TOKEN);
    currentUserResponse = null;
    await router.replace('/__reset');

    await router.push({ name: 'runs' });

    expect(router.currentRoute.value.name).toBe('login');
    expect(router.currentRoute.value.query.redirect).toBe('/runs');
  });
});
