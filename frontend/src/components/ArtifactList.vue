<script setup lang="ts">
/**
 * 作业产物清单与下载.
 *
 * 清单里可能有嵌套路径 (`Dump/<RunId>/t_trade.csv`), 显示相对路径而不是只显示文件名: 同名文件
 * 可能出现在不同子目录里, 只显示末段就分不清下的是哪一份.
 *
 * 一次只下一个: 后端逐个请求, 且浏览器对连续触发的保存会弹出"允许多文件下载"的询问.
 */

import { ref } from 'vue';
import { ElButton, ElInput } from 'element-plus';

import type { JobArtifact } from '../api/types';
import { formatByteSize } from '../domain/format';

defineProps<{
  artifacts: JobArtifact[];
  isDownloading: boolean;
  downloadingPath: string | null;
}>();

const emit = defineEmits<{
  download: [artifact: JobArtifact];
}>();

const filterQuery = ref('');
</script>

<template>
  <div>
    <ElInput
      v-if="artifacts.length > 8"
      v-model="filterQuery"
      type="search"
      class="mb-2"
      placeholder="按路径筛选"
    />

    <ul class="divide-y divide-line rounded-lg border border-line bg-surface">
      <li
        v-for="artifact in artifacts.filter((item) => item.relative_path.includes(filterQuery))"
        :key="artifact.relative_path"
        class="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
      >
        <code class="text-xs break-all text-slate-700">{{ artifact.relative_path }}</code>
        <div class="flex items-center gap-3">
          <span class="text-xs text-slate-400">{{ formatByteSize(artifact.size_bytes) }}</span>
          <ElButton
            size="small"
            :disabled="isDownloading"
            @click="emit('download', artifact)"
          >
            {{ isDownloading && downloadingPath === artifact.relative_path ? '下载中…' : '下载' }}
          </ElButton>
        </div>
      </li>
    </ul>
  </div>
</template>
