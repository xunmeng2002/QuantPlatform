<script setup lang="ts">
/**
 * 一个策略参数的输入控件.
 *
 * 控件形态由 manifest 的声明决定: 有 `options` 是下拉框, `boolean` 是复选框, 其余是输入框.
 * **取值一律以字符串 / 布尔量回给上层** (`ParameterInput` 的契约见
 * `domain/manifest.coerceParameterInput`), 类型转换与范围校验都在那边统一做 —— 控件这里做一次
 * 转换, 提交时再做一次, 就是两套会漂移的语义.
 */

import { computed } from 'vue';
import type { ParameterDescriptor, ParameterInput } from '../domain/manifest';

const props = defineProps<{
  descriptor: ParameterDescriptor;
  modelValue: ParameterInput;
  errorMessage?: string;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: ParameterInput];
}>();

const inputIdentifier = computed(() => `parameter-${props.descriptor.key}`);
const errorIdentifier = computed(() => `${inputIdentifier.value}-error`);
const hasOptions = computed(() => props.descriptor.options.length > 0);
const isBoolean = computed(() => props.descriptor.type === 'boolean');
const isNumeric = computed(
  () => props.descriptor.type === 'integer' || props.descriptor.type === 'number',
);
const stepValue = computed(() =>
  props.descriptor.type === 'integer' ? '1' : 'any',
);
const minimumValue = computed(() => props.descriptor.minimum ?? undefined);
const maximumValue = computed(() => props.descriptor.maximum ?? undefined);

function emitTextValue(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement).value);
}

function emitOptionValue(event: Event): void {
  emit('update:modelValue', (event.target as HTMLSelectElement).value);
}

function emitBooleanValue(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement).checked);
}
</script>

<template>
  <div class="flex flex-col gap-1">
    <label
      class="text-sm font-medium text-slate-700"
      :for="inputIdentifier"
    >
      {{ descriptor.label }}
      <span
        v-if="descriptor.isRequired"
        class="text-rose-600"
        aria-hidden="true"
      >*</span>
    </label>

    <select
      v-if="hasOptions"
      :id="inputIdentifier"
      class="rounded border bg-surface px-2 py-1.5 text-sm"
      :class="errorMessage ? 'border-rose-400' : 'border-line'"
      :value="modelValue"
      :aria-describedby="errorMessage ? errorIdentifier : undefined"
      @change="emitOptionValue"
    >
      <option value="">
        请选择
      </option>
      <option
        v-for="(option, optionIndex) in descriptor.options"
        :key="optionIndex"
        :value="String(optionIndex)"
      >
        {{ option.label || String(option.value) }}
      </option>
    </select>

    <input
      v-else-if="isBoolean"
      :id="inputIdentifier"
      type="checkbox"
      class="size-4 self-start accent-blue-700"
      :checked="modelValue === true"
      :aria-describedby="errorMessage ? errorIdentifier : undefined"
      @change="emitBooleanValue"
    >

    <input
      v-else
      :id="inputIdentifier"
      class="rounded border bg-surface px-2 py-1.5 text-sm"
      :class="errorMessage ? 'border-rose-400' : 'border-line'"
      :type="isNumeric ? 'number' : 'text'"
      :value="modelValue"
      :step="isNumeric ? stepValue : undefined"
      :min="minimumValue"
      :max="maximumValue"
      :aria-describedby="errorMessage ? errorIdentifier : undefined"
      @input="emitTextValue"
    >

    <p
      v-if="errorMessage"
      :id="errorIdentifier"
      class="text-xs text-rose-600"
    >
      {{ errorMessage }}
    </p>
    <p
      v-else-if="descriptor.minimum !== null || descriptor.maximum !== null"
      class="text-xs text-slate-400"
    >
      取值范围 {{ descriptor.minimum ?? '不限' }} ~ {{ descriptor.maximum ?? '不限' }}
    </p>
  </div>
</template>
