<script setup lang="ts">
/**
 * 多轮对比: 指标并列, 权益曲线叠加.
 *
 * **`?ids=a,b,c` 是唯一的状态源**, 勾选框只是它的另一种写法: 勾一下就是 `router.replace` 一次, 表
 * 与图都从路由重新算起. 于是"复制地址栏发给别人"与"在这个页面上勾出来"走的是同一条路, 没有第二份
 * 会漂移的选择状态.
 *
 * 取数**一次性, 不轮询**: 列表页轮询是因为那里要看着轮次跑完, 而这一页的语义是"拿这几次的静态快照
 * 比一比", 每 2 秒重画六条曲线只会让浮层没法用. 要新的就点「刷新」.
 *
 * 反馈分流: 对比取数失败是页面级 (c 类, 留 banner 可重试); 候选人列表取数失败只影响选择器那一块
 * (同样留 banner) —— 两者失败的原因与处置都不一样, 共用一个 ref 会互相覆盖. 而**某一轮没有曲线**
 * 既不是失败也不是页面级问题: 它是那一列自己的事, 由表头的一行小字说明.
 */

import { computed, onMounted, ref, watch } from 'vue';
import { ElButton, ElCheckbox } from 'element-plus';
import { RouterLink, useRoute, useRouter } from 'vue-router';

import { MAXIMUM_COMPARISON_RUNS, fetchRunComparison, fetchRuns } from '../api/runs';
import { MAXIMUM_PAGE_SIZE } from '../api/types';
import type { RunComparison, RunSummary } from '../api/types';
import ContentSkeleton from '../components/ContentSkeleton.vue';
import EmptyNotice from '../components/EmptyNotice.vue';
import EquityOverlayChart from '../components/EquityOverlayChart.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import RunComparisonTable from '../components/RunComparisonTable.vue';
import StatusBadge from '../components/StatusBadge.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import { describeApiFailure } from '../composables/use-feedback';
import { alignEquitySeries } from '../domain/equity';
import { formatDateTime } from '../domain/format';
import { buildRunComparisonTableModel, describeComparisonRound } from '../domain/run-comparison';
import { describeRunStatus, isTerminalRunStatus } from '../domain/run-status';
import { useStrategyCatalogStore } from '../stores/strategy-catalog';

const route = useRoute();
const router = useRouter();
const strategyCatalog = useStrategyCatalogStore();

const comparison = ref<RunComparison | null>(null);
/** 可勾选的那一份: 只列**终态**的轮——未结束的轮没有结果库, 连上去也只是一条空白列. */
const candidateRuns = ref<RunSummary[]>([]);
const errorMessage = ref<string | null>(null);
const candidateErrorMessage = ref<string | null>(null);
const isLoading = ref(false);
const isLoadingCandidates = ref(true);

/**
 * 勾选变化会连着发几次请求 (连点两个框就是两次), 迟到的回包必须丢掉: 否则表里会出现上一次勾选的
 * 那几轮, 与地址栏对不上 (同 `RunSubmitView.selectionToken` 的作法).
 */
let comparisonToken = 0;

const selectedRunIds = computed(() => parseComparisonRunIds(route.query.ids));
const isSelectionFull = computed(
  () => selectedRunIds.value.length >= MAXIMUM_COMPARISON_RUNS,
);

/** 策略名的查表: 目录 store 里有的用它, 没有的由 `run-comparison` 回落到策略号. */
const strategyNamesByStrategyId = computed<Record<string, string>>(() =>
  Object.fromEntries(
    strategyCatalog.strategies.map((strategy) => [strategy.id, strategy.name]),
  ),
);

const comparisonEntries = computed(() => comparison.value?.runs ?? []);

const tableModel = computed(() =>
  buildRunComparisonTableModel(
    comparisonEntries.value,
    strategyNamesByStrategyId.value,
  ),
);

/** 图上那条线的名字与表头**同一个串** (`describeComparisonRound`), 否则人没法把线与列对上. */
const overlaySeries = computed(() =>
  alignEquitySeries(
    comparisonEntries.value.map((entry, index) => ({
      label: describeComparisonRound(index),
      points: entry.equity_points,
    })),
  ),
);

/**
 * 解析地址栏里的 `ids`.
 *
 * 去空白、丢空项、保序去重 (同一轮勾不了两次, 但地址栏可以手写成 `?ids=a,a`). **不截断到 6 个**:
 * 手写的超长地址该让后端回一句「一次最多对比 6 轮」, 而不是在这里悄悄丢掉一轮——用户会以为少的那
 * 一轮是自己没勾上.
 */
function parseComparisonRunIds(rawIds: unknown): string[] {
  const rawIdValues = Array.isArray(rawIds) ? rawIds : [rawIds];
  const runIds = rawIdValues.flatMap((rawIdValue) =>
    typeof rawIdValue === 'string' ? rawIdValue.split(',') : [],
  );

  return [
    ...new Set(runIds.map((runId) => runId.trim()).filter((runId) => runId !== '')),
  ];
}

function isRunSelected(runId: string): boolean {
  return selectedRunIds.value.includes(runId);
}

/** 勾/取消勾: 只改地址栏, 取数由 `watch(route.query.ids)` 统一发起; 取消到空就不再请求. */
function toggleRunSelection(runId: string): void {
  const nextRunIds = isRunSelected(runId)
    ? selectedRunIds.value.filter((selectedRunId) => selectedRunId !== runId)
    : [...selectedRunIds.value, runId];

  void router.replace({
    name: 'compare',
    query: nextRunIds.length > 0 ? { ids: nextRunIds.join(',') } : {},
  });
}

async function loadComparison(): Promise<void> {
  const runIds = selectedRunIds.value;
  const requestToken = ++comparisonToken;

  errorMessage.value = null;

  if (runIds.length === 0) {
    comparison.value = null;
    isLoading.value = false;

    return;
  }

  isLoading.value = true;

  try {
    const runComparison = await fetchRunComparison(runIds);

    if (requestToken !== comparisonToken) {
      return;
    }

    comparison.value = runComparison;
  } catch (error) {
    if (requestToken !== comparisonToken) {
      return;
    }

    comparison.value = null;
    errorMessage.value = describeApiFailure(error, '加载对比数据失败');
  } finally {
    if (requestToken === comparisonToken) {
      isLoading.value = false;
    }
  }
}

/**
 * 选择器里的候选.
 *
 * 只取**一页**最近提交的轮 (后端没有"按多个状态过滤"的口子, 终态那几种得在这里挑出来), 更早的轮
 * 从运行详情页的「加入对比」进来 —— 那条路径不受这一页取了多少条限制.
 */
async function loadCandidateRuns(): Promise<void> {
  candidateErrorMessage.value = null;

  try {
    const runPage = await fetchRuns({
      sortBy: 'submitted_at',
      descending: true,
      limit: MAXIMUM_PAGE_SIZE,
    });

    candidateRuns.value = runPage.records.filter((run) =>
      isTerminalRunStatus(run.status),
    );
  } catch (error) {
    candidateRuns.value = [];
    candidateErrorMessage.value = describeApiFailure(error, '加载可对比的运行失败');
  } finally {
    isLoadingCandidates.value = false;
  }
}

watch(
  () => route.query.ids,
  () => {
    void loadComparison();
  },
);

onMounted(() => {
  void strategyCatalog.ensureLoaded();
  void loadCandidateRuns();
  void loadComparison();
});
</script>

<template>
  <section>
    <PageHeader title="多轮对比">
      <template #leading>
        <RouterLink
          class="text-sm text-brand hover:underline"
          :to="{ name: 'runs' }"
        >
          ← 运行列表
        </RouterLink>
      </template>

      <template #actions>
        <ElButton
          :loading="isLoading"
          :disabled="selectedRunIds.length === 0"
          @click="loadComparison"
        >
          刷新
        </ElButton>
      </template>
    </PageHeader>

    <SurfaceCard
      class="mb-6"
      title="选择要对比的运行"
    >
      <p class="mb-2 text-xs text-slate-500">
        已选 {{ selectedRunIds.length }} / {{ MAXIMUM_COMPARISON_RUNS }} 轮. 这里只列最近
        {{ MAXIMUM_PAGE_SIZE }} 轮里<strong class="font-semibold">已结束</strong>的运行;
        更早的轮请到它的详情页点「加入对比」.
      </p>

      <ErrorBanner
        :message="candidateErrorMessage"
        retry-label="重新加载"
        @retry="loadCandidateRuns"
      />

      <ContentSkeleton v-if="isLoadingCandidates" />

      <EmptyNotice
        v-else-if="candidateRuns.length === 0 && !candidateErrorMessage"
        message="没有已结束的运行可以对比"
        hint="先提交一轮回测, 等它跑完再回来"
      />

      <!-- 候选多于一屏时给个滚动区: 一页最多 100 条, 摊开会把下面那张表挤到屏幕外. -->
      <ul
        v-else
        class="max-h-72 divide-y divide-line overflow-y-auto"
      >
        <li
          v-for="run in candidateRuns"
          :key="run.id"
          class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
        >
          <ElCheckbox
            :model-value="isRunSelected(run.id)"
            :disabled="isSelectionFull && !isRunSelected(run.id)"
            @change="toggleRunSelection(run.id)"
          >
            <span class="font-mono text-xs text-slate-700">{{ run.id }}</span>
          </ElCheckbox>
          <span class="text-xs text-slate-500">
            {{ strategyCatalog.nameFor(run.strategy_id) }}
          </span>
          <span class="text-xs text-slate-500">
            {{ formatDateTime(run.submitted_at) }}
          </span>
          <StatusBadge v-bind="describeRunStatus(run.status)" />
          <RouterLink
            class="text-xs text-brand hover:underline"
            :to="{ name: 'run-detail', params: { id: run.id } }"
          >
            详情
          </RouterLink>
        </li>
      </ul>

      <p
        v-if="isSelectionFull"
        class="mt-2 text-xs text-amber-700"
      >
        已达 {{ MAXIMUM_COMPARISON_RUNS }} 轮上限, 想换一轮请先取消一个勾选.
      </p>
    </SurfaceCard>

    <ErrorBanner
      :message="errorMessage"
      @retry="loadComparison"
    />

    <ContentSkeleton v-if="isLoading && comparison === null" />

    <EmptyNotice
      v-else-if="selectedRunIds.length === 0"
      message="还没有选择要对比的运行"
      hint="在上面勾选两轮 (最多 6 轮); 也可以从某一轮的详情页点「加入对比」"
    />

    <template v-else-if="comparison">
      <SurfaceCard
        class="mb-6"
        title="权益曲线叠加"
      >
        <p class="mb-2 text-xs text-slate-500">
          逐日结算权益. 某轮没有的交易日<strong class="font-semibold">不连线</strong> (不插值、不取前值):
          两轮覆盖的区间不同, 图上就该看见一段真空.
        </p>
        <EquityOverlayChart
          :trading-day-labels="overlaySeries.tradingDayLabels"
          :curves="overlaySeries.curves"
        />
      </SurfaceCard>

      <SurfaceCard title="指标并列">
        <RunComparisonTable
          :columns="tableModel.columns"
          :row-groups="tableModel.rowGroups"
        />
      </SurfaceCard>
    </template>
  </section>
</template>
