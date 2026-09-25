<script setup lang="ts">
/**
 * 单文件选择.
 *
 * 上传策略要用两处 (源码 `.py` 与 manifest `.json`), 传新版本再用两处 —— 四处的 html 长得一样,
 * 而**清空**这一处容易漏: 原生 file 输入框的已选文件名不会因为 `v-model` 变了而消失, 提交成功后
 * 只清上层状态, 界面上会留着一个再也提交不上去的文件名.
 */

import { ref, watch } from 'vue';

const props = defineProps<{
  modelValue: File | null;
  accept?: string;
  hint?: string;
}>();

const emit = defineEmits<{
  'update:modelValue': [file: File | null];
}>();

const fileInputElement = ref<HTMLInputElement | null>(null);

watch(
  () => props.modelValue,
  (selectedFile) => {
    if (selectedFile === null && fileInputElement.value !== null) {
      fileInputElement.value.value = '';
    }
  },
);

function emitSelectedFile(event: Event): void {
  const inputElement = event.target as HTMLInputElement;

  emit('update:modelValue', inputElement.files?.[0] ?? null);
}
</script>

<template>
  <div class="flex flex-col gap-1">
    <input
      ref="fileInputElement"
      type="file"
      class="text-sm text-slate-600"
      :accept="accept"
      @change="emitSelectedFile"
    >
    <p class="text-xs text-slate-400">
      {{ modelValue?.name ?? '未选择文件' }}
      <span v-if="hint"> · {{ hint }}</span>
    </p>
  </div>
</template>
