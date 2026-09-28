/**
 * 运行指标的展示行: 详情页与对比页**共用同一份行定义**.
 *
 * 这些列是引擎结果文件在 `Runs` 表上的镜像, 逐行写死在各页模板里的话, 加一列就要改两处对齐, 而
 * 两处对齐的差别在界面上表现为"同一列在两个页面上是不同的数". 故行序、行名、取值口径都只在这里
 * 声明一次, 由两个入口按各自能拿到的字段切分:
 *
 *   - `buildSummaryMetricSections` 只收 `RunSummary` 有的列 (列表视图与对比页按它取数);
 *   - `buildDetailMetricSections` 全收, 多出来的是 `runner_pid` / `hostname` / `engine_version`
 *     那几个**只有详情接口才回**的宿主侧观察量.
 *
 * 纯函数: 不碰 DOM、不发请求, 故能在 vitest 的 node 环境里直接测.
 */

import type { RunDetail, RunSummary } from '../api/types';
import {
  ABSENT_PLACEHOLDER,
  formatAmount,
  formatCount,
  formatDateTime,
  formatDuration,
  formatFlag,
  formatTradingDay,
} from './format';

export interface MetricRow {
  label: string;
  value: string;
}

export interface MetricSection {
  title: string;
  rows: MetricRow[];
}

/**
 * 行定义读得到的运行记录.
 *
 * 是 `RunSummary` 加 `Partial<RunDetail>` 而不是 `RunDetail`: 同一个读取器要能在两种入参下编译
 * 通过, 而详情独有那些字段在概览入口下**根本不读** (整行会被筛掉). 写成 `Partial` 而非可选感叹
 * 号, 是为了让"这里可能没有"落在类型上——`formatCount(undefined)` 回占位符, 万一筛分写错也只是
 * 少一格数, 而不是抛异常把整页打掉.
 */
type RunRecordForMetrics = RunSummary & Partial<RunDetail>;

interface MetricRowDefinition {
  label: string;
  /** 这一行读的是 `RunDetail` 独有字段; 概览入口整行不收. */
  requiresRunDetail: boolean;
  readValue: (run: RunRecordForMetrics, strategyName: string) => string;
}

interface MetricSectionDefinition {
  title: string;
  rows: MetricRowDefinition[];
}

/**
 * 三节的定义, 次序即界面上的次序.
 *
 * 策略名由调用方查好了传进来 (它来自策略目录 store, 不在运行记录上): 把那一跳放进这里会让本文件
 * 依赖 store, 而 store 是网络取数的东西, 一进来这个文件就不再是纯的.
 */
const RUN_METRIC_SECTIONS: MetricSectionDefinition[] = [
  {
    title: '概要',
    rows: [
      { label: '运行 ID', requiresRunDetail: false, readValue: (run) => run.id },
      {
        label: '策略',
        requiresRunDetail: false,
        readValue: (_run, strategyName) => strategyName,
      },
      {
        label: '策略版本 ID',
        requiresRunDetail: false,
        readValue: (run) => run.strategy_version_id,
      },
      {
        label: '行情模式',
        requiresRunDetail: false,
        readValue: (run) => run.market_data_type ?? ABSENT_PLACEHOLDER,
      },
      {
        label: '提交时间',
        requiresRunDetail: false,
        readValue: (run) => formatDateTime(run.submitted_at),
      },
      {
        label: '开始时间',
        requiresRunDetail: false,
        readValue: (run) => formatDateTime(run.started_at),
      },
      {
        label: '结束时间',
        requiresRunDetail: false,
        readValue: (run) => formatDateTime(run.finished_at),
      },
      {
        label: '耗时',
        requiresRunDetail: false,
        readValue: (run) => formatDuration(run.duration_ms),
      },
      {
        label: '宿主退出码',
        requiresRunDetail: false,
        readValue: (run) => formatCount(run.exit_code),
      },
      {
        label: '宿主进程号',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.runner_pid),
      },
      {
        label: '执行主机',
        requiresRunDetail: true,
        readValue: (run) => run.hostname || ABSENT_PLACEHOLDER,
      },
      {
        label: '引擎版本',
        requiresRunDetail: true,
        readValue: (run) => run.engine_version || ABSENT_PLACEHOLDER,
      },
    ],
  },
  {
    title: '绩效指标',
    rows: [
      {
        label: '余额',
        requiresRunDetail: false,
        readValue: (run) => formatAmount(run.balance),
      },
      {
        label: '可用资金',
        requiresRunDetail: false,
        readValue: (run) => formatAmount(run.available),
      },
      {
        label: '交易笔数',
        requiresRunDetail: false,
        readValue: (run) => formatCount(run.trade_count),
      },
      {
        label: '订单笔数',
        requiresRunDetail: false,
        readValue: (run) => formatCount(run.order_count),
      },
      {
        label: '总手续费',
        requiresRunDetail: false,
        readValue: (run) => formatAmount(run.total_commission),
      },
      {
        label: '总印花税',
        requiresRunDetail: true,
        readValue: (run) => formatAmount(run.total_stamp_tax),
      },
      {
        label: '总过户费',
        requiresRunDetail: true,
        readValue: (run) => formatAmount(run.total_transfer_fee),
      },
    ],
  },
  {
    title: '引擎数据镜像',
    rows: [
      {
        label: '交易日区间',
        requiresRunDetail: false,
        readValue: (run) =>
          `${formatTradingDay(run.start_trading_day)} ~ ${formatTradingDay(run.end_trading_day)}`,
      },
      {
        label: '最后交易日',
        requiresRunDetail: true,
        readValue: (run) => formatTradingDay(run.last_trading_day),
      },
      {
        label: '结果文件版本',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.schema_version),
      },
      {
        label: '资金账户',
        requiresRunDetail: true,
        readValue: (run) => run.account_id ?? ABSENT_PLACEHOLDER,
      },
      {
        label: '基础数据已载入',
        requiresRunDetail: true,
        readValue: (run) => formatFlag(run.basic_data_loaded),
      },
      {
        label: '资金已初始化',
        requiresRunDetail: true,
        readValue: (run) => formatFlag(run.has_capital),
      },
      {
        label: '行情订阅数',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.md_subscribe_count),
      },
      {
        label: 'K 线行情数',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.bar_market_data_count),
      },
      {
        label: '深度行情数',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.depth_market_data_count),
      },
      {
        label: '合约数',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.instrument_count),
      },
      {
        label: '缺失手续费记录数',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.commission_missing_count),
      },
      {
        label: '手续费率为零的键数',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.commission_zero_rate_key_count),
      },
      {
        label: '手数倍数回退合约数',
        requiresRunDetail: true,
        readValue: (run) => formatCount(run.volume_multiple_fallback_product_count),
      },
      {
        label: '引擎错误号',
        requiresRunDetail: false,
        readValue: (run) => formatCount(run.error_id),
      },
    ],
  },
];

/** 概览入口: 只给 `RunSummary` 时能显示的那几节. 对比页一轮一列, 取的就是这一份. */
export function buildSummaryMetricSections(
  run: RunSummary,
  strategyName: string,
): MetricSection[] {
  return collectMetricSections(run, strategyName, false);
}

/** 详情入口: 全收. 与概览入口**同一份行定义**, 故两边不会各长一个样子. */
export function buildDetailMetricSections(
  run: RunDetail,
  strategyName: string,
): MetricSection[] {
  return collectMetricSections(run, strategyName, true);
}

function collectMetricSections(
  run: RunRecordForMetrics,
  strategyName: string,
  includesRunDetailFields: boolean,
): MetricSection[] {
  return RUN_METRIC_SECTIONS.map((section) => ({
    title: section.title,
    rows: section.rows
      .filter((row) => includesRunDetailFields || !row.requiresRunDetail)
      .map((row) => ({
        label: row.label,
        value: row.readValue(run, strategyName),
      })),
  })).filter((section) => section.rows.length > 0);
}
