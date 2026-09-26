/**
 * 路由表与登录守卫.
 *
 * 页面组件一律动态导入 (`() => import(...)`): 首屏只有登录页, 其余页面按访问按需下载.
 *
 * 守卫是**唯一**的鉴权入口: 视图里不写「没登录就跳走」, 否则每加一个页面就要重复一次, 漏一处
 * 就是一处白屏.
 */

import { createRouter, createWebHistory } from 'vue-router';

import { useSessionStore } from '../stores/session';

declare module 'vue-router' {
  interface RouteMeta {
    /** 无需登录即可访问 (登录页与 404 页). */
    isPublic?: boolean;
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
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { isPublic: true },
    },
    { path: '/', redirect: { name: 'runs' } },
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
      path: '/users',
      name: 'users',
      component: () => import('../views/UserAdminView.vue'),
      meta: { requiresAdmin: true },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('../views/NotFoundView.vue'),
      meta: { isPublic: true },
    },
  ],
});

router.beforeEach(async (to) => {
  const session = useSessionStore();

  if (to.meta.isPublic) {
    return true;
  }

  if (!session.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } };
  }

  if (session.currentUser === null) {
    try {
      await session.loadCurrentUser();
    } catch {
      // 拿不到当前用户就没有身份, 而令牌此刻同样用不了 (过期, 或后端不可达). 清掉回登录页:
      // 停在原地只会让每个页面各自报一次错, 而它们报的都是同一件事.
      session.clear();

      return { name: 'login', query: { redirect: to.fullPath } };
    }
  }

  if (to.meta.requiresAdmin && !session.isAdmin) {
    return { name: 'runs' };
  }

  return true;
});
