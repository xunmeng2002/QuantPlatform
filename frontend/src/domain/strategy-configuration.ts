/**
 * 上传的那份策略配置 JSON: 它既是策略自己要读的配置, 也是提交页的参数模板.
 *
 * **键即参数, 值即默认值** —— 平台不认识任何"参数声明": 不看标题、不看范围、不看可选项, 连类型也
 * 不是声明的, 而是按该键**当前取值的 JSON 类型**推出来的 (布尔 → 复选框、数值 → 数字框、其余 →
 * 文本框). 于是策略想加一个参数就是往这份 JSON 里加一个键, 想换控件形态就是改那个值的类型.
 *
 * 三条约定值得写在前面:
 *   - **键集不可增删**: 提交只改值 (`RunSubmitPayload.params` 只带改过的键, 未给的键后端原样留给
 *     模板), 故界面上没有"加一行"这种东西;
 *   - **三个平台键不进参数区**: `ExchangeId` / `InstrumentId` / `BarPreces` 由提交页的固定区覆写
 *     (见 `PLATFORM_CONFIGURATION_KEY_NAMES`), 在参数区再给一次只会让用户以为改得动;
 *   - **数组 / 对象 / `null` 的键原样透传**: JSON 里没有"空的数值输入框", 这类取值也没有自然的控件
 *     形态, 硬造一个只会引入"存得进去、读不出来"的一类失败. 界面把它们**列出来但改不了**, 取值由
 *     模板决定 —— 用户至少知道有这几个键, 而不是提交后发现配置里多了些没见过的字段.
 *
 * 纯函数, 故能直接单测: 视图只负责把结果画出来.
 */

import { PARAMETER_VALUE_TYPES, PLATFORM_CONFIGURATION_KEY_NAMES } from '../api/types';
import type { StrategyConfigurationTemplate } from '../api/types';

/** 与后端 `routers/strategies` 的解析失败同义; 前端先判一次是为了**选完文件就看见**, 不必等一轮请求. */
export const CONFIGURATION_NOT_JSON_MESSAGE = '不是合法的 JSON';
export const CONFIGURATION_SHAPE_MESSAGE = '配置必须是 JSON 对象';

/** 数值键的两条判据文案. */
export const BLANK_NUMBER_MESSAGE = '不能为空';
export const NOT_A_NUMBER_MESSAGE = '须为数值';

export type StrategyConfigurationParseResult =
  | { ok: true; template: StrategyConfigurationTemplate }
  | { ok: false; message: string };

export function parseStrategyConfigurationTemplate(
  configurationText: string,
): StrategyConfigurationParseResult {
  let parsedValue: unknown;

  try {
    parsedValue = JSON.parse(configurationText);
  } catch {
    return { ok: false, message: CONFIGURATION_NOT_JSON_MESSAGE };
  }

  // 顶层必须是对象: 数组与标量解不出"键即参数"这件事, 后端也拒 (`configuration_shape` 那条).
  if (!isJsonObject(parsedValue)) {
    return { ok: false, message: CONFIGURATION_SHAPE_MESSAGE };
  }

  return { ok: true, template: parsedValue };
}

function isJsonObject(value: unknown): value is StrategyConfigurationTemplate {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * 一个参数控件要的全部信息: 键名、控件形态、初值.
 *
 * 判别联合而不是 `{ type: ParameterValueType; defaultValue: boolean | number | string }`: 后者
 * 让"`type` 为 `number` 而 `defaultValue` 是字符串"成为一种可表达的写法, 于是每次读 `defaultValue`
 * 都要么写断言要么写检查.
 */
export type ParameterDescriptor =
  | { key: string; type: 'boolean'; defaultValue: boolean }
  | { key: string; type: 'number'; defaultValue: number }
  | { key: string; type: 'string'; defaultValue: string };

/** 控件回给上层的原始取值: 文本框给字符串 (数值也先当字符串收着), 复选框给布尔量. */
export type ParameterInput = string | boolean;

export type ParameterCoercion =
  | { ok: true; value: boolean | number | string }
  | { ok: false; message: string };

export type ParameterDerivation =
  | { ok: true; values: Record<string, boolean | number | string> }
  | { ok: false; errors: Record<string, string> };

export function deriveParameterDescriptors(
  template: StrategyConfigurationTemplate,
): ParameterDescriptor[] {
  const descriptors: ParameterDescriptor[] = [];

  for (const [parameterKey, value] of Object.entries(template)) {
    if (!isPlatformKey(parameterKey) && isRenderableValue(value)) {
      descriptors.push(describeParameterValue(parameterKey, value));
    }
  }

  return descriptors;
}

/**
 * 模板里那些**渲染不出控件**的键, 供界面如实告知.
 *
 * 与 `deriveParameterDescriptors` 是同一次遍历的两种取向, 故共用同一个判据 (`isRenderableValue`
 * 与 `isPlatformKey`) —— 两边各写一遍的结果是"参数区没渲染它, 提示里也不提它", 用户就只能在提交
 * 后的配置里发现它.
 */
export function findUnrenderableParameterKeys(
  template: StrategyConfigurationTemplate,
): string[] {
  const unrenderableKeys: string[] = [];

  for (const [parameterKey, value] of Object.entries(template)) {
    if (!isPlatformKey(parameterKey) && !isRenderableValue(value)) {
      unrenderableKeys.push(parameterKey);
    }
  }

  return unrenderableKeys;
}

/**
 * 初值: 记忆里的值能用就用, 否则用模板里的值.
 *
 * 只收**类型对得上**的记忆值 —— 上一轮跑的是同一份模板时类型本该一致, 不一致只说明那份记忆来自别的
 * 版本, 而模板才是这份版本自带的真相. 字符串不判空: 空串在新形态下是合法取值 (平台不再要求参数
 * 必填), 把空串当"没记住"会把用户特意清空的默认值又填回去.
 *
 * `rememberedValues` 为 `{}` (没跑过这个策略) 时结果就是模板本身, 与没有预填时一模一样.
 */
export function createInitialParameterInputs(
  descriptors: ParameterDescriptor[],
  rememberedValues: Record<string, unknown> = {},
): Record<string, ParameterInput> {
  const inputs: Record<string, ParameterInput> = {};

  for (const descriptor of descriptors) {
    inputs[descriptor.key] = readAcceptedInput(descriptor, rememberedValues[descriptor.key]);
  }

  return inputs;
}

/**
 * 把一整份原始输入收成可提交的取值; 任何一个键不成型就整份不收, 并把每个键的问题一起回.
 *
 * "整份不收"而不是"能收几个收几个": 部分收下的那半张表单会带着模板值提交, 而用户以为改过的都在
 * 里面 —— 一次说清哪几个框要改, 比提交一轮跑出一个不是他要的结果要好.
 */
export function deriveParameterValues(
  descriptors: ParameterDescriptor[],
  inputs: Record<string, ParameterInput>,
): ParameterDerivation {
  const values: Record<string, boolean | number | string> = {};
  const errors: Record<string, string> = {};

  for (const descriptor of descriptors) {
    const coercion = coerceParameterInput(descriptor, inputs[descriptor.key] ?? '');

    if (!coercion.ok) {
      errors[descriptor.key] = coercion.message;

      continue;
    }

    values[descriptor.key] = coercion.value;
  }

  return Object.keys(errors).length > 0 ? { ok: false, errors } : { ok: true, values };
}

/**
 * 一个原始输入的转换.
 *
 * 布尔量没有失败路径: 复选框只给 `true` / `false`, 而 JSON 里布尔也只有这两个取值 —— 没有"填错"
 * 这回事. 字符串同样没有: 空串是合法取值, 平台不再要求参数必填. **只有数值键会失败**, 且失败的是
 * "JSON 里表达不出来"的那种输入 (空、非数).
 */
export function coerceParameterInput(
  descriptor: ParameterDescriptor,
  input: ParameterInput,
): ParameterCoercion {
  if (descriptor.type === 'boolean') {
    return { ok: true, value: input === true };
  }

  const inputText = typeof input === 'string' ? input : String(input);

  if (descriptor.type === 'string') {
    return { ok: true, value: inputText };
  }

  return coerceNumberInputText(inputText);
}

/**
 * 数值键的判据: 空串判「不能为空」而不是「须为数值」.
 *
 * 两句分开是因为下一步动作不同 (一个是去填, 一个是去改), 而用户面对的是一个**必须填**的框 ——
 * `Number('')` 是 `0` 这件事更是不能顺手信了: 那会把"没填"变成一个静默的 0.
 */
function coerceNumberInputText(inputText: string): ParameterCoercion {
  const trimmedText = inputText.trim();

  if (!trimmedText) {
    return { ok: false, message: BLANK_NUMBER_MESSAGE };
  }

  const parsedValue = Number(trimmedText);

  if (!Number.isFinite(parsedValue)) {
    return { ok: false, message: NOT_A_NUMBER_MESSAGE };
  }

  return { ok: true, value: parsedValue };
}

function readAcceptedInput(
  descriptor: ParameterDescriptor,
  rememberedValue: unknown,
): ParameterInput {
  switch (descriptor.type) {
    case 'boolean':
      return typeof rememberedValue === 'boolean' ? rememberedValue : descriptor.defaultValue;
    case 'number':
      return String(typeof rememberedValue === 'number' ? rememberedValue : descriptor.defaultValue);
    case 'string':
      return typeof rememberedValue === 'string' ? rememberedValue : descriptor.defaultValue;
  }
}

function describeParameterValue(
  parameterKey: string,
  value: boolean | number | string,
): ParameterDescriptor {
  if (typeof value === 'boolean') {
    return { key: parameterKey, type: 'boolean', defaultValue: value };
  }

  if (typeof value === 'number') {
    return { key: parameterKey, type: 'number', defaultValue: value };
  }

  return { key: parameterKey, type: 'string', defaultValue: value };
}

/**
 * 能不能给这个取值渲染一个控件.
 *
 * 判据就是 `typeof` 落不落在 `PARAMETER_VALUE_TYPES` 里 —— `null` 与数组的 `typeof` 都是
 * `'object'`, 故一并挡在门外, 与后端 `strategy_configuration` 的"只认这三种小标量"同一批取值.
 */
function isRenderableValue(value: unknown): value is boolean | number | string {
  return (PARAMETER_VALUE_TYPES as readonly string[]).includes(typeof value);
}

function isPlatformKey(parameterKey: string): boolean {
  return (PLATFORM_CONFIGURATION_KEY_NAMES as readonly string[]).includes(parameterKey);
}
