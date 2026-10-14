<script setup lang="ts">
/**
 * 页面标题栏.
 *
 * 抽它的直接原因是那段类名在本批之前被复制了 6 处, 其中一处还**跨层**落在 `ErrorBanner.vue` 里
 * (一条提示条复用了页头的包裹层类名) —— 改页头间距要同时去改一个跟标题无关的组件.
 *
 * 三个插槽各有固定位置语义, 不是随手分的:
 *   - `leading`: 返回链接, 必须在标题**左侧**;
 *   - `badges`: 状态徽章, 跟在标题右侧同一行 (标题一行的宽度不够时整组折到下一行);
 *   - `actions`: 页面级动作, 靠右; 窄屏折行.
 */

defineProps<{
  title: string;
  description?: string;
}>();
</script>

<template>
  <header class="mb-4 flex flex-wrap items-center justify-between gap-3">
    <div class="flex min-w-0 flex-wrap items-center gap-2">
      <slot name="leading" />

      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="text-2xl font-semibold text-slate-900">{{ title }}</h1>
          <slot name="badges" />
        </div>

        <p
          v-if="description"
          class="mt-1 text-sm text-slate-500"
        >
          {{ description }}
        </p>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>
