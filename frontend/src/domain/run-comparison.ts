/**
 * 对比页的视图模型: 一轮一列, 行是各轮共有的指标加上参数的并集.
 *
 * 分工是"取数 → 造型 → 画": 本文件只做造型 (纯函数, 故能在 vitest 的 node 环境里直接测), 组件只
 * 管把表画出来. 造型里唯一有判断的两处:
 *
 *   1. **列的取值按 (节, 行名) 取**, 不是按位置: 各节的行名在节内唯一, 但两节可以重名 (今天还没有,
 *      加一行 `余额` 到别节就会撞上), 故键里带上节标题;
 *   2. **参数并集**: 两轮可能一个参数键多一个键少 (换过版本的策略尤其如此), 行取并集、缺的那格显示
 *      占位符 —— 这恰恰是"这两轮差在哪"最该看见的一格.
 */

import type { RunComparisonEntry, RunStatus } from '../api/types';
import { ABSENT_PLACEHOLDER, formatFlag } from './format';
import { buildSummaryMetricSections } from './run-metrics';

/** 参数并集那一节的标题; 它不在 `run-metrics` 的行定义里 (那些行都来自运行记录本身). */
export const PARAMETER_ROW_GROUP_TITLE = '提交的参数';

export interface RunComparisonRow {
  /** `节标题·行名`, 用来在列里取值. */
  key: string;
  label: string;
}

export interface RunComparisonRowGroup {
  title: string;
  rows: RunComparisonRow[];
}

export interface RunComparisonColumn {
  runId: string;
  /** 表头文案, 与图上那条线的图例名**同一个串**. */
  heading: string;
  status: RunStatus;
  isSuccess: boolean | null;
  /** 这一轮的曲线为什么没有; `null` 即有. 指标不受它影响 (两者是独立的可用性). */
  equityUnavailableReason: string | null;
  valuesByRowKey: Map<string, string>;
}

export interface RunComparisonTableModel {
  rowGroups: RunComparisonRowGroup[];
  columns: RunComparisonColumn[];
}

/**
 * 第几轮的说法.
 *
 * 表头与图上那条线的图例名共用这一份: 两处各写一遍的话, 改文案时漏一处就会出现"图例上是第 1 轮,
 * 表头是第 1 轮" 之外的东西, 而人靠这个名字把线与列对上.
 */
export function describeComparisonRound(index: number): string {
  return `第 ${index + 1} 轮`;
}

/**
 * 把一次对比的响应收成一张表的形状.
 *
 * `strategyNamesByStrategyId` 是**查好了的策略名表** (来自策略目录 store), 查不到就回落到策略号
 * 本身——与 `useStrategyCatalogStore.nameFor` 的兜底一致: 授权被撤销后策略不再可见, 而历史运行仍
 * 指着它, 那时显示 id 比显示空白有用.
 *
 * 行定义取**第一列**的那一份: `buildSummaryMetricSections` 的行名与行序只由定义决定, 与具体是哪
 * 一轮无关, 故各列的行集合逐字相同; 取值仍逐列各算一份 (`策略` 那一行的值本来就随列变).
 */
export function buildRunComparisonTableModel(
  entries: RunComparisonEntry[],
  strategyNamesByStrategyId: Record<string, string>,
): RunComparisonTableModel {
  const firstEntry = entries.at(0);

  if (firstEntry === undefined) {
    return { rowGroups: [], columns: [] };
  }

  const parameterKeys = collectParameterKeys(entries);

  return {
    rowGroups: [
      ...buildSummaryMetricSections(
        firstEntry.summary,
        readStrategyName(strategyNamesByStrategyId, firstEntry.summary.strategy_id),
      ).map((section) => ({
        title: section.title,
        rows: section.rows.map((row) => toRow(section.title, row.label)),
      })),
      {
        title: PARAMETER_ROW_GROUP_TITLE,
        rows: parameterKeys.map((parameterKey) =>
          toRow(PARAMETER_ROW_GROUP_TITLE, parameterKey),
        ),
      },
    ],
    columns: entries.map((entry, index) => ({
      runId: entry.summary.id,
      heading: describeComparisonRound(index),
      status: entry.summary.status,
      isSuccess: entry.summary.is_success,
      equityUnavailableReason: entry.equity_unavailable_reason,
      valuesByRowKey: buildColumnValues(
        entry,
        strategyNamesByStrategyId,
        parameterKeys,
      ),
    })),
  };
}

/** 把一列各节的值拉平成 `行键 → 显示文本`. */
function buildColumnValues(
  entry: RunComparisonEntry,
  strategyNamesByStrategyId: Record<string, string>,
  parameterKeys: string[],
): Map<string, string> {
  const valuesByRowKey = new Map<string, string>();

  for (const section of buildSummaryMetricSections(
    entry.summary,
    readStrategyName(strategyNamesByStrategyId, entry.summary.strategy_id),
  )) {
    for (const row of section.rows) {
      valuesByRowKey.set(toRow(section.title, row.label).key, row.value);
    }
  }

  // 各列的参数自己解析: 某一轮的 `params_json` 坏掉时只影响它这一列, 别的列照显示.
  const submittedParameters = readSubmittedParameters(entry.summary.params_json);

  for (const parameterKey of parameterKeys) {
    valuesByRowKey.set(
      toRow(PARAMETER_ROW_GROUP_TITLE, parameterKey).key,
      formatParameterValue(submittedParameters[parameterKey]),
    );
  }

  return valuesByRowKey;
}

/** 各轮参数键的并集, 按默认排序 (字典序) 稳定排列——同一次对比刷新两次的行序必须一样. */
function collectParameterKeys(entries: RunComparisonEntry[]): string[] {
  const parameterKeys = new Set<string>();

  for (const entry of entries) {
    for (const parameterKey of Object.keys(
      readSubmittedParameters(entry.summary.params_json),
    )) {
      parameterKeys.add(parameterKey);
    }
  }

  return [...parameterKeys].sort();
}

/**
 * 读一轮提交的参数.
 *
 * 解不动就当作"这套参数读不出来" (各格显示占位符), 而不是让整页报错: 参数是**背着指标的额外
 * 信息**, 为它把已经拿到的指标也挡掉不划算. 与后端 `parse_configuration_object` 的降级同一条口径
 * (那边解不动也只是一条 warning 加空参数集).
 */
function readSubmittedParameters(paramsJson: string): Record<string, unknown> {
  let parsedParameters: unknown;

  try {
    parsedParameters = JSON.parse(paramsJson);
  } catch {
    return {};
  }

  if (
    typeof parsedParameters !== 'object' ||
    parsedParameters === null ||
    Array.isArray(parsedParameters)
  ) {
    return {};
  }

  return parsedParameters as Record<string, unknown>;
}

/**
 * 参数取值 → 表格里的一格.
 *
 * 数值**不**过 `formatAmount` / `formatCount`: `0.01` 走 `formatAmount` 会变成 `0.01` (没问题) 但
 * 整数会被加上千位分隔符, 而参数值是要与 `BackTest.json` 逐字对上的东西, 加分隔符反而难认.
 */
function formatParameterValue(value: unknown): string {
  if (value === undefined || value === null || value === '') {
    return ABSENT_PLACEHOLDER;
  }

  if (typeof value === 'boolean') {
    return formatFlag(value);
  }

  if (typeof value === 'number' || typeof value === 'string') {
    return String(value);
  }

  return JSON.stringify(value) ?? ABSENT_PLACEHOLDER;
}

function readStrategyName(
  strategyNamesByStrategyId: Record<string, string>,
  strategyId: string,
): string {
  return strategyNamesByStrategyId[strategyId] ?? strategyId;
}

function toRow(groupTitle: string, label: string): RunComparisonRow {
  return { key: `${groupTitle}·${label}`, label };
}
