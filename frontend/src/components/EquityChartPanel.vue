<script setup lang="ts">
/**
 * 权益曲线与回撤曲线.
 *
 * 自己取 `/runs/{id}/equity`, 父组件只负责「什么时候挂载它」. **两张独立的图**而不是一张双网格:
 * 各是一层简单 series, 一处画错不会连带另一处, 而本仓没有组件测试, 看得见的东西越少越容易确认.
 * 曲线是**逐日结算权益** (引擎 `Capital` 一天一行), 故图上没有盘中回撤——库里根本不存在.
 *
 * ECharts 按需注册: 全量 `import * as echarts from 'echarts'` 会把整个图表库塞进首屏那一块
 * chunk, 而这里只用到折线、直角坐标系、浮层与缩放. 注册写在模块作用域 (本组件是唯一消费者).
 */

import { LineChart } from 'echarts/charts';
import { DataZoomComponent, GridComponent, TooltipComponent } from 'echarts/components';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { computed, onMounted, ref } from 'vue';
import VChart from 'vue-echarts';

import { ApiError } from '../api/client';
import { fetchRunEquity } from '../api/runs';
import type { EquityPoint } from '../api/types';
import { buildEquitySeries, summarizeEquity } from '../domain/equity';
import { ABSENT_PLACEHOLDER, formatAmount, formatPercentRatio, formatTradingDay } from '../domain/format';
import EmptyNotice from './EmptyNotice.vue';
import ErrorBanner from './ErrorBanner.vue';
import LoadingNotice from './LoadingNotice.vue';

use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

const props = defineProps<{ runId: string }>();

interface SummaryRow {
  label: string;
  value: string;
}

const equityPoints = ref<EquityPoint[]>([]);
const errorMessage = ref<string | null>(null);
const isLoading = ref(true);

const equitySeries = computed(() => buildEquitySeries(equityPoints.value));
const equitySummary = computed(() => summarizeEquity(equityPoints.value));

/** 概要四行一次算好 (同 `RunDetailView.metricSections` 的做法), 不在模板里逐行写死. */
const summaryRows = computed<SummaryRow[]>(() => {
  const summary = equitySummary.value;

  if (summary === null) {
    return [];
  }

  const maximumDrawdownText = `${formatPercentRatio(summary.maximumDrawdownRatio)} (${formatTradingDay(summary.maximumDrawdownTradingDay)})`;

  return [
    { label: '初始权益', value: formatAmount(summary.initialBalance) },
    { label: '期末权益', value: formatAmount(summary.finalBalance) },
    { label: '最大回撤', value: maximumDrawdownText },
    { label: '累计收益率', value: formatPercentRatio(summary.totalReturnRatio) },
  ];
});

/** 图上纵轴读百分数, 故在**显示层**乘一次 100; 领域层一律用比例 (见 `domain/equity`). */
const drawdownPercentages = computed(() =>
  equitySeries.value.drawdownRatios.map((drawdownRatio) => drawdownRatio * 100),
);

const equityChartOption = computed<EChartsOption>(() => ({
  grid: { left: 78, right: 20, top: 20, bottom: 60 },
  tooltip: {
    trigger: 'axis',
    valueFormatter: (value) => formatAmount(toChartNumber(value)),
  },
  xAxis: { type: 'category', data: equitySeries.value.tradingDayLabels, boundaryGap: false },
  yAxis: { type: 'value', scale: true },
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 12 }],
  series: [
    {
      name: '结算权益',
      type: 'line',
      showSymbol: false,
      data: equitySeries.value.balances,
    },
  ],
}));

const drawdownChartOption = computed<EChartsOption>(() => ({
  grid: { left: 78, right: 20, top: 20, bottom: 60 },
  tooltip: {
    trigger: 'axis',
    valueFormatter: (value) => describeDrawdownTooltip(value),
  },
  xAxis: { type: 'category', data: equitySeries.value.tradingDayLabels, boundaryGap: false },
  yAxis: { type: 'value', max: 0 },
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 12 }],
  series: [
    {
      name: '回撤',
      type: 'line',
      showSymbol: false,
      areaStyle: {},
      data: drawdownPercentages.value,
    },
  ],
}));

/** 浮层拿到的值可能是数组 (`axis` 触发时是数组), 只取第一项; 取不到回 `null` 由格式化回占位符. */
function toChartNumber(value: unknown): number | null {
  const scalar = Array.isArray(value) ? value[0] : value;

  return typeof scalar === 'number' ? scalar : null;
}

/** 图上纵轴是百分数, 浮层要还原成比例再交给 `formatPercentRatio` (它只收比例). */
function describeDrawdownTooltip(value: unknown): string {
  const drawdownPercentage = toChartNumber(value);

  return drawdownPercentage === null
    ? ABSENT_PLACEHOLDER
    : formatPercentRatio(drawdownPercentage / 100);
}

async function loadEquity(): Promise<void> {
  errorMessage.value = null;
  isLoading.value = true;

  try {
    const runEquity = await fetchRunEquity(props.runId);
    equityPoints.value = runEquity.points;
  } catch (error) {
    // 失败/取消/超时的轮没有结果库 (后端回 404), 未结束的轮回 409: 两种都不是页面级失败,
    // 一句话说清并给一次重试就够了.
    equityPoints.value = [];
    errorMessage.value = error instanceof ApiError ? error.detail : '加载权益曲线失败';
  } finally {
    isLoading.value = false;
  }
}

onMounted(loadEquity);
</script>

<template>
  <section>
    <h2 class="mb-2 text-sm font-semibold text-slate-700">
      权益曲线
    </h2>

    <ErrorBanner
      :message="errorMessage"
      @retry="loadEquity"
    />

    <LoadingNotice v-if="isLoading" />

    <template v-else-if="equitySummary">
      <dl class="mb-3 grid gap-x-6 gap-y-2 rounded-lg border border-line bg-surface p-4 sm:grid-cols-2 lg:grid-cols-4">
        <div
          v-for="summaryRow in summaryRows"
          :key="summaryRow.label"
          class="flex justify-between gap-3"
        >
          <dt class="text-xs text-slate-500">
            {{ summaryRow.label }}
          </dt>
          <dd class="text-right text-xs text-slate-800">
            {{ summaryRow.value }}
          </dd>
        </div>
      </dl>

      <div class="space-y-4">
        <div>
          <p class="mb-1 text-xs text-slate-500">
            逐日结算权益
          </p>
          <VChart
            class="h-72 w-full"
            :option="equityChartOption"
            autoresize
          />
        </div>
        <div>
          <p class="mb-1 text-xs text-slate-500">
            回撤 (相对逐日峰值)
          </p>
          <VChart
            class="h-56 w-full"
            :option="drawdownChartOption"
            autoresize
          />
        </div>
      </div>
    </template>

    <EmptyNotice v-else-if="!errorMessage" message="这个运行没有权益数据" hint="结果库的 Capital 表里没有行" />
  </section>
</template>
