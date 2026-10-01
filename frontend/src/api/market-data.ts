/** 提交页要的两份行情清单: 能选哪些合约, 以及"本地的够不够". */

import { request } from './client';
import type {
  MarketDataContractList,
  MarketDataCoverage,
  MarketDataCoverageQuery,
} from './types';

/**
 * 可选的合约与 K 线周期.
 *
 * 调用方**不必处理"组件不在位"**: 那时后端回的是 200 + `available=false` + 一句原因, 不是错误.
 * 于是这里只有一条失败分支 (网络 / 鉴权), 而且"另一个仓的组件没装"是一种正常状态而非异常.
 */
export function fetchMarketDataContracts(): Promise<MarketDataContractList> {
  return request<MarketDataContractList>('/market-data/contracts');
}

/**
 * 这一轮要不要先下载.
 *
 * 它只**告知**: 放行与否不归它管 —— 真下载发生在调度器里 (提交侧不碰文件系统这条纪律不破). 故
 * 调用方拿它只做一件事: 在表单上说清"本地缺 N 个交易日, 提交后需先下载".
 */
export function fetchMarketDataCoverage(
  query: MarketDataCoverageQuery,
): Promise<MarketDataCoverage> {
  return request<MarketDataCoverage>('/market-data/coverage', { query });
}
