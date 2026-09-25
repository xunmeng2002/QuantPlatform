/**
 * 权益曲线派生量的纯逻辑.
 *
 * 三条口径在这里钉住, 它们都是「看起来都对、但换一种写法就变味」的地方:
 *
 *   1. **首点是种子行** (初始资金), 故收益率以首点为基准而不是第一个交易日;
 *   2. **回撤记在历史峰值上**——谷后创新高时回撤回到 0, 而"最大回撤"仍指向谷底那一天;
 *   3. **峰值为 0 时回撤记 0** 而不是除零得到 `Infinity` / `NaN`.
 */

import { describe, expect, it } from 'vitest';

import type { EquityPoint } from '../api/types';
import { buildEquitySeries, summarizeEquity } from './equity';

function equityPoint(tradingDay: string, balance: number): EquityPoint {
  return { trading_day: tradingDay, balance, available: balance };
}

// 与真引擎一轮实测同形: 种子行 1000000.0 起步, 逐日升序.
const RISING_POINTS = [
  equityPoint('20241001', 1_000_000),
  equityPoint('20241008', 1_010_000),
  equityPoint('20241009', 1_020_000),
];

const PEAK_THEN_TROUGH_POINTS = [
  equityPoint('20241001', 1_000_000),
  equityPoint('20241008', 1_100_000),
  equityPoint('20241009', 900_000),
];

describe('buildEquitySeries', () => {
  it('没有点时三项都是空数组, 不是 undefined', () => {
    expect(buildEquitySeries([])).toEqual({
      tradingDayLabels: [],
      balances: [],
      drawdownRatios: [],
    });
  });

  it('x 轴标签过 formatTradingDay, 与权益逐位对应', () => {
    const series = buildEquitySeries(RISING_POINTS);

    expect(series.tradingDayLabels).toEqual(['2024-10-01', '2024-10-08', '2024-10-09']);
    expect(series.balances).toEqual([1_000_000, 1_010_000, 1_020_000]);
  });

  it('单点时回撤是 0 (自己就是峰值)', () => {
    expect(buildEquitySeries([equityPoint('20241001', 1_000_000)]).drawdownRatios).toEqual([0]);
  });

  it('逐日创新高时回撤恒为 0', () => {
    expect(buildEquitySeries(RISING_POINTS).drawdownRatios).toEqual([0, 0, 0]);
  });

  it('峰后回落给出相对峰值的负比例', () => {
    const series = buildEquitySeries(PEAK_THEN_TROUGH_POINTS);

    expect(series.drawdownRatios[0]).toBe(0);
    expect(series.drawdownRatios[1]).toBe(0);
    expect(series.drawdownRatios[2]).toBeCloseTo(-0.1818181818, 9);
  });

  it('谷后创新高时回撤归零, 而不是一直背着上一个谷', () => {
    const series = buildEquitySeries([
      ...PEAK_THEN_TROUGH_POINTS,
      equityPoint('20241010', 1_200_000),
    ]);

    expect(series.drawdownRatios.at(-1)).toBe(0);
  });

  it('峰值为 0 时回撤记 0, 不产生 Infinity 或 NaN', () => {
    const series = buildEquitySeries([
      equityPoint('20241001', 0),
      equityPoint('20241008', 0),
    ]);

    expect(series.drawdownRatios).toEqual([0, 0]);
  });
});

describe('summarizeEquity', () => {
  it('没有点时回 null, 由调用方显示空态', () => {
    expect(summarizeEquity([])).toBeNull();
  });

  it('单点时四项都落在同一个点上, 收益率是 0', () => {
    const summary = summarizeEquity([equityPoint('20241001', 1_000_000)]);

    expect(summary).toEqual({
      initialBalance: 1_000_000,
      finalBalance: 1_000_000,
      peakBalance: 1_000_000,
      maximumDrawdownRatio: 0,
      maximumDrawdownTradingDay: '20241001',
      totalReturnRatio: 0,
    });
  });

  it('首末点取整条序列的两端, 收益率以首点 (种子行) 为基准', () => {
    const summary = summarizeEquity([
      equityPoint('20241001', 1_000_000),
      equityPoint('20241008', 1_050_000),
      equityPoint('20241009', 1_100_000),
    ]);

    expect(summary?.initialBalance).toBe(1_000_000);
    expect(summary?.finalBalance).toBe(1_100_000);
    expect(summary?.totalReturnRatio).toBeCloseTo(0.1, 12);
  });

  it('亏损时收益率是负数', () => {
    const summary = summarizeEquity([
      equityPoint('20241001', 1_000_000),
      equityPoint('20241231', 900_000),
    ]);

    expect(summary?.totalReturnRatio).toBeCloseTo(-0.1, 12);
  });

  it('基准非正时收益率记 0, 不产生 Infinity', () => {
    const summary = summarizeEquity([
      equityPoint('20241001', 0),
      equityPoint('20241008', 100),
    ]);

    expect(summary?.totalReturnRatio).toBe(0);
  });

  it('峰值取全序列最高, 最大回撤记在谷底那一天', () => {
    const summary = summarizeEquity([
      equityPoint('20241001', 1_000_000),
      equityPoint('20241008', 1_100_000),
      equityPoint('20241009', 990_000),
      equityPoint('20241010', 1_200_000),
    ]);

    expect(summary?.peakBalance).toBe(1_200_000);
    expect(summary?.maximumDrawdownRatio).toBeCloseTo(-0.1, 12);
    expect(summary?.maximumDrawdownTradingDay).toBe('20241009');
  });

  it('谷底持平的日子报先到的那一天', () => {
    const summary = summarizeEquity([
      equityPoint('20241001', 1_000_000),
      equityPoint('20241008', 900_000),
      equityPoint('20241009', 900_000),
    ]);

    expect(summary?.maximumDrawdownRatio).toBeCloseTo(-0.1, 12);
    expect(summary?.maximumDrawdownTradingDay).toBe('20241008');
  });

  it('单调上升时最大回撤是 0, 那天就是首点', () => {
    const summary = summarizeEquity(RISING_POINTS);

    expect(summary?.maximumDrawdownRatio).toBe(0);
    expect(summary?.maximumDrawdownTradingDay).toBe('20241001');
    expect(summary?.peakBalance).toBe(1_020_000);
    expect(summary?.totalReturnRatio).toBeCloseTo(0.02, 12);
  });
});
