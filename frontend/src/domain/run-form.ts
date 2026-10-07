/**
 * 新建回测表单的校验与请求体组装.
 *
 * 与 `app/services/run_submission.py` **同一套语义**, 报错文案也照抄. 界面放行而后端回 400 是
 * 本项目最忌讳的「点了有反应但没用」, 故这里逐条对齐:
 *   - `bar_period` 是**策略的订阅周期**, 且须在 `SUBSCRIPTION_BAR_PERIODS` 之内 —— 那一格比后端更严:
 *     `run_submission` 只判非空, 而取值能否聚合由引擎在装载期判, 填一个聚合不出来的周期会让整轮白
 *     跑 (`ErrorMarketDataNotExist`), 不该等到跑完才发现;
 *   - `exchange_id` / `instrument_id` 恒必填: 平台按固定键名把它们覆写进策略配置, 没有"这个策略
 *     不需要合约"这回事;
 *   - 交易日是 8 位数字串, 且开始不得晚于结束 (等长数字串的字符串比较即为数值比较);
 *   - 初始资金须为大于 0 的有限数值;
 *   - 手续费组须已选定 —— 它决定这一轮按哪一套费率计费, **不给默认值**, 否则"没选"会被静默当成
 *     "选了某一组"; 这一格是下拉框选出来的, 选项由提交页从只读的选项路由取来.
 *
 * 纯函数, 故能直接单测: 视图只负责把校验结果画出来.
 */

import type {
  MarketDataCoverageQuery,
  MarketDataType,
  RunSubmitPayload,
  RunTemplateCreatePayload,
} from '../api/types';
import { SUBSCRIPTION_BAR_PERIODS } from './market-data';
import type { SubscriptionBarPeriod } from './market-data';

/** 与 `run_submission.MAXIMUM_RUN_FIELD_VALUE_LENGTH` 一致. */
export const MAXIMUM_RUN_FIELD_VALUE_LENGTH = 64;

/** 与 `catalog.schemas.MAXIMUM_RUN_TEMPLATE_NAME_LENGTH` 一致. */
export const MAXIMUM_RUN_TEMPLATE_NAME_LENGTH = 64;

/** 与 `routers/run_templates.BLANK_TEMPLATE_NAME_MESSAGE` 逐字一致. */
export const BLANK_TEMPLATE_NAME_MESSAGE = '模板名不能为空白';
const TRADING_DAY_PATTERN = /^\d{8}$/;

const INITIAL_CAPITAL_TEXT = '100000';

/** 表单里七个运行级控件的输入, 键名与 `RunFormInput` 的同名字段一致, 便于整体展开. */
export interface RunFieldInputs {
  barPeriod: string;
  exchangeId: string;
  instrumentId: string;
  startTradingDay: string;
  endTradingDay: string;
  initialCapitalText: string;
  /**
   * 手续费组号, `null` 即**还没选**.
   *
   * 与 `initialCapitalText` 不同, 这一格不拿文本承载数值: 它的取值只可能从下拉框里选出来 (界面
   * 没有给人手打的地方), 于是没有"半成品"要容忍, 也就不必在"文本 ↔ 数值"之间来回翻译.
   */
  commissionGroupId: number | null;
}

/**
 * 一份"填表用的取值集合": 键名与运行记录对齐 (snake_case), 取值全可空.
 *
 * 与 `RunFieldInputs` **不是一回事**: 那个是七个控件里的原始输入 (camelCase, 初始资金还是字符串),
 * 这个是已经过服务端语义的取值. `api/types` 的 `LastSubmittedParameters` (上次提交的记忆) 与
 * `RunTemplate` (保存过的模板) 都满足它, 且字段口径逐字相同 —— 于是"套用模板"与"按上次提交预填"
 * 在下面两个函数眼里是**同一个动作**, 取数路径不必分支.
 */
export interface RunFormPrefill {
  bar_period: string | null;
  exchange_id: string | null;
  instrument_id: string | null;
  start_trading_day: string | null;
  end_trading_day: string | null;
  initial_capital: number | null;
  /** 可空: 读不出来即**不回填**这一格 (见 `rememberedCommissionGroupId`). */
  commission_group_id: number | null;
  params: Record<string, unknown>;
}

/** 表单刚打开时的运行级输入. */
export const EMPTY_RUN_FIELDS: RunFieldInputs = {
  barPeriod: '',
  exchangeId: '',
  instrumentId: '',
  startTradingDay: '',
  endTradingDay: '',
  initialCapitalText: INITIAL_CAPITAL_TEXT,
  // 空着, **不预选**某号组: 只有"上次提交的参数 / 保存过的模板"里带了组号时才回填. 这正是"必填、
  // 不给默认值"那条决定在界面上的样子 —— 哪怕库里此刻只有一个组.
  commissionGroupId: null,
};

export interface RunFormInput extends RunFieldInputs {
  strategyId: string;
  strategyVersionId: string;
  matchMode: MarketDataType;
  /** 已由 `deriveParameterValues` 收好类型的策略参数, 这里不再复验. */
  parameterValues: Record<string, unknown>;
}

export type RunFormValidation =
  | { ok: true; payload: RunSubmitPayload }
  | { ok: false; errors: Record<string, string> };

/**
 * 用一份取值 (上次提交的记忆, 或保存过的模板) 填运行级字段: 逐字段"能用就用, 不能用就原样留着".
 *
 * 判据复用 `validateRunForm` 的那几个读取器 (一次性 `errors` 对象用完即丢), 故界面上的运行级
 * 规则**只有一处**: 那份取值里的字段若在本表单上会被判错, 那它就不该被填进来.
 *
 * 回落到 `current` 而不是空串, 是为了不静默抹掉用户已经敲进去的东西——预填是锦上添花, 每次重新
 * 载入就把用户填好的一半表单清掉, 比不预填更糟. `prefill` 为 `null` (没跑过这个策略) 时结果
 * 恒等于 `current`, 与没有这个功能时一模一样.
 */
export function buildPrefilledRunFields(
  prefill: RunFormPrefill | null,
  current: RunFieldInputs,
): RunFieldInputs {
  return {
    barPeriod: rememberedBarPeriod(prefill?.bar_period, current.barPeriod),
    exchangeId: rememberedRunField(
      prefill?.exchange_id,
      'exchange_id',
      current.exchangeId,
    ),
    instrumentId: rememberedRunField(
      prefill?.instrument_id,
      'instrument_id',
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
    commissionGroupId: rememberedCommissionGroupId(
      prefill?.commission_group_id,
      current.commissionGroupId,
    ),
  };
}

function rememberedRunField(
  rememberedValue: string | null | undefined,
  fieldName: string,
  fallbackValue: string,
): string {
  const errors: Record<string, string> = {};
  const acceptedValue = readRunFieldValue(rememberedValue ?? '', fieldName, errors);

  return acceptedValue ?? fallbackValue;
}

/**
 * 记忆里的 K 线周期, 与 `rememberedRunField` 同一套「能用就用」, 只是判据换成清单内的那条.
 *
 * 分开写而不是给 `readRunFieldValue` 加参数: 周期的判据与"通用字段"的判据长得不一样 (它有白名单,
 * 没有长度与字符那两条), 挤进一个函数只会让那个函数同时说两件事 —— 而 `1d` 这类旧取值如今会被
 * 判错, 正是它不该被预填进来的那份理由.
 */
function rememberedBarPeriod(
  rememberedValue: string | null | undefined,
  fallbackValue: string,
): string {
  const errors: Record<string, string> = {};

  return readBarPeriod(rememberedValue ?? '', errors) ?? fallbackValue;
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

/**
 * 记忆里的手续费组号, 同一套「能用就用」.
 *
 * 判据与 `validateRunForm` 共用那个读取器 (`rememberedInitialCapitalText` 也是这个路子): 存进
 * 记忆里的组号若是坏的, 它就**不该**被填进这一格 —— 填进去等于把一个不可提交的取值摆成"用户已经
 * 选好了"的样子, 而用户随后看到的提交失败会归因到别处.
 */
function rememberedCommissionGroupId(
  rememberedValue: number | null | undefined,
  fallbackValue: number | null,
): number | null {
  const errors: Record<string, string> = {};

  return readCommissionGroupId(rememberedValue ?? null, errors) ?? fallbackValue;
}

export function validateRunForm(input: RunFormInput): RunFormValidation {
  const errors: Record<string, string> = {};

  if (!input.strategyId) {
    errors.strategy_id = '请选择策略';
  }

  const barPeriod = readBarPeriod(input.barPeriod, errors);
  const exchangeId = readRunFieldValue(input.exchangeId, 'exchange_id', errors);
  const instrumentId = readRunFieldValue(input.instrumentId, 'instrument_id', errors);

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
  const commissionGroupId = readCommissionGroupId(input.commissionGroupId, errors);

  // 多判一次 `null` 是**类型上的必要**, 不是又一道业务闸: 上面那个 `initial_capital ?? 0` 之所以
  // 敢写, 是因为 0 在服务端必被拒 (它不会变成一个"看起来正常"的取值); 而组号的兜底 0 是**合法的
  // 0 号组**, 一旦漏到这里, 就会以"用户选了 0 号组"的样子冻进这一轮并按一套查不到的费率计费.
  if (Object.keys(errors).length > 0 || commissionGroupId === null) {
    return { ok: false, errors };
  }

  return {
    ok: true,
    payload: {
      strategy_id: input.strategyId,
      strategy_version_id: input.strategyVersionId || null,
      match_mode: input.matchMode,
      bar_period: barPeriod ?? '',
      exchange_id: exchangeId,
      instrument_id: instrumentId,
      start_trading_day: startTradingDay ?? '',
      end_trading_day: endTradingDay ?? '',
      initial_capital: initialCapital ?? 0,
      commission_group_id: commissionGroupId,
      params: input.parameterValues,
    },
  };
}

/**
 * 行情预检查询的参数; 四个字段里任何一个还没成型就让整条回 `null` —— 那时问不出有意义的结果, 问了
 * 也只是把"填了一半"变成一句要人去猜的提示.
 *
 * **不带 `bar_period`**: 覆盖判据问的是"本地有没有那段日期的数据", 而落盘只有 5m 一档, 用户的订阅
 * 周期不影响"数据在不在" (后端那条查询参数因此也去掉了). 把周期带上只会让预检看起来依赖它.
 *
 * 判据**复用 `validateRunForm` 的那几个读取器**, 故"预检说本地缺数据"与"提交被拒"用的是同一批
 * 规则, 不会各说各话. 开始晚于结束也一并排除: 那个区间算不出任何期望交易日, 预检只会回一句
 * "缺 0 个交易日"这种自相矛盾的话.
 */
export function buildCoverageQuery(
  input: RunFormInput,
): MarketDataCoverageQuery | null {
  const errors: Record<string, string> = {};
  const exchangeId = readRunFieldValue(input.exchangeId, 'exchange_id', errors);
  const instrumentId = readRunFieldValue(input.instrumentId, 'instrument_id', errors);
  const startTradingDay = readTradingDay(input.startTradingDay, 'start_trading_day', errors);
  const endTradingDay = readTradingDay(input.endTradingDay, 'end_trading_day', errors);

  if (
    exchangeId === null ||
    instrumentId === null ||
    startTradingDay === null ||
    endTradingDay === null ||
    startTradingDay > endTradingDay
  ) {
    return null;
  }

  return {
    exchange_id: exchangeId,
    instrument_id: instrumentId,
    start_trading_day: startTradingDay,
    end_trading_day: endTradingDay,
  };
}

export type TemplateDraftResult =
  | { ok: true; draft: RunTemplateCreatePayload }
  | { ok: false; errors: Record<string, string> };

/**
 * 把当前这份表单连同模板名收成"存模板"的请求体.
 *
 * 取值一律从 `validateRunForm` 的 payload 上抄, **不另走一条派生**: 提交得出去的东西才存得下来,
 * 于是"存得下、套用后提交不了"这种模板在设计上就不存在. 调用方因此必须把 `parameterValues` 喂成
 * `deriveParameterValues` 的产物 (表单原始输入是字符串, 存进去就变成 `"0.01"` 而不是 `0.01`, 而
 * `params` 在库里与 `Runs.ParamsJson` 同形同义, 不能是另一种东西).
 *
 * 模板名与表单内容一起校验、错误一起回: 名字空着而表单也缺字段时, 用户该一次看见全部要改的地方,
 * 而不是改完名字再看见下一批.
 */
export function buildTemplateDraft(
  templateName: string,
  input: RunFormInput,
): TemplateDraftResult {
  const nameErrors = readTemplateNameErrors(templateName);
  const validation = validateRunForm(input);

  if (!validation.ok) {
    return { ok: false, errors: { ...validation.errors, ...nameErrors } };
  }

  if (Object.keys(nameErrors).length > 0) {
    return { ok: false, errors: nameErrors };
  }

  const payload = validation.payload;

  return {
    ok: true,
    draft: {
      name: templateName.trim(),
      match_mode: payload.match_mode,
      bar_period: payload.bar_period,
      exchange_id: payload.exchange_id ?? null,
      instrument_id: payload.instrument_id ?? null,
      start_trading_day: payload.start_trading_day,
      end_trading_day: payload.end_trading_day,
      initial_capital: payload.initial_capital,
      commission_group_id: payload.commission_group_id,
      params: payload.params,
    },
  };
}

/**
 * 模板名的本地判据.
 *
 * 两句分别对齐后端的两处: 「不能为空白」是 `_resolve_template_name` (去空白后为空即 400), 「不得
 * 超过 64 个字符」是 `RunTemplateCreateRequest.name` 的 `Field(max_length=...)` (超了即 422).
 * 注意后者比的是**原串**: pydantic 的 `max_length` 不看空白, 故这里也比原串——比去空白后的串会
 * 让「64 个字符 + 一个尾空格」在界面上放行, 然后在后端吃一个 422.
 */
function readTemplateNameErrors(templateName: string): Record<string, string> {
  if (!templateName.trim()) {
    return { name: BLANK_TEMPLATE_NAME_MESSAGE };
  }

  if (templateName.length > MAXIMUM_RUN_TEMPLATE_NAME_LENGTH) {
    return {
      name: `不得超过 ${MAXIMUM_RUN_TEMPLATE_NAME_LENGTH} 个字符`,
    };
  }

  return {};
}

/**
 * 读订阅周期: 必填, 且须在 `SUBSCRIPTION_BAR_PERIODS` 之内.
 *
 * 「没填」与「填了清单外的值」分开报, 是因为下一步动作不同 (一个是去选一个, 一个是改掉它).
 * 清单本身是"5m 的整数倍且引擎聚合得出来"的那几个 (见 `domain/market-data.ts`), 界面上的下拉框给
 * 的就是这几个值; 这条判据同时护着预填与模板 —— 旧的一份记忆里可能存着 `1d`.
 */
function readBarPeriod(
  rawValue: string,
  errors: Record<string, string>,
): string | null {
  const normalizedValue = rawValue.trim();

  if (!normalizedValue) {
    errors.bar_period = '不能为空';

    return null;
  }

  if (!isSubscriptionBarPeriod(normalizedValue)) {
    errors.bar_period = `只能是 ${SUBSCRIPTION_BAR_PERIODS.join(' / ')}`;

    return null;
  }

  return normalizedValue;
}

function isSubscriptionBarPeriod(value: string): value is SubscriptionBarPeriod {
  return (SUBSCRIPTION_BAR_PERIODS as readonly string[]).includes(value);
}

/**
 * 读一个运行级字段.
 *
 * 空串即「未提供」(后端 `_normalize_run_field_value` 同一条约定), 而这两个字段恒必填, 故当场报错,
 * 而不是等后端说「{field} 不能为空」.
 */
function readRunFieldValue(
  rawValue: string,
  fieldName: string,
  errors: Record<string, string>,
): string | null {
  const normalizedValue = rawValue.trim();

  if (!normalizedValue) {
    errors[fieldName] = '不能为空';

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

/**
 * 读手续费组号: 必填.
 *
 * 报错键用**后端字段名**, 与上面几个运行级字段同口径 (视图按它定位控件). 两句分开报的理由同
 * `readBarPeriod`: "还没选"与"选了个坏的"下一步动作不同.
 *
 * 第二句防的是**运行时数据**: 这一格在界面上只能选出来, 但预填那一路收的是接口回来的取值, 类型
 * 标注管不住它 (`RunTemplate.commission_group_id` 声明成 `number`, 而库里的行可以是任何整数).
 * 判据与后端 `run_configuration.validate_commission_group_id` 一致.
 */
function readCommissionGroupId(
  selectedId: number | null,
  errors: Record<string, string>,
): number | null {
  if (selectedId === null) {
    errors.commission_group_id = '请选择手续费组';

    return null;
  }

  if (!Number.isInteger(selectedId) || selectedId < 0) {
    errors.commission_group_id = '须为不小于 0 的整数';

    return null;
  }

  return selectedId;
}

/** 控制字符会让引擎侧的日志与配置解析出问题, 后端也挡 (`RUN_FIELD_INVALID_CHARACTERS_MESSAGE`). */
function containsControlCharacter(value: string): boolean {
  return /[\u0000-\u001f\u007f]/.test(value);
}
