/**
 * 权益曲线的派生量: 回撤与概要.
 *
 * 后端只回 `Capital` 表逐日结算权益的原样序列 (一天一点), 回撤是**派生数据**, 在这里算:
 * 它属于图表的形状, 放进接口就等于「前端换一种画法」要动后端.
 *
 * **口径是逐日结算价, 不是日内**: 引擎的 `Capital` 一天一行, 盘中回撤在库里根本不存在
 * (样本里 `Margin` / `MarketValue` 恒为 0), 故曲线上的回撤是「相对逐日峰值的回落」.
 *
 * 多轮叠加画在一张图上时, 各轮的交易日集合**天然不同** (区间不同, 或某一轮中间停过), 故先按
 * `alignEquitySeries` 对齐到同一根 x 轴再交给图表. 对齐是纯逻辑, 放这里而不是组件里.
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

/** 要叠到同一张图上的一轮: 图例上的名字 + 它自己的逐日权益. */
export interface EquityCurveInput {
  /** 图例名 (通常写运行号或"第 N 轮"), 与输出逐位对应. */
  label: string;
  points: EquityPoint[];
}

export interface AlignedEquityCurve {
  label: string;
  /** 与 `tradingDayLabels` **逐位对应**; 该轮这一天没有点就是 `null`. */
  balances: (number | null)[];
}

export interface AlignedEquitySeries {
  /** 各轮交易日的并集, 升序, 已过 `formatTradingDay`. */
  tradingDayLabels: string[];
  /** 输出序 = 输入序, 故按序号取色/取名与传进来的那一列一一对应. */
  curves: AlignedEquityCurve[];
}

/**
 * 把多轮权益曲线对齐到同一根 x 轴.
 *
 * 缺的那天填 `null` 而**不插值、不取前值**: 插值等于编造一段引擎从没结算过的权益, 而"两轮覆盖的
 * 区间不同"恰恰是这个页面最该一眼看见的事实——取前值会让后开始的那一轮在它还没开始的日子里被
 * 画成一条水平的直线, 看上去像"这段时间没赚钱", 与"这段时间根本没跑"完全不是一回事. 空值交给
 * 图表断线 (ECharts 侧配 `connectNulls: false`).
 */
export function alignEquitySeries(
  curves: EquityCurveInput[],
): AlignedEquitySeries {
  const tradingDays = collectTradingDays(curves);

  return {
    // 标签最后统一过 `formatTradingDay`: 排序与去重都在**原始 8 位串**上做, 8 位 `YYYYMMDD`
    // 的字典序即时间序, 不必解析成日期——解析会把"某个点写坏了"变成整张图的静默错位.
    tradingDayLabels: tradingDays.map((tradingDay) =>
      formatTradingDay(tradingDay),
    ),
    curves: curves.map((curve) => alignSingleCurve(curve, tradingDays)),
  };
}

function alignSingleCurve(
  curve: EquityCurveInput,
  tradingDays: string[],
): AlignedEquityCurve {
  const balancesByTradingDay = collectLastBalances(curve.points);

  return {
    label: curve.label,
    // `?? null` 而不是 `||`: 余额为 0 是个**真实的结算结果**, 用 `||` 会把它当成缺失画成断线.
    balances: tradingDays.map(
      (tradingDay) => balancesByTradingDay.get(tradingDay) ?? null,
    ),
  };
}

/**
 * 各轮交易日的并集, 按**默认排序**去重升序.
 *
 * 不传比较函数是有意的: 默认比较逐 UTF-16 码元比, 对 8 位数字串与数值序一致; 换成
 * `localeCompare` 会引入运行环境的排序数据, 于是同一条数据在两台机器上的 x 轴次序可能不同.
 */
function collectTradingDays(curves: EquityCurveInput[]): string[] {
  const tradingDays = new Set<string>();

  for (const curve of curves) {
    for (const point of curve.points) {
      tradingDays.add(point.trading_day);
    }
  }

  return [...tradingDays].sort();
}

/** 一轮的逐日权益, 同日多点取**末值**: 引擎的 `Capital` 一天一行, 真出现两行时"后写的那行"是它最后的结算. */
function collectLastBalances(points: EquityPoint[]): Map<string, number> {
  const balancesByTradingDay = new Map<string, number>();

  for (const point of points) {
    balancesByTradingDay.set(point.trading_day, point.balance);
  }

  return balancesByTradingDay;
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
