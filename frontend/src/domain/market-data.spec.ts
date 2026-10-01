/**
 * 行情组件那两份镜像的换算.
 *
 * 这一小段是提交页与组件之间唯一的约定: 主键拼错的表现是下拉框选出来的合约**提交后跑不动**, 而
 * 不是当场报错, 故两个方向都钉住.
 */

import { describe, expect, it } from 'vitest';

import type { MarketDataContract } from '../api/types';
import { buildContractCode, formatContractLabel, SUPPORTED_BAR_PERIODS } from './market-data';

const CONTRACT: MarketDataContract = {
  code: 'sh.600519',
  exchange_id: 'SSE',
  instrument_id: '600519',
  display_name: '贵州茅台',
};

describe('buildContractCode', () => {
  it('两个交易所都拼得出来, 且与合约自己的主键逐字一致', () => {
    expect(buildContractCode('SSE', '600519')).toBe('sh.600519');
    expect(buildContractCode('SZSE', '000001')).toBe('sz.000001');
    expect(buildContractCode(CONTRACT.exchange_id, CONTRACT.instrument_id)).toBe(
      CONTRACT.code,
    );
  });

  it('认不出的交易所或空合约都回 null, 不拼出一串没人认识的字面量', () => {
    expect(buildContractCode('SHFE', 'rb2405')).toBeNull();
    expect(buildContractCode('', '600519')).toBeNull();
    expect(buildContractCode('SSE', '')).toBeNull();
  });
});

describe('formatContractLabel', () => {
  it('交易所 + 代码 + 名称都给, 下拉框的搜索因此三种都能命中', () => {
    expect(formatContractLabel(CONTRACT)).toBe('SSE 600519 贵州茅台');
  });
});

describe('SUPPORTED_BAR_PERIODS', () => {
  it('与组件 CLI 的 --frequency choices 一一对应, 没有 1 分钟线也没有日线', () => {
    expect(SUPPORTED_BAR_PERIODS.map((period) => period.replace('m', ''))).toEqual([
      '5',
      '15',
      '30',
      '60',
    ]);
  });
});
