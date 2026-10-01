<script setup lang="ts">
/**
 * 给既有策略上传一个新版本.
 *
 * 与「上传新策略」分开而不合并成一个带 `mode` 的组件: 后者除了两份文件, 一个字段都不共用 (版本没有
 * 名字、说明、可见性), 合成一个就得在每个字段上写「这个模式下显示吗」—— 那是把两个页面揉成一段谁
 * 也读不懂的分支.
 *
 * 判重是「与既有**任一**版本同内容」而不是「与最新版本同内容」, 故重复上传老内容不会产生新版本号.
 */

import { computed, ref } from 'vue';
import { ElButton } from 'element-plus';

import { uploadStrategyVersion } from '../api/strategies';
import type { StrategyVersion } from '../api/types';
import { useConfigurationFileSource } from '../composables/useConfigurationFileSource';
import { describeApiFailure } from '../composables/use-feedback';
import ErrorBanner from './ErrorBanner.vue';
import FilePicker from './FilePicker.vue';

const props = defineProps<{ strategyId: string }>();

const emit = defineEmits<{
  uploaded: [version: StrategyVersion];
}>();

const {
  configurationFile,
  problem: configurationProblem,
  isUsable: isConfigurationUsable,
  clear: clearConfigurationFile,
} = useConfigurationFileSource();

const sourceFile = ref<File | null>(null);
const errorMessage = ref<string | null>(null);
const isSubmitting = ref(false);

const isSubmitDisabled = computed(
  () => isSubmitting.value || sourceFile.value === null || !isConfigurationUsable.value,
);

function resetForm(): void {
  sourceFile.value = null;
  clearConfigurationFile();
}

async function submit(): Promise<void> {
  const selectedSourceFile = sourceFile.value;
  const selectedConfigurationFile = configurationFile.value;

  if (isSubmitDisabled.value || selectedSourceFile === null || selectedConfigurationFile === null) {
    return;
  }

  errorMessage.value = null;
  isSubmitting.value = true;

  try {
    const uploadedVersion = await uploadStrategyVersion(
      props.strategyId,
      selectedSourceFile,
      selectedConfigurationFile,
    );

    resetForm();
    emit('uploaded', uploadedVersion);
  } catch (error) {
    errorMessage.value = describeApiFailure(error, '上传失败');
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <form
    class="space-y-4"
    @submit.prevent="submit"
  >
    <ErrorBanner
      :message="errorMessage"
      :is-retry-visible="false"
    />

    <div class="grid gap-4 sm:grid-cols-2">
      <div class="flex flex-col gap-1">
        <span class="text-sm font-medium text-slate-700">策略源码 (*.py)</span>
        <FilePicker
          v-model="sourceFile"
          accept=".py"
          hint="文件名即作业目录里的文件名"
        />
      </div>

      <div class="flex flex-col gap-1">
        <span class="text-sm font-medium text-slate-700">策略配置 (*.json)</span>
        <FilePicker
          v-model="configurationFile"
          accept=".json"
          hint="策略启动时读的就是这一份, 它的键即提交页的参数"
        />
        <p
          v-if="configurationProblem"
          class="text-xs text-amber-700"
        >
          {{ configurationProblem }}
        </p>
      </div>
    </div>

    <ElButton
      type="primary"
      native-type="submit"
      :loading="isSubmitting"
      :disabled="isSubmitDisabled"
    >
      {{ isSubmitting ? '上传中…' : '上传新版本' }}
    </ElButton>
  </form>
</template>
