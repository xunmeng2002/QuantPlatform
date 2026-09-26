/**
 * manifest → 提交表单的派生规则.
 *
 * 平台**按 manifest 渲染策略配置, 不读策略自带的配置文件** (`app/manifest.py` 模块 docstring):
 * 策略读到的每一个键都必须由 manifest 声明, 漏声明的键不会"保持原样", 它会整个消失. 所以
 * 提交表单不能有任何一个写死的控件——这一整个文件就是那条约束的实现.
 *
 * 提交前的校验必须与 `app/manifest.validate_parameter_value` **同一套语义**, 报错文案也照抄:
 * 界面放行而后端回 400, 是本项目最忌讳的「点了有反应但没用」.
 *
 * 本文件全部是纯函数: 不碰 DOM、不发请求, 故能被 vitest 在 node 环境里直接测.
 */

import type {
  ManifestParameter,
  ManifestParameterOption,
  ParameterType,
  StrategyManifest,
} from '../api/types';
import { PARAMETER_TYPES } from '../api/types';

export const MANIFEST_NOT_JSON_MESSAGE = 'manifest 不是合法的 JSON';
export const MANIFEST_SHAPE_MESSAGE =
  'manifest 缺少 entry_filename、config_filename 或 supported_match_modes';
export const MANIFEST_PARAMETERS_MESSAGE =
  'manifest 的参数声明不完整: 每个参数都要有 key, type 只能是 integer / number / string / boolean';

/** 空输入的统一说法. 「没有 default 的参数即必填」以此收尾. */
export const REQUIRED_INPUT_MESSAGE = '必填';

/** 省 `type` 时的取值, 与后端 `StrategyParameter.type` 的默认值一致. */
const DEFAULT_PARAMETER_TYPE: ParameterType = 'string';

/** manifest 恒为 `string` (后端原样透传 `manifest_json`), 这里只做**读取**校验. */
export type ManifestParseResult =
  | { ok: true; manifest: StrategyManifest }
  | { ok: false; message: string };

export function parseStrategyManifest(manifestJson: string): ManifestParseResult {
  let parsedJson: unknown;

  try {
    parsedJson = JSON.parse(manifestJson);
  } catch {
    return { ok: false, message: MANIFEST_NOT_JSON_MESSAGE };
  }

  if (!hasManifestTopLevelShape(parsedJson)) {
    return { ok: false, message: MANIFEST_SHAPE_MESSAGE };
  }

  if (!hasReadableParameters(parsedJson.params ?? [])) {
    return { ok: false, message: MANIFEST_PARAMETERS_MESSAGE };
  }

  return { ok: true, manifest: parsedJson as unknown as StrategyManifest };
}

export interface ParameterDescriptor {
  key: string;
  /** manifest 的 `label` 可为空串, 此时回落成 `key`: 界面上不能出现没有标题的输入框. */
  label: string;
  type: ParameterType;
  /** `default` 缺失即必填 —— manifest 里没有 `required` 标志, 这是等价且不会自相矛盾的表达. */
  isRequired: boolean;
  defaultValue: unknown;
  options: ManifestParameterOption[];
  minimum: number | null;
  maximum: number | null;
  /** 空串即不分组. */
  group: string;
}

export interface ParameterGroup {
  group: string;
  parameters: ParameterDescriptor[];
}

/** 表单里一个参数的原始输入. 契约见 `coerceParameterInput`. */
export type ParameterInput = string | boolean;

export function deriveParameterDescriptors(
  manifest: StrategyManifest,
): ParameterDescriptor[] {
  return (manifest.params ?? []).map(toParameterDescriptor);
}

/** 按 manifest 的声明次序分组, 不重排: 次序是作者的意图 (同一组的参数通常要一起看). */
export function groupParameterDescriptors(
  descriptors: ParameterDescriptor[],
): ParameterGroup[] {
  const groups: ParameterGroup[] = [];

  for (const descriptor of descriptors) {
    const existingGroup = groups.find((group) => group.group === descriptor.group);

    if (existingGroup === undefined) {
      groups.push({ group: descriptor.group, parameters: [descriptor] });
    } else {
      existingGroup.parameters.push(descriptor);
    }
  }

  return groups;
}

export interface RunFieldRequirements {
  /** 引擎一定要 bar_period, 故与 manifest 无关, 恒显示且恒必填. */
  barPeriod: boolean;
  /** 引擎不认识这两个字段, 只有策略用; 未声明映射而显示出来, 提交时后端会回 400. */
  exchangeId: boolean;
  instrumentId: boolean;
}

export function deriveRunFieldRequirements(
  manifest: StrategyManifest,
): RunFieldRequirements {
  const runFieldKeys = manifest.run_field_keys ?? {};

  return {
    barPeriod: true,
    exchangeId: hasDeclaredKeyName(runFieldKeys.exchange_id),
    instrumentId: hasDeclaredKeyName(runFieldKeys.instrument_id),
  };
}

/**
 * 每个参数的初始输入: manifest 的默认值, 能被 `rememberedValues` 顶替的才顶替.
 *
 * `rememberedValues` 是上一次提交时的策略参数 (见 `api/strategies.fetchLastSubmittedParameters`).
 * 一个记忆值要顶替默认值, 必须先过 `coerceParameterInput` —— 判据与用户手填时**同一份**, 故范围
 * 与类型规则不会长出第二处真相. 过不了的 (越界、已不在选项里、类型不符、空串) 一律回落默认值:
 * 那份记忆来自旧版本的声明, 而"某个取值在当前版本里还能不能用"只有当前版本说了算.
 */
export function createInitialParameterInputs(
  descriptors: ParameterDescriptor[],
  rememberedValues: Record<string, unknown> = {},
): Record<string, ParameterInput> {
  const inputs: Record<string, ParameterInput> = {};

  for (const descriptor of descriptors) {
    const candidateInput = candidateInputFor(
      descriptor,
      rememberedValues[descriptor.key],
    );

    inputs[descriptor.key] =
      candidateInput !== null && coerceParameterInput(descriptor, candidateInput).ok
        ? candidateInput
        : initialInputFor(descriptor);
  }

  return inputs;
}

export type ParameterDerivation =
  | { ok: true; values: Record<string, unknown> }
  | { ok: false; errors: Record<string, string> };

/** 把整张表单的原始输入收成提交用的 `params`; 只要有一项不合法就整体不通过. */
export function deriveParameterValues(
  descriptors: ParameterDescriptor[],
  inputs: Record<string, ParameterInput | undefined>,
): ParameterDerivation {
  const values: Record<string, unknown> = {};
  const errors: Record<string, string> = {};

  for (const descriptor of descriptors) {
    const coercion = coerceParameterInput(descriptor, inputs[descriptor.key]);

    if (coercion.ok) {
      values[descriptor.key] = coercion.value;
    } else {
      errors[descriptor.key] = coercion.message;
    }
  }

  return Object.keys(errors).length > 0 ? { ok: false, errors } : { ok: true, values };
}

export type ParameterCoercion =
  | { ok: true; value: unknown }
  | { ok: false; message: string };

/**
 * 把控件给出来的原始输入转成提交用的取值.
 *
 * **原始输入的契约** (控件决定, 校验依赖它):
 *   - `boolean` 参数的控件是复选框, 给的是 `boolean`;
 *   - 有 `options` 的参数控件是下拉框, 给的是**选项下标的字符串** (`''` 表示未选) 而不是
 *     选项取值本身——走下标才能保住取值的类型 (`1` 与 `"1"` 不是同一个取值, 而 `<option>`
 *     的 value 只能是字符串);
 *   - 其余情况给的是用户在输入框里敲的字符串, 原样不 trim (字符串参数的取值可以带空白).
 */
export function coerceParameterInput(
  descriptor: ParameterDescriptor,
  rawInput: ParameterInput | undefined,
): ParameterCoercion {
  if (descriptor.options.length > 0) {
    return readOptionValue(descriptor, rawInput);
  }

  if (descriptor.type === 'boolean') {
    return readBooleanValue(rawInput);
  }

  if (descriptor.type === 'integer') {
    return readIntegerValue(descriptor, rawInput);
  }

  if (descriptor.type === 'number') {
    return readNumberValue(descriptor, rawInput);
  }

  return readStringValue(rawInput);
}

function toParameterDescriptor(parameter: ManifestParameter): ParameterDescriptor {
  return {
    key: parameter.key,
    label: readTrimmedText(parameter.label) || parameter.key,
    // 省 `type` 即后端 `StrategyParameter` 的 `type: string` 默认值, 不是坏数据.
    type: isParameterType(parameter.type) ? parameter.type : DEFAULT_PARAMETER_TYPE,
    isRequired: !hasValue(parameter.default),
    defaultValue: parameter.default,
    options: Array.isArray(parameter.options) ? parameter.options : [],
    minimum: readOptionalNumber(parameter.minimum),
    maximum: readOptionalNumber(parameter.maximum),
    group: readTrimmedText(parameter.group),
  };
}

function initialInputFor(descriptor: ParameterDescriptor): ParameterInput {
  const defaultInput = candidateInputFor(descriptor, descriptor.defaultValue);

  if (defaultInput !== null) {
    return defaultInput;
  }

  // 只有复选框的"未填"是未勾选; 其余控件的"未填"是空串. 没有默认值也不要替用户选第一项:
  // 那会让他提交一个自己没做过的决定.
  return descriptor.type === 'boolean' && descriptor.options.length === 0 ? false : '';
}

/**
 * 一个取值 → 控件认得的原始输入; 该控件根本表达不了这个取值时回 `null`.
 *
 * 默认值与记忆值共用本函数, 故"取值怎么落到控件上"只有这一处: 有 `options` 的参数给出的是
 * **选项下标** (`coerceParameterInput` 的契约), 没有下标就没有这个输入.
 */
function candidateInputFor(
  descriptor: ParameterDescriptor,
  value: unknown,
): ParameterInput | null {
  if (descriptor.options.length > 0) {
    const optionIndex = descriptor.options.findIndex((option) =>
      isSameOptionValue(option.value, value),
    );

    return optionIndex >= 0 ? String(optionIndex) : null;
  }

  if (descriptor.type === 'boolean') {
    return typeof value === 'boolean' ? value : null;
  }

  if (descriptor.type === 'integer' || descriptor.type === 'number') {
    return typeof value === 'number' && Number.isFinite(value)
      ? String(value)
      : null;
  }

  // 空串按"未提供"处理 (同 `readStringValue`), 故它不是候选.
  return typeof value === 'string' && value !== '' ? value : null;
}

function readOptionValue(
  descriptor: ParameterDescriptor,
  rawInput: ParameterInput | undefined,
): ParameterCoercion {
  if (typeof rawInput !== 'string' || !rawInput.trim()) {
    return { ok: false, message: REQUIRED_INPUT_MESSAGE };
  }

  const optionIndex = Number(rawInput.trim());

  if (
    !Number.isInteger(optionIndex) ||
    optionIndex < 0 ||
    optionIndex >= descriptor.options.length
  ) {
    return { ok: false, message: '请选择一个取值' };
  }

  return { ok: true, value: descriptor.options[optionIndex].value };
}

function readBooleanValue(rawInput: ParameterInput | undefined): ParameterCoercion {
  if (typeof rawInput !== 'boolean') {
    return { ok: false, message: '需为 true 或 false' };
  }

  return { ok: true, value: rawInput };
}

function readIntegerValue(
  descriptor: ParameterDescriptor,
  rawInput: ParameterInput | undefined,
): ParameterCoercion {
  const inputText = readTrimmedText(rawInput);

  if (!inputText) {
    return { ok: false, message: REQUIRED_INPUT_MESSAGE };
  }

  const parsedValue = Number(inputText);

  // `Number('')` 是 0, 故空串必须先挡掉; bool 走不到这里 (它是独立的 type).
  if (!Number.isInteger(parsedValue)) {
    return { ok: false, message: '需为整数' };
  }

  return checkNumericBounds(descriptor, parsedValue);
}

function readNumberValue(
  descriptor: ParameterDescriptor,
  rawInput: ParameterInput | undefined,
): ParameterCoercion {
  const inputText = readTrimmedText(rawInput);

  if (!inputText) {
    return { ok: false, message: REQUIRED_INPUT_MESSAGE };
  }

  const parsedValue = Number(inputText);

  if (!Number.isFinite(parsedValue)) {
    return { ok: false, message: '需为数值' };
  }

  return checkNumericBounds(descriptor, parsedValue);
}

function readStringValue(rawInput: ParameterInput | undefined): ParameterCoercion {
  if (typeof rawInput !== 'string') {
    return { ok: false, message: '需为字符串' };
  }

  // 空串按「未提供」处理: 表单上不存在"没填"的状态, 于是空串就是它唯一的表现形式, 而
  // 后端对没有 default 的参数收不到值时回的是「缺少必填参数」. 要让空串合法, 声明 `default: ""`.
  if (!rawInput) {
    return { ok: false, message: REQUIRED_INPUT_MESSAGE };
  }

  return { ok: true, value: rawInput };
}

/** 范围校验的文案与 `manifest.validate_parameter_value` 逐字一致. */
function checkNumericBounds(
  descriptor: ParameterDescriptor,
  parsedValue: number,
): ParameterCoercion {
  if (descriptor.minimum !== null && parsedValue < descriptor.minimum) {
    return { ok: false, message: `不得小于 ${descriptor.minimum}` };
  }

  if (descriptor.maximum !== null && parsedValue > descriptor.maximum) {
    return { ok: false, message: `不得大于 ${descriptor.maximum}` };
  }

  return { ok: true, value: parsedValue };
}

/**
 * 两个取值是否同一.
 *
 * 按「同类型才相等」比, 与后端 `manifest._is_same_option_value` 同理: JS 里 `1` 与 `true`
 * 在 `==` 下也不相等, 但 `1 === true` 为假这一点必须显式写出来, 否则读代码的人会以为
 * `===` 已经够了——它确实够, 这里写明是为了对齐后端那条同名规则.
 */
function isSameOptionValue(value: unknown, optionValue: unknown): boolean {
  if (typeof value !== typeof optionValue) {
    return false;
  }

  return value === optionValue;
}

function hasValue(value: unknown): boolean {
  return value !== undefined && value !== null;
}

function hasDeclaredKeyName(keyName: unknown): boolean {
  return typeof keyName === 'string' && keyName.trim() !== '';
}

function readTrimmedText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readOptionalNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function hasManifestTopLevelShape(parsedJson: unknown): parsedJson is {
  entry_filename: string;
  config_filename: string;
  supported_match_modes: unknown[];
  params?: unknown[];
} {
  if (typeof parsedJson !== 'object' || parsedJson === null) {
    return false;
  }

  const candidate = parsedJson as Record<string, unknown>;

  return (
    readTrimmedText(candidate.entry_filename) !== '' &&
    readTrimmedText(candidate.config_filename) !== '' &&
    Array.isArray(candidate.supported_match_modes) &&
    candidate.supported_match_modes.length > 0 &&
    // `params` 与 `run_field_keys` 一样是可省的: 后端 `StrategyManifest` 给了它 default_factory,
    // 只声明入口与行情模式的 manifest 是一份合法的 manifest.
    (candidate.params === undefined || Array.isArray(candidate.params))
  );
}

/**
 * 参数项是否读得动.
 *
 * 存库前 manifest 已过 pydantic 校验, 能坏到这里说明数据被外部改过——那就不该猜, 直接当成
 * 「读不动」让用户重传. 悄悄跳过一个坏参数更糟: 表单上会少一个控件, 而提交时后端回的是
 * 「缺少必填参数」, 用户对着一个不存在的输入框无从下手.
 *
 * `type` **缺省是合法的** (后端默认 `string`), 只有写了一个后端不认的取值才算坏.
 */
function hasReadableParameters(parameters: unknown[]): boolean {
  return parameters.every((parameter) => {
    if (typeof parameter !== 'object' || parameter === null) {
      return false;
    }

    const candidate = parameter as Record<string, unknown>;

    if (readTrimmedText(candidate.key) === '') {
      return false;
    }

    if (candidate.type !== undefined && !isParameterType(candidate.type)) {
      return false;
    }

    return candidate.options === undefined || Array.isArray(candidate.options);
  });
}

function isParameterType(value: unknown): value is ParameterType {
  return PARAMETER_TYPES.includes(value as ParameterType);
}
