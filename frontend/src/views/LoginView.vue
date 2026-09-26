<script setup lang="ts">
/**
 * 登录.
 *
 * 失败文案直接用后端回的原文 (「用户名或口令不正确」): 后端刻意不区分"用户不存在"与"口令错",
 * 前端若自作聪明地换成更具体的说法, 等于把那层防用户名枚举的努力还回去.
 */

import { computed, ref } from 'vue';
import { ElButton, ElInput } from 'element-plus';
import { RouterLink, useRoute, useRouter } from 'vue-router';

import { MAXIMUM_USERNAME_LENGTH } from '../api/types';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import { describeApiFailure } from '../composables/use-feedback';
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
    errorMessage.value = describeApiFailure(error, '登录失败');
    password.value = '';
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <!-- 这是全站唯一"没有外壳"的页面 (登录页不挂顶栏), 所以它自己那张卡要窄 -->
  <SurfaceCard class="mx-auto mt-16 max-w-sm">
    <PageHeader
      title="薪火量化"
      description="请登录后继续"
    >
      <!-- 顶栏在这页不渲染, 主页也公开了: 没有这条链接, 从主页翻过来的人只能靠浏览器后退. -->
      <template #leading>
        <RouterLink
          class="text-sm text-brand hover:underline"
          :to="{ name: 'home' }"
        >
          ← 回到主页
        </RouterLink>
      </template>
    </PageHeader>

    <form
      class="space-y-4"
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
        <ElInput
          id="login-username"
          v-model="username"
          type="text"
          autocomplete="username"
          :maxlength="MAXIMUM_USERNAME_LENGTH"
        />
      </div>

      <div class="flex flex-col gap-1">
        <label
          class="text-sm font-medium text-slate-700"
          for="login-password"
        >口令</label>
        <ElInput
          id="login-password"
          v-model="password"
          type="password"
          autocomplete="current-password"
        />
      </div>

      <!-- :loading 之外文案也跟着换: 转圈不进可访问名, 读屏用户只能靠"登录中…"这三个字知道这次
           提交在飞 (与提交回测、上传表单同一处理). 按钮是 w-full, 换字不会让宽度跳一下. -->
      <ElButton
        class="w-full"
        type="primary"
        native-type="submit"
        :loading="isSubmitting"
        :disabled="isSubmitDisabled"
      >
        {{ isSubmitting ? '登录中…' : '登录' }}
      </ElButton>
    </form>
  </SurfaceCard>
</template>
