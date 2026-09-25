<script setup lang="ts">
/**
 * 状态徽章.
 *
 * 只认 `label` + `tone` 两个展示用的入参, 不认识任何枚举: 运行状态、引擎判定、账号状态、可见性
 * 都用它, 各自的中文与色调由 `domain/` 决定. 色调到色值的映射**只在这里**出现一次.
 */

import { computed } from 'vue';
import type { StatusTone } from '../domain/run-status';

const props = defineProps<{
  label: string;
  tone: StatusTone;
}>();

const TONE_CLASSES: Record<StatusTone, string> = {
  neutral: 'bg-slate-100 text-slate-700 ring-slate-300',
  progress: 'bg-blue-50 text-blue-700 ring-blue-300',
  success: 'bg-emerald-50 text-emerald-700 ring-emerald-300',
  danger: 'bg-rose-50 text-rose-700 ring-rose-300',
  warning: 'bg-amber-50 text-amber-800 ring-amber-300',
};

const toneClasses = computed(() => TONE_CLASSES[props.tone]);
</script>

<template>
  <span
    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset"
    :class="toneClasses"
  >
    {{ label }}
  </span>
</template>
