<script setup lang="ts">
/** 加载中提示. 只在**首次**加载时说「加载中」, 轮询刷新不该把已有内容换成这三个字. */

withDefaults(defineProps<{ message?: string }>(), {
  message: '加载中…',
});
</script>

<template>
  <p class="py-8 text-center text-sm text-slate-500" role="status">{{ message }}</p>
</template>
