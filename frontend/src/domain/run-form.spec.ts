/**
 * 新建回测表单的校验.
 *
 * 用例逐条对齐 `backend/app/services/run_submission.py`: 那里会 400 的每一种输入, 这里都必须
 * 在提交前就报出来 (且报的是同一件事), 否则用户看到的是「点了有反应, 然后弹一句后端的错」.
 */

import { describe, expect, it } from 'vitest';

import type { LastSubmittedParameters, RunTemplate } from '../api/types';
import type { StrategyConfigurationTemplate } from '../api/types';
import {
  createInitialParameterInputs,
  deriveParameterDescriptors,
  deriveParameterValues,
} from './strategy-configuration';
import type { ParameterInput } from './strategy-configuration';
import {
  BLANK_TEMPLATE_NAME_MESSAGE,
  EMPTY_RUN_FIELDS,
  MAXIMUM_RUN_FIELD_VALUE_LENGTH,
  MAXIMUM_RUN_TEMPLATE_NAME_LENGTH,
  buildCoverageQuery,
  buildPrefilledRunFields,
  buildTemplateDraft,
  validateRunForm,
} from './run-form';
import type { RunFieldInputs, RunFormInput } from './run-form';

/** 三种参数取值类型各一个, 用来验"存模板 → 套用"这条往返路不丢类型. */
const ROUND_TRIP_TEMPLATE: StrategyConfigurationTemplate = {
  GridStep: 0.01,
  TradeVolume: 100,
  UseStopLoss: false,
  AccountId: '模板占位账号',
};

function buildFormInput(overrides: Partial<RunFormInput> = {}): RunFormInput {
  return {
    strategyId: 'strategy-1',
    strategyVersionId: 'version-1',
    matchMode: 'Bar',
    barPeriod: '5m',
    exchangeId: 'SSE',
    instrumentId: '600519',
    startTradingDay: '20240102',
    endTradingDay: '20241231',
    initialCapitalText: '100000',
    // 刻意用一个**不是 1** 的组号: 1 号组是模型那一列的回填默认值, 用它当夹具的话, "组号有没有
    // 被带进请求体"这一问在 1 上分不出来.
    commissionGroupId: 7,
    parameterValues: { GridStep: 0.01 },
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
  it('两个合约字段都带上, 且前后空白去掉', () => {
    const validation = validateRunForm(
      buildFormInput({ exchangeId: ' SSE ', instrumentId: '600519 ' }),
    );

    expect(validation.ok).toBe(true);
    expect(validation.ok && validation.payload).toEqual({
      strategy_id: 'strategy-1',
      strategy_version_id: 'version-1',
      match_mode: 'Bar',
      bar_period: '5m',
      exchange_id: 'SSE',
      instrument_id: '600519',
      start_trading_day: '20240102',
      end_trading_day: '20241231',
      initial_capital: 100000,
      commission_group_id: 7,
      params: { GridStep: 0.01 },
    });
  });

  it('没有选版本时 version 字段是 null, 由后端取最新版本', () => {
    const validation = validateRunForm(buildFormInput({ strategyVersionId: '' }));

    expect(validation.ok && validation.payload.strategy_version_id).toBeNull();
  });
});

describe('validateRunForm 的必填与格式', () => {
  it('没选策略当场报错', () => {
    expect(readErrors(buildFormInput({ strategyId: '' })).strategy_id).toBe('请选择策略');
  });

  it('bar_period 恒必填', () => {
    expect(readErrors(buildFormInput({ barPeriod: '   ' })).bar_period).toBe('不能为空');
  });

  it('bar_period 只收清单内的周期, 清单外的取值当场挡下', () => {
    // `1d` 与 `1m` 是引擎聚合不出来的周期: 提交出去不会静默零成交, 但整轮会在装载期被拒 —— 那时
    // 已经白跑了一轮, 故在这里就挡住.
    for (const unsupported of ['1d', '1m', '5', '5M', 'daily']) {
      expect(readErrors(buildFormInput({ barPeriod: unsupported })).bar_period).toBe(
        '只能是 5m / 15m / 30m / 60m',
      );
    }
  });

  it('合约与交易所恒必填: 平台按固定键名覆写它们, 没有"这个策略不用合约"这回事', () => {
    expect(readErrors(buildFormInput({ exchangeId: '' })).exchange_id).toBe('不能为空');
    expect(readErrors(buildFormInput({ instrumentId: '  ' })).instrument_id).toBe('不能为空');
  });

  it('运行级字段的超长与控制字符都在本地挡住', () => {
    const tooLong = 'a'.repeat(MAXIMUM_RUN_FIELD_VALUE_LENGTH + 1);

    expect(readErrors(buildFormInput({ exchangeId: tooLong })).exchange_id).toBe(
      `不得超过 ${MAXIMUM_RUN_FIELD_VALUE_LENGTH} 个字符`,
    );
    expect(readErrors(buildFormInput({ exchangeId: 'SSE\u0007' })).exchange_id).toBe(
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
      buildFormInput({
        strategyId: '',
        barPeriod: '',
        startTradingDay: 'x',
        commissionGroupId: null,
      }),
    );

    expect(Object.keys(errors).sort()).toEqual([
      'bar_period',
      'commission_group_id',
      'start_trading_day',
      'strategy_id',
    ]);
  });

  it('手续费组恒必填: 没选时就地报错, 且报错键是后端那个字段名', () => {
    // 不给默认值是用户拍板的那条决定在界面上的一环: "没选"与"选了 1 号组"必须是两件可分辨的事,
    // 而后端收的正是 `commission_group_id` (它自己会回 400), 故错误键照抄那个名字.
    expect(readErrors(buildFormInput({ commissionGroupId: null })).commission_group_id).toBe(
      '请选择手续费组',
    );
  });

  it('手续费组须为不小于 0 的整数', () => {
    // 这一格在界面上只能从下拉框选出来, 故这句防的是**运行时数据**: 预填与套用收的是接口回来的
    // 取值, 类型标注管不住它.
    for (const badGroupId of [-1, 1.5, Number.NaN]) {
      expect(
        readErrors(buildFormInput({ commissionGroupId: badGroupId })).commission_group_id,
      ).toBe('须为不小于 0 的整数');
    }

    // 0 号组是**合法**的 (组号由管理员手工指定, 没有"必须从 1 开始"这条约定).
    expect(validateRunForm(buildFormInput({ commissionGroupId: 0 })).ok).toBe(true);
  });
});

describe('buildTemplateDraft', () => {
  it('取值整套从提交 payload 上抄, 模板名去空白', () => {
    const result = buildTemplateDraft(
      '  网格默认  ',
      buildFormInput({ exchangeId: 'SZSE', instrumentId: '000001' }),
    );

    expect(result).toEqual({
      ok: true,
      draft: {
        name: '网格默认',
        match_mode: 'Bar',
        bar_period: '5m',
        exchange_id: 'SZSE',
        instrument_id: '000001',
        start_trading_day: '20240102',
        end_trading_day: '20241231',
        initial_capital: 100000,
        commission_group_id: 7,
        params: { GridStep: 0.01 },
      },
    });
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
    const descriptors = deriveParameterDescriptors(ROUND_TRIP_TEMPLATE);
    // 控件给出来的原始输入: 数值是字符串, 布尔量是布尔.
    const parameterInputs: Record<string, ParameterInput> = {
      GridStep: '0.01',
      TradeVolume: '100',
      UseStopLoss: false,
      AccountId: '真实账号',
    };
    const derivation = deriveParameterValues(descriptors, parameterInputs);
    const result = buildTemplateDraft(
      '网格默认',
      buildFormInput({
        parameterValues: derivation.ok ? derivation.values : {},
      }),
    );

    // 存进去的是 JSON 取值 (`0.01` 而不是 `"0.01"`), 与 `Runs.ParamsJson` 同形同义.
    expect(result.ok && result.draft.params).toEqual({
      GridStep: 0.01,
      TradeVolume: 100,
      UseStopLoss: false,
      AccountId: '真实账号',
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
      commission_group_id: 12,
      params: {},
      ...overrides,
    };
  }

  it('记忆里的运行级字段填进来, 数值原样成文本', () => {
    expect(buildPrefilledRunFields(buildPrefill(), EMPTY_RUN_FIELDS)).toEqual({
      barPeriod: '30m',
      exchangeId: 'SZSE',
      instrumentId: '000001',
      startTradingDay: '20220104',
      endTradingDay: '20221230',
      initialCapitalText: '250000',
      commissionGroupId: 12,
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
      commission_group_id: 12,
      params: {},
      created_at: '2026-09-26T03:00:00',
      updated_at: '2026-09-26T03:00:00',
    };

    expect(buildPrefilledRunFields(template, EMPTY_RUN_FIELDS)).toEqual(
      buildPrefilledRunFields(buildPrefill(), EMPTY_RUN_FIELDS),
    );
  });

  it('没有记忆时结果恒等于当前输入', () => {
    const current: RunFieldInputs = {
      barPeriod: '5m',
      exchangeId: 'SSE',
      instrumentId: '600519',
      startTradingDay: '20240102',
      endTradingDay: '20241231',
      initialCapitalText: '500000',
      commissionGroupId: 3,
    };

    expect(buildPrefilledRunFields(null, current)).toEqual(current);
  });

  it('单项读不动时只丢那一项, 其余照填', () => {
    // 日期不是 8 位数字、资金非正、组号是个负数: 各自回落当前输入, 不让其余几项跟着失效.
    const prefill = buildPrefill({
      start_trading_day: '2022-01-04',
      initial_capital: 0,
      commission_group_id: -1,
    });

    expect(buildPrefilledRunFields(prefill, EMPTY_RUN_FIELDS)).toEqual({
      barPeriod: '30m',
      exchangeId: 'SZSE',
      instrumentId: '000001',
      startTradingDay: '',
      endTradingDay: '20221230',
      initialCapitalText: '100000',
      commissionGroupId: null,
    });
  });

  it('记忆里没带组号时这一格留空, 不替用户认领任何一组', () => {
    // 后端那一格可空 (`LastSubmittedParameters.commission_group_id`): 读不出来就不回填 —— 认领一个
    // 组号等于按一套不知道是谁的费率预填.
    expect(
      buildPrefilledRunFields(
        buildPrefill({ commission_group_id: null }),
        EMPTY_RUN_FIELDS,
      ).commissionGroupId,
    ).toBeNull();
  });

  it('读不动的取值按运行级字段那套判据挡下', () => {
    // 旧的一份记忆里可能存着组件根本没有的周期 (界面以前是自由文本), 它不该被填进下拉框.
    expect(buildPrefilledRunFields(buildPrefill({ bar_period: '1d' }), EMPTY_RUN_FIELDS).barPeriod).toBe(
      '',
    );

    expect(
      buildPrefilledRunFields(buildPrefill({ exchange_id: 'SSE\u0007' }), EMPTY_RUN_FIELDS)
        .exchangeId,
    ).toBe('');
  });
});

describe('buildCoverageQuery', () => {
  it('四格都成型时才给出预检查询, 取值去空白', () => {
    expect(
      buildCoverageQuery(
        buildFormInput({ exchangeId: ' SSE ', instrumentId: '600519 ' }),
      ),
    ).toEqual({
      exchange_id: 'SSE',
      instrument_id: '600519',
      start_trading_day: '20240102',
      end_trading_day: '20241231',
    });
  });

  it('问的是"本地有没有那段日期", 与用户选的订阅周期无关', () => {
    // 落盘只有 5m 一档, 换订阅周期不改变"数据在不在" —— 查询里因此没有 bar_period 这一格.
    expect(buildCoverageQuery(buildFormInput({ barPeriod: '' }))).toEqual(
      buildCoverageQuery(buildFormInput({ barPeriod: '60m' })),
    );
  });

  it('缺任何一格都问不出结果, 于是回 null', () => {
    const incomplete: Partial<RunFormInput>[] = [
      { exchangeId: '' },
      { instrumentId: '' },
      { startTradingDay: '' },
      { endTradingDay: '2024-12-31' },
    ];

    for (const overrides of incomplete) {
      expect(buildCoverageQuery(buildFormInput(overrides))).toBeNull();
    }
  });

  it('开始晚于结束时回 null: 那个区间算不出期望交易日, 问了只会得到"缺 0 个交易日"', () => {
    expect(
      buildCoverageQuery(
        buildFormInput({ startTradingDay: '20241231', endTradingDay: '20240102' }),
      ),
    ).toBeNull();
  });
});
