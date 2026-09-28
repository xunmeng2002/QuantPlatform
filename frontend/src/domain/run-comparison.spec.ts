/**
 * 对比表的造型.
 *
 * 这里钉的是**视图层看不出来的两条判断**: ① 列的取值按 `节标题·行名` 取, 不是按位置 —— 否则
 * 加一行到别的节就会串值; ② 参数行取各列的**并集**, 某一轮没有的那个键显示占位符, 而这恰恰是
 * "这两轮差在哪"最该看见的一格.
 *
 * 指标行本身不在这里逐行比 (那是 `run-metrics.spec.ts` 的事), 故断言只取几个能认出列的数.
 */

import { describe, expect, it } from 'vitest';

import type { RunComparisonEntry, RunSummary } from '../api/types';
import { ABSENT_PLACEHOLDER } from './format';
import { PARAMETER_ROW_GROUP_TITLE, buildRunComparisonTableModel } from './run-comparison';

const STRATEGY_ID = 'strategy-1';
const STRATEGY_NAME = '网格策略';

const BALANCE_ROW_KEY = '绩效指标·余额';
const STRATEGY_ROW_KEY = '概要·策略';
const GRID_STEP_ROW_KEY = `${PARAMETER_ROW_GROUP_TITLE}·GridStep`;
const TRADE_VOLUME_ROW_KEY = `${PARAMETER_ROW_GROUP_TITLE}·TradeVolume`;
const USE_STOP_LOSS_ROW_KEY = `${PARAMETER_ROW_GROUP_TITLE}·UseStopLoss`;

function buildSummary(runId: string, overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: runId,
    user_id: 'user-1',
    strategy_id: STRATEGY_ID,
    strategy_version_id: 'version-1',
    status: 'succeeded',
    submitted_at: '2026-09-27T02:03:04',
    started_at: '2026-09-27T02:03:10',
    finished_at: '2026-09-27T02:04:20',
    duration_ms: 70_000,
    exit_code: 0,
    is_success: true,
    market_data_type: 'Bar',
    start_trading_day: '20240102',
    end_trading_day: '20241231',
    trade_count: 84,
    order_count: 92,
    balance: 100_000,
    available: 90_000,
    total_commission: 1_234.5,
    params_json: '{}',
    error_id: null,
    error_msg: null,
    ...overrides,
  };
}

function buildEntry(
  runId: string,
  overrides: Partial<RunSummary> = {},
): RunComparisonEntry {
  return {
    summary: buildSummary(runId, overrides),
    equity_points: [],
    equity_unavailable_reason: null,
  };
}

describe('空输入', () => {
  it('一轮都没有时给零值, 而不是抛异常', () => {
    expect(buildRunComparisonTableModel([], {})).toEqual({
      rowGroups: [],
      columns: [],
    });
  });
});

describe('列', () => {
  it('列序 = 请求序, 表头是第 N 轮', () => {
    const tableModel = buildRunComparisonTableModel(
      [buildEntry('run-b'), buildEntry('run-a'), buildEntry('run-c')],
      {},
    );

    expect(tableModel.columns.map((column) => column.runId)).toEqual([
      'run-b',
      'run-a',
      'run-c',
    ]);
    expect(tableModel.columns.map((column) => column.heading)).toEqual([
      '第 1 轮',
      '第 2 轮',
      '第 3 轮',
    ]);
  });

  it('曲线的可用性与指标各算各的: 曲线缺失的那一列, 指标照常有值', () => {
    const tableModel = buildRunComparisonTableModel(
      [
        buildEntry('run-a', { balance: 100_000 }),
        {
          ...buildEntry('run-b', { balance: 200_000 }),
          equity_unavailable_reason: '结果库文件已不存在',
        },
      ],
      { [STRATEGY_ID]: STRATEGY_NAME },
    );

    expect(tableModel.columns[1]?.equityUnavailableReason).toBe('结果库文件已不存在');
    expect(tableModel.columns[1]?.valuesByRowKey.get(BALANCE_ROW_KEY)).toBe(
      '200,000.00',
    );
  });

  it('各列各算一份: 策略名随列走', () => {
    const otherStrategyEntry: RunComparisonEntry = {
      ...buildEntry('run-b'),
      summary: buildSummary('run-b', { strategy_id: 'strategy-2' }),
    };
    const tableModel = buildRunComparisonTableModel(
      [buildEntry('run-a'), otherStrategyEntry],
      { [STRATEGY_ID]: STRATEGY_NAME, 'strategy-2': '动量策略' },
    );

    expect(tableModel.columns[0]?.valuesByRowKey.get(STRATEGY_ROW_KEY)).toBe(
      STRATEGY_NAME,
    );
    expect(tableModel.columns[1]?.valuesByRowKey.get(STRATEGY_ROW_KEY)).toBe('动量策略');
  });

  it('策略名查不到时回落到策略号 (授权被撤销了, 而历史运行仍指着它)', () => {
    const tableModel = buildRunComparisonTableModel([buildEntry('run-a')], {});

    expect(tableModel.columns[0]?.valuesByRowKey.get(STRATEGY_ROW_KEY)).toBe(
      STRATEGY_ID,
    );
  });
});

describe('参数行', () => {
  it('取各列的并集, 按字典序排列, 缺的那一格是占位符', () => {
    const tableModel = buildRunComparisonTableModel(
      [
        buildEntry('run-a', { params_json: '{"GridStep":0.01,"TradeVolume":100}' }),
        buildEntry('run-b', { params_json: '{"GridStep":0.02,"UseStopLoss":false}' }),
      ],
      {},
    );
    const parameterGroup = tableModel.rowGroups.at(-1);

    expect(parameterGroup?.title).toBe(PARAMETER_ROW_GROUP_TITLE);
    expect(parameterGroup?.rows.map((row) => row.label)).toEqual([
      'GridStep',
      'TradeVolume',
      'UseStopLoss',
    ]);

    // 数值不过千位分隔: 参数值是要与 `BackTest.json` 逐字对上的东西.
    expect(tableModel.columns[0]?.valuesByRowKey.get(GRID_STEP_ROW_KEY)).toBe('0.01');
    expect(tableModel.columns[1]?.valuesByRowKey.get(GRID_STEP_ROW_KEY)).toBe('0.02');
    expect(tableModel.columns[0]?.valuesByRowKey.get(TRADE_VOLUME_ROW_KEY)).toBe('100');
    expect(tableModel.columns[1]?.valuesByRowKey.get(TRADE_VOLUME_ROW_KEY)).toBe(
      ABSENT_PLACEHOLDER,
    );
    // 布尔量走三态文案, 与指标里那几个开关一致.
    expect(tableModel.columns[1]?.valuesByRowKey.get(USE_STOP_LOSS_ROW_KEY)).toBe('否');
    expect(tableModel.columns[0]?.valuesByRowKey.get(USE_STOP_LOSS_ROW_KEY)).toBe(
      ABSENT_PLACEHOLDER,
    );
  });

  it('某一轮的参数读不动时只影响它这一列', () => {
    const tableModel = buildRunComparisonTableModel(
      [
        buildEntry('run-a', { params_json: '{"GridStep":0.01}' }),
        buildEntry('run-b', { params_json: '这不是 JSON' }),
      ],
      {},
    );

    expect(tableModel.columns[0]?.valuesByRowKey.get(GRID_STEP_ROW_KEY)).toBe('0.01');
    expect(tableModel.columns[1]?.valuesByRowKey.get(GRID_STEP_ROW_KEY)).toBe(
      ABSENT_PLACEHOLDER,
    );
  });

  it('参数并集排在指标节之后 (它不在 `run-metrics` 的行定义里)', () => {
    const tableModel = buildRunComparisonTableModel(
      [buildEntry('run-a', { params_json: '{"GridStep":0.01}' })],
      {},
    );

    expect(tableModel.rowGroups.map((group) => group.title)).toEqual([
      '概要',
      '绩效指标',
      '引擎数据镜像',
      PARAMETER_ROW_GROUP_TITLE,
    ]);
  });
});
