<script setup lang="ts">
/**
 * 多轮权益曲线的叠加图.
 *
 * **纯展示, 不自取**: 图形数据由父组件取好、对齐好再传进来 (`domain/equity.alignEquitySeries`) ——
 * 一轮一列的那张表与这张图必须画的是同一份数, 各取一次就迟早会对不上.
 *
 * ECharts 按需注册: 注册写在模块作用域, 而本组件与 `EquityChartPanel` 是两个模块, 各自 `use` 一次
 * 是必需的 (不是重复注册) —— 谁先被加载谁注册, 少一处就会出现"这张图能画那张图空白".
 * 比单轮那张图多注册了 `LegendComponent`: 多条线要靠图例认人.
 */

import { LineChart } from 'echarts/charts';
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { computed } from 'vue';
import VChart from 'vue-echarts';

import type { AlignedEquityCurve } from '../domain/equity';
import { formatAmount } from '../domain/format';
import EmptyNotice from './EmptyNotice.vue';

use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

const props = defineProps<{
  /** 各轮交易日的并集, 升序; 由 `alignEquitySeries` 给出, 与每条曲线逐位对应. */
  tradingDayLabels: string[];
  curves: AlignedEquityCurve[];
}>();

/**
 * 点多到一定程度就不画点 (同 `EquityChartPanel` 的 `showSymbol: false`).
 *
 * 少的时候必须画: 某轮只覆盖一天时, 它前后都是 `null`, 而折线要有**两个**点才成线段——不画点就
 * 整条看不见, 看上去像"这轮没有数据".
 */
const MAXIMUM_MARKED_POINT_COUNT = 60;

const chartOption = computed<EChartsOption>(() => ({
  // 图例要占一行: 网格上边留 40px, 否则图例会压在曲线上.
  grid: { left: 78, right: 20, top: 40, bottom: 60 },
  legend: { type: 'scroll', top: 8 },
  tooltip: {
    trigger: 'axis',
    valueFormatter: (value) => formatAmount(toChartNumber(value)),
  },
  xAxis: { type: 'category', data: props.tradingDayLabels, boundaryGap: false },
  yAxis: { type: 'value', scale: true },
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 12 }],
  series: props.curves.map((curve) => ({
    name: curve.label,
    type: 'line',
    showSymbol: props.tradingDayLabels.length <= MAXIMUM_MARKED_POINT_COUNT,
    // **断线就断线**: 某轮那天没有点时值为 `null`, 连起来等于编造一段它没结算过的权益, 于是"两轮
    // 覆盖区间不同"这件最该看见的事就被抹平了.
    connectNulls: false,
    data: curve.balances,
  })),
}));

/** 浮层拿到的值可能是数组 (`axis` 触发时是数组), 只取第一项; 取不到回 `null` 由格式化回占位符. */
function toChartNumber(value: unknown): number | null {
  const scalar = Array.isArray(value) ? value[0] : value;

  return typeof scalar === 'number' ? scalar : null;
}
</script>

<template>
  <div v-if="props.tradingDayLabels.length > 0">
    <!--
      高度必须给**外层 div**: vue-echarts 自带一条未分层的 `x-vue-echarts { height: 100% }`,
      而 Tailwind 的工具类在 `@layer utilities` 里 —— 未分层优先于任何图层, 直接写在 <VChart>
      上的高度会被压成 0, 图就画在零高容器里 (画面全空, 只有一句 ECharts 告警).
    -->
    <div class="h-80 w-full">
      <VChart
        :option="chartOption"
        autoresize
      />
    </div>
  </div>

  <EmptyNotice
    v-else
    message="没有可叠加的权益曲线"
    hint="至少有一轮取到了权益, 图上才有线; 各轮取不到的原因见下表表头"
  />
</template>
