<script setup lang="ts">
/**
 * 登录.
 *
 * 失败文案直接用后端回的原文 (「用户名或口令不正确」): 后端刻意不区分"用户不存在"与"口令错",
 * 前端若自作聪明地换成更具体的说法, 等于把那层防用户名枚举的努力还回去.
 */

import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { ApiError } from '../api/client';
import { MAXIMUM_USERNAME_LENGTH } from '../api/types';
import ErrorBanner from '../components/ErrorBanner.vue';
import { useSessionStore } from '../stores/session';

const route = useRoute();
const router = useRouter();
const session = useSessionStore();

const username = ref('');
const password = ref('');
const errorMessage = ref<string | null>(null);
const isSubmitting = ref(false);

const isSubmitDisabled = computed(
  () => isSubmitting.value || !username.value.trim() || !password.value,
);

/**
 * 只接受站内路径.
 *
 * 不校验的话 `?redirect=https://example.com` 会被当成一个路由跳过去, 于是登录页成了一个跳板.
 * 双斜杠也要挡: `//example.com` 在浏览器里是协议相对的绝对地址.
 */
const redirectTarget = computed(() => {
  const requestedTarget = route.query.redirect;

  if (typeof requestedTarget !== 'string' || !requestedTarget.startsWith('/')) {
    return null;
  }

  return requestedTarget.startsWith('//') ? null : requestedTarget;
});

async function submit(): Promise<void> {
  if (isSubmitDisabled.value) {
    return;
  }

  errorMessage.value = null;
  isSubmitting.value = true;

  try {
    // 口令不做 trim: 首尾空格是口令的一部分, 后端也不去.
    await session.login(username.value.trim(), password.value);
    await router.push(redirectTarget.value ?? { name: 'runs' });
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.detail : '登录失败';
    password.value = '';
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <div class="mx-auto mt-16 max-w-sm rounded-lg border border-line bg-surface p-6 shadow-sm">
    <h1 class="text-lg font-semibold text-slate-900">量化回测平台</h1>
    <p class="mt-1 text-sm text-slate-500">请登录后继续</p>

    <form
      class="mt-6 space-y-4"
      @submit.prevent="submit"
    >
      <ErrorBanner
        :message="errorMessage"
        :is-retry-visible="false"
      />

      <div class="flex flex-col gap-1">
        <label
          class="text-sm font-medium text-slate-700"
          for="login-username"
        >用户名</label>
        <input
          id="login-username"
          v-model="username"
          type="text"
          autocomplete="username"
          class="rounded border border-line px-2 py-1.5 text-sm"
          :maxlength="MAXIMUM_USERNAME_LENGTH"
        >
      </div>

      <div class="flex flex-col gap-1">
        <label
          class="text-sm font-medium text-slate-700"
          for="login-password"
        >口令</label>
        <input
          id="login-password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          class="rounded border border-line px-2 py-1.5 text-sm"
        >
      </div>

      <button
        type="submit"
        class="w-full rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong disabled:opacity-50"
        :disabled="isSubmitDisabled"
      >
        {{ isSubmitting ? '登录中…' : '登录' }}
      </button>
    </form>
  </div>
</template>
