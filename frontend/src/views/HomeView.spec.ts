// @vitest-environment jsdom
/**
 * 主页的两态入口.
 *
 * 这一页是公开的, 于是"访客"与"已登录"必须**看起来是两回事**: 访客看到「登录」, 已登录看到「进入回测
 * 运行」. 判据是 `session.hasCurrentUser` (拿到用户) 而不是 `isAuthenticated` (只看到令牌) —— 只有
 * 令牌时显示名与角色都画不出来, 那一刻把人当已登录, 顶栏就会是空的.
 *
 * 静态文案 (副标题、能力卡、边界卡) 刻意**不测**: 测它等于把文案抄一份进测试, 改文案要改两处.
 * 这里只钉"哪一态渲染了哪个入口".
 *
 * **整个文件共用同一个 pinia**, 不是每个用例一个. 这不是偷懒, 是必须的: 路由 `router` 是模块级单例,
 * 第一次 `mount` 把它装到那个 app 上之后, **每一个**导航守卫都在那个 app 的注入上下文里跑
 * (`app.runWithContext`), 守卫里的 `useSessionStore()` 于是永远取到**第一个** app 的那个 pinia. 每个
 * 用例换一个新 pinia 的话, 守卫读的是旧 pinia 里的旧 store (令牌早就清掉了), 用例读的是新 store, 两边
 * 永远对不上 —— 症状极难认: 令牌明明在, 守卫就是不认, 而 `push` 本身还是成功的.
 *
 * 代价是 store 跨用例存活, 所以每个用例开头显式清一次会话 (见 `resetSession`), 那才是"重新打开
 * 一个页面".
 */

import { mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { User } from '../api/types';
import { router } from '../router';
import { useSessionStore } from '../stores/session';
import HomeView from './HomeView.vue';

const TEST_TOKEN = 'test-access-token';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

const REGULAR_USER: User = {
  id: '22222222-2222-2222-2222-222222222222',
  username: 'trader',
  display_name: '李四',
  user_type: 'user',
  status: 'active',
  created_at: '2026-09-26T00:00:00',
};

/** 访客态独有的一句副文案, 与已登录态的那条入口互为"另一态没漏进来"的判据. */
const GUEST_ONLY_COPY = '账号由管理员开通';
const SIGNED_IN_ENTRY = '进入回测运行';

const pinia = createPinia();
setActivePinia(pinia);

function stubFetch(): void {
  vi.stubGlobal('fetch', (): Promise<Response> =>
    Promise.resolve(
      new Response(JSON.stringify(REGULAR_USER), { status: 200, headers: JSON_HEADERS }),
    ),
  );
}

/** 「重新打开一个页面」: 存储与会话都清空. store 跨用例存活, 这一步不能省. */
function resetSession(): void {
  window.localStorage.clear();
  useSessionStore().clear();
}

async function enterHome(): Promise<VueWrapper> {
  // 先热身到另一个地址: push 到**当前**地址时 vue-router 直接返回 `NAVIGATION_DUPLICATED`,
  // 守卫根本不跑, 于是"该补身份却没补"会假绿. `/__reset` 落在不挂外壳的 404 上, 不会发请求.
  await router.replace('/__reset');
  await router.push({ name: 'home' });

  return mount(HomeView, { global: { plugins: [pinia, router] } });
}

/**
 * 令牌是**直接赋给 store** 而不是写 localStorage: store 在这个文件里只建一次, 建的时候还没有令牌,
 * 它不会再去读存储. 赋值会经那个 `flush: 'sync'` 的 watch 立刻推给 client 并落进存储, 与真登录同路.
 */
async function enterHomeSignedIn(): Promise<VueWrapper> {
  useSessionStore().accessToken = TEST_TOKEN;

  return enterHome();
}

beforeEach(() => {
  stubFetch();
  // jsdom 没实现 window.scrollTo (路由的 scrollBehavior 会调它), 打桩只为不让它刷一行 stderr.
  vi.stubGlobal('scrollTo', () => undefined);
  resetSession();
});

describe('主视觉', () => {
  it('标题是全页唯一的 h1, 且就是产品名', async () => {
    const wrapper = await enterHome();

    expect(wrapper.findAll('h1')).toHaveLength(1);
    expect(wrapper.get('h1').text()).toBe('薪火量化');
  });
});

describe('入口的两态', () => {
  it('访客看到「登录」, 看不到工作入口', async () => {
    const pageText = (await enterHome()).text();

    expect(pageText).toContain('登录');
    expect(pageText).toContain(GUEST_ONLY_COPY);
    expect(pageText).not.toContain(SIGNED_IN_ENTRY);
  });

  it('已登录看到「进入回测运行」与「新建回测」, 不再问要不要登录', async () => {
    const pageText = (await enterHomeSignedIn()).text();

    expect(pageText).toContain(SIGNED_IN_ENTRY);
    expect(pageText).toContain('新建回测');
    expect(pageText).not.toContain(GUEST_ONLY_COPY);
  });
});
