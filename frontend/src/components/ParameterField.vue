<script setup lang="ts">
/**
 * 一个策略参数的输入控件.
 *
 * 控件形态由 manifest 的声明决定: 有 `options` 是下拉框, `boolean` 是复选框, 其余是输入框.
 * **取值一律以字符串 / 布尔量回给上层** (`ParameterInput` 的契约见
 * `domain/manifest.coerceParameterInput`), 类型转换与范围校验都在那边统一做 —— 控件这里做一次
 * 转换, 提交时再做一次, 就是两套会漂移的语义.
 *
 * 三个事件处理器收到的都是 **Element Plus 给的"值"本身, 不是 DOM 事件** —— 与原生控件时代
 * (`event.target.value`) 不同. 上抛时一律原样透传, 不做任何"顺手"归一化: 下拉的取值必须是
 * **选项下标的字符串** (`''` = 未选), 转成数字或空串以外的任何东西都是静默的契约破坏.
 */

import { computed } from 'vue';
import { ElCheckbox, ElInput, ElOption, ElSelect } from 'element-plus';
import type { CheckboxValueType } from 'element-plus';

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

/** 下拉时不加 `clearable`: EP 的清空默认值是 `undefined`, 而 domain 要的是 `''`. */
function emitOptionValue(optionIndex: string): void {
  emit('update:modelValue', optionIndex);
}

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
    >
      {{ descriptor.label }}
      <span
        v-if="descriptor.isRequired"
        class="text-rose-600"
        aria-hidden="true"
      >*</span>
    </label>

    <!-- `id` 是 el-select 的声明 prop, EP 会把它绑到内层那个 role="combobox" 的 input 上,
         故上面 <label for> 的关联仍成立. `aria-describedby` 则**落不到控件上**: 它在 EP 里不是
         声明 prop, 只会顺着 attrs 落到最外层的容器 div 上 —— 下拉这一支失去了"错误文案随控件
         一起播报"的那层关联 (可见的红字还在, 且必填的 `*` 仍由 label 承担).

         同一件事在复选框那一支同样成立 (el-checkbox 只声明了 ariaControls). 三分支里**只有
         el-input 支能保住它** —— `id` / `aria-describedby` / `step` / `min` / `max` 在它的产物里
         都落在内层原生 input 上. 这是本轮换库唯一一处可访问性净损失, 已在变更说明里记下. -->
    <ElSelect
      v-if="hasOptions"
      :id="inputIdentifier"
      placeholder="请选择"
      :model-value="String(modelValue)"
      :aria-describedby="errorMessage ? errorIdentifier : undefined"
      @change="emitOptionValue"
    >
      <ElOption
        v-for="(option, optionIndex) in descriptor.options"
        :key="optionIndex"
        :label="option.label || String(option.value)"
        :value="String(optionIndex)"
      />
    </ElSelect>

    <!-- 不再套自己的 label: el-checkbox 的根节点本身就是 <label for=inputId>, 外面那个
         <label for> 是它的兄弟 (不是嵌套), 两者指向同一个 input, 空文本的那个不贡献名称. -->
    <ElCheckbox
      v-else-if="isBoolean"
      :id="inputIdentifier"
      class="self-start"
      :model-value="modelValue === true"
      :aria-describedby="errorMessage ? errorIdentifier : undefined"
      @change="emitBooleanValue"
    />

    <ElInput
      v-else
      :id="inputIdentifier"
      :model-value="String(modelValue)"
      :type="isNumeric ? 'number' : 'text'"
      :step="isNumeric ? stepValue : undefined"
      :min="minimumValue"
      :max="maximumValue"
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
    <p
      v-else-if="descriptor.minimum !== null || descriptor.maximum !== null"
      class="text-xs text-slate-400"
    >
      取值范围 {{ descriptor.minimum ?? '不限' }} ~ {{ descriptor.maximum ?? '不限' }}
    </p>
  </div>
</template>
