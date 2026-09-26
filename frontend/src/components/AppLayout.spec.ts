// @vitest-environment jsdom
/**
 * 页面外壳: 顶栏画不画, 以及右上角那块"用户区"到底显示什么.
 *
 * 用户区这条断言钉的是一个**真实存在的错误态**: 判据若取 `isAuthenticated` (只看令牌), 公开主页上的
 * 访客会看到 `?` 头像 + 空名字, 旁边还挂一个点了只会跳登录页的「退出登录」. 主页一公开, 这个错误态
 * 就在第一屏上. 因此用例同时钉"有登录按钮"与"没有退出登录、没有那个 `?` 圆圈".
 *
 * 用真的路由单例与真的 store, 只在 fetch 上打桩: 组件读的是 `route.meta.hidesHeader` 与
 * `session.hasCurrentUser`, 抄一份最小路由表就造出了第二个真相.
 *
 * **整个文件共用同一个 pinia** —— 理由见 `views/HomeView.spec.ts` 的文件头: 路由单例第一次被 `mount`
 * 装进某个 app 之后, 守卫一律在那个 app 的注入上下文里取 store, 每个用例换新 pinia 会让守卫读到旧
 * store. 代价是 store 跨用例存活, 所以每个用例开头显式清一次会话.
 *
 * 每个用例先 `replace('/__reset')` 再 push 目标: push 到**当前**地址时 vue-router 直接返回
 * `NAVIGATION_DUPLICATED`, 守卫不跑, 于是"令牌在但用户没取回来"这种状态会假绿.
 * 反过来, `/__reset` 落在**不挂外壳**的 404 上, 因此它只当热身用, 断言一律在 push 到目标之后.
 */

import { mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { User } from '../api/types';
import { router } from '../router';
import { useSessionStore } from '../stores/session';
import AppLayout from './AppLayout.vue';

const TEST_TOKEN = 'test-access-token';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** 显示名刻意不含"管理员"三个字: 顶栏那个角色徽章的文字正是它, 混在一起就分不清是谁在渲染. */
const ADMIN_USER: User = {
  id: '11111111-1111-1111-1111-111111111111',
  username: 'admin',
  display_name: '张三',
  user_type: 'admin',
  status: 'active',
  created_at: '2026-09-26T00:00:00',
};

const REGULAR_USER: User = {
  id: '22222222-2222-2222-2222-222222222222',
  username: 'trader',
  display_name: '李四',
  user_type: 'user',
  status: 'active',
  created_at: '2026-09-26T00:00:00',
};

const pinia = createPinia();
setActivePinia(pinia);

let currentUserResponse: User;

function stubFetch(): void {
  vi.stubGlobal('fetch', (): Promise<Response> =>
    Promise.resolve(
      new Response(JSON.stringify(currentUserResponse), { status: 200, headers: JSON_HEADERS }),
    ),
  );
}

/** 「重新打开一个页面」: 存储与会话都清空. store 跨用例存活, 这一步不能省. */
function resetSession(): void {
  window.localStorage.clear();
  useSessionStore().clear();
}

/**
 * 令牌是**直接赋给 store** 而不是写 localStorage: store 在这个文件里只建一次, 建的时候还没有令牌,
 * 它不会再去读存储. 赋值会经那个 `flush: 'sync'` 的 watch 立刻推给 client 并落进存储, 与真登录同路.
 */
async function enterHomeAs(user: User): Promise<void> {
  currentUserResponse = user;
  useSessionStore().accessToken = TEST_TOKEN;

  await router.replace('/__reset');
  await router.push({ name: 'home' });
}

function mountLayout(): VueWrapper {
  return mount(AppLayout, { global: { plugins: [pinia, router] } });
}

async function enterRoute(routeName: string): Promise<void> {
  await router.replace('/__reset');
  await router.push({ name: routeName });
}

beforeEach(() => {
  currentUserResponse = ADMIN_USER;
  stubFetch();
  // jsdom 没实现 window.scrollTo (路由的 scrollBehavior 会调它), 打桩只为不让它刷一行 stderr.
  vi.stubGlobal('scrollTo', () => undefined);
  resetSession();
});

describe('顶栏的显隐', () => {
  it('主页上渲染顶栏 (访客要有地方点登录)', async () => {
    await enterRoute('home');

    expect(mountLayout().find('header').exists()).toBe(true);
  });

  it('登录页上不渲染顶栏', async () => {
    await enterRoute('login');

    expect(mountLayout().find('header').exists()).toBe(false);
  });
});

describe('访客态的用户区', () => {
  beforeEach(async () => {
    await enterRoute('home');
  });

  it('是一个「登录」按钮, 不是空名字加一个 `?` 头像', () => {
    const wrapper = mountLayout();
    const headerText = wrapper.get('header').text();

    expect(headerText).toContain('登录');
    expect(headerText).not.toContain('退出登录');
    expect(wrapper.find('.rounded-full').exists()).toBe(false);
  });

  it('登录按钮是真锚点, 中键能开新标签页', () => {
    const loginLink = mountLayout().find('header a[href="/login"]');

    expect(loginLink.exists()).toBe(true);
    expect(loginLink.text()).toBe('登录');
  });

  it('品牌区指回主页', () => {
    expect(mountLayout().find('header a[href="/"]').exists()).toBe(true);
  });
});

describe('已登录态的用户区', () => {
  it('显示显示名与「退出登录」', async () => {
    await enterHomeAs(REGULAR_USER);

    const wrapper = mountLayout();
    const headerText = wrapper.get('header').text();

    expect(headerText).toContain('李四');
    expect(headerText).toContain('退出登录');
    expect(wrapper.find('.rounded-full').exists()).toBe(true);
  });

  it('普通用户不挂角色徽章', async () => {
    await enterHomeAs(REGULAR_USER);

    expect(mountLayout().get('header').text()).not.toContain('管理员');
  });

  it('管理员挂角色徽章', async () => {
    await enterHomeAs(ADMIN_USER);

    const headerText = mountLayout().get('header').text();

    expect(headerText).toContain('张三');
    expect(headerText).toContain('管理员');
  });
});

describe('导航项', () => {
  it('普通用户看不到「用户管理」', async () => {
    await enterHomeAs(REGULAR_USER);

    const navigationText = mountLayout().get('nav').text();

    expect(navigationText).toContain('回测运行');
    expect(navigationText).toContain('策略');
    expect(navigationText).not.toContain('用户管理');
  });

  it('管理员看得到「用户管理」', async () => {
    await enterHomeAs(ADMIN_USER);

    expect(mountLayout().get('nav').text()).toContain('用户管理');
  });
});
