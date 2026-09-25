<script setup lang="ts">
/**
 * 按 manifest 生成的参数表单.
 *
 * 本组件**不持有状态**: 原始输入由父页面拿着 (提交时要与日期、初始资金一起取用), 这里只负责
 * 分组渲染与把某一项的变化冒上去. 于是「哪些填错了」由父页面用纯函数算一次, 而不是散在控件里.
 */

import { computed } from 'vue';

import ParameterField from './ParameterField.vue';
import { groupParameterDescriptors } from '../domain/manifest';
import type { ParameterDescriptor, ParameterInput } from '../domain/manifest';

const props = defineProps<{
  descriptors: ParameterDescriptor[];
  modelValue: Record<string, ParameterInput>;
  errors: Record<string, string>;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, ParameterInput>];
}>();

const parameterGroups = computed(() =>
  groupParameterDescriptors(props.descriptors),
);

/** 显式工厂而不是模板里的内联箭头: 内联箭头的参数类型要靠上下文推断, 不必赌它推不推得出来. */
function createParameterUpdater(parameterKey: string) {
  return (value: ParameterInput): void => {
    emit('update:modelValue', { ...props.modelValue, [parameterKey]: value });
  };
}
</script>

<template>
  <div class="space-y-6">
    <fieldset
      v-for="parameterGroup in parameterGroups"
      :key="parameterGroup.group || 'ungrouped'"
      class="rounded-lg border border-line bg-surface p-4"
    >
      <legend
        v-if="parameterGroup.group"
        class="px-1 text-sm font-semibold text-slate-700"
      >
        {{ parameterGroup.group }}
      </legend>

      <div class="grid gap-4 sm:grid-cols-2">
        <ParameterField
          v-for="descriptor in parameterGroup.parameters"
          :key="descriptor.key"
          :descriptor="descriptor"
          :model-value="modelValue[descriptor.key] ?? ''"
          :error-message="errors[descriptor.key]"
          @update:model-value="createParameterUpdater(descriptor.key)"
        />
      </div>
    </fieldset>
  </div>
</template>
