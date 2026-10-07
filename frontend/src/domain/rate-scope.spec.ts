/**
 * 费率作用域的读法. 它是**纯函数**, 而它错了的症状是"界面说这是一条品种级规则, 引擎却当合约级
 * 用" —— 两边都照同一个数字判, 故这里逐条钉住边界.
 */

import { describe, expect, it } from 'vitest';

import {
  describeRateScope,
  describeRateScopeTarget,
  resolveRateScope,
} from './rate-scope';

describe('resolveRateScope', () => {
  it('空串是交易所级 —— 那一格合法为空, 不是漏填', () => {
    expect(resolveRateScope('')).toBe('exchange');
  });

  it('不长于 4 位的短码是品种级 (A 股三位数字 / 期货字母)', () => {
    expect(resolveRateScope('600')).toBe('product');
    expect(resolveRateScope('rb')).toBe('product');
  });

  it('恰好 4 位仍按品种级 —— 阈值是「不长于」', () => {
    // 这一条是**已知近似的边界**: 真出现 4 位的合约码会被认成品种级. 后端守门读的是同一个数字,
    // 故界面与引擎仍然一致, 只是两者的叫法都不是用户本意.
    expect(resolveRateScope('6005')).toBe('product');
  });

  it('长过阈值的是合约级', () => {
    expect(resolveRateScope('600519')).toBe('contract');
    expect(resolveRateScope('rb2401')).toBe('contract');
  });
});

describe('describeRateScope', () => {
  it('三级各有自己的中文与色调', () => {
    expect(describeRateScope('contract').label).toBe('合约级');
    expect(describeRateScope('product').label).toBe('品种级');
    expect(describeRateScope('exchange').label).toBe('交易所级');
  });
});

describe('describeRateScopeTarget', () => {
  it('合约级就是交易所加合约码', () => {
    expect(describeRateScopeTarget('SSE', '600519')).toBe('SSE 600519');
  });

  it('品种级标出它是一条品种规则', () => {
    expect(describeRateScopeTarget('SSE', '600')).toBe('SSE 600 (品种)');
  });

  it('交易所级不渲染成双空格 —— 那一行的合约格本来就是空的', () => {
    expect(describeRateScopeTarget('SSE', '')).toBe('SSE (全交易所)');
  });
});
