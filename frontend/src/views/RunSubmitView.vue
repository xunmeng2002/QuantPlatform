<script setup lang="ts">
/**
 * 新建回测.
 *
 * 页面结构跟着 manifest 走: 选策略 → 选版本 (缺省最新) → 用该版本的 manifest 生成参数控件, 并
 * 决定 `exchange_id` / `instrument_id` / `bar_period` 显示不显示. 平台**按 manifest 渲染策略配置,
 * 不读策略自带的配置文件**, 所以这里不能有任何写死的参数控件.
 *
 * 选中策略时按"你上次提交的那一份"预填 (参数与运行级字段都填): 每次回到这个页面都要重敲一遍
 * 标的、日期、资金与一整组参数, 是纯粹的重复劳动. 取值由后端从该用户在该策略下最新那一轮运行里
 * 解出来 (`GET /api/strategies/{id}/last-submitted-parameters`), 前端只按当前 manifest 判一判
 * 能不能用 (见 `domain/manifest.createInitialParameterInputs`)——那份记忆可能来自旧版本.
 *
 * 「配置模板」是同一件事的**第二份取值来源**: 命名的取值集合存在库里, 随时套用. 两条路在下面
 * `applyFormForCurrentSelection` 里合流 (模板的字段与记忆逐字对齐, 见 `domain/run-form.RunFormPrefill`),
 * 故套用不是另一套填表逻辑, 而只是换一个 `appliedPrefill`. 模板只存"能提交出去的那部分", 且
 * **不存版本**: 它挂在策略上, 换版本后仍该能用.
 *
 * 表单里**没有行情模式选择**: 提交侧当前只收 Bar (`run_submission.MATCH_MODE_NOT_SUBMITTABLE_MESSAGE`),
 * 给了 Tick 也只是让用户点一个必然被拒的选项.
 *
 * 反馈分流: 提交失败是**表单自己的失败** (a 类) —— 参数不合法、标的没填, 那句话的读者正在这张表单上,
 * 所以它就地留在 `submitErrorMessage` 里, 不弹 toast. 成功才弹: 回包之后立刻跳运行详情页, 提示条会
 * 跟着这次跳转一起消失, 而 toast 挂在 body 上, 正好落在"东西真的在跑"的那一页.
 * 版本加载失败是 c 类, 留在 `loadErrorMessage` 的 banner 上; 模板列表加载失败同理, 但只留在模板区
 * 自己的 banner 上 —— 它是取值的第二个来源, 不该把整张表单挡掉.
 */

import { computed, onMounted, ref } from 'vue';
import { ElAlert, ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus';
import { RouterLink, useRouter } from 'vue-router';

import { submitRun } from '../api/runs';
import { createRunTemplate, fetchRunTemplates } from '../api/run-templates';
import { fetchLastSubmittedParameters, fetchStrategyDetail } from '../api/strategies';
import { SUBMITTABLE_MATCH_MODE } from '../api/types';
import type { LastSubmittedParameters, MarketDataType, RunSubmitPayload, RunTemplate, StrategyDetail } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import ParameterForm from '../components/ParameterForm.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import { describeApiFailure, showSuccessToast } from '../composables/use-feedback';
import { parseStrategyManifest, createInitialParameterInputs, deriveParameterDescriptors, deriveParameterValues, deriveRunFieldRequirements } from '../domain/manifest';
import type { ParameterInput, RunFieldRequirements } from '../domain/manifest';
import { EMPTY_RUN_FIELDS, buildPrefilledRunFields, buildTemplateDraft, validateRunForm } from '../domain/run-form';
import type { RunFieldInputs, RunFormInput, RunFormPrefill } from '../domain/run-form';
import { formatDateTime } from '../domain/format';
import { useStrategyCatalogStore } from '../stores/strategy-catalog';

/** 提交侧唯一可用的行情模式, 取自 `api/types` 的契约镜像而不是写死字面量. */
const MATCH_MODE: MarketDataType = SUBMITTABLE_MATCH_MODE;

const EMPTY_RUN_FIELD_REQUIREMENTS: RunFieldRequirements = {
  barPeriod: true,
  exchangeId: false,
  instrumentId: false,
};

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

/** 版本里的 `manifest_json` 可能读不动 (存量坏行), 那时整张表单都生成不出来. */
const manifestResult = computed(() => {
  const version = selectedVersion.value;

  return version === null ? null : parseStrategyManifest(version.manifest_json);
});

const descriptors = computed(() =>
  manifestResult.value?.ok === true
    ? deriveParameterDescriptors(manifestResult.value.manifest)
    : [],
);

const runFieldRequirements = computed<RunFieldRequirements>(() =>
  manifestResult.value?.ok === true
    ? deriveRunFieldRequirements(manifestResult.value.manifest)
    : EMPTY_RUN_FIELD_REQUIREMENTS,
);

const isMatchModeSupported = computed(
  () =>
    manifestResult.value?.ok === true &&
    manifestResult.value.manifest.supported_match_modes.includes(MATCH_MODE),
);

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
  isMatchModeSupported: isMatchModeSupported.value,
  runFieldRequirements: runFieldRequirements.value,
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
  runFields.value = buildPrefilledRunFields(
    prefill,
    runFieldRequirements.value,
    runFields.value,
  );
}

/** 「重置为默认值」: 丢掉本轮套用的那份取值, 把整张表单恢复成 manifest 默认值. */
function resetToDefaults(): void {
  appliedPrefill.value = null;
  runFields.value = { ...EMPTY_RUN_FIELDS };
  parameterInputs.value = createInitialParameterInputs(descriptors.value);
  hasAttemptedSubmit.value = false;
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

onMounted(() => {
  void strategyCatalog.ensureLoaded();
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
          v-else-if="manifestResult && !manifestResult.ok"
          type="warning"
          :closable="false"
          :title="`${manifestResult.message} — 该版本无法生成提交表单, 请重新上传该版本.`"
        />

        <dl
          v-else-if="manifestResult?.ok"
          class="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2"
        >
          <div class="flex gap-2">
            <dt class="text-slate-500">
              入口文件
            </dt>
            <dd class="text-slate-700">
              {{ manifestResult.manifest.entry_filename }}
            </dd>
          </div>
          <div class="flex gap-2">
            <dt class="text-slate-500">
              配置文件
            </dt>
            <dd class="text-slate-700">
              {{ manifestResult.manifest.config_filename }}
            </dd>
          </div>
          <div class="flex gap-2">
            <dt class="text-slate-500">
              支持行情模式
            </dt>
            <dd class="text-slate-700">
              {{ manifestResult.manifest.supported_match_modes.join(', ') }}
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
          <strong class="font-semibold">不存版本</strong> —— 换版本后仍可套用, 而当前 manifest
          里没有的那个参数会被忽略.
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
            K 线周期 (bar_period)
            <ElInput
              v-model="runFields.barPeriod"
              type="text"
            />
            <span
              v-if="visibleFieldErrors.bar_period"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.bar_period }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >引擎必收该字段, 例如 1d / 5m</span>
          </label>

          <label
            v-if="runFieldRequirements.exchangeId"
            class="flex flex-col gap-1 text-sm font-medium text-slate-700"
          >
            交易所 (exchange_id)
            <ElInput
              v-model="runFields.exchangeId"
              type="text"
            />
            <span
              v-if="visibleFieldErrors.exchange_id"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.exchange_id }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >该策略的 manifest 声明了这个键, 故必填</span>
          </label>

          <label
            v-if="runFieldRequirements.instrumentId"
            class="flex flex-col gap-1 text-sm font-medium text-slate-700"
          >
            合约 (instrument_id)
            <ElInput
              v-model="runFields.instrumentId"
              type="text"
            />
            <span
              v-if="visibleFieldErrors.instrument_id"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.instrument_id }}</span>
            <span
              v-else
              class="text-xs text-slate-400"
            >该策略的 manifest 声明了这个键, 故必填</span>
          </label>
        </div>
      </SurfaceCard>

      <SurfaceCard
        v-if="descriptors.length > 0"
        tag="fieldset"
        class="space-y-4"
      >
        <legend class="px-1 text-sm font-semibold text-slate-700">
          策略参数
        </legend>
        <ParameterForm
          v-model="parameterInputs"
          :descriptors="descriptors"
          :errors="visibleFieldErrors"
        />
      </SurfaceCard>

      <p
        v-else-if="manifestResult?.ok"
        class="text-sm text-slate-500"
      >
        该策略的 manifest 没有声明任何参数.
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
