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
 * 表单里**没有行情模式选择**: 提交侧当前只收 Bar (`run_submission.MATCH_MODE_NOT_SUBMITTABLE_MESSAGE`),
 * 给了 Tick 也只是让用户点一个必然被拒的选项.
 *
 * 反馈分流: 提交失败是**表单自己的失败** (a 类) —— 参数不合法、标的没填, 那句话的读者正在这张表单上,
 * 所以它就地留在 `submitErrorMessage` 里, 不弹 toast. 成功才弹: 回包之后立刻跳运行详情页, 提示条会
 * 跟着这次跳转一起消失, 而 toast 挂在 body 上, 正好落在"东西真的在跑"的那一页.
 * 版本加载失败是 c 类, 留在 `loadErrorMessage` 的 banner 上.
 */

import { computed, onMounted, ref } from 'vue';
import { ElAlert, ElButton, ElInput, ElOption, ElSelect } from 'element-plus';
import { RouterLink, useRouter } from 'vue-router';

import { submitRun } from '../api/runs';
import { fetchLastSubmittedParameters, fetchStrategyDetail } from '../api/strategies';
import { SUBMITTABLE_MATCH_MODE } from '../api/types';
import type { LastSubmittedParameters, MarketDataType, RunSubmitPayload, StrategyDetail } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import ParameterForm from '../components/ParameterForm.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import { describeApiFailure, showSuccessToast } from '../composables/use-feedback';
import { parseStrategyManifest, createInitialParameterInputs, deriveParameterDescriptors, deriveParameterValues, deriveRunFieldRequirements } from '../domain/manifest';
import type { ParameterInput, RunFieldRequirements } from '../domain/manifest';
import { EMPTY_RUN_FIELDS, buildPrefilledRunFields, validateRunForm } from '../domain/run-form';
import type { RunFieldInputs } from '../domain/run-form';
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

/** 当前这份表单套用的记忆; `null` 即没有 (提示条据此显隐). */
const appliedPrefill = ref<LastSubmittedParameters | null>(null);

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

/** 能与不能提交, 只有这一个判断; 按钮状态与提交动作都读它. */
const submissionPayload = computed<RunSubmitPayload | null>(() => {
  if (!parameterDerivation.value.ok) {
    return null;
  }

  const formValidation = validateRunForm({
    ...runFields.value,
    strategyId: selectedStrategyId.value,
    strategyVersionId: selectedVersionId.value,
    matchMode: MATCH_MODE,
    isMatchModeSupported: isMatchModeSupported.value,
    runFieldRequirements: runFieldRequirements.value,
    parameterValues: parameterDerivation.value.values,
  });

  return formValidation.ok ? formValidation.payload : null;
});

/** 提交**尝试过之后**才显示错误, 且随改随消: 一进页面就满屏红字毫无帮助. */
const visibleFieldErrors = computed<Record<string, string>>(() => {
  if (!hasAttemptedSubmit.value) {
    return {};
  }

  const errors: Record<string, string> = {};

  if (submissionPayload.value === null) {
    const formValidation = validateRunForm({
      ...runFields.value,
      strategyId: selectedStrategyId.value,
      strategyVersionId: selectedVersionId.value,
      matchMode: MATCH_MODE,
      isMatchModeSupported: isMatchModeSupported.value,
      runFieldRequirements: runFieldRequirements.value,
      parameterValues: parameterDerivation.value.ok
        ? parameterDerivation.value.values
        : {},
    });

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
 * 把填好的记忆清掉. 改成两个 `@change` 各调一次本函数, 重置点因此只有这一处.
 */
function applyFormForCurrentSelection(): void {
  const prefill = appliedPrefill.value;

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

/** 「重置为默认值」: 丢掉本轮套用的那份记忆, 把整张表单恢复成 manifest 默认值. */
function resetToDefaults(): void {
  appliedPrefill.value = null;
  runFields.value = { ...EMPTY_RUN_FIELDS };
  parameterInputs.value = createInitialParameterInputs(descriptors.value);
  hasAttemptedSubmit.value = false;
}

async function loadStrategyDetail(strategyId: string): Promise<void> {
  const requestToken = ++selectionToken;

  strategyDetail.value = null;
  selectedVersionId.value = '';
  appliedPrefill.value = null;
  loadErrorMessage.value = null;

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
    appliedPrefill.value = prefill;
    // 版本号倒序, 故第一个就是最新版本 —— 这也是「缺省最新」的含义.
    selectedVersionId.value = detail.versions[0]?.id ?? '';

    // 顺序有意如此: 这两行读的都是上面刚写下的 state, 故先落 state 再算表单.
    applyFormForCurrentSelection();
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
  <!-- mx-auto 是本批新加的: 内容区从 1152px 放宽到 1280px 之后, 这个自限 896px 的窄栏若仍靠左,
       右边会空出一大块, 看起来像没做完. 页头在这个 section 里, 跟着一起居中. -->
  <section class="mx-auto max-w-4xl">
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
      <div v-if="appliedPrefill?.run_id">
        <!-- closable 必须显式关掉: 关掉只翻组件内部的可见标志, appliedPrefill 还在, 于是
             「提示被关掉了但表单里仍是记忆值」, 而且「重置为默认值」这个唯一入口也没了. -->
        <ElAlert
          type="info"
          :closable="false"
        >
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>已按你上次提交的参数填充 ({{ formatDateTime(appliedPrefill.submitted_at) }})</span>
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
          <label class="flex flex-col gap-1 text-sm text-slate-600">
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

          <label class="flex flex-col gap-1 text-sm text-slate-600">
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

      <SurfaceCard
        tag="fieldset"
        class="space-y-4"
      >
        <legend class="px-1 text-sm font-semibold text-slate-700">
          运行范围
        </legend>

        <div class="grid gap-4 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm text-slate-600">
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

          <label class="flex flex-col gap-1 text-sm text-slate-600">
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

          <label class="flex flex-col gap-1 text-sm text-slate-600">
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

          <label class="flex flex-col gap-1 text-sm text-slate-600">
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
            class="flex flex-col gap-1 text-sm text-slate-600"
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
            class="flex flex-col gap-1 text-sm text-slate-600"
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
  </section>
</template>
