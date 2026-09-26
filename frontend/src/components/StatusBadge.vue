<script setup lang="ts">
/**
 * 状态徽章.
 *
 * 只认 `label` + `tone` 两个展示用的入参, 不认识任何枚举: 运行状态、引擎判定、账号状态、可见性
 * 都用它, 各自的中文与色调由 `domain/` 决定.
 *
 * 色调到表现有**两个**相邻的映射表: 底色与边框交给 el-tag 自己的 type, 文字色由下面那张 class
 * 表决定 —— 两者都只有这一处出处.
 *
 * 为什么要自己管文字色: el-tag 的色阶来自 EP 自己的调色板, 而它浅色底的文字对比度只有
 * 2.0-2.6:1 (本项目一直是 5.3-6.8:1). span 是我们自己的元素, EP 没有任何规则命中它, 所以这层
 * 覆盖既有效, 又不与组件库的样式打架.
 */

import { computed } from 'vue';
import { ElTag } from 'element-plus';

import type { StatusTone } from '../domain/run-status';

const props = defineProps<{
  label: string;
  tone: StatusTone;
}>();

type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger';

const TONE_TAG_TYPES: Record<StatusTone, TagType> = {
  neutral: 'info',
  progress: 'primary',
  success: 'success',
  danger: 'danger',
  warning: 'warning',
};

const TONE_TEXT_CLASSES: Record<StatusTone, string> = {
  neutral: 'text-slate-700',
  progress: 'text-blue-700',
  success: 'text-emerald-700',
  danger: 'text-rose-700',
  warning: 'text-amber-800',
};

const tagType = computed(() => TONE_TAG_TYPES[props.tone]);
const toneTextClass = computed(() => TONE_TEXT_CLASSES[props.tone]);
</script>

<template>
  <ElTag :type="tagType" size="small">
    <span class="font-medium whitespace-nowrap" :class="toneTextClass">{{ label }}</span>
  </ElTag>
</template>
