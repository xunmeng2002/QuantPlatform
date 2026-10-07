<script setup lang="ts">
/**
 * 新建回测.
 *
 * 页面结构跟着**选中版本的配置模板**走: 选策略 → 选版本 (缺省最新) → 用那份 JSON 的键生成参数控件.
 * 平台不读策略自带的配置文件, 而是把它整份复制出来、覆写三个平台键 —— 所以这里不能有任何写死的
 * 参数控件.
 *
 * 选中策略时按"你上次提交的那一份"预填 (参数与运行级字段都填): 每次回到这个页面都要重敲一遍
 * 标的、日期、资金与一整组参数, 是纯粹的重复劳动. 取值由后端从该用户在该策略下最新那一轮运行里
 * 解出来 (`GET /api/strategies/{id}/last-submitted-parameters`), 前端只按当前模板判一判能不能用
 * (见 `domain/strategy-configuration.createInitialParameterInputs`)——那份记忆可能来自旧版本.
 *
 * 「配置模板」是同一件事的**第二份取值来源**: 命名的取值集合存在库里, 随时套用. 两条路在下面
 * `applyFormForCurrentSelection` 里合流 (模板的字段与记忆逐字对齐, 见 `domain/run-form.RunFormPrefill`),
 * 故套用不是另一套填表逻辑, 而只是换一个 `appliedPrefill`. 模板只存"能提交出去的那部分", 且
 * **不存版本**: 它挂在策略上, 换版本后仍该能用.
 *
 * 表单里**没有行情模式选择**: 提交侧当前只收 Bar (`run_submission.MATCH_MODE_NOT_SUBMITTABLE_MESSAGE`),
 * 给了 Tick 也只是让用户点一个必然被拒的选项.
 *
 * **三种周期不是一件事**: 合约下拉里的取值要拿去和行情组件对账 (合约清单来自它的库, 够不够由它
 * 已落地的数据判定), 而 `K 线周期` 是**策略的订阅周期**, 落盘精度恒为 5m —— 选 15m 时引擎在运行时
 * 把 5m 聚合成 15m. 选一个聚合不出来的周期不会静默零成交, 但会让整轮白跑, 故这里只给清单内的值.
 * 选完合约后表单就地做一次覆盖预检, 提前说清"提交后还要先下载"—— 预检只告知, 不挡提交: 真下载发生
 * 在调度器里, 这一页不碰文件系统. 组件不在位时下拉禁用并给出原因, **不退回自由文本**.
 *
 * **手续费组**是这一页第三个与策略无关的取数 (与合约清单并列): 它决定这一轮按哪一套费率计费, 选项
 * 来自一条只要求登录的投影路由, 故提交页不必是管理员页. 一个组都没建时同样禁用 + 给出那句话.
 *
 * 反馈分流: 提交失败是**表单自己的失败** (a 类) —— 参数不合法、标的没填, 那句话的读者正在这张表单上,
 * 所以它就地留在 `submitErrorMessage` 里, 不弹 toast. 成功才弹: 回包之后立刻跳运行详情页, 提示条会
 * 跟着这次跳转一起消失, 而 toast 挂在 body 上, 正好落在"东西真的在跑"的那一页.
 * 版本加载失败是 c 类, 留在 `loadErrorMessage` 的 banner 上; 模板列表加载失败同理, 但只留在模板区
 * 自己的 banner 上 —— 它是取值的第二个来源, 不该把整张表单挡掉.
 */

import { computed, onMounted, ref } from 'vue';
import { ElAlert, ElButton, ElDialog, ElInput, ElOption, ElSelect, ElSelectV2 } from 'element-plus';
import { RouterLink, useRouter } from 'vue-router';

import { fetchMarketDataContracts, fetchMarketDataCoverage } from '../api/market-data';
import { collectAllPages } from '../api/pagination';
import { fetchCommissionGroupOptions } from '../api/reference-data';
import { submitRun } from '../api/runs';
import { createRunTemplate, fetchRunTemplates } from '../api/run-templates';
import { fetchLastSubmittedParameters, fetchStrategyDetail } from '../api/strategies';
import { SUBMITTABLE_MATCH_MODE } from '../api/types';
import type { CommissionGroupOption, LastSubmittedParameters, MarketDataContractList, MarketDataCoverage, MarketDataType, RunSubmitPayload, RunTemplate, StrategyDetail } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import ParameterForm from '../components/ParameterForm.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import { describeApiFailure, showSuccessToast } from '../composables/use-feedback';
import { buildContractCode, buildContractOptions, SUBSCRIPTION_BAR_PERIODS } from '../domain/market-data';
import {
  createInitialParameterInputs,
  deriveParameterDescriptors,
  deriveParameterValues,
  findUnrenderableParameterKeys,
  parseStrategyConfigurationTemplate,
} from '../domain/strategy-configuration';
import type { ParameterInput } from '../domain/strategy-configuration';
import { EMPTY_RUN_FIELDS, buildCoverageQuery, buildPrefilledRunFields, buildTemplateDraft, validateRunForm } from '../domain/run-form';
import type { RunFieldInputs, RunFormInput, RunFormPrefill } from '../domain/run-form';
import { formatDateTime } from '../domain/format';
import { useStrategyCatalogStore } from '../stores/strategy-catalog';

/** 提交侧唯一可用的行情模式, 取自 `api/types` 的契约镜像而不是写死字面量. */
const MATCH_MODE: MarketDataType = SUBMITTABLE_MATCH_MODE;

/** 与 `run_submission.CONFIGURATION_UNREADABLE_MESSAGE` 同一件事, 这里只负责说在提交页上. */
const PRE_CHANGE_VERSION_MESSAGE = '该版本落在改形态之前, 没有配置模板';

/**
 * 一个手续费组都没有时那句原因.
 *
 * 这不是异常状态: 全新安装的组表就是空的 (`reference_seed/CommissionGroup.csv` 只有表头), 组由
 * 管理员在「基础数据」页维护. 空下拉框配一句"还没有组"是**可行动**的; 空下拉框什么都不说, 用户
 * 只会以为页面坏了.
 */
const NO_COMMISSION_GROUP_MESSAGE =
  '还没有手续费组. 回测的费用按组计算, 请先请管理员到「基础数据」页建一个组并录入费率';

const router = useRouter();
const strategyCatalog = useStrategyCatalogStore();

const selectedStrategyId = ref('');
const selectedVersionId = ref('');
const strategyDetail = ref<StrategyDetail | null>(null);
const isLoadingVersions = ref(false);
const loadErrorMessage = ref<string | null>(null);
const submitErrorMessage = ref<string | null>(null);
const isSubmitting = ref(false);
const hasAttemptedSubmit = ref(false);

const runFields = ref<RunFieldInputs>({ ...EMPTY_RUN_FIELDS });
const parameterInputs = ref<Record<string, ParameterInput>>({});

/**
 * 当前这份表单套用的那份取值, 以及它从哪来.
 *
 * 两个来处 (上次提交的记忆 / 保存过的模板) 在下面 `applyFormForCurrentSelection` 眼里**没有区别**
 * ——字段口径本就是同一套 (见 `domain/run-form.RunFormPrefill`), 只有提示条上那句话不同, 故这里
 * 多带一句. `null` 即"什么都没套用", 提示条据此显隐.
 */
interface AppliedPrefill {
  /** 喂给 `createInitialParameterInputs` / `buildPrefilledRunFields` 的取值集合. */
  prefill: RunFormPrefill;
  /** 提示条上那句来源说明. */
  bannerText: string;
}

const appliedPrefill = ref<AppliedPrefill | null>(null);

/** 该策略下本人保存的模板 (最近创建的在前). 取值的第二个来源, 与记忆并列. */
const runTemplates = ref<RunTemplate[]>([]);
const selectedTemplateId = ref('');
/** 模板列表加载失败 (c 类). 只有模板区受影响, 表单照常可用. */
const templateErrorMessage = ref<string | null>(null);
const isLoadingTemplates = ref(false);

/** 「存为模板」的弹窗: 名字与它自己的失败文案 (a 类, 就地显示在弹窗里). */
const isTemplateDialogVisible = ref(false);
const templateNameInput = ref('');
const templateDialogErrorMessage = ref<string | null>(null);
const isSavingTemplate = ref(false);

/**
 * 可选的合约清单. 它是**行情组件**的事实, 与本页选的策略无关, 故与策略详情分两路取: 组件不在位
 * 只该让合约下拉框换成一句原因, 不该把整张表单挡在"加载中".
 */
const marketDataContracts = ref<MarketDataContractList | null>(null);
const isLoadingContracts = ref(false);
/** 取清单这一路自己的失败 (网络 / 鉴权). 组件不在位不走这里, 它是 200 里的一种状态. */
const contractLoadErrorMessage = ref<string | null>(null);

/**
 * 可选的手续费组.
 *
 * 与合约清单同理: 它是**平台侧**的事实, 与本页选的策略无关, 故跟着页面取一次而不是跟着策略. 这一
 * 格**不预选**: 只有"上次提交的参数 / 保存过的模板"里带了组号时才回填 (见 `domain/run-form.ts`),
 * 否则空着等用户选 —— 组号决定这一轮按哪一套费率计费, 替用户认领一个就等于替他选了一套不知道是
 * 谁的费率.
 */
const commissionGroupOptions = ref<CommissionGroupOption[]>([]);
const isLoadingCommissionGroups = ref(false);
const commissionGroupLoadErrorMessage = ref<string | null>(null);

/** 下拉里的文案: 组号在前, 因为计费只认组号, 组名只是给人看的. */
const commissionGroupLabel = (option: CommissionGroupOption): string =>
  `${option.commission_group_id} · ${option.commission_group_name}`;

/** 提交前的行情预检结果. 只告知, 不挡提交. */
const coverageNotice = ref<CoverageNotice | null>(null);

const COVERAGE_NOTICE_CLASSES: Record<CoverageNoticeTone, string> = {
  covered: 'text-xs text-emerald-600',
  missing: 'text-xs text-amber-600',
  unknown: 'text-xs text-slate-400',
};

/**
 * 选择切换的序号, 用来丢弃"迟到的回包".
 *
 * 快速连着换两次策略时, 第一次的请求可能后到, 于是 A 策略的详情 (与它的记忆) 落进 B 策略的表单.
 * 每次发起前自增并记下, 回包时若已不是最新就整份丢掉.
 */
let selectionToken = 0;

const selectedVersion = computed(
  () =>
    strategyDetail.value?.versions.find(
      (version) => version.id === selectedVersionId.value,
    ) ?? null,
);

/**
 * 版本下拉的占位文案.
 *
 * 原生 `<select>` 是用一个 `value=""` 的空选项承担这句话的; 换成 el-select 后那个位置归
 * placeholder —— 而 EP 的 `<el-option value="">` 永远显示不出自己的标签 (空串被判为"未选中").
 */
const versionPlaceholder = computed(() =>
  strategyDetail.value === null ? '请先选择策略' : '没有可用版本',
);

/**
 * 选中版本的配置模板; 读不动时整张表单都生成不出来.
 *
 * 两种读不动分开说: `configuration_json` 为 `null` 是**改形态之前落的版本** (那时存的是 manifest),
 * 它没有模板可解, 后端也会拒 —— 那不是"文件坏了", 而是"这份版本要重传"; 而字符串解不开才是坏行.
 * 两句的下一步动作不同, 故不合成一句.
 */
const configurationResult = computed(() => {
  const version = selectedVersion.value;

  if (version === null) {
    return null;
  }

  return version.configuration_json === null
    ? { ok: false as const, message: PRE_CHANGE_VERSION_MESSAGE }
    : parseStrategyConfigurationTemplate(version.configuration_json);
});

const descriptors = computed(() =>
  configurationResult.value?.ok === true
    ? deriveParameterDescriptors(configurationResult.value.template)
    : [],
);

/** 模板里那些渲染不出控件的键 (数组 / 对象 / `null`): 界面改不了, 但要说出来. */
const unrenderableParameterKeys = computed(() =>
  configurationResult.value?.ok === true
    ? findUnrenderableParameterKeys(configurationResult.value.template)
    : [],
);

const contracts = computed(() => marketDataContracts.value?.contracts ?? []);

/**
 * 合约下拉的选项.
 *
 * 五千多条**全都在这儿**, 但 `el-select-v2` 按可视区虚拟渲染, 落进 DOM 的始终只有那十几行 ——
 * 这正是它换掉 `el-select` + `v-for` 的全部理由 (见 `domain/market-data.buildContractOptions`).
 */
const contractOptions = computed(() => buildContractOptions(contracts.value));

const isContractListAvailable = computed(
  () => marketDataContracts.value?.available === true,
);

/**
 * 下拉框被禁用 / 列表为空时那句原因.
 *
 * 两种来处合成一句: 请求本身失败 (网络、鉴权), 与后端回的"组件不在位". 对用户是同一件事 ——
 * 现在选不了合约, 以及为什么. 空串即"没话要说".
 */
const contractUnavailableReason = computed(
  () =>
    contractLoadErrorMessage.value ??
    (isContractListAvailable.value ? '' : marketDataContracts.value?.reason ?? ''),
);

/**
 * 手续费组下拉被禁用 / 空着时那句原因.
 *
 * 两件事合成一句, 因为对用户都是"现在选不了组, 以及为什么": 请求本身失败 (网络 / 鉴权), 与一个组
 * 都还没建. 加载途中**不出话** —— 那时列表空是暂时的, 说"还没有组"就是在编.
 */
const commissionGroupUnavailableReason = computed(() => {
  if (commissionGroupLoadErrorMessage.value !== null) {
    return commissionGroupLoadErrorMessage.value;
  }

  if (isLoadingCommissionGroups.value || commissionGroupOptions.value.length > 0) {
    return '';
  }

  return NO_COMMISSION_GROUP_MESSAGE;
});

/**
 * 下拉框的选中值: 组件主键 (`sh.600519`).
 *
 * 它是一份**派生视图而不是独立状态**: 预填与模板直接写 `runFields` 里的 `exchangeId` /
 * `instrumentId` 两格, 若这里另存一份, 两条路就会各说各话 (提示条说已套用, 下拉框却空着).
 * 写回时同时落两格, 且拆写用后端**已经拆好**的那一份 —— 前端不解析主键.
 */
const selectedContractCode = computed<string>({
  get: () =>
    buildContractCode(runFields.value.exchangeId, runFields.value.instrumentId) ?? '',
  set: (code) => {
    const chosen = contracts.value.find((contract) => contract.code === code);

    if (chosen === undefined) {
      return;
    }

    runFields.value = {
      ...runFields.value,
      exchangeId: chosen.exchange_id,
      instrumentId: chosen.instrument_id,
    };
  },
});

const parameterDerivation = computed(() =>
  deriveParameterValues(descriptors.value, parameterInputs.value),
);

/**
 * 当前这份表单的输入.
 *
 * 提交、"哪里错了"和"存成模板"读的都是**同一份**: 三处各拼一次的话, 加一个字段就要记得改三个
 * 地方, 而漏改的那一处不会报错, 只会让"按钮能点"与"点了报什么错"对不上. 参数派生失败时给空集
 * ——那时 `submissionPayload` 当场回 `null`, 参数那几条错误另由 `parameterDerivation.errors` 报.
 */
const formInput = computed<RunFormInput>(() => ({
  ...runFields.value,
  strategyId: selectedStrategyId.value,
  strategyVersionId: selectedVersionId.value,
  matchMode: MATCH_MODE,
  parameterValues: parameterDerivation.value.ok ? parameterDerivation.value.values : {},
}));

/** 能与不能提交, 只有这一个判断; 按钮状态与提交动作都读它. */
const submissionPayload = computed<RunSubmitPayload | null>(() => {
  if (!parameterDerivation.value.ok) {
    return null;
  }

  const formValidation = validateRunForm(formInput.value);

  return formValidation.ok ? formValidation.payload : null;
});

/** 提交**尝试过之后**才显示错误, 且随改随消: 一进页面就满屏红字毫无帮助. */
const visibleFieldErrors = computed<Record<string, string>>(() => {
  if (!hasAttemptedSubmit.value) {
    return {};
  }

  const errors: Record<string, string> = {};

  if (submissionPayload.value === null) {
    const formValidation = validateRunForm(formInput.value);

    Object.assign(
      errors,
      formValidation.ok ? {} : formValidation.errors,
      parameterDerivation.value.ok ? {} : parameterDerivation.value.errors,
    );
  }

  return errors;
});

const isSubmitDisabled = computed(
  () => isSubmitting.value || selectedVersion.value === null,
);

/**
 * 按当前选中的版本重算整张表单: 参数控件重置成该版本的默认值 (能被记忆顶替的顶替), 运行级字段
 * 由记忆补上.
 *
 * 换版本时**参数一定会重建**: 沿用上一个版本的输入, 会把只在旧版本里存在的取值带进来, 而后端对
 * 未声明的参数是直接 400. 运行级字段反过来只补不改——它们与版本无关, 而用户刚敲进去的东西不该
 * 因为换了个版本就没了 (`buildPrefilledRunFields` 的回落值是当前输入).
 *
 * 这里**没有 watcher**: 预填要发一次请求, 必然晚于 `descriptors` 变化, 用 watcher 重置就一定会
 * 把填好的记忆清掉. 改成两个 `@change` 加「套用」共三处显式调用, 重置点因此只有这一处.
 */
function applyFormForCurrentSelection(): void {
  const prefill = appliedPrefill.value?.prefill ?? null;

  parameterInputs.value = createInitialParameterInputs(
    descriptors.value,
    prefill?.params ?? {},
  );
  runFields.value = buildPrefilledRunFields(prefill, runFields.value);
  // 套用来的取值可能整好凑齐了预检需要的四格, 那时提示条该立刻跟上, 而不是等用户再去碰一下某个框.
  void refreshCoverageNotice();
}

/** 「重置为默认值」: 丢掉本轮套用的那份取值, 把整张表单恢复成模板里的取值. */
function resetToDefaults(): void {
  appliedPrefill.value = null;
  runFields.value = { ...EMPTY_RUN_FIELDS };
  parameterInputs.value = createInitialParameterInputs(descriptors.value);
  hasAttemptedSubmit.value = false;
  void refreshCoverageNotice();
}

/**
 * 把后端那份"上次提交"收成提示条要的形状.
 *
 * `run_id` 为空即**没有记忆**: 后端那时回的是全空字段加空 `params`, 与"什么都没套用"逐字等价,
 * 故在这里就归一成 `null`, 免得下游还要再判一次.
 */
function buildAppliedPrefill(
  prefill: LastSubmittedParameters | null,
): AppliedPrefill | null {
  if (prefill === null || prefill.run_id === null) {
    return null;
  }

  return {
    prefill,
    bannerText: `已按你上次提交的参数填充 (${formatDateTime(prefill.submitted_at)})`,
  };
}

async function loadStrategyDetail(strategyId: string): Promise<void> {
  const requestToken = ++selectionToken;

  strategyDetail.value = null;
  selectedVersionId.value = '';
  appliedPrefill.value = null;
  loadErrorMessage.value = null;
  runTemplates.value = [];
  selectedTemplateId.value = '';
  templateErrorMessage.value = null;
  isLoadingTemplates.value = false;

  if (!strategyId) {
    return;
  }

  isLoadingVersions.value = true;

  try {
    const [detail, prefill] = await Promise.all([
      fetchStrategyDetail(strategyId),
      readLastSubmittedParameters(strategyId),
    ]);

    if (requestToken !== selectionToken) {
      return;
    }

    strategyDetail.value = detail;
    appliedPrefill.value = buildAppliedPrefill(prefill);
    // 版本号倒序, 故第一个就是最新版本 —— 这也是「缺省最新」的含义.
    selectedVersionId.value = detail.versions[0]?.id ?? '';

    // 顺序有意如此: 这两行读的都是上面刚写下的 state, 故先落 state 再算表单.
    applyFormForCurrentSelection();
    // 模板与上面那两路**分开取**: 它是取值的第二个来源, 失败只该让模板区少一份列表, 不该把整张
    // 表单挡在"加载中".
    void loadRunTemplates(strategyId, requestToken);
  } catch (error) {
    if (requestToken !== selectionToken) {
      return;
    }

    loadErrorMessage.value = describeApiFailure(error, '加载策略版本失败');
  } finally {
    if (requestToken === selectionToken) {
      isLoadingVersions.value = false;
    }
  }
}

/**
 * 该策略下本人保存的模板.
 *
 * 与预填同一口径: 这一路失败不该让整张表单加载不出来. 但**不静默**——「这个策略还没有模板」与
 * 「模板没取到」对用户是两件事, 混成一句"暂无模板"就等于骗人: 前者的下一步是自己存一份, 后者的
 * 下一步是重试.
 */
async function loadRunTemplates(
  strategyId: string,
  requestToken: number,
): Promise<void> {
  isLoadingTemplates.value = true;

  try {
    const templateList = await fetchRunTemplates(strategyId);

    if (requestToken !== selectionToken) {
      return;
    }

    runTemplates.value = templateList.templates;
  } catch (error) {
    if (requestToken !== selectionToken) {
      return;
    }

    runTemplates.value = [];
    templateErrorMessage.value = describeApiFailure(error, '加载配置模板失败');
  } finally {
    if (requestToken === selectionToken) {
      isLoadingTemplates.value = false;
    }
  }
}

/** 模板区的「重新加载」: 复用当前的选择与序号, 免得模板里要去读那个裸的 `selectionToken`. */
function reloadRunTemplates(): void {
  void loadRunTemplates(selectedStrategyId.value, selectionToken);
}

/**
 * 上次提交的参数; 取不到就当没有.
 *
 * 预填是锦上添花: 这一路失败不该让整张表单加载不出来 (策略详情那条路径的失败才是致命的, 故它
 * 照常报错). 无令牌 (401) 之类的失败在这里与"没跑过"落到同一种表现——空表单.
 */
async function readLastSubmittedParameters(
  strategyId: string,
): Promise<LastSubmittedParameters | null> {
  try {
    return await fetchLastSubmittedParameters(strategyId);
  } catch {
    return null;
  }
}

function handleStrategyChange(): void {
  hasAttemptedSubmit.value = false;
  void loadStrategyDetail(selectedStrategyId.value);
}

function handleVersionChange(): void {
  hasAttemptedSubmit.value = false;
  applyFormForCurrentSelection();
}

/** 「套用」: 把选中那份模板填进表单. 与按记忆预填走**同一条路**, 只是换一份取值. */
function applyTemplate(): void {
  const selectedTemplate = runTemplates.value.find(
    (template) => template.id === selectedTemplateId.value,
  );

  if (selectedTemplate === undefined) {
    return;
  }

  appliedPrefill.value = {
    prefill: selectedTemplate,
    bannerText: `已套用模板「${selectedTemplate.name}」`,
  };
  applyFormForCurrentSelection();
  hasAttemptedSubmit.value = false;
}

/**
 * 「存为模板」: 表单现在提交得出去才开弹窗.
 *
 * 提交不出去时**不弹**, 而是把 `hasAttemptedSubmit` 翻起来 —— 那一瞬间表单上会就地出现那几条
 * 红字 (与点「提交回测」走同一条路), 用户看到的是"哪里要改", 而不是"点了没反应"或一句笼统的
 * "表单不完整". 按钮因此**不置灰**: 置灰就没人告诉他为什么点不动.
 */
function openTemplateDialog(): void {
  hasAttemptedSubmit.value = true;

  if (submissionPayload.value === null) {
    return;
  }

  templateNameInput.value = '';
  templateDialogErrorMessage.value = null;
  isTemplateDialogVisible.value = true;
}

/**
 * 弹窗里的「保存」.
 *
 * 请求体的取值一律由 `buildTemplateDraft` 从 `formInput` 上抄 (提交得出去的东西才存得下来), 故这
 * 里不另判一次表单——它若此刻仍不合法, 那是弹窗开着的这段时间里表单被改了, 而弹窗是模态的, 走不到.
 */
async function saveTemplate(): Promise<void> {
  const draftResult = buildTemplateDraft(templateNameInput.value, formInput.value);

  if (!draftResult.ok) {
    // 名字的问题 (空着 / 超长) 就地显示在弹窗里, 不关弹窗: 关掉等于让人从头再填一遍名字.
    templateDialogErrorMessage.value = Object.values(draftResult.errors).join('；');

    return;
  }

  isSavingTemplate.value = true;

  try {
    const createdTemplate = await createRunTemplate(
      selectedStrategyId.value,
      draftResult.draft,
    );

    // 列表按 `created_at DESC` 排, 故新的一份在最前 —— 与后端列表的次序一致, 免得存完还要重取.
    runTemplates.value = [createdTemplate, ...runTemplates.value];
    selectedTemplateId.value = createdTemplate.id;
    isTemplateDialogVisible.value = false;
    showSuccessToast(`已保存模板「${createdTemplate.name}」.`);
  } catch (error) {
    templateDialogErrorMessage.value = describeApiFailure(error, '保存模板失败');
  } finally {
    isSavingTemplate.value = false;
  }
}

async function submit(): Promise<void> {
  hasAttemptedSubmit.value = true;
  submitErrorMessage.value = null;

  const payload = submissionPayload.value;

  if (payload === null) {
    return;
  }

  isSubmitting.value = true;

  try {
    const submittedRun = await submitRun(payload);

    // 先弹再跳: 这一页马上就要被卸载, 就地写的任何东西都看不到.
    showSuccessToast('回测已提交.');
    await router.push({ name: 'run-detail', params: { id: submittedRun.id } });
  } catch (error) {
    submitErrorMessage.value = describeApiFailure(error, '提交失败');
  } finally {
    isSubmitting.value = false;
  }
}

async function loadMarketDataContracts(): Promise<void> {
  isLoadingContracts.value = true;
  contractLoadErrorMessage.value = null;

  try {
    marketDataContracts.value = await fetchMarketDataContracts();
  } catch (error) {
    marketDataContracts.value = null;
    contractLoadErrorMessage.value = describeApiFailure(
      error,
      '无法读取行情合约清单',
    );
  } finally {
    isLoadingContracts.value = false;
  }
}

/**
 * 手续费组选项.
 *
 * 取的是那条**只要求登录**的投影路由 (`api/reference-data.fetchCommissionGroupOptions`): 普通用户
 * 要提交回测就得选组, 而"有哪几个组"不是管理面的东西 (要拦的是**改**它们). 一次收全: 下拉框里没有
 * 翻页这回事.
 *
 * 这一路失败只该让这一格换成一句原因, 不该把整张表单挡掉 (同合约清单那一路).
 */
async function loadCommissionGroupOptions(): Promise<void> {
  isLoadingCommissionGroups.value = true;
  commissionGroupLoadErrorMessage.value = null;

  try {
    commissionGroupOptions.value = await collectAllPages(fetchCommissionGroupOptions);
  } catch (error) {
    commissionGroupOptions.value = [];
    commissionGroupLoadErrorMessage.value = describeApiFailure(
      error,
      '无法读取手续费组',
    );
  } finally {
    isLoadingCommissionGroups.value = false;
  }
}

/** 预检的色调. `unknown` 是"没法判" —— 组件不在位、预检请求本身失败, 都落这一档. */
type CoverageNoticeTone = 'covered' | 'missing' | 'unknown';

interface CoverageNotice {
  tone: CoverageNoticeTone;
  text: string;
}

/**
 * 预检的序号, 用来丢弃"迟到的回包".
 *
 * 与 `selectionToken` 同一个理由: 用户连着改两格时, 前一次的回包可能后到, 于是 A 区间的结论落进
 * B 区间的表单. 每次发起前自增并记下, 回包时若已不是最新就整份丢掉.
 */
let coverageToken = 0;

/**
 * 查一次"本地的行情够不够", 把结论收成表单上那句话.
 *
 * 表单还差字段时 `buildCoverageQuery` 回 `null`, 这时**什么也不显示** —— 提示条只在问得出来的
 * 时候才出现. 这个动作既不挡提交也不改任何字段: 真下载发生在调度器里, 这一句只是让用户提前知道
 * "提交之后还要等一会儿".
 *
 * 显式调用 (四处 `@change` 加套用取值那一处), 不用 watcher: 交易日那种自由输入框每次击键都发一次
 * 请求, 而"填完才问"才是这里想要的时机.
 */
async function refreshCoverageNotice(): Promise<void> {
  const query = buildCoverageQuery(formInput.value);

  if (query === null) {
    coverageToken += 1;
    coverageNotice.value = null;

    return;
  }

  const requestToken = ++coverageToken;

  try {
    const coverage = await fetchMarketDataCoverage(query);

    if (requestToken === coverageToken) {
      coverageNotice.value = describeCoverage(coverage);
    }
  } catch (error) {
    if (requestToken === coverageToken) {
      coverageNotice.value = {
        tone: 'unknown',
        text: describeApiFailure(error, '无法预检本地行情'),
      };
    }
  }
}

function describeCoverage(coverage: MarketDataCoverage): CoverageNotice {
  if (!coverage.available) {
    return { tone: 'unknown', text: `无法预检本地行情: ${coverage.reason}` };
  }

  if (coverage.sufficient) {
    return { tone: 'covered', text: '本地行情已覆盖该区间, 提交后不需要下载' };
  }

  return {
    tone: 'missing',
    text: `本地行情缺 ${coverage.missing_day_count} 个交易日, 提交后需先下载`,
  };
}

onMounted(() => {
  void strategyCatalog.ensureLoaded();
  // 合约清单与手续费组都跟本页的策略选择无关, 故跟着页面走而不是跟着策略走.
  void loadMarketDataContracts();
  void loadCommissionGroupOptions();
});
</script>

<template>
  <section class="mx-auto max-w-4xl">
    <!-- mx-auto 是本批新加的: 内容区从 1152px 放宽到 1280px 之后, 这个自限 896px 的窄栏若仍靠左,
         右边会空出一大块, 看起来像没做完. 页头在这个 section 里, 跟着一起居中.
         写在根元素**里面**是有意的: 根节点前多一个节点会让本组件编译成片段, 路由过渡会白屏. -->

    <PageHeader title="新建回测">
      <template #leading>
        <RouterLink
          class="text-sm text-brand hover:underline"
          :to="{ name: 'runs' }"
        >
          ← 运行列表
        </RouterLink>
      </template>
    </PageHeader>

    <ErrorBanner
      :message="loadErrorMessage"
      retry-label="重新加载版本"
      @retry="loadStrategyDetail(selectedStrategyId)"
    />
    <ErrorBanner
      :message="submitErrorMessage"
      :is-retry-visible="false"
    />

    <EmptyNotice
      v-if="strategyCatalog.hasLoaded && strategyCatalog.strategies.length === 0"
      message="还没有可运行的策略"
      hint="先到「策略」页上传一个策略, 再回来提交回测"
    />

    <form
      v-else
      class="space-y-6"
      @submit.prevent="submit"
    >
      <!-- 外面这个 div 不是多余的: EP 的 `.el-alert{margin:0}` 是无层样式, 会压掉 `space-y-6`
           给它的上边距, 所以纵向间距只能挂在我们自己的包裹层上. -->
      <div v-if="appliedPrefill">
        <!-- closable 必须显式关掉: 关掉只翻组件内部的可见标志, appliedPrefill 还在, 于是
             「提示被关掉了但表单里仍是套用来的值」, 而且「重置为默认值」这个唯一入口也没了. -->
        <ElAlert
          type="info"
          :closable="false"
        >
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{{ appliedPrefill.bannerText }}</span>
            <button
              type="button"
              class="text-brand hover:underline"
              @click="resetToDefaults"
            >
              重置为默认值
            </button>
          </div>
        </ElAlert>
      </div>

      <SurfaceCard
        tag="fieldset"
        class="space-y-4"
      >
        <legend class="px-1 text-sm font-semibold text-slate-700">
          策略与版本
        </legend>

        <div class="grid gap-4 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            策略
            <ElSelect
              v-model="selectedStrategyId"
              placeholder="请选择策略"
              @change="handleStrategyChange"
            >
              <ElOption
                v-for="strategy in strategyCatalog.strategies"
                :key="strategy.id"
                :label="strategy.name"
                :value="strategy.id"
              />
            </ElSelect>
            <span
              v-if="visibleFieldErrors.strategy_id"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.strategy_id }}</span>
          </label>

          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            版本
            <ElSelect
              v-model="selectedVersionId"
              :placeholder="versionPlaceholder"
              :disabled="isLoadingVersions || strategyDetail === null"
              @change="handleVersionChange"
            >
              <ElOption
                v-for="version in strategyDetail?.versions ?? []"
                :key="version.id"
                :label="`v${version.version_no} · ${version.entry_filename}`"
                :value="version.id"
              />
            </ElSelect>
          </label>
        </div>

        <p
          v-if="isLoadingVersions"
          role="status"
          class="text-xs text-slate-400"
        >
          加载版本中…
        </p>

        <!-- closable 显式关掉: 这条提示说的是"这个版本不可用", 关掉它不会让那个版本变得可用 ——
             与上面那条预填提示同一理由 (关闭只翻内部标志, 状态没有变). -->
        <ElAlert
          v-else-if="configurationResult && !configurationResult.ok"
          type="warning"
          :closable="false"
          :title="`${configurationResult.message} — 该版本无法提交, 请重新上传该版本.`"
        />

        <!-- 三个运行级取值由平台**覆写**进策略配置, 故在这里明说它们会落成什么键 —— 用户拿这份
             配置去对策略源码时, 得知道那三个键不是他填的参数, 而是平台写的. -->
        <dl
          v-else-if="configurationResult?.ok"
          class="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2"
        >
          <div class="flex gap-2">
            <dt class="text-slate-500">
              入口文件
            </dt>
            <dd class="text-slate-700">
              {{ selectedVersion?.entry_filename }}
            </dd>
          </div>
          <div class="flex gap-2">
            <dt class="text-slate-500">
              配置文件
            </dt>
            <dd class="text-slate-700">
              {{ selectedVersion?.config_filename }}
            </dd>
          </div>
          <div class="flex gap-2">
            <dt class="text-slate-500">
              平台覆写的键
            </dt>
            <dd class="text-slate-700">
              ExchangeId / InstrumentId / BarPreces
            </dd>
          </div>
          <div class="flex gap-2">
            <dt class="text-slate-500">
              本次行情模式
            </dt>
            <dd class="text-slate-700">
              {{ MATCH_MODE }}
            </dd>
          </div>
        </dl>
      </SurfaceCard>

      <!-- 模板区跟着策略走 (模板挂在策略下), 故选到策略才渲染. 它**不**用 fieldset: 这里没有要
           一起提交的控件, 「套用」与「存为模板」都是即时生效的按钮. -->
      <SurfaceCard
        v-if="strategyDetail !== null"
        class="space-y-3"
      >
        <h2 class="text-sm font-semibold text-slate-700">
          配置模板
        </h2>

        <p class="text-xs text-slate-500">
          模板存的是一份<strong class="font-semibold">取值</strong> (运行级字段与策略参数),
          <strong class="font-semibold">不存版本</strong> —— 换版本后仍可套用, 而当前配置模板里
          没有的那个参数会被忽略.
        </p>

        <ErrorBanner
          :message="templateErrorMessage"
          retry-label="重新加载模板"
          @retry="reloadRunTemplates"
        />

        <div class="flex flex-wrap items-center gap-3">
          <ElSelect
            v-model="selectedTemplateId"
            class="w-64"
            placeholder="选择一份已保存的模板"
            :loading="isLoadingTemplates"
            :disabled="runTemplates.length === 0"
            clearable
          >
            <ElOption
              v-for="template in runTemplates"
              :key="template.id"
              :label="template.name"
              :value="template.id"
            />
          </ElSelect>

          <ElButton
            :disabled="selectedTemplateId === ''"
            @click="applyTemplate"
          >
            套用
          </ElButton>

          <ElButton @click="openTemplateDialog">
            存为模板
          </ElButton>
        </div>

        <p
          v-if="runTemplates.length === 0 && !isLoadingTemplates && templateErrorMessage === null"
          class="text-xs text-slate-400"
        >
          这个策略下还没有保存过模板. 把参数与运行范围填好, 点「存为模板」即可留作下次直接套用.
        </p>
      </SurfaceCard>

      <SurfaceCard
        tag="fieldset"
        class="space-y-4"
      >
        <legend class="px-1 text-sm font-semibold text-slate-700">
          运行范围
        </legend>

        <div class="grid gap-4 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            开始交易日
            <ElInput
              v-model="runFields.startTradingDay"
              type="text"
              inputmode="numeric"
              maxlength="8"
              placeholder="20240102"
              @change="refreshCoverageNotice"
            />
            <span
              v-if="visibleFieldErrors.start_trading_day"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.start_trading_day }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >8 位数字</span>
          </label>

          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            结束交易日
            <ElInput
              v-model="runFields.endTradingDay"
              type="text"
              inputmode="numeric"
              maxlength="8"
              placeholder="20241231"
              @change="refreshCoverageNotice"
            />
            <span
              v-if="visibleFieldErrors.end_trading_day"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.end_trading_day }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >8 位数字</span>
          </label>

          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            初始资金
            <!-- 仍是 type="text": `initialCapitalText` 是"以文本承载的数值", `domain/run-form.ts`
                 按字符串读它. 改成 number 会让浏览器放行 `1e5` 一类并弹原生校验气泡, 与 domain 的
                 判据打架. -->
            <ElInput
              v-model="runFields.initialCapitalText"
              type="text"
              inputmode="decimal"
            />
            <span
              v-if="visibleFieldErrors.initial_capital"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.initial_capital }}</span>
          </label>

          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            手续费组
            <!-- 下拉而不是数字输入: 取值只能是**已登记**的那几组 (后端还要再查一遍"这一组在不在"),
                 手打一个组号等于让用户凭记忆敲一个必然被拒的数. 默认空着**不预选**: 组号决定这一轮
                 按哪一套费率计费, 而"没选"与"选了 1 号组"必须是两件可分辨的事 (见 `domain/run-form.ts`). -->
            <ElSelect
              id="run-commission-group"
              v-model="runFields.commissionGroupId"
              :disabled="commissionGroupOptions.length === 0"
              :loading="isLoadingCommissionGroups"
              placeholder="请选择"
            >
              <ElOption
                v-for="option in commissionGroupOptions"
                :key="option.commission_group_id"
                :label="commissionGroupLabel(option)"
                :value="option.commission_group_id"
              />
            </ElSelect>
            <span
              v-if="visibleFieldErrors.commission_group_id"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.commission_group_id }}</span>
            <!-- 一个组都没有时给出**可行动**的那句话, 而不是留一个空下拉 (同合约下拉的形制). -->
            <span
              v-else-if="commissionGroupUnavailableReason"
              class="text-xs text-rose-600"
            >{{ commissionGroupUnavailableReason }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >决定这一轮按哪一套费率计费, 由管理员在「基础数据」页维护</span>
          </label>

          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            K 线周期 (策略的订阅周期)
            <!-- 下拉而不是自由文本: 落盘只有 5m, 用户选的这个值写进策略配置并由策略声明成订阅目标,
                 引擎在运行时聚合. 填一个聚合不出来的周期不会静默零成交, 但会让整轮在装载期被拒 ——
                 那一轮已经白跑了, 故只给清单内的值. 清单见 `domain/market-data.ts`. -->
            <ElSelect
              v-model="runFields.barPeriod"
              placeholder="请选择"
              @change="refreshCoverageNotice"
            >
              <ElOption
                v-for="barPeriod in SUBSCRIPTION_BAR_PERIODS"
                :key="barPeriod"
                :label="barPeriod"
                :value="barPeriod"
              />
            </ElSelect>
            <span
              v-if="visibleFieldErrors.bar_period"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.bar_period }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >落盘行情恒为 5m, 更长的周期由引擎在运行时聚合 (须是 5m 的整数倍)</span>
          </label>

          <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
            回测合约
            <!-- `ElSelectV2` 而不是 `ElSelect` + `v-for`: 合约五千多条, 而 `el-select` 的下拉内容
                 在挂载期就渲染, 五千多个 `<li>` 会把主线程冻住若干秒 —— 冻的还是进页面后第一次
                 点击那一下. 虚拟滚动只渲染可视区那几行, 也不吃 `ElOption` 子节点, 选项走 `options`. -->
            <ElSelectV2
              v-model="selectedContractCode"
              :options="contractOptions"
              :disabled="!isContractListAvailable"
              :loading="isLoadingContracts"
              filterable
              placeholder="按代码或名称搜索"
              @change="refreshCoverageNotice"
            />
            <span
              v-if="visibleFieldErrors.exchange_id"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.exchange_id }}</span>
            <!-- 组件不在位时**不退回自由文本**: 那会让用户填出一个跑不起来、却看着正常的取值.
                 禁用下拉 + 给出原因, 用户至少知道这一步现在做不了以及为什么. -->
            <span
              v-else-if="contractUnavailableReason"
              class="text-xs text-rose-600"
            >{{ contractUnavailableReason }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >合约清单来自行情组件, 同时决定 ExchangeId 与 InstrumentId</span>
          </label>
        </div>

        <p
          v-if="coverageNotice"
          :class="COVERAGE_NOTICE_CLASSES[coverageNotice.tone]"
        >
          {{ coverageNotice.text }}
        </p>
      </SurfaceCard>

      <SurfaceCard
        v-if="descriptors.length > 0"
        tag="fieldset"
        class="space-y-4"
      >
        <legend class="px-1 text-sm font-semibold text-slate-700">
          策略参数
        </legend>

        <!-- 说在前面: 参数区只是那份配置的一部分键. 用户改不了 ExchangeId 一类的平台键与数组/
             对象键, 若不讲明, 他会以为提交上去的就是界面上这些. -->
        <p class="text-xs text-slate-500">
          这些键来自该版本上传的配置 JSON, <strong class="font-semibold">键集不可增删</strong>,
          这里只能改值.
          <template v-if="unrenderableParameterKeys.length > 0">
            另有 {{ unrenderableParameterKeys.join(' / ') }} 不在这里显示, 提交时原样保留.
          </template>
        </p>

        <ParameterForm
          v-model="parameterInputs"
          :descriptors="descriptors"
          :errors="visibleFieldErrors"
        />
      </SurfaceCard>

      <p
        v-else-if="unrenderableParameterKeys.length > 0"
        class="text-sm text-slate-500"
      >
        该版本的配置里没有可在界面上改的键; {{ unrenderableParameterKeys.join(' / ') }}
        提交时原样保留.
      </p>

      <div class="flex items-center gap-3">
        <!-- 文案里的「提交中…」不交给 EP 的 loading 转圈去表达: 转圈不进可访问名, 屏幕阅读器
             只会念到「提交回测」. 两者一起给. -->
        <ElButton
          type="primary"
          native-type="submit"
          :loading="isSubmitting"
          :disabled="isSubmitDisabled"
        >
          {{ isSubmitting ? '提交中…' : '提交回测' }}
        </ElButton>
        <RouterLink
          class="text-sm text-slate-500 hover:underline"
          :to="{ name: 'runs' }"
        >
          取消
        </RouterLink>
      </div>
    </form>

    <!-- 弹窗放在 form **外面**: EP 的弹窗内容默认 teleport 到 body, 但节点仍算在这个 form 里,
         那样在名字输入框上敲回车会顺着 form 提交一次回测. -->
    <ElDialog
      v-model="isTemplateDialogVisible"
      title="存为模板"
      width="440px"
    >
      <label class="flex flex-col gap-1 text-sm font-medium text-slate-700">
        模板名
        <ElInput
          v-model="templateNameInput"
          maxlength="64"
          placeholder="例如 沪深300 日线 慢速"
          @keyup.enter="saveTemplate"
        />
      </label>

      <p class="mt-2 text-xs text-slate-500">
        会保存当前表单的运行级字段与策略参数, 不含策略版本.
      </p>

      <p
        v-if="templateDialogErrorMessage"
        class="mt-2 text-xs text-rose-600"
      >
        {{ templateDialogErrorMessage }}
      </p>

      <template #footer>
        <ElButton @click="isTemplateDialogVisible = false">
          取消
        </ElButton>
        <!-- 文案里的「保存中…」与提交按钮同一理由: 转圈不进可访问名. -->
        <ElButton
          type="primary"
          :loading="isSavingTemplate"
          @click="saveTemplate"
        >
          {{ isSavingTemplate ? '保存中…' : '保存' }}
        </ElButton>
      </template>
    </ElDialog>
  </section>
</template>
