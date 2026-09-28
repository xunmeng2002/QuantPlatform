/**
 * 新建回测表单的校验.
 *
 * 用例逐条对齐 `backend/app/services/run_submission.py`: 那里会 400 的每一种输入, 这里都必须
 * 在提交前就报出来 (且报的是同一件事), 否则用户看到的是「点了有反应, 然后弹一句后端的错」.
 */

import { describe, expect, it } from 'vitest';

import type { LastSubmittedParameters, RunTemplate, StrategyManifest } from '../api/types';
import {
  createInitialParameterInputs,
  deriveParameterDescriptors,
  deriveParameterValues,
} from './manifest';
import type { ParameterInput, RunFieldRequirements } from './manifest';
import {
  BLANK_TEMPLATE_NAME_MESSAGE,
  EMPTY_RUN_FIELDS,
  MAXIMUM_RUN_FIELD_VALUE_LENGTH,
  MAXIMUM_RUN_TEMPLATE_NAME_LENGTH,
  buildPrefilledRunFields,
  buildTemplateDraft,
  validateRunForm,
} from './run-form';
import type { RunFieldInputs, RunFormInput } from './run-form';
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

/** 四种参数类型各一个, 用来验"存模板 → 套用"这条往返路不丢类型. */
const ROUND_TRIP_MANIFEST: StrategyManifest = {
  entry_filename: 'StrategyMain.py',
  config_filename: 'BackTest.json',
  supported_match_modes: ['Bar'],
  params: [
    { key: 'GridStep', label: '网格步长', type: 'number', default: 0.01 },
    { key: 'TradeVolume', label: '单笔手数', type: 'integer', default: 100 },
    { key: 'UseStopLoss', label: '启用止损', type: 'boolean', default: false },
    {
      key: 'Mode',
      label: '模式',
      type: 'string',
      options: [
        { value: 'fast', label: '快' },
        { value: 'slow', label: '慢' },
      ],
    },
  ],
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

describe('buildTemplateDraft', () => {
  it('取值整套从提交 payload 上抄, 模板名去空白', () => {
    const result = buildTemplateDraft(
      '  网格默认  ',
      buildFormInput({
        runFieldRequirements: MAPPED_RUN_FIELDS,
        exchangeId: 'SHFE',
        instrumentId: 'rb2405',
      }),
    );

    expect(result).toEqual({
      ok: true,
      draft: {
        name: '网格默认',
        match_mode: 'Bar',
        bar_period: '1d',
        exchange_id: 'SHFE',
        instrument_id: 'rb2405',
        start_trading_day: '20240102',
        end_trading_day: '20241231',
        initial_capital: 100000,
        params: { period: 5 },
      },
    });
  });

  it('没映射的运行级字段存 null, 不存空串', () => {
    const result = buildTemplateDraft('默认', buildFormInput());

    expect(result.ok && result.draft.exchange_id).toBeNull();
    expect(result.ok && result.draft.instrument_id).toBeNull();
  });

  it('提交不出去的表单存不成模板, 报的是 validateRunForm 那套说法', () => {
    const result = buildTemplateDraft(
      '默认',
      buildFormInput({ barPeriod: '', initialCapitalText: '0' }),
    );

    expect(result.ok).toBe(false);
    expect(result.ok ? {} : result.errors).toEqual({
      bar_period: '不能为空',
      initial_capital: '须为大于 0 的有限数值',
    });
  });

  it('模板名空着与表单不合格时两批错误一起回', () => {
    const result = buildTemplateDraft('   ', buildFormInput({ barPeriod: '' }));

    expect(result.ok ? {} : result.errors).toEqual({
      name: BLANK_TEMPLATE_NAME_MESSAGE,
      bar_period: '不能为空',
    });
  });

  it('模板名的长度判在 64 个字符上, 且比的是**原串**', () => {
    const longestAcceptedName = 'x'.repeat(MAXIMUM_RUN_TEMPLATE_NAME_LENGTH);

    expect(buildTemplateDraft(longestAcceptedName, buildFormInput()).ok).toBe(true);

    const tooLong = buildTemplateDraft(`${longestAcceptedName}x`, buildFormInput());

    expect(tooLong.ok ? {} : tooLong.errors).toEqual({
      name: `不得超过 ${MAXIMUM_RUN_TEMPLATE_NAME_LENGTH} 个字符`,
    });

    // 64 个字符 + 一个尾空格: 去空白后是 64, 但后端 pydantic 的 max_length 看的是原串, 故这里
    // 也判原串 —— 放行的话用户会吃一个后端 422.
    const trailingSpace = buildTemplateDraft(`${longestAcceptedName} `, buildFormInput());

    expect(trailingSpace.ok ? {} : trailingSpace.errors).toEqual({
      name: `不得超过 ${MAXIMUM_RUN_TEMPLATE_NAME_LENGTH} 个字符`,
    });
  });

  it('存下来的参数能原样填回控件 (往返回同一条控件路径)', () => {
    const descriptors = deriveParameterDescriptors(ROUND_TRIP_MANIFEST);
    // 控件给出来的原始输入: 数值是字符串, 有选项的参数是**选项下标** (`coerceParameterInput` 的契约).
    const parameterInputs: Record<string, ParameterInput> = {
      GridStep: '0.01',
      TradeVolume: '100',
      UseStopLoss: false,
      Mode: '1',
    };
    const derivation = deriveParameterValues(descriptors, parameterInputs);
    const result = buildTemplateDraft(
      '网格默认',
      buildFormInput({
        parameterValues: derivation.ok ? derivation.values : {},
      }),
    );

    // 存进去的是引擎取值 (`0.01` 而不是 `"0.01"`), 与 `Runs.ParamsJson` 同形同义.
    expect(result.ok && result.draft.params).toEqual({
      GridStep: 0.01,
      TradeVolume: 100,
      UseStopLoss: false,
      Mode: 'slow',
    });

    // 套用时走的还是 `createInitialParameterInputs`, 故原始输入能一位不差地回来.
    expect(
      createInitialParameterInputs(descriptors, result.ok ? result.draft.params : {}),
    ).toEqual(parameterInputs);
  });
});

describe('buildPrefilledRunFields', () => {
  function buildPrefill(
    overrides: Partial<LastSubmittedParameters> = {},
  ): LastSubmittedParameters {
    return {
      run_id: 'run-1',
      submitted_at: '2026-09-26T03:00:00',
      match_mode: 'Bar',
      bar_period: '30m',
      exchange_id: 'SZSE',
      instrument_id: '000001',
      start_trading_day: '20220104',
      end_trading_day: '20221230',
      initial_capital: 250000,
      params: {},
      ...overrides,
    };
  }

  it('记忆里的运行级字段填进来, 数值原样成文本', () => {
    expect(
      buildPrefilledRunFields(buildPrefill(), MAPPED_RUN_FIELDS, EMPTY_RUN_FIELDS),
    ).toEqual({
      barPeriod: '30m',
      exchangeId: 'SZSE',
      instrumentId: '000001',
      startTradingDay: '20220104',
      endTradingDay: '20221230',
      initialCapitalText: '250000',
    });
  });

  it('模板与记忆在填表眼里是同一种东西', () => {
    // 模板**没有** `run_id` / `submitted_at` —— 这一段编译得过, 就是"套用模板与按记忆预填走同一
    // 条路"的判据: 只要那几个运行级字段在, 它就是一份能填的取值.
    const template: RunTemplate = {
      id: 'template-1',
      strategy_id: 'strategy-1',
      name: '网格默认',
      match_mode: 'Bar',
      bar_period: '30m',
      exchange_id: 'SZSE',
      instrument_id: '000001',
      start_trading_day: '20220104',
      end_trading_day: '20221230',
      initial_capital: 250000,
      params: {},
      created_at: '2026-09-26T03:00:00',
      updated_at: '2026-09-26T03:00:00',
    };

    expect(
      buildPrefilledRunFields(template, MAPPED_RUN_FIELDS, EMPTY_RUN_FIELDS),
    ).toEqual(
      buildPrefilledRunFields(buildPrefill(), MAPPED_RUN_FIELDS, EMPTY_RUN_FIELDS),
    );
  });

  it('没有记忆时结果恒等于当前输入', () => {
    const current: RunFieldInputs = {
      barPeriod: '1d',
      exchangeId: 'SSE',
      instrumentId: '600519',
      startTradingDay: '20240102',
      endTradingDay: '20241231',
      initialCapitalText: '500000',
    };

    expect(buildPrefilledRunFields(null, MAPPED_RUN_FIELDS, current)).toEqual(current);
  });

  it('单项读不动时只丢那一项, 其余照填', () => {
    // 日期不是 8 位数字、资金非正: 各自回落当前输入, 不让其余三项跟着失效.
    const prefill = buildPrefill({
      start_trading_day: '2022-01-04',
      initial_capital: 0,
    });

    expect(
      buildPrefilledRunFields(prefill, MAPPED_RUN_FIELDS, EMPTY_RUN_FIELDS),
    ).toEqual({
      barPeriod: '30m',
      exchangeId: 'SZSE',
      instrumentId: '000001',
      startTradingDay: '',
      endTradingDay: '20221230',
      initialCapitalText: '100000',
    });
  });

  it('未声明映射的运行级字段不预填: 那两个输入框根本不渲染', () => {
    expect(
      buildPrefilledRunFields(buildPrefill(), UNMAPPED_RUN_FIELDS, EMPTY_RUN_FIELDS),
    ).toEqual({
      barPeriod: '30m',
      exchangeId: '',
      instrumentId: '',
      startTradingDay: '20220104',
      endTradingDay: '20221230',
      initialCapitalText: '250000',
    });
  });

  it('超长与含控制字符的取值按运行级字段那套判据挡下', () => {
    expect(
      buildPrefilledRunFields(
        buildPrefill({ bar_period: 'x'.repeat(MAXIMUM_RUN_FIELD_VALUE_LENGTH + 1) }),
        MAPPED_RUN_FIELDS,
        EMPTY_RUN_FIELDS,
      ).barPeriod,
    ).toBe('');

    expect(
      buildPrefilledRunFields(
        buildPrefill({ exchange_id: 'SSE\u0007' }),
        MAPPED_RUN_FIELDS,
        EMPTY_RUN_FIELDS,
      ).exchangeId,
    ).toBe('');
  });
});
