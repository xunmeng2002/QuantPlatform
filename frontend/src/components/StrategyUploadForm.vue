<script setup lang="ts">
/**
 * 上传一个新策略.
 *
 * manifest 有两种填法, 落到同一个请求字段: 直接粘贴 JSON, 或选一份 `manifest.json` 读进来再改.
 * 两条路都保留, 因为「从策略开发目录里直接拖一份过来」和「照着模板手填」都是真实用法.
 *
 * 提交前的 manifest 检查只说「是不是合法 JSON / 有没有顶层那三项」: 完整的 manifest 校验在后端
 * (`app/manifest.py`), 它给的报错带出错位置, 在前端重写一遍只会得到两份会漂移的规则.
 */

import { computed, ref, watch } from 'vue';

import { ApiError } from '../api/client';
import { createStrategy } from '../api/strategies';
import {
  MAXIMUM_STRATEGY_DESCRIPTION_LENGTH,
  MAXIMUM_STRATEGY_NAME_LENGTH,
  STRATEGY_VISIBILITIES,
} from '../api/types';
import type { StrategyVisibility } from '../api/types';
import { useManifestTextSource } from '../composables/useManifestTextSource';
import { describeStrategyVisibility } from '../domain/labels';
import ErrorBanner from './ErrorBanner.vue';
import FilePicker from './FilePicker.vue';

const emit = defineEmits<{
  created: [strategyId: string];
}>();

const {
  manifestText,
  problem: manifestProblem,
  isUsable: isManifestUsable,
  loadFromFile: loadManifestFromFile,
  clear: clearManifestText,
} = useManifestTextSource();

const name = ref('');
const description = ref('');
const visibilityType = ref<StrategyVisibility>('private');
const sourceFile = ref<File | null>(null);
const manifestFile = ref<File | null>(null);
const errorMessage = ref<string | null>(null);
const isSubmitting = ref(false);

// 选中文件即读进文本域: 之后用户可以接着改, 或直接重贴一份.
watch(manifestFile, (selectedFile) => {
  if (selectedFile === null) {
    clearManifestText();

    return;
  }

  void loadManifestFromFile(selectedFile);
});

const isSubmitDisabled = computed(
  () =>
    isSubmitting.value ||
    !name.value.trim() ||
    sourceFile.value === null ||
    !isManifestUsable.value,
);

function resetForm(): void {
  name.value = '';
  description.value = '';
  sourceFile.value = null;
  manifestFile.value = null;
  clearManifestText();
}

async function submit(): Promise<void> {
  const selectedSourceFile = sourceFile.value;

  if (isSubmitDisabled.value || selectedSourceFile === null) {
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
      manifestText: manifestText.value,
    });

    resetForm();
    emit('created', createdStrategy.strategy.id);
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.detail : '上传失败';
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
        <input
          id="strategy-name"
          v-model="name"
          type="text"
          class="rounded border border-line px-2 py-1.5 text-sm"
          :maxlength="MAXIMUM_STRATEGY_NAME_LENGTH"
        >
      </div>

      <div class="flex flex-col gap-1">
        <label
          class="text-sm font-medium text-slate-700"
          for="strategy-visibility"
        >可见性</label>
        <select
          id="strategy-visibility"
          v-model="visibilityType"
          class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
        >
          <option
            v-for="visibility in STRATEGY_VISIBILITIES"
            :key="visibility"
            :value="visibility"
          >
            {{ describeStrategyVisibility(visibility).label }}
          </option>
        </select>
      </div>
    </div>

    <div class="flex flex-col gap-1">
      <label
        class="text-sm font-medium text-slate-700"
        for="strategy-description"
      >说明 (可留空)</label>
      <textarea
        id="strategy-description"
        v-model="description"
        rows="2"
        class="rounded border border-line px-2 py-1.5 text-sm"
        :maxlength="MAXIMUM_STRATEGY_DESCRIPTION_LENGTH"
      />
    </div>

    <div class="grid gap-4 sm:grid-cols-2">
      <div class="flex flex-col gap-1">
        <span class="text-sm font-medium text-slate-700">策略源码 (*.py)</span>
        <FilePicker
          v-model="sourceFile"
          accept=".py"
          hint="文件名须与 manifest 的 entry_filename 一致"
        />
      </div>

      <div class="flex flex-col gap-1">
        <span class="text-sm font-medium text-slate-700">从文件载入 manifest</span>
        <FilePicker
          v-model="manifestFile"
          accept=".json"
          hint="载入后还可以在下面改"
        />
      </div>
    </div>

    <div class="flex flex-col gap-1">
      <label
        class="text-sm font-medium text-slate-700"
        for="strategy-manifest"
      >manifest (JSON)</label>
      <textarea
        id="strategy-manifest"
        v-model="manifestText"
        rows="8"
        class="rounded border border-line px-2 py-1.5 font-mono text-xs"
        placeholder='{"entry_filename": "strategy.py", "config_filename": "Strategy.json", "supported_match_modes": ["Bar"], "params": []}'
      />
      <p
        v-if="manifestProblem"
        class="text-xs text-amber-700"
      >
        {{ manifestProblem }}
      </p>
    </div>

    <button
      type="submit"
      class="rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong disabled:opacity-50"
      :disabled="isSubmitDisabled"
    >
      {{ isSubmitting ? '上传中…' : '上传策略' }}
    </button>
  </form>
</template>
