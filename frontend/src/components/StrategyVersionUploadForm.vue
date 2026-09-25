<script setup lang="ts">
/**
 * 给既有策略上传一个新版本.
 *
 * 与「上传新策略」分开而不合并成一个带 `mode` 的组件: 后者除了名字与 manifest, 一个字段都不共用
 * (版本没有名字、说明、可见性), 合成一个就得在每个字段上写「这个模式下显示吗」—— 那是把两个页面
 * 揉成一段谁也读不懂的分支.
 *
 * 判重是「与既有**任一**版本同内容」而不是「与最新版本同内容」, 故重复上传老内容不会产生新版本号.
 */

import { computed, ref, watch } from 'vue';

import { ApiError } from '../api/client';
import { uploadStrategyVersion } from '../api/strategies';
import type { StrategyVersion } from '../api/types';
import { useManifestTextSource } from '../composables/useManifestTextSource';
import ErrorBanner from './ErrorBanner.vue';
import FilePicker from './FilePicker.vue';

const props = defineProps<{ strategyId: string }>();

const emit = defineEmits<{
  uploaded: [version: StrategyVersion];
}>();

const {
  manifestText,
  problem: manifestProblem,
  isUsable: isManifestUsable,
  loadFromFile: loadManifestFromFile,
  clear: clearManifestText,
} = useManifestTextSource();

const sourceFile = ref<File | null>(null);
const manifestFile = ref<File | null>(null);
const errorMessage = ref<string | null>(null);
const isSubmitting = ref(false);

watch(manifestFile, (selectedFile) => {
  if (selectedFile === null) {
    clearManifestText();

    return;
  }

  void loadManifestFromFile(selectedFile);
});

const isSubmitDisabled = computed(
  () => isSubmitting.value || sourceFile.value === null || !isManifestUsable.value,
);

function resetForm(): void {
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
    const uploadedVersion = await uploadStrategyVersion(
      props.strategyId,
      selectedSourceFile,
      manifestText.value,
    );

    resetForm();
    emit('uploaded', uploadedVersion);
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
        for="version-manifest"
      >manifest (JSON)</label>
      <textarea
        id="version-manifest"
        v-model="manifestText"
        rows="8"
        class="rounded border border-line px-2 py-1.5 font-mono text-xs"
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
      {{ isSubmitting ? '上传中…' : '上传新版本' }}
    </button>
  </form>
</template>
