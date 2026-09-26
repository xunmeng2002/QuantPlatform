<script setup lang="ts">
/**
 * 运行列表.
 *
 * 非终态的轮存在时每 2 秒刷新一次, 全部结束就停 —— 这是本页唯一"活"的地方, 也是轮询唯一的
 * 使用处. 筛选条件改变时回到第一页: 停在第三页再改筛选, 多半会看到一张空表.
 */

import { computed, onMounted, ref, watch } from 'vue';
import { ElButton, ElCheckbox, ElOption, ElSelect, ElTable, ElTableColumn } from 'element-plus';
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
      <!-- tag="a" + href 而不是 @click="router.push": 中键 / 右键「在新标签页打开」是真实用法,
           丢掉真锚点就没了. navigate 会自己 preventDefault, 左键仍是单页跳转. -->
      <RouterLink
        v-slot="{ navigate, href }"
        custom
        :to="{ name: 'run-submit' }"
      >
        <ElButton
          tag="a"
          type="primary"
          :href="href"
          @click="navigate"
        >
          新建回测
        </ElButton>
      </RouterLink>
    </header>

    <!-- 筛选 / 排序 / 每页. 「全部」不再是一个 value="" 的选项 —— el-option 的 value 为空串时
         EP 永远显示不出它的标签 (空串被判为"未选中"), 改用 placeholder + 右上角的 × 清空;
         value-on-clear 必须显式写 '' (EP 的清空默认值是 undefined, 与 domain 的判据不符). -->
    <div class="mb-4 grid gap-3 sm:grid-cols-4">
      <label class="flex flex-col gap-1 text-sm text-slate-600">
        状态
        <ElSelect
          v-model="statusFilter"
          clearable
          placeholder="全部状态"
          :value-on-clear="''"
          @change="reloadFromFirstPage"
        >
          <ElOption
            v-for="status in RUN_STATUSES"
            :key="status"
            :label="describeRunStatus(status).label"
            :value="status"
          />
        </ElSelect>
      </label>

      <label class="flex flex-col gap-1 text-sm text-slate-600">
        策略
        <ElSelect
          v-model="strategyFilter"
          clearable
          placeholder="全部策略"
          :value-on-clear="''"
          @change="reloadFromFirstPage"
        >
          <ElOption
            v-for="strategy in strategyCatalog.strategies"
            :key="strategy.id"
            :label="strategy.name"
            :value="strategy.id"
          />
        </ElSelect>
      </label>

      <!-- 排序与每页都**没有**空值: 清空它们会让 sort_by 变成空串, 后端直接判非法.
           所以这两个不 clearable, 也就不需要 placeholder. -->
      <label class="flex flex-col gap-1 text-sm text-slate-600">
        排序
        <ElSelect
          v-model="sortBy"
          @change="reloadFromFirstPage"
        >
          <ElOption
            v-for="sortColumn in RUN_SORT_COLUMNS"
            :key="sortColumn"
            :label="describeRunSortColumn(sortColumn)"
            :value="sortColumn"
          />
        </ElSelect>
      </label>

      <label class="flex flex-col gap-1 text-sm text-slate-600">
        每页
        <ElSelect
          v-model="limit"
          @change="reloadFromFirstPage"
        >
          <ElOption
            v-for="pageSize in PAGE_SIZE_OPTIONS"
            :key="pageSize"
            :label="String(pageSize)"
            :value="pageSize"
          />
        </ElSelect>
      </label>
    </div>

    <!-- el-checkbox 的根节点自己就是一个 <label>, 所以这里不能再套一层 label (嵌套 label 是非法
         HTML, 而且点一下会切两次), 文字改为它的子节点. -->
    <div class="mb-4">
      <ElCheckbox
        v-model="descending"
        @change="reloadFromFirstPage"
      >
        倒序
      </ElCheckbox>
    </div>

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
      <!-- 不加 row-key: EP 里它的全部用途是选中 / 展开 / 树形 / 当前行的记账, 本表一个都不用,
           2 秒轮询整体换数据也是按位置 patch. 空表头就是省掉 label (它没有默认值). -->
      <div class="overflow-hidden rounded-lg border border-line">
        <ElTable :data="runs">
          <ElTableColumn label="提交时间">
            <template #default="{ row }">
              <span class="whitespace-nowrap">{{ formatDateTime(row.submitted_at) }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn label="策略">
            <template #default="{ row }">
              {{ strategyCatalog.nameFor(row.strategy_id) }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="状态">
            <template #default="{ row }">
              <StatusBadge v-bind="describeRunStatus(row.status)" />
            </template>
          </ElTableColumn>

          <ElTableColumn label="引擎判定">
            <template #default="{ row }">
              <StatusBadge v-bind="describeEngineVerdict(row.is_success)" />
            </template>
          </ElTableColumn>

          <ElTableColumn label="交易日区间">
            <template #default="{ row }">
              <span class="whitespace-nowrap">
                {{ formatTradingDay(row.start_trading_day) }} ~ {{ formatTradingDay(row.end_trading_day) }}
              </span>
            </template>
          </ElTableColumn>

          <ElTableColumn label="耗时">
            <template #default="{ row }">
              <span class="whitespace-nowrap">{{ formatDuration(row.duration_ms) }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn
            label="交易笔数"
            align="right"
          >
            <template #default="{ row }">
              {{ formatCount(row.trade_count) }}
            </template>
          </ElTableColumn>

          <ElTableColumn
            label="余额"
            align="right"
          >
            <template #default="{ row }">
              {{ formatAmount(row.balance) }}
            </template>
          </ElTableColumn>

          <ElTableColumn align="right">
            <template #default="{ row }">
              <RouterLink
                class="text-brand hover:underline"
                :to="{ name: 'run-detail', params: { id: row.id } }"
              >
                详情
              </RouterLink>
            </template>
          </ElTableColumn>
        </ElTable>
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
