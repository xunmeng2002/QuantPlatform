/**
 * 组装根: 装插件、把 401 的全局处置接上、挂载.
 *
 * `client.ts` 刻意不 import router / store (会造出循环依赖), 于是「401 之后做什么」由这里注入.
 * 这是全项目**唯一**一处把 api 层与路由/状态层接在一起的地方.
 */

import { createApp } from 'vue';
import { createPinia } from 'pinia';

import App from './App.vue';
import { configureUnauthorizedHandler } from './api/client';
import { router } from './router';
import { useSessionStore } from './stores/session';
import './style.css';

const app = createApp(App);

app.use(createPinia());
app.use(router);

/** 令牌失效时清会话并回登录页; 已在公开页 (登录页 / 404) 上就不再跳一次. */
function handleUnauthorized(): void {
  const session = useSessionStore();
  const currentRoute = router.currentRoute.value;

  session.clear();

  if (currentRoute.meta.isPublic === true) {
    return;
  }

  router
    .push({ name: 'login', query: { redirect: currentRoute.fullPath } })
    .catch((error: unknown) => {
      // 跳转本身被拒 (导航被拦截) 不影响已清掉的会话: 下一个受保护页面照样把人送回登录页.
      console.error('登录失效后跳转登录页失败', error);
    });
}

/** 组件内未捕获的异常. P4 没有日志上报通道, 控制台是唯一的落点 —— 出问题时至少留得下痕迹. */
app.config.errorHandler = (error: unknown): void => {
  console.error('前端未捕获异常', error);
};

configureUnauthorizedHandler(handleUnauthorized);

app.mount('#app');
