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
 */

import { computed, onMounted, ref } from 'vue';
import { RouterLink, useRouter } from 'vue-router';

import { ApiError } from '../api/client';
import { submitRun } from '../api/runs';
import { fetchLastSubmittedParameters, fetchStrategyDetail } from '../api/strategies';
import { SUBMITTABLE_MATCH_MODE } from '../api/types';
import type { LastSubmittedParameters, MarketDataType, RunSubmitPayload, StrategyDetail } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import ParameterForm from '../components/ParameterForm.vue';
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

    loadErrorMessage.value =
      error instanceof ApiError ? error.detail : '加载策略版本失败';
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

    await router.push({ name: 'run-detail', params: { id: submittedRun.id } });
  } catch (error) {
    submitErrorMessage.value = error instanceof ApiError ? error.detail : '提交失败';
  } finally {
    isSubmitting.value = false;
  }
}

onMounted(() => {
  void strategyCatalog.ensureLoaded();
});
</script>

<template>
  <section class="max-w-4xl">
    <header class="mb-4 flex items-center gap-3">
      <RouterLink
        class="text-sm text-brand hover:underline"
        :to="{ name: 'runs' }"
      >
        ← 运行列表
      </RouterLink>
      <h1 class="text-lg font-semibold text-slate-900">新建回测</h1>
    </header>

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
      <div
        v-if="appliedPrefill?.run_id"
        class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-slate-500"
      >
        <span>已按你上次提交的参数填充 ({{ formatDateTime(appliedPrefill.submitted_at) }})</span>
        <button
          type="button"
          class="text-brand hover:underline"
          @click="resetToDefaults"
        >
          重置为默认值
        </button>
      </div>

      <fieldset class="space-y-4 rounded-lg border border-line bg-surface p-4">
        <legend class="px-1 text-sm font-semibold text-slate-700">
          策略与版本
        </legend>

        <div class="grid gap-4 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm text-slate-600">
            策略
            <select
              v-model="selectedStrategyId"
              class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
              @change="handleStrategyChange"
            >
              <option value="">
                请选择策略
              </option>
              <option
                v-for="strategy in strategyCatalog.strategies"
                :key="strategy.id"
                :value="strategy.id"
              >
                {{ strategy.name }}
              </option>
            </select>
            <span
              v-if="visibleFieldErrors.strategy_id"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.strategy_id }}</span>
          </label>

          <label class="flex flex-col gap-1 text-sm text-slate-600">
            版本
            <select
              v-model="selectedVersionId"
              class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
              :disabled="isLoadingVersions || strategyDetail === null"
              @change="handleVersionChange"
            >
              <option value="">
                {{ strategyDetail === null ? '请先选择策略' : '没有可用版本' }}
              </option>
              <option
                v-for="version in strategyDetail?.versions ?? []"
                :key="version.id"
                :value="version.id"
              >
                v{{ version.version_no }} · {{ version.entry_filename }}
              </option>
            </select>
          </label>
        </div>

        <p
          v-if="isLoadingVersions"
          class="text-xs text-slate-400"
        >
          加载版本中…
        </p>

        <div
          v-else-if="manifestResult && !manifestResult.ok"
          class="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
        >
          {{ manifestResult.message }} — 该版本无法生成提交表单, 请重新上传该版本.
        </div>

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
      </fieldset>

      <fieldset class="space-y-4 rounded-lg border border-line bg-surface p-4">
        <legend class="px-1 text-sm font-semibold text-slate-700">
          运行范围
        </legend>

        <div class="grid gap-4 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm text-slate-600">
            开始交易日
            <input
              v-model="runFields.startTradingDay"
              type="text"
              inputmode="numeric"
              maxlength="8"
              placeholder="20240102"
              class="rounded border border-line px-2 py-1.5 text-sm"
            >
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
            <input
              v-model="runFields.endTradingDay"
              type="text"
              inputmode="numeric"
              maxlength="8"
              placeholder="20241231"
              class="rounded border border-line px-2 py-1.5 text-sm"
            >
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
            <input
              v-model="runFields.initialCapitalText"
              type="text"
              inputmode="decimal"
              class="rounded border border-line px-2 py-1.5 text-sm"
            >
            <span
              v-if="visibleFieldErrors.initial_capital"
              class="text-xs text-rose-600"
            >{{ visibleFieldErrors.initial_capital }}</span>
          </label>

          <label class="flex flex-col gap-1 text-sm text-slate-600">
            K 线周期 (bar_period)
            <input
              v-model="runFields.barPeriod"
              type="text"
              class="rounded border border-line px-2 py-1.5 text-sm"
            >
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
            <input
              v-model="runFields.exchangeId"
              type="text"
              class="rounded border border-line px-2 py-1.5 text-sm"
            >
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
            <input
              v-model="runFields.instrumentId"
              type="text"
              class="rounded border border-line px-2 py-1.5 text-sm"
            >
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
      </fieldset>

      <fieldset
        v-if="descriptors.length > 0"
        class="space-y-4 rounded-lg border border-line bg-surface p-4"
      >
        <legend class="px-1 text-sm font-semibold text-slate-700">
          策略参数
        </legend>
        <ParameterForm
          v-model="parameterInputs"
          :descriptors="descriptors"
          :errors="visibleFieldErrors"
        />
      </fieldset>

      <p
        v-else-if="manifestResult?.ok"
        class="text-sm text-slate-500"
      >
        该策略的 manifest 没有声明任何参数.
      </p>

      <div class="flex items-center gap-3">
        <button
          type="submit"
          class="rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong disabled:opacity-50"
          :disabled="isSubmitDisabled"
        >
          {{ isSubmitting ? '提交中…' : '提交回测' }}
        </button>
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
