/**
 * manifest → 表单控件的派生规则.
 *
 * 这一批用例守的是同一件事: **界面放行的取值, 后端必须也放行**. 故每个类型的报错文案都与
 * `backend/app/manifest.py` 的 `validate_parameter_value` 逐字对齐, 对那些文案的断言就是
 * 「两边语义一致」的凭据.
 *
 * 断言一律走 `.map()` 而不是取下标: 数组下标在 `noUncheckedIndexedAccess` 下是可能不存在的,
 * 用例不该为了少写一行而引入非空断言.
 */

import { describe, expect, it } from 'vitest';

import type { ManifestParameter, StrategyManifest } from '../api/types';
import {
  MANIFEST_NOT_JSON_MESSAGE,
  MANIFEST_PARAMETERS_MESSAGE,
  MANIFEST_SHAPE_MESSAGE,
  REQUIRED_INPUT_MESSAGE,
  createInitialParameterInputs,
  deriveParameterDescriptors,
  deriveParameterValues,
  deriveRunFieldRequirements,
  groupParameterDescriptors,
  parseStrategyManifest,
} from './manifest';

const TOP_LEVEL_ONLY: Omit<StrategyManifest, 'params'> = {
  entry_filename: 'strategy.py',
  config_filename: 'Strategy.json',
  supported_match_modes: ['Bar'],
};

function buildManifest(overrides: Partial<StrategyManifest> = {}): StrategyManifest {
  return { ...TOP_LEVEL_ONLY, params: [], ...overrides };
}

function parseManifestOrThrow(manifest: StrategyManifest): StrategyManifest {
  const result = parseStrategyManifest(JSON.stringify(manifest));

  if (!result.ok) {
    throw new Error(`测试数据本身就无法解析: ${result.message}`);
  }

  return result.manifest;
}

describe('parseStrategyManifest', () => {
  it('坏 JSON 与缺顶层字段各说各的', () => {
    expect(parseStrategyManifest('{ 不是 JSON')).toEqual({
      ok: false,
      message: MANIFEST_NOT_JSON_MESSAGE,
    });

    expect(
      parseStrategyManifest(
        JSON.stringify({ entry_filename: 'strategy.py', config_filename: 'Strategy.json' }),
      ),
    ).toEqual({ ok: false, message: MANIFEST_SHAPE_MESSAGE });
  });

  it('supported_match_modes 为空数组也算缺少顶层字段', () => {
    expect(
      parseStrategyManifest(JSON.stringify(buildManifest({ supported_match_modes: [] }))),
    ).toEqual({ ok: false, message: MANIFEST_SHAPE_MESSAGE });
  });

  it('参数项读不动时整份 manifest 判为读不动, 不跳过坏参数', () => {
    // 刻意绕过类型直接拼 JSON: 这一段测的正是「库里存了一份坏数据」这条路径.
    expect(
      parseStrategyManifest(
        JSON.stringify({ ...TOP_LEVEL_ONLY, params: [{ key: 'period', type: '整数' }] }),
      ),
    ).toEqual({ ok: false, message: MANIFEST_PARAMETERS_MESSAGE });

    expect(
      parseStrategyManifest(
        JSON.stringify({ ...TOP_LEVEL_ONLY, params: [{ key: '  ', type: 'integer' }] }),
      ),
    ).toEqual({ ok: false, message: MANIFEST_PARAMETERS_MESSAGE });

    expect(
      parseStrategyManifest(
        JSON.stringify({ ...TOP_LEVEL_ONLY, params: [{ key: 'period', type: 'integer', options: 3 }] }),
      ),
    ).toEqual({ ok: false, message: MANIFEST_PARAMETERS_MESSAGE });
  });

  it('只声明 key 的参数是合法的: 后端给 label / type / options 都写了默认值', () => {
    const result = parseStrategyManifest(
      JSON.stringify({ ...TOP_LEVEL_ONLY, params: [{ key: 'period', default: 5 }] }),
    );

    if (!result.ok) {
      throw new Error(`预期 manifest 能解析, 实际: ${result.message}`);
    }

    expect(deriveParameterDescriptors(result.manifest)).toEqual([
      {
        key: 'period',
        label: 'period',
        type: 'string',
        isRequired: false,
        defaultValue: 5,
        options: [],
        minimum: null,
        maximum: null,
        group: '',
      },
    ]);
  });

  it('params 缺省是合法的 (策略可以一个参数都没有)', () => {
    const manifest = buildManifest();
    delete manifest.params;

    const result = parseStrategyManifest(JSON.stringify(manifest));

    if (!result.ok) {
      throw new Error(`预期 manifest 能解析, 实际: ${result.message}`);
    }

    expect(deriveParameterDescriptors(result.manifest)).toEqual([]);
  });
});

describe('deriveParameterDescriptors', () => {
  it('label 为空串时回落成 key', () => {
    const manifest = parseManifestOrThrow(
      buildManifest({
        params: [
          { key: 'period', label: '  ', type: 'integer', default: 5 },
          { key: 'symbol', label: '标的', type: 'string' },
        ],
      }),
    );

    expect(deriveParameterDescriptors(manifest).map((descriptor) => descriptor.label)).toEqual([
      'period',
      '标的',
    ]);
  });

  it('没有 default 就是必填; default 为 null 同样算没有', () => {
    const manifest = parseManifestOrThrow(
      buildManifest({
        params: [
          { key: 'has_default', type: 'string', default: 'x' },
          { key: 'null_default', type: 'string', default: null },
          { key: 'no_default', type: 'string' },
          { key: 'zero_default', type: 'integer', default: 0 },
          { key: 'false_default', type: 'boolean', default: false },
        ],
      }),
    );

    expect(
      deriveParameterDescriptors(manifest).map((descriptor) => descriptor.isRequired),
    ).toEqual([false, true, true, false, false]);
  });

  it('minimum / maximum 只认有限数, 其余按没写处理', () => {
    const manifest = parseManifestOrThrow(
      buildManifest({
        params: [
          { key: 'bounded', type: 'number', default: 1, minimum: 0, maximum: 10 },
          { key: 'unbounded', type: 'number', default: 1, minimum: null, maximum: null },
        ],
      }),
    );

    expect(
      deriveParameterDescriptors(manifest).map((descriptor) => [descriptor.minimum, descriptor.maximum]),
    ).toEqual([
      [0, 10],
      [null, null],
    ]);
  });

  it('group 分区按声明次序出现, 空串即不分组', () => {
    const manifest = parseManifestOrThrow(
      buildManifest({
        params: [
          { key: 'b', type: 'integer', default: 1, group: '风控' },
          { key: 'a', type: 'integer', default: 1 },
          { key: 'c', type: 'integer', default: 1, group: '风控' },
        ],
      }),
    );

    const groups = groupParameterDescriptors(deriveParameterDescriptors(manifest));

    expect(groups.map((group) => group.group)).toEqual(['风控', '']);
    expect(groups.map((group) => group.parameters.map((descriptor) => descriptor.key))).toEqual([
      ['b', 'c'],
      ['a'],
    ]);
  });
});

describe('deriveRunFieldRequirements', () => {
  it('bar_period 恒必填, 另两个只在 manifest 声明了映射时才要', () => {
    expect(deriveRunFieldRequirements(buildManifest())).toEqual({
      barPeriod: true,
      exchangeId: false,
      instrumentId: false,
    });

    expect(
      deriveRunFieldRequirements(
        buildManifest({
          run_field_keys: { exchange_id: 'Exchange', instrument_id: '   ' },
        }),
      ),
    ).toEqual({ barPeriod: true, exchangeId: true, instrumentId: false });
  });
});

describe('createInitialParameterInputs', () => {
  it('按类型给出各自的初始输入', () => {
    const manifest = parseManifestOrThrow(
      buildManifest({
        params: [
          { key: 'count', type: 'integer', default: 3 },
          { key: 'ratio', type: 'number', default: 0.5 },
          { key: 'label', type: 'string', default: '甲' },
          { key: 'flag', type: 'boolean', default: true },
          { key: 'required_text', type: 'string' },
          { key: 'required_flag', type: 'boolean' },
        ],
      }),
    );

    expect(createInitialParameterInputs(deriveParameterDescriptors(manifest))).toEqual({
      count: '3',
      ratio: '0.5',
      label: '甲',
      flag: true,
      required_text: '',
      required_flag: false,
    });
  });

  it('下拉框绑的是**选项下标**, 且没有默认值时不替用户预选', () => {
    const manifest = parseManifestOrThrow(
      buildManifest({
        params: [
          {
            key: 'mode',
            type: 'integer',
            default: 2,
            options: [
              { value: 1, label: '激进' },
              { value: 2, label: '稳健' },
            ],
          },
          {
            key: 'unselected',
            type: 'string',
            options: [
              { value: 'a', label: 'A' },
              { value: 'b', label: 'B' },
            ],
          },
        ],
      }),
    );

    expect(createInitialParameterInputs(deriveParameterDescriptors(manifest))).toEqual({
      mode: '1',
      unselected: '',
    });
  });
});

describe('deriveParameterValues', () => {
  function deriveOne(parameter: ManifestParameter, rawInput: string | boolean) {
    const manifest = parseManifestOrThrow(buildManifest({ params: [parameter] }));

    return deriveParameterValues(deriveParameterDescriptors(manifest), {
      [parameter.key]: rawInput,
    });
  }

  it('integer 收整数, 小数与文本都拒', () => {
    const parameter: ManifestParameter = { key: 'period', type: 'integer', default: 5 };

    expect(deriveOne(parameter, ' 20 ')).toEqual({ ok: true, values: { period: 20 } });
    expect(deriveOne(parameter, '1.5')).toEqual({
      ok: false,
      errors: { period: '需为整数' },
    });
    expect(deriveOne(parameter, '五')).toEqual({
      ok: false,
      errors: { period: '需为整数' },
    });
  });

  it('number 收数值, boolean 只收真假, string 收非空串', () => {
    expect(deriveOne({ key: 'ratio', type: 'number' }, '0.25')).toEqual({
      ok: true,
      values: { ratio: 0.25 },
    });
    expect(deriveOne({ key: 'ratio', type: 'number' }, '四分之一')).toEqual({
      ok: false,
      errors: { ratio: '需为数值' },
    });

    expect(deriveOne({ key: 'flag', type: 'boolean' }, true)).toEqual({
      ok: true,
      values: { flag: true },
    });
    expect(deriveOne({ key: 'flag', type: 'boolean' }, 'true')).toEqual({
      ok: false,
      errors: { flag: '需为 true 或 false' },
    });

    expect(deriveOne({ key: 'label', type: 'string' }, '甲')).toEqual({
      ok: true,
      values: { label: '甲' },
    });
    expect(deriveOne({ key: 'label', type: 'string' }, '')).toEqual({
      ok: false,
      errors: { label: REQUIRED_INPUT_MESSAGE },
    });
  });

  it('范围越界用的是后端那句原话', () => {
    const bounded: ManifestParameter = {
      key: 'ratio',
      type: 'number',
      minimum: 0.1,
      maximum: 0.9,
    };

    expect(deriveOne(bounded, '0.05')).toEqual({
      ok: false,
      errors: { ratio: '不得小于 0.1' },
    });
    expect(deriveOne(bounded, '1')).toEqual({
      ok: false,
      errors: { ratio: '不得大于 0.9' },
    });
  });

  it('下拉框交出的是选项的**原始取值**, 类型不会被下拉框压成字符串', () => {
    const parameter: ManifestParameter = {
      key: 'mode',
      type: 'integer',
      options: [
        { value: 1, label: '激进' },
        { value: true, label: '开关' },
      ],
    };

    expect(deriveOne(parameter, '0')).toEqual({ ok: true, values: { mode: 1 } });
    expect(deriveOne(parameter, '1')).toEqual({ ok: true, values: { mode: true } });
    expect(deriveOne(parameter, '')).toEqual({
      ok: false,
      errors: { mode: REQUIRED_INPUT_MESSAGE },
    });
    expect(deriveOne(parameter, '9')).toEqual({
      ok: false,
      errors: { mode: '请选择一个取值' },
    });
  });

  it('一项不合法就整张表单不通过, 且该项自己的说法在', () => {
    const manifest = parseManifestOrThrow(
      buildManifest({
        params: [
          { key: 'period', type: 'integer', default: 5 },
          { key: 'ratio', type: 'number', default: 0.5 },
        ],
      }),
    );

    const derivation = deriveParameterValues(deriveParameterDescriptors(manifest), {
      period: '五',
      ratio: '0.5',
    });

    expect(derivation).toEqual({ ok: false, errors: { period: '需为整数' } });
  });
});
