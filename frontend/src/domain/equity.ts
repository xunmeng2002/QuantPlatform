/**
 * 权益曲线的派生量: 回撤与概要.
 *
 * 后端只回 `Capital` 表逐日结算权益的原样序列 (一天一点), 回撤是**派生数据**, 在这里算:
 * 它属于图表的形状, 放进接口就等于「前端换一种画法」要动后端.
 *
 * **口径是逐日结算价, 不是日内**: 引擎的 `Capital` 一天一行, 盘中回撤在库里根本不存在
 * (样本里 `Margin` / `MarketValue` 恒为 0), 故曲线上的回撤是「相对逐日峰值的回落」.
 */

import type { EquityPoint } from '../api/types';
import { formatTradingDay } from './format';

export interface EquitySeries {
  /** 经 `formatTradingDay` 的 x 轴标签, 与 `balances` 逐位对应. */
  tradingDayLabels: string[];
  balances: number[];
  /** `(balance - 峰值) / 峰值`, 恒 <= 0; 单位是**比例**而不是百分数. */
  drawdownRatios: number[];
}

export interface EquitySummary {
  initialBalance: number;
  finalBalance: number;
  peakBalance: number;
  maximumDrawdownRatio: number;
  /** 8 字符 `YYYYMMDD` 串, 展示前过 `formatTradingDay`. */
  maximumDrawdownTradingDay: string;
  totalReturnRatio: number;
}

export function buildEquitySeries(points: EquityPoint[]): EquitySeries {
  return {
    tradingDayLabels: points.map((point) => formatTradingDay(point.trading_day)),
    balances: points.map((point) => point.balance),
    drawdownRatios: calculateDrawdownRatios(points),
  };
}

/**
 * 概要四项; 没有点时回 `null`.
 *
 * 收益率以**首点** (引擎的种子行 = 初始资金) 为基准, 而不是第一个交易日的收盘权益——基准不同,
 * 同一轮会算出两个数.
 *
 * 极值用一遍遍历求, 不写 `Math.max(...balances)`: 点数上万时展开成实参会撞上引擎的参数个数上限,
 * 那是个只在大轮上才出现的失败.
 */
export function summarizeEquity(points: EquityPoint[]): EquitySummary | null {
  const initialPoint = points.at(0);
  const finalPoint = points.at(-1);

  if (initialPoint === undefined || finalPoint === undefined) {
    return null;
  }

  const drawdownRatios = calculateDrawdownRatios(points);

  let peakBalance = Number.NEGATIVE_INFINITY;
  let maximumDrawdownRatio = 0;
  let maximumDrawdownIndex = 0;

  for (const [index, point] of points.entries()) {
    peakBalance = Math.max(peakBalance, point.balance);

    const drawdownRatio = drawdownRatios[index] ?? 0;

    // 取**第一个**最深的点 (`>` 而不是 `>=`): 谷底持平的日子里, 报先到的那一天更符合
    // 「最大回撤发生在什么时候」的问法.
    if (drawdownRatio < maximumDrawdownRatio) {
      maximumDrawdownRatio = drawdownRatio;
      maximumDrawdownIndex = index;
    }
  }

  return {
    initialBalance: initialPoint.balance,
    finalBalance: finalPoint.balance,
    peakBalance,
    maximumDrawdownRatio,
    maximumDrawdownTradingDay:
      points[maximumDrawdownIndex]?.trading_day ?? finalPoint.trading_day,
    totalReturnRatio: calculateReturnRatio(
      initialPoint.balance,
      finalPoint.balance,
    ),
  };
}

/** 基准非正时比例无意义 (除以零或负基准), 一律回 0 而不是 `Infinity` / `NaN`. */
function calculateReturnRatio(
  initialBalance: number,
  finalBalance: number,
): number {
  if (initialBalance <= 0) {
    return 0;
  }

  return (finalBalance - initialBalance) / initialBalance;
}

function calculateDrawdownRatios(points: EquityPoint[]): number[] {
  let peakBalance = Number.NEGATIVE_INFINITY;

  return points.map((point) => {
    peakBalance = Math.max(peakBalance, point.balance);

    if (peakBalance <= 0) {
      return 0;
    }

    return (point.balance - peakBalance) / peakBalance;
  });
}
