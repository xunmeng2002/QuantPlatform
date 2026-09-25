<script setup lang="ts">
/**
 * 分页条.
 *
 * 只看 `total` / `offset` / `limit` 三个数 (后端 `PageResponse` 恰好就是这三个), 不认识具体
 * 资源. 上一页 / 下一页之外不做跳页: 运行数上千之后才需要, 那时再说.
 */

import { computed } from 'vue';

const props = defineProps<{
  total: number;
  offset: number;
  limit: number;
}>();

const emit = defineEmits<{
  'update:offset': [offset: number];
}>();

const firstVisibleIndex = computed(() =>
  props.total === 0 ? 0 : props.offset + 1,
);
const lastVisibleIndex = computed(() =>
  Math.min(props.offset + props.limit, props.total),
);
const isPreviousDisabled = computed(() => props.offset <= 0);
const isNextDisabled = computed(() => props.offset + props.limit >= props.total);

function goToPreviousPage(): void {
  emit('update:offset', Math.max(0, props.offset - props.limit));
}

function goToNextPage(): void {
  emit('update:offset', props.offset + props.limit);
}
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-3 py-3 text-sm text-slate-600">
    <span>
      第 {{ firstVisibleIndex }}–{{ lastVisibleIndex }} 条, 共 {{ total }} 条
    </span>
    <div class="flex gap-2">
      <button
        type="button"
        class="rounded border border-line bg-surface px-3 py-1 font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="isPreviousDisabled"
        @click="goToPreviousPage"
      >
        上一页
      </button>
      <button
        type="button"
        class="rounded border border-line bg-surface px-3 py-1 font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="isNextDisabled"
        @click="goToNextPage"
      >
        下一页
      </button>
    </div>
  </div>
</template>
