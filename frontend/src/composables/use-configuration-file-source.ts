/**
 * 配置文件的录入.
 *
 * 上传策略与上传新版本共用这一段: 选的都必须是**一份文件**而不是一段文本 —— 配置模板要与源码一起
 * 落到作业目录里, 文件名就是那份文件自己的名字 (`api/strategies` 的 multipart 因此是两个文件字段).
 *
 * 读进来的文本**不对外暴露**: 它只用来在本地判一次"是不是一个 JSON 对象", 让用户在**选完文件就
 * 看见**问题, 而不必等一轮请求. 真正的校验在后端 —— 在前端重写一遍只会得到两份会漂移的规则.
 */

import { computed, ref, watch } from 'vue';
import type { ComputedRef, Ref } from 'vue';

import { parseStrategyConfigurationTemplate } from '../domain/strategy-configuration';

export const CONFIGURATION_REQUIRED_MESSAGE = '请选择配置文件';
export const CONFIGURATION_FILE_READ_FAILED_MESSAGE = '无法读取该文件';

export interface ConfigurationFileSource {
  configurationFile: Ref<File | null>;
  /** 可直接展示的问题描述; 文件还在读时为空 (那一瞬间没什么可说的). */
  problem: ComputedRef<string | null>;
  isUsable: ComputedRef<boolean>;
  clear: () => void;
}

export function useConfigurationFileSource(): ConfigurationFileSource {
  const configurationFile = ref<File | null>(null);
  const configurationText = ref<string | null>(null);
  const readFailureMessage = ref<string | null>(null);

  // 读了就不再回头核对: 用户在慢盘上连着换两次文件时, 先选的那份可能后读完 —— 后到的结果会把新的
  // 那份覆盖掉, 而界面上显示的是新文件名. 认一下"还是不是当初那份"就够, 文件很小, 不必取消读取.
  watch(configurationFile, async (selectedFile) => {
    configurationText.value = null;
    readFailureMessage.value = null;

    if (selectedFile === null) {
      return;
    }

    try {
      const readText = await selectedFile.text();

      if (configurationFile.value === selectedFile) {
        configurationText.value = readText;
      }
    } catch {
      if (configurationFile.value === selectedFile) {
        readFailureMessage.value = CONFIGURATION_FILE_READ_FAILED_MESSAGE;
      }
    }
  });

  const problem = computed(() => {
    if (readFailureMessage.value !== null) {
      return readFailureMessage.value;
    }

    if (configurationFile.value === null) {
      return CONFIGURATION_REQUIRED_MESSAGE;
    }

    if (configurationText.value === null) {
      return null;
    }

    const parseResult = parseStrategyConfigurationTemplate(configurationText.value);

    return parseResult.ok ? null : parseResult.message;
  });

  const isUsable = computed(
    () => problem.value === null && configurationText.value !== null,
  );

  function clear(): void {
    configurationFile.value = null;
  }

  return { configurationFile, problem, isUsable, clear };
}
