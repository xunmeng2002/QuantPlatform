<script setup lang="ts">
/**
 * 策略列表 + 上传入口.
 *
 * 「上传」是折叠在同一个页面里的, 不做单独的 `/strategies/new` 路由: 上传完要看到的正是列表里
 * 多了那一条, 换页会把这一步的反馈切走.
 *
 * 归属人用目录把 id 映成显示名 —— 列表里有别人共享给我的策略, 只显示 32 位 hex 等于没显示.
 */

import { computed, onMounted, ref } from 'vue';
import { RouterLink, useRouter } from 'vue-router';

import { ApiError } from '../api/client';
import { fetchStrategies } from '../api/strategies';
import { DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS } from '../api/types';
import type { PageResponse, Strategy } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import LoadingNotice from '../components/LoadingNotice.vue';
import PaginationBar from '../components/PaginationBar.vue';
import StatusBadge from '../components/StatusBadge.vue';
import StrategyUploadForm from '../components/StrategyUploadForm.vue';
import { formatDateTime } from '../domain/format';
import { describeStrategyVisibility } from '../domain/labels';
import { useStrategyCatalogStore } from '../stores/strategy-catalog';
import { useUserDirectoryStore } from '../stores/user-directory';

const router = useRouter();
const strategyCatalog = useStrategyCatalogStore();
const directoryStore = useUserDirectoryStore();

const strategiesPage = ref<PageResponse<Strategy> | null>(null);
const errorMessage = ref<string | null>(null);
const isLoading = ref(true);
const isUploadPanelOpen = ref(false);

const offset = ref(0);
const limit = ref<number>(DEFAULT_PAGE_SIZE);

const strategies = computed(() => strategiesPage.value?.records ?? []);
const totalCount = computed(() => strategiesPage.value?.total ?? 0);

async function refreshStrategies(): Promise<void> {
  isLoading.value = true;
  errorMessage.value = null;

  try {
    const page = await fetchStrategies(offset.value, limit.value);

    strategiesPage.value = page;
    // 新出现的一页里可能有没见过的归属人, 顺手补齐名字.
    await directoryStore.ensureKnown(page.records.map((record) => record.owner_user_id));
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.detail : '加载策略列表失败';
  } finally {
    isLoading.value = false;
  }
}

function goToOffset(nextOffset: number): void {
  offset.value = nextOffset;
  void refreshStrategies();
}

function handlePageSizeChange(): void {
  offset.value = 0;
  void refreshStrategies();
}

async function handleCreated(strategyId: string): Promise<void> {
  isUploadPanelOpen.value = false;
  // 列表缓存与当前页都要失效: 新建的策略按更新时间倒序排在最前, 若停在第二页, 它在上一页.
  await strategyCatalog.refresh();
  offset.value = 0;
  await refreshStrategies();
  await router.push({ name: 'strategy-detail', params: { id: strategyId } });
}

onMounted(() => {
  void refreshStrategies();
});
</script>

<template>
  <section>
    <header class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h1 class="text-lg font-semibold text-slate-900">策略</h1>
      <button
        type="button"
        class="rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong"
        @click="isUploadPanelOpen = !isUploadPanelOpen"
      >
        {{ isUploadPanelOpen ? '收起上传表单' : '上传策略' }}
      </button>
    </header>

    <section
      v-if="isUploadPanelOpen"
      class="mb-6 rounded-lg border border-line bg-surface p-4"
    >
      <h2 class="mb-4 text-sm font-semibold text-slate-700">
        上传新策略
      </h2>
      <StrategyUploadForm @created="handleCreated" />
    </section>

    <ErrorBanner
      :message="errorMessage"
      @retry="refreshStrategies"
    />

    <LoadingNotice v-if="isLoading" />

    <EmptyNotice
      v-else-if="strategies.length === 0"
      message="还没有任何策略"
      hint="点右上角「上传策略」提交第一份策略源码与 manifest"
    />

    <template v-else>
      <div class="overflow-x-auto rounded-lg border border-line bg-surface">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th class="px-3 py-2 font-medium">
                策略名
              </th>
              <th class="px-3 py-2 font-medium">
                说明
              </th>
              <th class="px-3 py-2 font-medium">
                可见性
              </th>
              <th class="px-3 py-2 font-medium">
                归属
              </th>
              <th class="px-3 py-2 font-medium">
                更新时间
              </th>
              <th class="px-3 py-2 font-medium" />
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr
              v-for="strategy in strategies"
              :key="strategy.id"
              class="hover:bg-slate-50"
            >
              <td class="px-3 py-2 text-slate-800">
                {{ strategy.name }}
              </td>
              <td class="max-w-md truncate px-3 py-2 text-slate-500">
                {{ strategy.description || '—' }}
              </td>
              <td class="px-3 py-2">
                <StatusBadge v-bind="describeStrategyVisibility(strategy.visibility_type)" />
              </td>
              <td class="px-3 py-2 text-slate-600">
                {{ directoryStore.displayNameFor(strategy.owner_user_id) }}
              </td>
              <td class="px-3 py-2 whitespace-nowrap text-slate-600">
                {{ formatDateTime(strategy.updated_at) }}
              </td>
              <td class="px-3 py-2 text-right">
                <RouterLink
                  class="text-brand hover:underline"
                  :to="{ name: 'strategy-detail', params: { id: strategy.id } }"
                >
                  详情
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
        <label class="flex items-center gap-2 text-sm text-slate-600">
          每页
          <select
            v-model.number="limit"
            class="rounded border border-line bg-surface px-2 py-1 text-sm"
            @change="handlePageSizeChange"
          >
            <option
              v-for="pageSize in PAGE_SIZE_OPTIONS"
              :key="pageSize"
              :value="pageSize"
            >
              {{ pageSize }}
            </option>
          </select>
        </label>
        <PaginationBar
          :total="totalCount"
          :offset="offset"
          :limit="limit"
          @update:offset="goToOffset"
        />
      </div>
    </template>
  </section>
</template>
