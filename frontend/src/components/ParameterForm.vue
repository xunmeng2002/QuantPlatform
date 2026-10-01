<script setup lang="ts">
/**
 * 按配置模板的键生成的参数表单.
 *
 * 本组件**不持有状态**: 原始输入由父页面拿着 (提交时要与日期、初始资金一起取用), 这里只负责把每一项
 * 画出来并把它自身的变化冒上去. 于是「哪些填错了」由父页面用纯函数算一次
 * (`domain/strategy-configuration.deriveParameterValues`), 而不是散在控件里.
 *
 * 没有分组: 分组曾经来自 manifest 的声明, 而新形态下模板里只有"键 = 取值", 没有地方安放组名 ——
 * 凭空按字母或类型分组, 只会把用户熟悉的那个顺序打乱. 模板里键的书写顺序就是这里的渲染顺序.
 */

import ParameterField from './ParameterField.vue';
import type { ParameterDescriptor, ParameterInput } from '../domain/strategy-configuration';

const props = defineProps<{
  descriptors: ParameterDescriptor[];
  modelValue: Record<string, ParameterInput>;
  errors: Record<string, string>;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, ParameterInput>];
}>();

/**
 * 模板里必须写成 `updateParameter(descriptor.key, $event)` —— 那个 `$event` 不能省.
 * 若写成 `updateParameter(descriptor.key)`, Vue 会把它当内联语句, 编译出
 * `($event) => updateParameter(descriptor.key)`: 参数被丢掉, 值永远冒不到父状态, 界面上一声不响.
 */
function updateParameter(parameterKey: string, value: ParameterInput): void {
  emit('update:modelValue', { ...props.modelValue, [parameterKey]: value });
}
</script>

<template>
  <div class="grid gap-4 sm:grid-cols-2">
    <ParameterField
      v-for="descriptor in descriptors"
      :key="descriptor.key"
      :descriptor="descriptor"
      :model-value="modelValue[descriptor.key] ?? ''"
      :error-message="errors[descriptor.key]"
      @update:model-value="updateParameter(descriptor.key, $event)"
    />
  </div>
</template>
