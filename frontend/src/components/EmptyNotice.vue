<script setup lang="ts">
/**
 * 空列表提示.
 *
 * 「一条都没有」与「筛掉了全部」要给不同的说法: 同一个空表格配一句「暂无数据」时, 用户分不清
 * 是自己筛错了还是真没有, 于是反复改筛选条件.
 */

withDefaults(
  defineProps<{
    message: string;
    hint?: string;
  }>(),
  { hint: '' },
);
</script>

<template>
  <div class="py-10 text-center">
    <p class="text-sm text-slate-600">{{ message }}</p>
    <p v-if="hint" class="mt-1 text-xs text-slate-400">{{ hint }}</p>
  </div>
</template>
