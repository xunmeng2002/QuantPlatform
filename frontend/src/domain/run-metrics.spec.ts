/**
 * 运行指标行.
 *
 * 这里最要紧的一条断言是「详情三节与重构前的内联版逐字相同」——`RunDetailView` 那三段行定义被搬到
 * 本模块时, 行名、行序、取值口径都**不该有任何变化**, 而这件事只有逐字比一遍才说得清. 故下面把
 * 三节拉平成一行一节标签 + 一串 `标签 = 取值`, 一个 `toEqual` 就能看出差在哪一行.
 *
 * 时间列比的是 `formatDateTime` 的结果而不是写死的文本: 它按**本机时区**渲染, 写死了在别的时区上
 * 就红 (`format.spec.ts` 同样这么处理).
 */

import { describe, expect, it } from 'vitest';

import type { RunDetail, RunSummary } from '../api/types';
import { formatDateTime } from './format';
import { buildDetailMetricSections, buildSummaryMetricSections } from './run-metrics';
import type { MetricSection } from './run-metrics';

const STRATEGY_NAME = '网格策略';

const DETAIL_RUN: RunDetail = {
  id: 'run-fine-grid',
  user_id: 'user-1',
  strategy_id: 'strategy-1',
  strategy_version_id: 'version-3',
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
  balance: 1_002_500.5,
  available: 900_000,
  total_commission: 1_234.5,
  error_id: null,
  error_msg: null,
  runner_pid: 4_321,
  hostname: 'build-host',
  engine_version: 'sha256:abcdef',
  params_json: '{"GridStep":0.01}',
  backtest_config_json: '{}',
  workspace_path: 'run-fine-grid',
  db_path: './run-fine-grid/result.db',
  dump_path: './run-fine-grid/Dump',
  stdout_tail: '',
  stderr_tail: '',
  schema_version: 3,
  last_trading_day: '20241231',
  account_id: 'ACC-1',
  md_subscribe_count: 11,
  bar_market_data_count: 22,
  depth_market_data_count: 33,
  instrument_count: 44,
  commission_missing_count: 5,
  commission_zero_rate_key_count: 6,
  volume_multiple_fallback_product_count: 7,
  basic_data_loaded: true,
  has_capital: false,
  total_stamp_tax: 12.25,
  total_transfer_fee: 3.5,
};

function flattenMetricRows(sections: MetricSection[]): string[] {
  return sections.flatMap((section) => [
    `# ${section.title}`,
    ...section.rows.map((row) => `${row.label} = ${row.value}`),
  ]);
}

function readMetricValue(sections: MetricSection[], label: string): string {
  const row = sections
    .flatMap((section) => section.rows)
    .find((metricRow) => metricRow.label === label);

  return row?.value ?? '<没有这一行>';
}

describe('buildDetailMetricSections', () => {
  it('三节的行名、行序与取值与重构前的内联版逐字相同', () => {
    expect(
      flattenMetricRows(buildDetailMetricSections(DETAIL_RUN, STRATEGY_NAME)),
    ).toEqual([
      '# 概要',
      '运行 ID = run-fine-grid',
      `策略 = ${STRATEGY_NAME}`,
      '策略版本 ID = version-3',
      '行情模式 = Bar',
      `提交时间 = ${formatDateTime(DETAIL_RUN.submitted_at)}`,
      `开始时间 = ${formatDateTime(DETAIL_RUN.started_at)}`,
      `结束时间 = ${formatDateTime(DETAIL_RUN.finished_at)}`,
      '耗时 = 1 分 10 秒',
      '宿主退出码 = 0',
      '宿主进程号 = 4,321',
      '执行主机 = build-host',
      '引擎版本 = sha256:abcdef',
      '# 绩效指标',
      '余额 = 1,002,500.50',
      '可用资金 = 900,000.00',
      '交易笔数 = 84',
      '订单笔数 = 92',
      '总手续费 = 1,234.50',
      '总印花税 = 12.25',
      '总过户费 = 3.50',
      '# 引擎数据镜像',
      '交易日区间 = 2024-01-02 ~ 2024-12-31',
      '最后交易日 = 2024-12-31',
      '结果文件版本 = 3',
      '资金账户 = ACC-1',
      '基础数据已载入 = 是',
      '资金已初始化 = 否',
      '行情订阅数 = 11',
      'K 线行情数 = 22',
      '深度行情数 = 33',
      '合约数 = 44',
      '缺失手续费记录数 = 5',
      '手续费率为零的键数 = 6',
      '手数倍数回退合约数 = 7',
      '引擎错误号 = —',
    ]);
  });
});

describe('buildSummaryMetricSections', () => {
  it('只收 RunSummary 上有的行, 次序与详情一致', () => {
    expect(
      flattenMetricRows(buildSummaryMetricSections(DETAIL_RUN, STRATEGY_NAME)),
    ).toEqual([
      '# 概要',
      '运行 ID = run-fine-grid',
      `策略 = ${STRATEGY_NAME}`,
      '策略版本 ID = version-3',
      '行情模式 = Bar',
      `提交时间 = ${formatDateTime(DETAIL_RUN.submitted_at)}`,
      `开始时间 = ${formatDateTime(DETAIL_RUN.started_at)}`,
      `结束时间 = ${formatDateTime(DETAIL_RUN.finished_at)}`,
      '耗时 = 1 分 10 秒',
      '宿主退出码 = 0',
      '# 绩效指标',
      '余额 = 1,002,500.50',
      '可用资金 = 900,000.00',
      '交易笔数 = 84',
      '订单笔数 = 92',
      '总手续费 = 1,234.50',
      '# 引擎数据镜像',
      '交易日区间 = 2024-01-02 ~ 2024-12-31',
      '引擎错误号 = —',
    ]);
  });

  it('详情独有那几行是**不出现**, 而不是显示成占位符', () => {
    const sections = buildSummaryMetricSections(DETAIL_RUN, STRATEGY_NAME);

    for (const detailOnlyLabel of ['宿主进程号', '执行主机', '引擎版本', '总印花税', '资金账户']) {
      expect(readMetricValue(sections, detailOnlyLabel)).toBe('<没有这一行>');
    }
  });
});

describe('取不到的值一律是占位符', () => {
  const SPARSE_RUN: RunSummary = {
    ...DETAIL_RUN,
    market_data_type: null,
    start_trading_day: null,
    end_trading_day: null,
    error_id: null,
    error_msg: null,
    balance: null,
    available: null,
    trade_count: null,
    order_count: null,
    total_commission: null,
  };

  it('空字段显示 —, 既不抛也不留空', () => {
    const sections = buildSummaryMetricSections(SPARSE_RUN, STRATEGY_NAME);

    expect(readMetricValue(sections, '行情模式')).toBe('—');
    expect(readMetricValue(sections, '交易日区间')).toBe('— ~ —');
    expect(readMetricValue(sections, '引擎错误号')).toBe('—');
    expect(readMetricValue(sections, '余额')).toBe('—');
  });

  it('策略名由调用方查好了传进来, 这里原样透传', () => {
    expect(
      readMetricValue(buildSummaryMetricSections(SPARSE_RUN, '另一个策略'), '策略'),
    ).toBe('另一个策略');
  });
});
