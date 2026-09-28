<script setup lang="ts">
/**
 * 多轮对比表: 一轮一列, 指标与参数为行.
 *
 * **纯展示**: 行与列都由父组件算好 (`domain/run-comparison.buildRunComparisonTableModel`) 传进来,
 * 本组件不碰接口、不查 store —— 表与图必须画同一份数, 造型只做一次是这件事唯一的保证.
 *
 * 表头那两行徽标 (`status` 与 `is_success`) 是**分开**的两个信号, 同运行详情页: 进程退出码为 0 只
 * 说明宿主没崩, 引擎可以自己报失败. 曲线缺失只说这一列的事, 不串到别的列.
 */

import { RouterLink } from 'vue-router';

import { ABSENT_PLACEHOLDER } from '../domain/format';
import type {
  RunComparisonColumn,
  RunComparisonRow,
  RunComparisonRowGroup,
} from '../domain/run-comparison';
import { describeEngineVerdict, describeRunStatus } from '../domain/run-status';
import StatusBadge from './StatusBadge.vue';

defineProps<{
  columns: RunComparisonColumn[];
  rowGroups: RunComparisonRowGroup[];
}>();

function readCell(column: RunComparisonColumn, row: RunComparisonRow): string {
  return column.valuesByRowKey.get(row.key) ?? ABSENT_PLACEHOLDER;
}
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full text-xs">
      <thead class="text-left text-slate-500">
        <tr>
          <th
            scope="col"
            class="w-40 py-2 pr-3 align-bottom font-medium"
          >
            指标
          </th>
          <th
            v-for="column in columns"
            :key="column.runId"
            scope="col"
            class="min-w-40 py-2 pr-3 align-bottom font-medium"
          >
            <div class="flex flex-col gap-1">
              <span class="text-sm font-semibold text-slate-800">{{ column.heading }}</span>
              <RouterLink
                class="font-mono text-[10px] break-all text-brand hover:underline"
                :to="{ name: 'run-detail', params: { id: column.runId } }"
              >
                {{ column.runId }}
              </RouterLink>
              <span class="flex flex-wrap gap-1">
                <StatusBadge v-bind="describeRunStatus(column.status)" />
                <StatusBadge v-bind="describeEngineVerdict(column.isSuccess)" />
              </span>
              <!-- 曲线缺失**只在表头这一处说**, 指标列照常显示: 两者是独立的可用性 (结果库文件
                   被清掉之后指标仍在 `Runs` 表上), 把整列标成不可用会连同还成立的数据一起否掉. -->
              <span
                v-if="column.equityUnavailableReason"
                class="text-[10px] text-amber-700"
              >
                曲线缺失: {{ column.equityUnavailableReason }}
              </span>
            </div>
          </th>
        </tr>
      </thead>

      <tbody
        v-for="rowGroup in rowGroups"
        :key="rowGroup.title"
        class="divide-y divide-line border-t border-line"
      >
        <tr>
          <th
            :colspan="columns.length + 1"
            scope="colgroup"
            class="bg-surface py-1 pr-3 text-left font-semibold text-slate-600"
          >
            {{ rowGroup.title }}
          </th>
        </tr>
        <tr
          v-for="row in rowGroup.rows"
          :key="row.key"
        >
          <th
            scope="row"
            class="py-1 pr-3 font-normal text-slate-500"
          >
            {{ row.label }}
          </th>
          <td
            v-for="column in columns"
            :key="column.runId"
            class="py-1 pr-3 break-all text-slate-800"
          >
            {{ readCell(column, row) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
