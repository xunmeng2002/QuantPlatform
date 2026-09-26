// @vitest-environment jsdom
/**
 * 外壳与路由出口: **页面切换之后, 内容区还得在**.
 *
 * 钉的是一个真实发生过的故障: `<Transition mode="out-in">` 的孩子若不是单个元素 (被路由的组件渲染成
 * 片段 —— 模板根节点前面写一行注释就会这样, dev 保留注释而 prod 剥掉), 过渡的状态机就再也配不上,
 * 表现是**切一次页面之后内容区永久空白, 按 F5 才回来** —— 而且没有任何报错, 首屏也正常 (首屏不走
 * 过渡), 所以「打开就行、一切就白」这种症状很难从代码上看出来. 用户就是这样报的:
 * 「切换登录状态后页面不显示出来, 要按 F5 才显示」——因为登录与退出登录都要经过 `/login`.
 *
 * 断言放在 `main` 的文字上, 而不是 `wrapper.html()`: 空白时 `main` 里只剩一个注释节点, `html()` 是
 * `<!---->` 这种**非空字符串**, 断言「非空」会假绿.
 *
 * 用真的路由单例与真的 store (只 stub 掉 `scrollTo`): RouterView 与守卫读的都是它们, 手抄一份最小
 * 路由表就造出了第二个真相. 与 `components/AppLayout.spec.ts` 同一个 pinia 层面的注意点 —— 路由单例
 * 第一次被 mount 装进某个 app 之后, 守卫一律在那个 app 的注入上下文里取 store, 所以整个文件共用一个
 * pinia, 每个用例只清会话.
 *
 * 过渡要**显式排空**: `mode="out-in"` 先离场再进场, 各占一帧; jsdom 里 CSS 过渡时长为 0, 但进出场仍
 * 排在 rAF 上, 只 `await nextTick()` 会在"离场完成、进场未开始"那一拍上做断言.
 *
 * **`stubs: { transition: false }` 不能省**: `@vue/test-utils` 默认把 `<Transition>` 换成一个
 * `transition-stub`, 于是这段测试跑的是一次**不会过渡**的切换 —— 本文件钉的正是过渡本身, 开着默认
 * stub 就成了假绿 (实测: 在修复前那份代码上, 带 stub 的两个用例全过, 关掉 stub 第三个断言立刻失败).
 *
 * 触发条件比"组件渲染成片段"更具体一点: 是**离开**那种页面时才出事 —— 实测进片段根那一页正常, 从它
 * 离开之后内容区变 `<!---->` 并且再也回不来. 用户的路径正是这条: 登录页是片段根, 登录成功要离开它.
 */

import { mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { nextTick } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App.vue';
import { router } from './router';
import { useSessionStore } from './stores/session';

const pinia = createPinia();
setActivePinia(pinia);

/** 先离场再进场, 各要一帧; 多跑两轮让两段都落定. */
async function settlePageTransition(): Promise<void> {
  for (let round = 0; round < 3; round += 1) {
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => resolve());
    });
    await nextTick();
  }
}

/** 挂上根组件, 并把一个地址走完 (先热身到 `/__reset`, 免得 push 到当前地址时不跑守卫). */
async function mountAppOn(routeName: string): Promise<VueWrapper> {
  await router.replace('/__reset');
  await router.push({ name: routeName });

  const wrapper = mount(App, {
    global: { plugins: [pinia, router], stubs: { transition: false } },
    attachTo: document.body,
  });
  await settlePageTransition();

  return wrapper;
}

beforeEach(() => {
  window.localStorage.clear();
  useSessionStore().clear();
  // jsdom 没实现 window.scrollTo (路由的 scrollBehavior 会调它), 打桩只为不让它刷一行 stderr.
  vi.stubGlobal('scrollTo', () => undefined);
});

describe('页面切换之后内容区还在', () => {
  it('主页 -> 登录页 -> 主页, 每一步都能看到目标页的内容', async () => {
    const wrapper = await mountAppOn('home');
    expect(wrapper.get('main').text()).toContain('薪火量化');

    await router.push({ name: 'login' });
    await settlePageTransition();
    expect(wrapper.get('main').text()).toContain('请登录后继续');

    await router.push({ name: 'home' });
    await settlePageTransition();
    expect(wrapper.get('main').text()).toContain('多用户的量化回测平台');
  });

  it('切到登录页时顶栏跟着消失, 切回主页时跟着回来', async () => {
    // 判据取 `nav` 而不是 `header` 也不是文字: 页面自己的 `PageHeader` 根节点**也是** `<header>`,
    // 而登录页的页头标题恰好也叫「薪火量化」—— 两个字符判断在这里都会认错人. `nav` 只有外壳有.
    const wrapper = await mountAppOn('home');
    expect(wrapper.find('nav').exists()).toBe(true);

    await router.push({ name: 'login' });
    await settlePageTransition();
    expect(wrapper.find('nav').exists()).toBe(false);

    await router.push({ name: 'home' });
    await settlePageTransition();
    expect(wrapper.find('nav').exists()).toBe(true);
  });
});
