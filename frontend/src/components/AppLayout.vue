<script setup lang="ts">
/**
 * 页面外壳: 顶栏 + 内容区.
 *
 * 顶栏在 `meta.isPublic` 的页面 (登录页、404 页) 上整个不渲染: 那里既没有身份可显示, 也没有
 * 可以安全点过去的链接.
 */

import { computed } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';

import { useSessionStore } from '../stores/session';

const route = useRoute();
const router = useRouter();
const session = useSessionStore();

interface NavigationLink {
  routeName: string;
  label: string;
}

const isHeaderVisible = computed(() => route.meta.isPublic !== true);

/** 用户管理是管理员专属, 故链接也跟着身份走 —— 普通用户看到一个点了会被弹回来的入口没有意义. */
const navigationLinks = computed<NavigationLink[]>(() => {
  const links: NavigationLink[] = [
    { routeName: 'runs', label: '回测运行' },
    { routeName: 'strategies', label: '策略' },
  ];

  if (session.isAdmin) {
    links.push({ routeName: 'users', label: '用户管理' });
  }

  return links;
});

async function signOut(): Promise<void> {
  session.clear();
  await router.push({ name: 'login' });
}
</script>

<template>
  <div class="min-h-screen bg-page text-slate-800">
    <header
      v-if="isHeaderVisible"
      class="border-b border-line bg-surface"
    >
      <div class="mx-auto flex max-w-6xl flex-wrap items-center gap-4 px-4 py-3">
        <span class="text-sm font-semibold text-slate-900">量化回测平台</span>

        <nav class="flex flex-1 gap-1">
          <RouterLink
            v-for="link in navigationLinks"
            :key="link.routeName"
            :to="{ name: link.routeName }"
            class="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
            active-class="bg-slate-100 font-medium text-brand"
          >
            {{ link.label }}
          </RouterLink>
        </nav>

        <span class="text-sm text-slate-500">{{ session.displayName }}</span>
        <button
          type="button"
          class="rounded border border-line px-3 py-1.5 text-sm hover:bg-slate-50"
          @click="signOut"
        >
          退出登录
        </button>
      </div>
    </header>

    <main class="mx-auto max-w-6xl px-4 py-6">
      <slot />
    </main>
  </div>
</template>
