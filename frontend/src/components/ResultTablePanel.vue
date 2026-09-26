<script setup lang="ts">
/**
 * 引擎结果表的明细分页表.
 *
 * 页签就是 `RESULT_TABLE_NAMES` (后端白名单的镜像), **按需取数**: 打开哪个页签才取哪个. 表头取
 * 响应里的 `columns` 而不是在界面上写死——`Order` 有 33 列, 写死就是 33 处会漂移的真相.
 */

import { computed, ref, watch } from 'vue';

import { fetchRunResultTable } from '../api/runs';
import {
  DEFAULT_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
  RESULT_TABLE_LABELS,
  RESULT_TABLE_NAMES,
} from '../api/types';
import type {
  ResultTableName,
  ResultTableRecord,
  ResultTableValue,
} from '../api/types';
import { describeApiFailure } from '../composables/use-feedback';
import { ABSENT_PLACEHOLDER, formatAmount, formatCount, formatFlag, formatTradingDay } from '../domain/format';
import EmptyNotice from './EmptyNotice.vue';
import ErrorBanner from './ErrorBanner.vue';
import LoadingNotice from './LoadingNotice.vue';
import PaginationBar from './PaginationBar.vue';

const props = defineProps<{ runId: string }>();

const TRADING_DAY_COLUMN_NAME = 'TradingDay';

/** 非整数保留 4 位: 引擎的价格/费用列到小数第 4 位, 而浮点原样 `String()` 会给出 16 位的尾巴. */
const DECIMAL_FRACTION_DIGITS = 4;

const activeTable = ref<ResultTableName>(RESULT_TABLE_NAMES[0]);
const columns = ref<string[]>([]);
const records = ref<ResultTableRecord[]>([]);
const total = ref(0);
const offset = ref(0);
const pageSize = ref<number>(DEFAULT_PAGE_SIZE);
const errorMessage = ref<string | null>(null);
const isLoading = ref(false);

const isEmpty = computed(() => records.value.length === 0);

async function loadTablePage(): Promise<void> {
  errorMessage.value = null;
  isLoading.value = true;

  try {
    const tablePage = await fetchRunResultTable(props.runId, activeTable.value, {
      offset: offset.value,
      limit: pageSize.value,
    });

    columns.value = tablePage.columns;
    records.value = tablePage.records;
    total.value = tablePage.total;
  } catch (error) {
    columns.value = [];
    records.value = [];
    total.value = 0;
    errorMessage.value = describeApiFailure(error, '加载结果表失败');
  } finally {
    isLoading.value = false;
  }
}

/** 换页签回第一页: 沿用上一页的 `offset` 会落到新表的中间, 而用户刚打开它. */
function selectTable(tableName: ResultTableName): void {
  if (tableName === activeTable.value) {
    return;
  }

  activeTable.value = tableName;
  offset.value = 0;
}

/** 换页大小回第一页: 留在原来的 `offset` 上会跳过或重看一段. */
function resetToFirstPage(): void {
  offset.value = 0;
}

function goToOffset(nextOffset: number): void {
  offset.value = nextOffset;
}

function describeTabClass(tableName: ResultTableName): string {
  return tableName === activeTable.value
    ? 'border-brand bg-brand text-white'
    : 'border-line bg-surface text-slate-600 hover:bg-slate-50';
}

/**
 * 单元格的展示文本.
 *
 * `TradingDay` 是唯一的按列名特判 (引擎在 5 张表里都把它写成 8 字符 `YYYYMMDD`); 其余列按值渲染,
 * 因为它们的语义只有引擎自己知道. 该特判不会改写坏别的列: `formatTradingDay` 对非 8 位数字串
 * 原样返回.
 */
function describeCell(columnName: string, value: ResultTableValue | undefined): string {
  if (columnName === TRADING_DAY_COLUMN_NAME) {
    return formatTradingDay(typeof value === 'string' ? value : null);
  }

  if (value === null || value === undefined) {
    return ABSENT_PLACEHOLDER;
  }

  if (typeof value === 'number') {
    return Number.isInteger(value)
      ? formatCount(value)
      : formatAmount(value, DECIMAL_FRACTION_DIGITS);
  }

  if (typeof value === 'boolean') {
    return formatFlag(value);
  }

  return value;
}

watch([activeTable, offset, pageSize], loadTablePage, { immediate: true });
</script>

<template>
  <section>
    <h2 class="mb-2 text-sm font-semibold text-slate-700">
      明细表
    </h2>

    <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap gap-2">
        <button
          v-for="tableName in RESULT_TABLE_NAMES"
          :key="tableName"
          type="button"
          class="rounded border px-3 py-1 text-xs font-medium"
          :class="describeTabClass(tableName)"
          @click="selectTable(tableName)"
        >
          {{ RESULT_TABLE_LABELS[tableName] }}
        </button>
      </div>

      <label class="flex items-center gap-2 text-sm font-medium text-slate-700">
        每页
        <select
          v-model.number="pageSize"
          class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
          @change="resetToFirstPage"
        >
          <option v-for="pageSizeOption in PAGE_SIZE_OPTIONS" :key="pageSizeOption" :value="pageSizeOption">
            {{ pageSizeOption }}
          </option>
        </select>
      </label>
    </div>

    <ErrorBanner :message="errorMessage" retry-label="重新加载" @retry="loadTablePage" />

    <LoadingNotice v-if="isLoading" />

    <template v-else-if="!errorMessage">
      <EmptyNotice v-if="isEmpty" message="这张表没有数据" hint="引擎这一轮没有往这张表里写行" />

      <div v-else class="overflow-x-auto rounded-lg border border-line">
        <table class="min-w-full text-xs">
          <thead class="bg-slate-50 text-slate-500">
            <tr>
              <th v-for="columnName in columns" :key="columnName" class="px-3 py-2 text-left font-medium whitespace-nowrap">
                {{ columnName }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line bg-surface text-slate-700">
            <!-- 行的 `:key` 用「页内序号 + 本页起点」: 结果表没有暴露主键, 而翻页整批换掉这张表,
                 位置键在这里是稳定的 (不排序、不复用旧行). -->
            <tr v-for="(record, recordIndex) in records" :key="`${offset}-${recordIndex}`">
              <td v-for="columnName in columns" :key="columnName" class="px-3 py-1.5 whitespace-nowrap">
                {{ describeCell(columnName, record[columnName]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <PaginationBar
        :total="total"
        :offset="offset"
        :limit="pageSize"
        @update:offset="goToOffset"
      />
    </template>
  </section>
</template>
