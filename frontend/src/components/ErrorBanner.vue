<script setup lang="ts">
/**
 * 失败提示条.
 *
 * 每条消息都可以带一个「重试」: 本项目的失败几乎都是「点一下就好了」(令牌过期后重登、后端刚
 * 起来), 让用户自己去猜重进哪个页面是最没必要的摩擦.
 */

defineProps<{
  message: string | null;
  retryLabel?: string;
  isRetryVisible?: boolean;
}>();

const emit = defineEmits<{
  retry: [];
}>();
</script>

<template>
  <div
    v-if="message"
    class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-800"
    role="alert"
  >
    <span>{{ message }}</span>
    <button
      v-if="isRetryVisible !== false"
      type="button"
      class="rounded border border-rose-400 px-2 py-1 text-xs font-medium hover:bg-rose-100"
      @click="emit('retry')"
    >
      {{ retryLabel ?? '重试' }}
    </button>
  </div>
</template>
