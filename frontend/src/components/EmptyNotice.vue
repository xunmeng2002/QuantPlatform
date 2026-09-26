<script setup lang="ts">
/**
 * 空列表提示.
 *
 * 「一条都没有」与「筛掉了全部」要给不同的说法: 同一个空表格配一句「暂无数据」时, 用户分不清
 * 是自己筛错了还是真没有, 于是反复改筛选条件.
 *
 * 两行都放进 el-empty 的 `description` 插槽: 它的**默认**插槽渲染在描述下方那块 (__bottom) 里,
 * 是第三个层级, 而这两行本来就是同一句话的主句与注解. `image-size` 是把默认插画压小 —— 它自带
 * 的那张尺寸偏大, 一个空态不该占掉半屏.
 */

import { ElEmpty } from 'element-plus';

withDefaults(
  defineProps<{
    message: string;
    hint?: string;
  }>(),
  { hint: '' },
);
</script>

<template>
  <ElEmpty :image-size="72">
    <template #description>
      <p class="text-sm text-slate-600">{{ message }}</p>
      <p v-if="hint" class="mt-1 text-xs text-slate-400">{{ hint }}</p>
    </template>
  </ElEmpty>
</template>
