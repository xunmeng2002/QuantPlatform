/**
 * 会话: 访问令牌与当前用户.
 *
 * 令牌**只在这里**落地到 localStorage, 再经下面那个 `watch` 单向推给 `api/client`. 两处各存
 * 一份是「某一处忘了更新」的经典产地: 刷新页面后 store 有令牌而 client 没有, 表现为「刚进去
 * 每一步都 401」.
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

  watch(
    accessToken,
    (token) => {
      setAccessToken(token);
      persistAccessToken(token);
    },
    { immediate: true },
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
