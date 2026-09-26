/**
 * 新建回测表单的校验与请求体组装.
 *
 * 与 `app/services/run_submission.py` **同一套语义**, 报错文案也照抄. 界面放行而后端回 400 是
 * 本项目最忌讳的「点了有反应但没用」, 故这里逐条对齐:
 *   - `bar_period` 恒必填 (引擎一定要它, 与 manifest 有没有映射无关);
 *   - `exchange_id` / `instrument_id` **只在 manifest 声明了映射时**才收: 引擎不认识它们,
 *     没映射而提交, 后端回的是「该策略未映射 {field}」;
 *   - 交易日是 8 位数字串, 且开始不得晚于结束 (等长数字串的字符串比较即为数值比较);
 *   - 初始资金须为大于 0 的有限数值.
 *
 * 纯函数, 故能直接单测: 视图只负责把校验结果画出来.
 */

import type {
  LastSubmittedParameters,
  MarketDataType,
  RunSubmitPayload,
} from '../api/types';
import type { RunFieldRequirements } from './manifest';

/** 与 `run_submission.MAXIMUM_RUN_FIELD_VALUE_LENGTH` 一致. */
export const MAXIMUM_RUN_FIELD_VALUE_LENGTH = 64;

const TRADING_DAY_PATTERN = /^\d{8}$/;

const INITIAL_CAPITAL_TEXT = '100000';

/** 表单里六个运行级控件的输入, 键名与 `RunFormInput` 的同名字段一致, 便于整体展开. */
export interface RunFieldInputs {
  barPeriod: string;
  exchangeId: string;
  instrumentId: string;
  startTradingDay: string;
  endTradingDay: string;
  initialCapitalText: string;
}

/** 表单刚打开时的运行级输入. */
export const EMPTY_RUN_FIELDS: RunFieldInputs = {
  barPeriod: '',
  exchangeId: '',
  instrumentId: '',
  startTradingDay: '',
  endTradingDay: '',
  initialCapitalText: INITIAL_CAPITAL_TEXT,
};

export interface RunFormInput extends RunFieldInputs {
  strategyId: string;
  strategyVersionId: string;
  matchMode: MarketDataType;
  /** 选中的版本是否支持该行情模式 (`manifest.supported_match_modes`). */
  isMatchModeSupported: boolean;
  runFieldRequirements: RunFieldRequirements;
  /** 已由 `deriveParameterValues` 收好类型的策略参数, 这里不再复验. */
  parameterValues: Record<string, unknown>;
}

export type RunFormValidation =
  | { ok: true; payload: RunSubmitPayload }
  | { ok: false; errors: Record<string, string> };

/**
 * 用上一次提交的运行级字段填表: 逐字段"能用就用, 不能用就原样留着".
 *
 * 判据复用 `validateRunForm` 的那三个读取器 (一次性 `errors` 对象用完即丢), 故界面上的运行级
 * 规则**只有一处**: 记忆里的取值若在本表单上会被判错, 那它就不该被填进来.
 *
 * 回落到 `current` 而不是空串, 是为了不静默抹掉用户已经敲进去的东西——记忆是锦上添花, 每次重新
 * 载入就把用户填好的一半表单清掉, 比不预填更糟. `prefill` 为 `null` (没跑过这个策略) 时结果
 * 恒等于 `current`, 与没有这个功能时一模一样.
 *
 * `exchange_id` / `instrument_id` 未声明映射时不预填: 那两个输入框在界面上根本不渲染, 填了只是
 * 死数据 (`validateRunForm` 也会把它丢掉).
 */
export function buildPrefilledRunFields(
  prefill: LastSubmittedParameters | null,
  requirements: RunFieldRequirements,
  current: RunFieldInputs,
): RunFieldInputs {
  return {
    barPeriod: rememberedRunField(
      prefill?.bar_period,
      'bar_period',
      true,
      current.barPeriod,
    ),
    exchangeId: rememberedRunField(
      prefill?.exchange_id,
      'exchange_id',
      requirements.exchangeId,
      current.exchangeId,
    ),
    instrumentId: rememberedRunField(
      prefill?.instrument_id,
      'instrument_id',
      requirements.instrumentId,
      current.instrumentId,
    ),
    startTradingDay: rememberedTradingDay(
      prefill?.start_trading_day,
      'start_trading_day',
      current.startTradingDay,
    ),
    endTradingDay: rememberedTradingDay(
      prefill?.end_trading_day,
      'end_trading_day',
      current.endTradingDay,
    ),
    initialCapitalText: rememberedInitialCapitalText(
      prefill?.initial_capital,
      current.initialCapitalText,
    ),
  };
}

function rememberedRunField(
  rememberedValue: string | null | undefined,
  fieldName: string,
  isRequired: boolean,
  fallbackValue: string,
): string {
  const errors: Record<string, string> = {};
  const acceptedValue = readRunFieldValue(
    rememberedValue ?? '',
    fieldName,
    errors,
    isRequired,
  );

  return acceptedValue ?? fallbackValue;
}

function rememberedTradingDay(
  rememberedValue: string | null | undefined,
  fieldName: string,
  fallbackValue: string,
): string {
  const errors: Record<string, string> = {};

  return (
    readTradingDay(rememberedValue ?? '', fieldName, errors) ?? fallbackValue
  );
}

function rememberedInitialCapitalText(
  rememberedValue: number | null | undefined,
  fallbackValue: string,
): string {
  const errors: Record<string, string> = {};
  const rememberedText =
    typeof rememberedValue === 'number' && Number.isFinite(rememberedValue)
      ? String(rememberedValue)
      : '';
  const parsedValue = readInitialCapital(rememberedText, errors);

  return parsedValue === null ? fallbackValue : rememberedText;
}

export function validateRunForm(input: RunFormInput): RunFormValidation {
  const errors: Record<string, string> = {};

  if (!input.strategyId) {
    errors.strategy_id = '请选择策略';
  } else if (!input.isMatchModeSupported) {
    errors.strategy_id = `该策略不支持 ${input.matchMode} 行情模式`;
  }

  const barPeriod = readRunFieldValue(
    input.barPeriod,
    'bar_period',
    errors,
    true,
  );
  const exchangeId = readRunFieldValue(
    input.exchangeId,
    'exchange_id',
    errors,
    input.runFieldRequirements.exchangeId,
  );
  const instrumentId = readRunFieldValue(
    input.instrumentId,
    'instrument_id',
    errors,
    input.runFieldRequirements.instrumentId,
  );

  const startTradingDay = readTradingDay(input.startTradingDay, 'start_trading_day', errors);
  const endTradingDay = readTradingDay(input.endTradingDay, 'end_trading_day', errors);

  if (
    startTradingDay !== null &&
    endTradingDay !== null &&
    startTradingDay > endTradingDay
  ) {
    errors.start_trading_day = '不得晚于结束交易日';
  }

  const initialCapital = readInitialCapital(input.initialCapitalText, errors);

  if (Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }

  return {
    ok: true,
    payload: {
      strategy_id: input.strategyId,
      strategy_version_id: input.strategyVersionId || null,
      match_mode: input.matchMode,
      bar_period: barPeriod ?? '',
      // 未映射时给 null 而不是空串: 两种都不带值, 但 null 明确表达"这个字段不存在于本策略".
      exchange_id: exchangeId,
      instrument_id: instrumentId,
      start_trading_day: startTradingDay ?? '',
      end_trading_day: endTradingDay ?? '',
      initial_capital: initialCapital ?? 0,
      params: input.parameterValues,
    },
  };
}

/**
 * 读一个可选的运行级字段.
 *
 * 空串即「未提供」(后端 `_normalize_run_field_value` 同一条约定). 未映射的字段即便填了也不发送,
 * 否则后端回 400; 有映射而没填则当场报错, 而不是等后端说「{field} 不能为空」.
 */
function readRunFieldValue(
  rawValue: string,
  fieldName: string,
  errors: Record<string, string>,
  isRequired: boolean,
): string | null {
  const normalizedValue = rawValue.trim();

  if (!normalizedValue) {
    if (isRequired) {
      errors[fieldName] = '不能为空';
    }

    return null;
  }

  if (!isRequired) {
    return null;
  }

  if (normalizedValue.length > MAXIMUM_RUN_FIELD_VALUE_LENGTH) {
    errors[fieldName] = `不得超过 ${MAXIMUM_RUN_FIELD_VALUE_LENGTH} 个字符`;

    return null;
  }

  if (containsControlCharacter(normalizedValue)) {
    errors[fieldName] = '不得含控制字符';

    return null;
  }

  return normalizedValue;
}

function readTradingDay(
  rawValue: string,
  fieldName: string,
  errors: Record<string, string>,
): string | null {
  const normalizedValue = rawValue.trim();

  if (!TRADING_DAY_PATTERN.test(normalizedValue)) {
    errors[fieldName] = '须为 8 位数字';

    return null;
  }

  return normalizedValue;
}

function readInitialCapital(
  rawValue: string,
  errors: Record<string, string>,
): number | null {
  const normalizedValue = rawValue.trim();
  const parsedValue = Number(normalizedValue);

  if (!normalizedValue || !Number.isFinite(parsedValue) || parsedValue <= 0) {
    errors.initial_capital = '须为大于 0 的有限数值';

    return null;
  }

  return parsedValue;
}

/** 控制字符会让引擎侧的日志与配置解析出问题, 后端也挡 (`RUN_FIELD_INVALID_CHARACTERS_MESSAGE`). */
function containsControlCharacter(value: string): boolean {
  return /[\u0000-\u001f\u007f]/.test(value);
}
