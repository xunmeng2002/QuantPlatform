/**
 * 行情组件的两侧镜像: 可选的订阅周期, 以及平台字段与组件主键之间的换算.
 *
 * 「两侧」指本仓与 `../QuoteHub`. 组件按自己的一套字面量解释这些取值, 而平台**不 import 它**,
 * 只能把约定镜像过来; 镜像因此集中在这一个模块里 —— 散到各处的结果是改组件时只改得动一半.
 */

import type { MarketDataContract } from '../api/types';

/**
 * 用户可选的**策略订阅周期**, 与 `backend/app/config.SUBSCRIPTION_BAR_PERIODS` 逐字对应.
 *
 * 这不是数据源精度: 落盘的 parquet 只有 5m (`MARKET_DATA_PRECISION`), 引擎那份
 * `BackTest.json.BarPreces` 恒为它; 用户选的值写进**策略配置**, 由策略声明成订阅目标, 5m → 15m /
 * 30m / 60m 由引擎在运行时聚合. 故这里的清单是"5m 的整数倍且能被聚合出来"的那几个, 组件没有 1
 * 分钟线也没有日线.
 *
 * 选一个引擎聚合不出来的周期**不是静默零成交**: 引擎在装载期就判 `ErrorMarketDataNotExist` 拒掉
 * 整轮 (`BarAggregator::ValidatePrecesRelation`), 用户拿到的是明确失败而不是一份空结果 —— 但那一轮
 * 已经白跑了, 故界面上只给清单内的值, 不给自由文本.
 */
export const SUBSCRIPTION_BAR_PERIODS = ['5m', '15m', '30m', '60m'] as const;

export type SubscriptionBarPeriod = (typeof SUBSCRIPTION_BAR_PERIODS)[number];

/**
 * 交易所字面量 → 组件主键前缀. 与 `backend/app/services/quote_hub.EXCHANGE_PREFIX_TO_IDENTIFIER`
 * 以及行情目录名 `Identity=SSE.Stock` 三处一致.
 */
const EXCHANGE_IDENTIFIER_TO_PREFIX: Record<string, string> = {
  SSE: 'sh',
  SZSE: 'sz',
};

const CONTRACT_CODE_SEPARATOR = '.';

/**
 * `("SSE", "600519")` → `"sh.600519"`; 拼不出组件主键时回 `null`.
 *
 * 这个方向只用于**把一个已选中的合约显示出来**: 下拉框的选中值是组件主键, 而它不是一份独立状态
 * ——预填与模板都直接写 `exchangeId` / `instrumentId` 两格, 另存一份的后果是两条路各说各话. 反向
 * 的拆写走后端已经拆好的 `MarketDataContract` (见 `api/types`), 前端不解析主键.
 */
export function buildContractCode(
  exchangeId: string,
  instrumentId: string,
): string | null {
  const prefix = EXCHANGE_IDENTIFIER_TO_PREFIX[exchangeId];

  if (prefix === undefined || !instrumentId) {
    return null;
  }

  return `${prefix}${CONTRACT_CODE_SEPARATOR}${instrumentId}`;
}

/**
 * 下拉框里那一行的文案: 交易所 + 代码 + 名称.
 *
 * 三样都给是因为 `filterable` 按这个串做子串匹配 —— 用户可能记得代码也可能记得名字, 少一样就
 * 搜不到.
 */
export function formatContractLabel(contract: MarketDataContract): string {
  return `${contract.exchange_id} ${contract.instrument_id} ${contract.display_name}`;
}

/**
 * `el-select-v2` 吃的那份选项. 字段名照它的默认映射 (`label` / `value`), 不另传 `props`.
 *
 * 取值仍是**组件主键** (`sh.600519`), 与 `ElOption` 那版的 `:value` 逐字相同 —— 选中后由
 * `selectedContractCode` 的 setter 交给后端已经拆好的那一份, 前端不解析主键.
 *
 * 用 `el-select-v2` 而不是 `el-select` + `v-for` 的原因是**渲染量**: 合约有五千多条, 而
 * `el-select` 的 `persistent` 默认为真 (见 `element-plus/es/components/select/src/select.mjs`),
 * 它的下拉内容在挂载期就渲染 —— 实测 (jsdom 一次性探针) 五千余项要十几秒, 且下拉框一次都没被
 * 打开过. 虚拟滚动只渲染可视区那几行, 这一项开销随之消失; 本地筛选 (`filterable` 按 `label`
 * 做子串匹配) 由 `el-select-v2` 自己保证, 故 `formatContractLabel` 的三段式一个字不用改.
 */
export interface ContractOption {
  value: string;
  label: string;
}

export function buildContractOptions(
  contracts: readonly MarketDataContract[],
): ContractOption[] {
  return contracts.map((contract) => ({
    value: contract.code,
    label: formatContractLabel(contract),
  }));
}
