/**
 * 配置模板 → 控件 → 提交取值这条链.
 *
 * 这一段是"参数填错"唯一还能被拦住的地方: 平台不要声明、不做范围校验, 于是控件分派与数值判据就是
 * 全部防线. 三条最要紧的: 类型由**取值**推出来 (不是声明的), 平台键不进参数区, 数值框空着必须判错
 * 而不是变成 0.
 */

import { describe, expect, it } from 'vitest';

import {
  BLANK_NUMBER_MESSAGE,
  CONFIGURATION_NOT_JSON_MESSAGE,
  CONFIGURATION_SHAPE_MESSAGE,
  NOT_A_NUMBER_MESSAGE,
  coerceParameterInput,
  createInitialParameterInputs,
  deriveParameterDescriptors,
  deriveParameterValues,
  findUnrenderableParameterKeys,
  parseStrategyConfigurationTemplate,
  type ParameterDescriptor,
} from './strategy-configuration';

describe('parseStrategyConfigurationTemplate', () => {
  it('顶层对象才收得下, 原样透传不做任何加工', () => {
    const result = parseStrategyConfigurationTemplate('{"GridStep": 0.01}');

    expect(result).toEqual({ ok: true, template: { GridStep: 0.01 } });
  });

  it('不是 JSON 与不是对象分开报, 因为下一步动作不同', () => {
    expect(parseStrategyConfigurationTemplate('GridStep: 0.01')).toEqual({
      ok: false,
      message: CONFIGURATION_NOT_JSON_MESSAGE,
    });
    expect(parseStrategyConfigurationTemplate('[1, 2]')).toEqual({
      ok: false,
      message: CONFIGURATION_SHAPE_MESSAGE,
    });
    expect(parseStrategyConfigurationTemplate('null')).toEqual({
      ok: false,
      message: CONFIGURATION_SHAPE_MESSAGE,
    });
  });
});

describe('deriveParameterDescriptors', () => {
  it('控件形态由该键当前取值的 JSON 类型决定', () => {
    const descriptors = deriveParameterDescriptors({
      IsHedging: true,
      GridCount: 10,
      AccountId: '账号',
    });

    expect(descriptors).toEqual([
      { key: 'IsHedging', type: 'boolean', defaultValue: true },
      { key: 'GridCount', type: 'number', defaultValue: 10 },
      { key: 'AccountId', type: 'string', defaultValue: '账号' },
    ]);
  });

  it('三个平台键不进参数区: 提交页的固定区覆写它们, 这里再给一次只会让用户以为改得动', () => {
    const descriptors = deriveParameterDescriptors({
      ExchangeId: 'SZSE',
      InstrumentId: '000001',
      BarPreces: '60m',
      GridStep: 0.01,
    });

    expect(descriptors.map((descriptor) => descriptor.key)).toEqual(['GridStep']);
  });

  it('数组/对象/null 渲染不出控件, 但与"不存在"分开: 它们在提示里列得出来', () => {
    const template = {
      GridStep: 0.01,
      NestedRule: { Slots: [] },
      OptionalWindow: null,
      Weekdays: ['mon'],
    };

    expect(deriveParameterDescriptors(template).map((descriptor) => descriptor.key)).toEqual([
      'GridStep',
    ]);
    expect(findUnrenderableParameterKeys(template)).toEqual([
      'NestedRule',
      'OptionalWindow',
      'Weekdays',
    ]);
  });
});

describe('createInitialParameterInputs', () => {
  const descriptors = deriveParameterDescriptors({
    IsHedging: false,
    GridCount: 10,
    AccountId: '模板占位账号',
  });

  it('没有记忆时初值就是模板里的值', () => {
    expect(createInitialParameterInputs(descriptors)).toEqual({
      IsHedging: false,
      GridCount: '10',
      AccountId: '模板占位账号',
    });
  });

  it('记忆里类型对得上的键用记忆值', () => {
    expect(
      createInitialParameterInputs(descriptors, { GridCount: 3, AccountId: '上次的账号' }),
    ).toEqual({ IsHedging: false, GridCount: '3', AccountId: '上次的账号' });
  });

  it('记忆里类型对不上的键退回模板值: 那份记忆来自别的版本, 模板才是这份版本的真相', () => {
    expect(
      createInitialParameterInputs(descriptors, {
        IsHedging: 'true',
        GridCount: '3',
        AccountId: 42,
      }),
    ).toEqual({ IsHedging: false, GridCount: '10', AccountId: '模板占位账号' });
  });

  it('空串是合法取值而不是"没记住": 用户特意清空的默认值不被填回去', () => {
    const stringDescriptors: ParameterDescriptor[] = [
      { key: 'AccountId', type: 'string', defaultValue: '模板占位账号' },
    ];

    expect(createInitialParameterInputs(stringDescriptors, { AccountId: '' })).toEqual({
      AccountId: '',
    });
  });
});

describe('deriveParameterValues', () => {
  const descriptors = deriveParameterDescriptors({
    IsHedging: false,
    GridCount: 10,
    AccountId: '模板占位账号',
  });

  it('收好的取值与 JSON 里的形态一致: 数值是数, 布尔是布尔', () => {
    const derivation = deriveParameterValues(descriptors, {
      IsHedging: true,
      GridCount: '3',
      AccountId: '真实账号',
    });

    expect(derivation).toEqual({
      ok: true,
      values: { IsHedging: true, GridCount: 3, AccountId: '真实账号' },
    });

    if (derivation.ok) {
      expect(typeof derivation.values.GridCount).toBe('number');
    }
  });

  it('数值框空着判「不能为空」而不是偷偷变成 0', () => {
    expect(
      deriveParameterValues(descriptors, { IsHedging: false, GridCount: '  ', AccountId: '' }),
    ).toEqual({ ok: false, errors: { GridCount: BLANK_NUMBER_MESSAGE } });
  });

  it('非数同样只影响它自己那一格, 字符串可以留空', () => {
    expect(
      deriveParameterValues(descriptors, {
        IsHedging: false,
        GridCount: '三格',
        AccountId: '',
      }),
    ).toEqual({ ok: false, errors: { GridCount: NOT_A_NUMBER_MESSAGE } });
  });

  it('一次报出所有填错的格子, 而不是改一个再看见下一个', () => {
    const twoNumberDescriptors = deriveParameterDescriptors({ First: 1, Second: 2 });

    expect(
      deriveParameterValues(twoNumberDescriptors, { First: 'a', Second: '' }),
    ).toEqual({
      ok: false,
      errors: { First: NOT_A_NUMBER_MESSAGE, Second: BLANK_NUMBER_MESSAGE },
    });
  });
});

describe('coerceParameterInput', () => {
  it('字符串键原样收下, 空串也收', () => {
    const descriptor: ParameterDescriptor = { key: 'AccountId', type: 'string', defaultValue: '' };

    expect(coerceParameterInput(descriptor, '')).toEqual({ ok: true, value: '' });
  });

  it('数值键的两句判据分开: 一个是去填, 一个是去改', () => {
    const descriptor: ParameterDescriptor = { key: 'GridCount', type: 'number', defaultValue: 1 };

    expect(coerceParameterInput(descriptor, '  ')).toEqual({
      ok: false,
      message: BLANK_NUMBER_MESSAGE,
    });
    expect(coerceParameterInput(descriptor, '0.01')).toEqual({ ok: true, value: 0.01 });
    expect(coerceParameterInput(descriptor, '1e3')).toEqual({ ok: true, value: 1000 });
  });
});
