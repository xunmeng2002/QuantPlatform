<script setup lang="ts">
/**
 * 从受限用户目录里选一个人.
 *
 * 目录只回 `id` 与 `display_name`, 所以这里只能按显示名认人 —— 同名时选错人是已知缺口
 * (目录数据本身不足以区分), 界面上**不假装**能区分. 被排除的两种账号 (自己和停用的) 本来就不
 * 在结果里, 所以「搜不到某人」不必报告成错误.
 */

import { onMounted, ref } from 'vue';

import type { UserDirectoryEntry } from '../api/types';
import { describeApiFailure } from '../composables/use-feedback';
import { useUserDirectoryStore } from '../stores/user-directory';

const emit = defineEmits<{
  select: [entry: UserDirectoryEntry];
}>();

const directoryStore = useUserDirectoryStore();

const searchQuery = ref('');
const errorMessage = ref<string | null>(null);

onMounted(() => {
  void runSearch();
});

async function runSearch(): Promise<void> {
  errorMessage.value = null;

  try {
    await directoryStore.search(searchQuery.value.trim());
  } catch (error) {
    errorMessage.value = describeApiFailure(error, '查询用户失败');
  }
}

function selectEntry(entry: UserDirectoryEntry): void {
  emit('select', entry);
}
</script>

<template>
  <div class="rounded-lg border border-line bg-surface p-4">
    <div class="flex gap-2">
      <input
        v-model="searchQuery"
        type="search"
        class="flex-1 rounded border border-line px-2 py-1.5 text-sm"
        placeholder="按显示名搜索"
        @keyup.enter="runSearch"
      >
      <button
        type="button"
        class="rounded border border-line px-3 py-1.5 text-sm hover:bg-slate-50"
        :disabled="directoryStore.isLoading"
        @click="runSearch"
      >
        搜索
      </button>
    </div>

    <p
      v-if="errorMessage"
      class="mt-2 text-xs text-rose-600"
    >
      {{ errorMessage }}
    </p>

    <p
      v-else-if="directoryStore.isLoading"
      class="mt-2 text-xs text-slate-400"
    >
      搜索中…
    </p>

    <ul
      v-else-if="directoryStore.searchMatches.length > 0"
      class="mt-3 max-h-56 divide-y divide-line overflow-y-auto"
    >
      <li
        v-for="entry in directoryStore.searchMatches"
        :key="entry.id"
        class="flex items-center justify-between py-2"
      >
        <span class="text-sm text-slate-700">{{ entry.display_name }}</span>
        <button
          type="button"
          class="rounded border border-line px-2 py-1 text-xs hover:bg-slate-50"
          @click="selectEntry(entry)"
        >
          选择
        </button>
      </li>
    </ul>

    <p
      v-else
      class="mt-3 text-xs text-slate-400"
    >
      没有匹配的用户. 目录只含启用中且已填显示名的账号, 自己也搜不到.
    </p>
  </div>
</template>
