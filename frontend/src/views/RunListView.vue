<script setup lang="ts">
/**
 * 运行列表.
 *
 * 非终态的轮存在时每 2 秒刷新一次, 全部结束就停 —— 这是本页唯一"活"的地方, 也是轮询唯一的
 * 使用处. 筛选条件改变时回到第一页: 停在第三页再改筛选, 多半会看到一张空表.
 */

import { computed, onMounted, ref, watch } from 'vue';
import { RouterLink } from 'vue-router';

import { ApiError } from '../api/client';
import { DEFAULT_RUN_SORT_COLUMN, RUN_SORT_COLUMNS, fetchRuns } from '../api/runs';
import type { RunSortColumn } from '../api/runs';
import { DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS, RUN_STATUSES } from '../api/types';
import type { PageResponse, RunStatus, RunSummary } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import LoadingNotice from '../components/LoadingNotice.vue';
import PaginationBar from '../components/PaginationBar.vue';
import StatusBadge from '../components/StatusBadge.vue';
import { usePolling } from '../composables/usePolling';
import {
  formatAmount,
  formatCount,
  formatDateTime,
  formatDuration,
  formatTradingDay,
} from '../domain/format';
import { describeRunSortColumn } from '../domain/labels';
import { describeEngineVerdict, describeRunStatus, isTerminalRunStatus } from '../domain/run-status';
import { useStrategyCatalogStore } from '../stores/strategy-catalog';

const strategyCatalog = useStrategyCatalogStore();

const runsPage = ref<PageResponse<RunSummary> | null>(null);
const errorMessage = ref<string | null>(null);
const isLoading = ref(true);

const offset = ref(0);
const limit = ref<number>(DEFAULT_PAGE_SIZE);
const statusFilter = ref<RunStatus | ''>('');
const strategyFilter = ref('');
const sortBy = ref<RunSortColumn>(DEFAULT_RUN_SORT_COLUMN);
const descending = ref(true);

const runs = computed(() => runsPage.value?.records ?? []);
const totalCount = computed(() => runsPage.value?.total ?? 0);
const hasUnfinishedRun = computed(() =>
  runs.value.some((run) => !isTerminalRunStatus(run.status)),
);
const hasActiveFilter = computed(
  () => statusFilter.value !== '' || strategyFilter.value !== '',
);

async function refreshRuns(): Promise<void> {
  // 首屏才显示「加载中」: 轮询把已看到一半的表格换成这三个字, 比不刷新还糟.
  isLoading.value = runsPage.value === null;
  errorMessage.value = null;

  try {
    runsPage.value = await fetchRuns({
      status: statusFilter.value,
      strategyId: strategyFilter.value,
      sortBy: sortBy.value,
      descending: descending.value,
      offset: offset.value,
      limit: limit.value,
    });
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.detail : '加载运行列表失败';
  } finally {
    isLoading.value = false;
  }
}

function reloadFromFirstPage(): void {
  offset.value = 0;
  void refreshRuns();
}

function goToOffset(nextOffset: number): void {
  offset.value = nextOffset;
  void refreshRuns();
}

const { start: startPolling, stop: stopPolling } = usePolling(refreshRuns);

watch(hasUnfinishedRun, (hasUnfinished) => {
  if (hasUnfinished) {
    startPolling();
  } else {
    stopPolling();
  }
});

onMounted(() => {
  void strategyCatalog.ensureLoaded();
  void refreshRuns();
});
</script>

<template>
  <section>
    <header class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h1 class="text-lg font-semibold text-slate-900">回测运行</h1>
      <RouterLink
        class="rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong"
        :to="{ name: 'run-submit' }"
      >
        新建回测
      </RouterLink>
    </header>

    <div class="mb-4 grid gap-3 sm:grid-cols-4">
      <label class="flex flex-col gap-1 text-sm text-slate-600">
        状态
        <select
          v-model="statusFilter"
          class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
          @change="reloadFromFirstPage"
        >
          <option value="">
            全部
          </option>
          <option
            v-for="status in RUN_STATUSES"
            :key="status"
            :value="status"
          >
            {{ describeRunStatus(status).label }}
          </option>
        </select>
      </label>

      <label class="flex flex-col gap-1 text-sm text-slate-600">
        策略
        <select
          v-model="strategyFilter"
          class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
          @change="reloadFromFirstPage"
        >
          <option value="">
            全部
          </option>
          <option
            v-for="strategy in strategyCatalog.strategies"
            :key="strategy.id"
            :value="strategy.id"
          >
            {{ strategy.name }}
          </option>
        </select>
      </label>

      <label class="flex flex-col gap-1 text-sm text-slate-600">
        排序
        <select
          v-model="sortBy"
          class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
          @change="reloadFromFirstPage"
        >
          <option
            v-for="sortColumn in RUN_SORT_COLUMNS"
            :key="sortColumn"
            :value="sortColumn"
          >
            {{ describeRunSortColumn(sortColumn) }}
          </option>
        </select>
      </label>

      <label class="flex flex-col gap-1 text-sm text-slate-600">
        每页
        <select
          v-model.number="limit"
          class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
          @change="reloadFromFirstPage"
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
    </div>

    <label class="mb-4 flex items-center gap-2 text-sm text-slate-600">
      <input
        v-model="descending"
        type="checkbox"
        class="size-4 accent-blue-700"
        @change="reloadFromFirstPage"
      >
      倒序
    </label>

    <ErrorBanner
      :message="errorMessage"
      @retry="refreshRuns"
    />

    <LoadingNotice v-if="isLoading" />

    <EmptyNotice
      v-else-if="runs.length === 0"
      :message="hasActiveFilter ? '当前筛选条件下没有运行' : '还没有任何回测运行'"
      :hint="hasActiveFilter ? '试试放宽筛选条件' : '点右上角「新建回测」提交第一次运行'"
    />

    <template v-else>
      <div class="overflow-x-auto rounded-lg border border-line bg-surface">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th class="px-3 py-2 font-medium">
                提交时间
              </th>
              <th class="px-3 py-2 font-medium">
                策略
              </th>
              <th class="px-3 py-2 font-medium">
                状态
              </th>
              <th class="px-3 py-2 font-medium">
                引擎判定
              </th>
              <th class="px-3 py-2 font-medium">
                交易日区间
              </th>
              <th class="px-3 py-2 font-medium">
                耗时
              </th>
              <th class="px-3 py-2 text-right font-medium">
                交易笔数
              </th>
              <th class="px-3 py-2 text-right font-medium">
                余额
              </th>
              <th class="px-3 py-2 font-medium" />
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr
              v-for="run in runs"
              :key="run.id"
              class="hover:bg-slate-50"
            >
              <td class="px-3 py-2 whitespace-nowrap text-slate-600">
                {{ formatDateTime(run.submitted_at) }}
              </td>
              <td class="px-3 py-2">
                {{ strategyCatalog.nameFor(run.strategy_id) }}
              </td>
              <td class="px-3 py-2">
                <StatusBadge v-bind="describeRunStatus(run.status)" />
              </td>
              <td class="px-3 py-2">
                <StatusBadge v-bind="describeEngineVerdict(run.is_success)" />
              </td>
              <td class="px-3 py-2 whitespace-nowrap text-slate-600">
                {{ formatTradingDay(run.start_trading_day) }} ~ {{ formatTradingDay(run.end_trading_day) }}
              </td>
              <td class="px-3 py-2 whitespace-nowrap text-slate-600">
                {{ formatDuration(run.duration_ms) }}
              </td>
              <td class="px-3 py-2 text-right text-slate-600">
                {{ formatCount(run.trade_count) }}
              </td>
              <td class="px-3 py-2 text-right text-slate-600">
                {{ formatAmount(run.balance) }}
              </td>
              <td class="px-3 py-2 text-right">
                <RouterLink
                  class="text-brand hover:underline"
                  :to="{ name: 'run-detail', params: { id: run.id } }"
                >
                  详情
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <PaginationBar
        :total="totalCount"
        :offset="offset"
        :limit="limit"
        @update:offset="goToOffset"
      />
    </template>
  </section>
</template>
