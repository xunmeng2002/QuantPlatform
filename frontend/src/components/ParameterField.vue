<script setup lang="ts">
/**
 * 一个策略参数的输入控件.
 *
 * 控件形态由**该键当前取值的 JSON 类型**决定 (布尔 → 复选框、数值 → 数字框、其余 → 文本框), 平台
 * 不认识任何"参数声明". **取值一律以字符串 / 布尔量回给上层** (`ParameterInput` 的契约见
 * `domain/strategy-configuration.coerceParameterInput`), 类型转换与数值判据都在那边统一做 —— 控件
 * 这里做一次, 提交时再做一次, 就是两套会漂移的语义.
 *
 * 标签就是**键名本身**: 那份 JSON 是策略自己要读的, 键名才是用户与策略之间唯一的共同语言, 编一个
 * 漂亮的标题只会让用户对不上策略里的写法.
 *
 * 两个事件处理器收到的都是 **Element Plus 给的"值"本身, 不是 DOM 事件** —— 与原生控件时代
 * (`event.target.value`) 不同.
 */

import { computed } from 'vue';
import { ElCheckbox, ElInput } from 'element-plus';
import type { CheckboxValueType } from 'element-plus';

import type { ParameterDescriptor, ParameterInput } from '../domain/strategy-configuration';

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
const isBoolean = computed(() => props.descriptor.type === 'boolean');

/**
 * el-checkbox 的载荷类型是 `string | number | boolean` —— 它要同时覆盖 `true-value` / `false-value`
 * 那套用法. 本组件从不传这两个 prop, 故实际值恒为 `true` / `false`; 下面这一行是**类型收窄**,
 * 不是语义转换 (`ParameterInput` 里布尔就是布尔).
 */
function emitBooleanValue(checkboxValue: CheckboxValueType): void {
  emit('update:modelValue', checkboxValue === true);
}

function emitTextValue(text: string): void {
  emit('update:modelValue', text);
}
</script>

<template>
  <div class="flex flex-col gap-1">
    <label
      class="text-sm font-medium text-slate-700"
      :for="inputIdentifier"
    >{{ descriptor.key }}</label>

    <!-- 不再套自己的 label: el-checkbox 的根节点本身就是 <label for=inputId>, 外面那个
         <label for> 是它的兄弟 (不是嵌套), 两者指向同一个 input, 空文本的那个不贡献名称.

         代价是 `aria-describedby` 在这一支**落不到控件上**: 它在 EP 里不是声明 prop (el-checkbox
         只声明了 ariaControls), 只会顺着 attrs 落到最外层的容器 div 上 —— "错误文案随控件一起
         播报"的那层关联丢了 (可见的红字还在). 文本框那一支保得住它, 因为它的产物里 `id` /
         `aria-describedby` 都落在内层原生 input 上. -->
    <ElCheckbox
      v-if="isBoolean"
      :id="inputIdentifier"
      class="self-start"
      :model-value="modelValue === true"
      :aria-describedby="errorMessage ? errorIdentifier : undefined"
      @change="emitBooleanValue"
    />

    <!-- `step="any"` 而不是 `1`: JSON 只有一种数, 整数与小数都能存; 而数字框自带的 min/max 会拦在
         提交之前 —— 参数范围由策略自己守, 平台不替它定, 也就不该在控件上给一个假的边界. -->
    <ElInput
      v-else
      :id="inputIdentifier"
      :model-value="String(modelValue)"
      :type="descriptor.type === 'number' ? 'number' : 'text'"
      :step="descriptor.type === 'number' ? 'any' : undefined"
      :aria-describedby="errorMessage ? errorIdentifier : undefined"
      @input="emitTextValue"
    />

    <p
      v-if="errorMessage"
      :id="errorIdentifier"
      class="text-xs text-rose-600"
    >
      {{ errorMessage }}
    </p>
  </div>
</template>
