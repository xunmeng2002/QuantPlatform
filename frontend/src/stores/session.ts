/**
 * 会话: 访问令牌与当前用户.
 *
 * 令牌**只在这里**落地到 localStorage, 再经下面那个 `watch` 单向推给 `api/client`. 两处各存
 * 一份是「某一处忘了更新」的经典产地: 刷新页面后 store 有令牌而 client 没有, 表现为「刚进去
 * 每一步都 401」. 那个 `watch` 因此带 `flush: 'sync'`——默认的 `pre` 会让两处在一整个微任务
 * 的窗口里不一致, 而窗口两端都出过真 bug (见该处的注释与 session.spec.ts).
 *
 * 没有 refresh 端点, 令牌过期后唯一的恢复路径就是重新登录.
 */

import { computed, ref, watch } from 'vue';
import { defineStore } from 'pinia';

import * as authApi from '../api/auth';
import { setAccessToken } from '../api/client';
import type { User } from '../api/types';

export const ACCESS_TOKEN_STORAGE_KEY = 'quantplatform.access-token';

export const useSessionStore = defineStore('session', () => {
  const accessToken = ref<string | null>(readStoredAccessToken());
  const currentUser = ref<User | null>(null);
  const isLoadingCurrentUser = ref(false);

  const isAuthenticated = computed(() => accessToken.value !== null);
  const isAdmin = computed(() => currentUser.value?.user_type === 'admin');
  const displayName = computed(() => currentUser.value?.display_name ?? '');

  /**
   * 「己方身份已就绪」. 与 `isAuthenticated` 的区别在于它要求用户真的取回来了 —— 只有令牌时
   * 显示名与角色都画不出来. 凡是要**渲染身份**的地方 (顶栏用户区, 主页的两态入口) 都读它,
   * 于是「半登录」这个状态在界面上不可能出现, 这条判断也只有一个出处.
   */
  const hasCurrentUser = computed(() => currentUser.value !== null);

  watch(
    accessToken,
    (token) => {
      setAccessToken(token);
      persistAccessToken(token);
    },
    // `flush: 'sync'` 是**载重**的, 不是随手加的性能选项. 默认的 `pre` 把回调排进微任务, 于是
    // 「store 的令牌」与「client 的令牌」之间有一整个微任务的窗口不一致: `login()` 里紧跟的
    // `/auth/me` 会赶在 `setAccessToken` 之前发出去 (不带 Authorization 头 → 后端 401
    // 「缺少访问令牌」→ client 拿到 401 顺手清会话 → 怎么登都进不去); `clear()` 之后同样有
    // 一个窗口把已作废的令牌继续带出去. 回归用例见同目录的 session.spec.ts.
    { immediate: true, flush: 'sync' },
  );

  async function login(username: string, password: string): Promise<void> {
    const tokenResponse = await authApi.login(username, password);

    accessToken.value = tokenResponse.access_token;
    currentUser.value = null;
    await loadCurrentUser();
  }

  /** 由路由守卫与登录后各调一次; 拿到用户后 `isAdmin` 才可信. */
  async function loadCurrentUser(): Promise<User> {
    isLoadingCurrentUser.value = true;

    try {
      const user = await authApi.fetchCurrentUser();
      currentUser.value = user;

      return user;
    } finally {
      isLoadingCurrentUser.value = false;
    }
  }

  /** 401 的全局处置与「退出登录」共用这一条: 两者都是「这份令牌不再作数」. */
  function clear(): void {
    accessToken.value = null;
    currentUser.value = null;
  }

  return {
    accessToken,
    currentUser,
    isLoadingCurrentUser,
    isAuthenticated,
    isAdmin,
    displayName,
    hasCurrentUser,
    login,
    loadCurrentUser,
    clear,
  };
});

/** 隐私模式或站点策略禁用 localStorage 时会抛异常: 当作没存过, 重新登录即可. */
function readStoredAccessToken(): string | null {
  try {
    return window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** 写不进去只影响「刷新页面后是否还登着」: 令牌已在内存里, 本次会话照常可用. */
function persistAccessToken(token: string | null): boolean {
  try {
    if (token === null) {
      window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    } else {
      window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token);
    }

    return true;
  } catch {
    return false;
  }
}
