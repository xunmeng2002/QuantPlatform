/**
 * manifest 文本的录入.
 *
 * 上传策略与上传新版本共用这一段: 两条路 (手填 / 读入一份 manifest.json) 最终落到**同一个**
 * 请求字段, 服务端因此只有一条路径 —— 前端这里也一样, 两个表单共用同一个录入状态.
 */

import { computed, ref } from 'vue';
import type { ComputedRef, Ref } from 'vue';

import { parseStrategyManifest } from '../domain/manifest';
import type { ManifestParseResult } from '../domain/manifest';

export const MANIFEST_FILE_READ_FAILED_MESSAGE = '无法读取该文件';

export interface ManifestTextSource {
  manifestText: Ref<string>;
  sourceFilename: Ref<string>;
  readFailureMessage: Ref<string | null>;
  parseResult: ComputedRef<ManifestParseResult>;
  /** 可直接展示的问题描述; 空文本时提示「必填」而不是「不是合法 JSON」. */
  problem: ComputedRef<string | null>;
  isUsable: ComputedRef<boolean>;
  loadFromFile: (file: File) => Promise<void>;
  clear: () => void;
}

export function useManifestTextSource(): ManifestTextSource {
  const manifestText = ref('');
  const sourceFilename = ref('');
  const readFailureMessage = ref<string | null>(null);

  const parseResult = computed(() => parseStrategyManifest(manifestText.value));

  const problem = computed(() => {
    if (!manifestText.value.trim()) {
      return '请填入 manifest';
    }

    return parseResult.value.ok ? null : parseResult.value.message;
  });

  const isUsable = computed(
    () => parseResult.value.ok && readFailureMessage.value === null,
  );

  async function loadFromFile(file: File): Promise<void> {
    try {
      manifestText.value = await file.text();
      sourceFilename.value = file.name;
      readFailureMessage.value = null;
    } catch {
      readFailureMessage.value = MANIFEST_FILE_READ_FAILED_MESSAGE;
    }
  }

  function clear(): void {
    manifestText.value = '';
    sourceFilename.value = '';
    readFailureMessage.value = null;
  }

  return {
    manifestText,
    sourceFilename,
    readFailureMessage,
    parseResult,
    problem,
    isUsable,
    loadFromFile,
    clear,
  };
}
