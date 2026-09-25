/**
 * 新建回测表单的校验.
 *
 * 用例逐条对齐 `backend/app/services/run_submission.py`: 那里会 400 的每一种输入, 这里都必须
 * 在提交前就报出来 (且报的是同一件事), 否则用户看到的是「点了有反应, 然后弹一句后端的错」.
 */

import { describe, expect, it } from 'vitest';

import type { RunFieldRequirements } from './manifest';
import { MAXIMUM_RUN_FIELD_VALUE_LENGTH, validateRunForm } from './run-form';
import type { RunFormInput } from './run-form';

const UNMAPPED_RUN_FIELDS: RunFieldRequirements = {
  barPeriod: true,
  exchangeId: false,
  instrumentId: false,
};

const MAPPED_RUN_FIELDS: RunFieldRequirements = {
  barPeriod: true,
  exchangeId: true,
  instrumentId: true,
};

function buildFormInput(overrides: Partial<RunFormInput> = {}): RunFormInput {
  return {
    strategyId: 'strategy-1',
    strategyVersionId: 'version-1',
    matchMode: 'Bar',
    isMatchModeSupported: true,
    runFieldRequirements: UNMAPPED_RUN_FIELDS,
    barPeriod: '1d',
    exchangeId: '',
    instrumentId: '',
    startTradingDay: '20240102',
    endTradingDay: '20241231',
    initialCapitalText: '100000',
    parameterValues: { period: 5 },
    ...overrides,
  };
}

function readErrors(input: RunFormInput): Record<string, string> {
  const validation = validateRunForm(input);

  if (validation.ok) {
    throw new Error('预期这份表单不通过, 但它通过了');
  }

  return validation.errors;
}

describe('validateRunForm 的正向路径', () => {
  it('未映射 exchange/instrument 时, payload 里是 null 而不是空串', () => {
    const validation = validateRunForm(buildFormInput());

    expect(validation.ok).toBe(true);
    expect(validation.ok && validation.payload).toEqual({
      strategy_id: 'strategy-1',
      strategy_version_id: 'version-1',
      match_mode: 'Bar',
      bar_period: '1d',
      exchange_id: null,
      instrument_id: null,
      start_trading_day: '20240102',
      end_trading_day: '20241231',
      initial_capital: 100000,
      params: { period: 5 },
    });
  });

  it('映射了就带上这两个字段, 前后空白去掉', () => {
    const validation = validateRunForm(
      buildFormInput({
        runFieldRequirements: MAPPED_RUN_FIELDS,
        exchangeId: ' SHFE ',
        instrumentId: 'rb2405',
      }),
    );

    expect(validation.ok && validation.payload.exchange_id).toBe('SHFE');
    expect(validation.ok && validation.payload.instrument_id).toBe('rb2405');
  });

  it('没有选版本时 version 字段是 null, 由后端取最新版本', () => {
    const validation = validateRunForm(buildFormInput({ strategyVersionId: '' }));

    expect(validation.ok && validation.payload.strategy_version_id).toBeNull();
  });
});

describe('validateRunForm 的必填与格式', () => {
  it('没选策略 / 选中的版本不支持该行情模式', () => {
    expect(readErrors(buildFormInput({ strategyId: '' })).strategy_id).toBe('请选择策略');
    expect(
      readErrors(buildFormInput({ isMatchModeSupported: false })).strategy_id,
    ).toBe('该策略不支持 Bar 行情模式');
  });

  it('bar_period 与 manifest 的映射无关, 恒必填', () => {
    expect(readErrors(buildFormInput({ barPeriod: '   ' })).bar_period).toBe('不能为空');
  });

  it('映射了却没填 / 没映射但填了', () => {
    expect(
      readErrors(buildFormInput({ runFieldRequirements: MAPPED_RUN_FIELDS })).exchange_id,
    ).toBe('不能为空');

    // 没映射而填了: 后端回「该策略未映射 exchange_id」, 故这里也不放行 —— 但不是报错, 是不发送.
    const validation = validateRunForm(
      buildFormInput({ exchangeId: 'SHFE', instrumentId: 'rb2405' }),
    );

    expect(validation.ok && validation.payload.exchange_id).toBeNull();
    expect(validation.ok && validation.payload.instrument_id).toBeNull();
  });

  it('运行级字段的超长与控制字符都在本地挡住', () => {
    const tooLong = 'a'.repeat(MAXIMUM_RUN_FIELD_VALUE_LENGTH + 1);

    expect(readErrors(buildFormInput({ barPeriod: tooLong })).bar_period).toBe(
      `不得超过 ${MAXIMUM_RUN_FIELD_VALUE_LENGTH} 个字符`,
    );
    expect(readErrors(buildFormInput({ barPeriod: '1d\n5m' })).bar_period).toBe(
      '不得含控制字符',
    );
  });

  it('交易日是 8 位数字, 且开始不得晚于结束', () => {
    expect(readErrors(buildFormInput({ startTradingDay: '2024-01-02' })).start_trading_day).toBe(
      '须为 8 位数字',
    );
    expect(readErrors(buildFormInput({ endTradingDay: '2024131' })).end_trading_day).toBe(
      '须为 8 位数字',
    );
    expect(
      readErrors(
        buildFormInput({ startTradingDay: '20241231', endTradingDay: '20240102' }),
      ).start_trading_day,
    ).toBe('不得晚于结束交易日');
  });

  it('同一天的区间是合法的', () => {
    const validation = validateRunForm(
      buildFormInput({ startTradingDay: '20240102', endTradingDay: '20240102' }),
    );

    expect(validation.ok).toBe(true);
  });

  it('初始资金须为大于 0 的有限数值', () => {
    for (const badCapital of ['', '0', '-1', '十万', 'Infinity']) {
      expect(
        readErrors(buildFormInput({ initialCapitalText: badCapital })).initial_capital,
      ).toBe('须为大于 0 的有限数值');
    }

    const validation = validateRunForm(buildFormInput({ initialCapitalText: ' 50000.5 ' }));

    expect(validation.ok && validation.payload.initial_capital).toBe(50000.5);
  });

  it('多处不合格时每处都有各自的说法', () => {
    const errors = readErrors(
      buildFormInput({ strategyId: '', barPeriod: '', startTradingDay: 'x' }),
    );

    expect(Object.keys(errors).sort()).toEqual([
      'bar_period',
      'start_trading_day',
      'strategy_id',
    ]);
  });
});
