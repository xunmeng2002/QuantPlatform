/**
 * 权益曲线派生量的纯逻辑.
 *
 * 三条口径在这里钉住, 它们都是「看起来都对、但换一种写法就变味」的地方:
 *
 *   1. **首点是种子行** (初始资金), 故收益率以首点为基准而不是第一个交易日;
 *   2. **回撤记在历史峰值上**——谷后创新高时回撤回到 0, 而"最大回撤"仍指向谷底那一天;
 *   3. **峰值为 0 时回撤记 0** 而不是除零得到 `Infinity` / `NaN`;
 *   4. **叠加时缺的那天是 `null`**——不插值也不取前值, 覆盖区间不同必须在图上是一段真空.
 */

import { describe, expect, it } from 'vitest';

import type { EquityPoint } from '../api/types';
import { alignEquitySeries, buildEquitySeries, summarizeEquity } from './equity';
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

describe('alignEquitySeries', () => {
  // 两轮: 细网格从 20241001 起步, 粗网格从 20241002 起步, 中间重叠一天而两轮的收尾各差一天.
  const FINE_CURVE = {
    label: '细网格',
    points: [
      equityPoint('20241003', 1_000_300),
      equityPoint('20241001', 1_000_100),
      equityPoint('20241002', 1_000_200),
    ],
  };
  const COARSE_CURVE = {
    label: '粗网格',
    points: [
      equityPoint('20241002', 1_000_000),
      equityPoint('20241004', 999_000),
    ],
  };

  it('x 轴是各轮交易日的并集, 去重升序 (8 位串的字典序即时间序)', () => {
    expect(alignEquitySeries([FINE_CURVE, COARSE_CURVE]).tradingDayLabels).toEqual([
      '2024-10-01',
      '2024-10-02',
      '2024-10-03',
      '2024-10-04',
    ]);
  });

  it('某轮缺的那天是 null: 不插值, 也不拿前一天的值顶替', () => {
    const aligned = alignEquitySeries([FINE_CURVE, COARSE_CURVE]);

    expect(aligned.curves[0]?.balances).toEqual([1_000_100, 1_000_200, 1_000_300, null]);
    expect(aligned.curves[1]?.balances).toEqual([null, 1_000_000, null, 999_000]);
  });

  it('同一序列同一天有多个点时取末值', () => {
    const aligned = alignEquitySeries([
      {
        label: '重复日',
        points: [
          equityPoint('20241001', 1_000_000),
          equityPoint('20241001', 1_000_500),
        ],
      },
    ]);

    expect(aligned.curves[0]?.balances).toEqual([1_000_500]);
  });

  it('余额是 0 时保留 0, 不当成缺失画成断线', () => {
    const aligned = alignEquitySeries([
      { label: '清零', points: [equityPoint('20241001', 0)] },
    ]);

    expect(aligned.curves[0]?.balances).toEqual([0]);
  });

  it('一条曲线都没有时是零值, 不是 undefined', () => {
    expect(alignEquitySeries([])).toEqual({ tradingDayLabels: [], curves: [] });
  });

  it('某一轮一个点都没有时, 它在整根 x 轴上全是 null', () => {
    const aligned = alignEquitySeries([
      { label: '空的一轮', points: [] },
      COARSE_CURVE,
    ]);

    expect(aligned.curves[0]?.balances).toEqual([null, null]);
    expect(aligned.curves[1]?.label).toBe('粗网格');
  });

  it('输出序 = 输入序 (按序号取色与传进来的那一列一一对应)', () => {
    const aligned = alignEquitySeries([COARSE_CURVE, FINE_CURVE]);

    expect(aligned.curves.map((curve) => curve.label)).toEqual(['粗网格', '细网格']);
    expect(aligned.curves[0]?.balances).toEqual([null, 1_000_000, null, 999_000]);
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
