/**
 * 路由表与登录守卫.
 *
 * 页面组件一律动态导入 (`() => import(...)`): 首屏是主页, 登录页与其余页面按访问按需下载.
 *
 * 守卫是**唯一**的鉴权入口: 视图里不写「没登录就跳走」, 否则每加一个页面就要重复一次, 漏一处
 * 就是一处白屏.
 */

import { createRouter, createWebHistory } from 'vue-router';

import { useSessionStore } from '../stores/session';

declare module 'vue-router' {
  interface RouteMeta {
    /** 无需登录即可访问 (主页, 登录页与 404 页). 只管鉴权, 与外壳无关. */
    isPublic?: boolean;
    /**
     * 不渲染顶栏 (只有登录页与 404 页). 与 `isPublic` 相互独立: 主页公开但**要**顶栏
     * —— 访客要从那里登录, 而登录页 / 404 上既没有身份可显示, 也没有能安全点过去的去处.
     *
     * 只有两个消费者: `AppLayout` 拿它决定画不画顶栏; 守卫拿它决定要不要为这一页补一次身份
     * —— 不挂外壳的页面不需要这份身份, 也不该为此多付一次网络往返.
     *
     * 写成"抑制"而不是"显示": 新加一条公开放行的路由时, 漏写的后果是"多出一个顶栏" (肉眼可见),
     * 而不是静默地少掉导航.
     */
    hidesHeader?: boolean;
    /** 仅管理员; 非管理员访问时退回运行列表. */
    requiresAdmin?: boolean;
  }
}

export const router = createRouter({
  history: createWebHistory(),

  // 没有它时导航会沿用上一页的滚动位置 —— 从长列表底部点进详情页, 新页停在半空中. 页面过渡
  // (App.vue 的 `<Transition name="page" mode="out-in">`) 会让这件事更显眼: 离场那一拍里旧页
  // 已经不在 DOM, 高度塌到只剩顶栏, 浏览器顺手把滚动位置截到顶部, 于是"先跳一下再落回中间".
  // `savedPosition` 只在浏览器前进/后退时才有值, 那时恢复原位, 其余一律回到顶部.
  scrollBehavior: (_to, _from, savedPosition) => savedPosition ?? { top: 0 },
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('../views/HomeView.vue'),
      meta: { isPublic: true },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { isPublic: true, hidesHeader: true },
    },
    {
      path: '/runs',
      name: 'runs',
      component: () => import('../views/RunListView.vue'),
    },
    {
      path: '/runs/new',
      name: 'run-submit',
      component: () => import('../views/RunSubmitView.vue'),
    },
    {
      path: '/runs/:id',
      name: 'run-detail',
      component: () => import('../views/RunDetailView.vue'),
      props: true,
    },
    {
      // 顶层而不是 `/runs/compare`: 对比页与运行列表是**两个工作面** (一个是"看这次跑得怎么样", 一个
      // 是"看这几次差在哪"), 地址上不必套一层. 也因此与 `/runs/:id` 没有先后之争.
      path: '/compare',
      name: 'compare',
      component: () => import('../views/RunCompareView.vue'),
    },
    {
      path: '/strategies',
      name: 'strategies',
      component: () => import('../views/StrategyListView.vue'),
    },
    {
      path: '/strategies/:id',
      name: 'strategy-detail',
      component: () => import('../views/StrategyDetailView.vue'),
      props: true,
    },
    {
      path: '/reference-data',
      name: 'reference-data',
      component: () => import('../views/ReferenceDataView.vue'),
      meta: { requiresAdmin: true },
    },
    {
      path: '/users',
      name: 'users',
      component: () => import('../views/UserAdminView.vue'),
      meta: { requiresAdmin: true },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('../views/NotFoundView.vue'),
      meta: { isPublic: true, hidesHeader: true },
    },
  ],
});

router.beforeEach(async (to) => {
  const session = useSessionStore();
  const isPublicPage = to.meta.isPublic === true;

  if (!session.isAuthenticated) {
    return isPublicPage ? true : { name: 'login', query: { redirect: to.fullPath } };
  }

  // 有令牌但还没拿到用户时补一次: 顶栏要显示名与角色徽章, 而顶栏在**主页**上也渲染; 不补的话
  // 登录后刷新停在主页会看到"半登录"态 (顶栏空着, 主页还在问你要不要登录).
  // 判据取 `hidesHeader` 而不是 `isPublic`: 登录页与 404 不挂外壳, 没有消费者需要这份身份.
  if (to.meta.hidesHeader !== true && session.currentUser === null) {
    try {
      await session.loadCurrentUser();
    } catch {
      // 拿不到当前用户就没有身份, 而令牌此刻同样用不了 (过期, 或后端不可达). 先清掉 ——
      // 之后分两种情况: 公开页**不跳**, 把人从公开的主页弹到登录页会让"公开"名不副实;
      // 受保护页回登录页, 否则每个页面各自报一次错, 而它们报的都是同一件事.
      session.clear();

      return isPublicPage ? true : { name: 'login', query: { redirect: to.fullPath } };
    }
  }

  if (to.meta.requiresAdmin && !session.isAdmin) {
    return { name: 'runs' };
  }

  return true;
});
