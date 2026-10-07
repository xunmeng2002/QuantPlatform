/**
 * 一条费率规则的作用域怎么认、怎么显示.
 *
 * 三级 (合约 / 品种 / 交易所) 是**同一张表**上的三种规则, 表里没有"作用域"这一列 —— 它由「合约
 * 格」的取值承载:
 *
 * | 作用域 | `ExchangeId` | `InstrumentId` |
 * | :--- | :--- | :--- |
 * | 合约级 | `SSE` | `600519` |
 * | 品种级 | `SSE` | `600` (已登记在品种表) |
 * | 交易所级 | `SSE` | *空串* |
 *
 * 于是"这一行是哪一级"是个**纯函数**, 不必多取一次品种清单. 判据与后端
 * `_ensure_instrument_scope_is_known` 的守门判据是同一个数字 (`MAXIMUM_PRODUCT_CODE_LENGTH`):
 * 那个守门保证了**凡是平台收下的行**, 非空且不长于阈值的合约格都一定是已登记的品种码 ——
 * 短码没登记会被 400 拦下, 长于阈值的无论登记与否都按合约级放行. 故这里照阈值读出来的作用域
 * 与后端一致, 不会出现"界面说品种、后端当合约".
 *
 * **已知近似**: 阈值是个启发式 (A 股品种码 3 位、期货 1-4 位字母, 而合约码总在 6 位以上). 真出现
 * 4 位以内的合约码, 或者长于 4 位的品种码, 这一格会被认成另一级 —— 后端也是这么认的, 故界面与
 * 引擎仍然一致, 只是两者的叫法都不是用户本意.
 */

import { MAXIMUM_PRODUCT_CODE_LENGTH } from '../api/types';
import type { RateScope } from '../api/types';
import type { StatusPresentation } from './run-status';

/**
 * 交易所级的合约格是**空串**而不是某个占位符: 空串是这个表里的合法查找键, 而引擎拿成交合约去
 * 查时永远不会给出空串. 与后端 `rate_expansion.EXCHANGE_LEVEL_INSTRUMENT_ID` 逐字一致.
 */
export const EXCHANGE_LEVEL_INSTRUMENT_ID = '';

/** 一行费率的合约格 → 它的作用域. 空串是交易所级, 不长于阈值的是品种级, 其余是合约级. */
export function resolveRateScope(instrumentId: string): RateScope {
  if (instrumentId === EXCHANGE_LEVEL_INSTRUMENT_ID) {
    return 'exchange';
  }

  return instrumentId.length <= MAXIMUM_PRODUCT_CODE_LENGTH ? 'product' : 'contract';
}

const RATE_SCOPE_PRESENTATIONS: Record<RateScope, StatusPresentation> = {
  contract: { label: '合约级', tone: 'progress' },
  product: { label: '品种级', tone: 'neutral' },
  exchange: { label: '交易所级', tone: 'warning' },
};

export function describeRateScope(scope: RateScope): StatusPresentation {
  return RATE_SCOPE_PRESENTATIONS[scope];
}

/**
 * 一行费率指哪儿, 形如 `SSE 600519` / `SSE 600 (品种)` / `SSE (全交易所)`.
 *
 * 交易所级那一行的合约格是空串, 直接拼两个字段会渲染出"SSE  的买向费率"这种双空格 —— 用户看到
 * 的是"这里少了个东西", 而不是"这一条管的是整个交易所".
 */
export function describeRateScopeTarget(exchangeId: string, instrumentId: string): string {
  const scope = resolveRateScope(instrumentId);

  if (scope === 'exchange') {
    return `${exchangeId} (全交易所)`;
  }

  if (scope === 'product') {
    return `${exchangeId} ${instrumentId} (品种)`;
  }

  return `${exchangeId} ${instrumentId}`;
}
