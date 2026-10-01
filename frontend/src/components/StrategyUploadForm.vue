<script setup lang="ts">
/**
 * 上传一个新策略.
 *
 * 一次要两份文件: 策略源码 `.py`, 以及策略启动时真正会读的那份配置 `.json`. 没有让用户"照着模板
 * 手填"的第二条路 —— 那份 JSON 本来就存在于策略的开发目录里, 平台上现编一份只会与策略实际认的那份
 * 分叉, 而这个分叉要到跑起来才看得出来.
 *
 * 提交前的检查只说「是不是一个 JSON 对象」: 完整的校验在后端 (`routers/strategies.py`), 它给的报错
 * 带出错的位置, 在前端重写一遍只会得到两份会漂移的规则.
 */

import { computed, ref } from 'vue';
import { ElButton, ElInput, ElOption, ElSelect } from 'element-plus';

import { createStrategy } from '../api/strategies';
import {
  MAXIMUM_STRATEGY_DESCRIPTION_LENGTH,
  MAXIMUM_STRATEGY_NAME_LENGTH,
  STRATEGY_VISIBILITIES,
} from '../api/types';
import type { StrategyVisibility } from '../api/types';
import { useConfigurationFileSource } from '../composables/useConfigurationFileSource';
import { describeApiFailure } from '../composables/use-feedback';
import { describeStrategyVisibility } from '../domain/labels';
import ErrorBanner from './ErrorBanner.vue';
import FilePicker from './FilePicker.vue';

const emit = defineEmits<{
  created: [strategyId: string];
}>();

const {
  configurationFile,
  problem: configurationProblem,
  isUsable: isConfigurationUsable,
  clear: clearConfigurationFile,
} = useConfigurationFileSource();

const name = ref('');
const description = ref('');
const visibilityType = ref<StrategyVisibility>('private');
const sourceFile = ref<File | null>(null);
const errorMessage = ref<string | null>(null);
const isSubmitting = ref(false);

const isSubmitDisabled = computed(
  () =>
    isSubmitting.value ||
    !name.value.trim() ||
    sourceFile.value === null ||
    !isConfigurationUsable.value,
);

function resetForm(): void {
  name.value = '';
  description.value = '';
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
    const createdStrategy = await createStrategy({
      name: name.value.trim(),
      description: description.value.trim(),
      visibilityType: visibilityType.value,
      sourceFile: selectedSourceFile,
      configurationFile: selectedConfigurationFile,
    });

    resetForm();
    emit('created', createdStrategy.strategy.id);
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
        <label
          class="text-sm font-medium text-slate-700"
          for="strategy-name"
        >策略名</label>
        <ElInput
          id="strategy-name"
          v-model="name"
          type="text"
          :maxlength="MAXIMUM_STRATEGY_NAME_LENGTH"
        />
      </div>

      <div class="flex flex-col gap-1">
        <label
          class="text-sm font-medium text-slate-700"
          for="strategy-visibility"
        >可见性</label>
        <ElSelect
          id="strategy-visibility"
          v-model="visibilityType"
        >
          <ElOption
            v-for="visibility in STRATEGY_VISIBILITIES"
            :key="visibility"
            :label="describeStrategyVisibility(visibility).label"
            :value="visibility"
          />
        </ElSelect>
      </div>
    </div>

    <div class="flex flex-col gap-1">
      <label
        class="text-sm font-medium text-slate-700"
        for="strategy-description"
      >说明 (可留空)</label>
      <ElInput
        id="strategy-description"
        v-model="description"
        type="textarea"
        :rows="2"
        :maxlength="MAXIMUM_STRATEGY_DESCRIPTION_LENGTH"
      />
    </div>

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
      {{ isSubmitting ? '上传中…' : '上传策略' }}
    </ElButton>
  </form>
</template>
