<script setup lang="ts">
/**
 * 新建回测.
 *
 * 页面结构跟着 manifest 走: 选策略 → 选版本 (缺省最新) → 用该版本的 manifest 生成参数控件, 并
 * 决定 `exchange_id` / `instrument_id` / `bar_period` 显示不显示. 平台**按 manifest 渲染策略配置,
 * 不读策略自带的配置文件**, 所以这里不能有任何写死的参数控件.
 *
 * 表单里**没有行情模式选择**: 提交侧当前只收 Bar (`run_submission.MATCH_MODE_NOT_SUBMITTABLE_MESSAGE`),
 * 给了 Tick 也只是让用户点一个必然被拒的选项.
 */

import { computed, onMounted, ref, watch } from 'vue';
import { RouterLink, useRouter } from 'vue-router';

import { ApiError } from '../api/client';
import { submitRun } from '../api/runs';
import { fetchStrategyDetail } from '../api/strategies';
import { SUBMITTABLE_MATCH_MODE } from '../api/types';
import type { MarketDataType, RunSubmitPayload, StrategyDetail } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import ParameterForm from '../components/ParameterForm.vue';
import { parseStrategyManifest, createInitialParameterInputs, deriveParameterDescriptors, deriveParameterValues, deriveRunFieldRequirements } from '../domain/manifest';
import type { ParameterInput, RunFieldRequirements } from '../domain/manifest';
import { validateRunForm } from '../domain/run-form';
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

const barPeriod = ref('');
const exchangeId = ref('');
const instrumentId = ref('');
const startTradingDay = ref('');
const endTradingDay = ref('');
const initialCapitalText = ref('100000');
const parameterInputs = ref<Record<string, ParameterInput>>({});

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
    strategyId: selectedStrategyId.value,
    strategyVersionId: selectedVersionId.value,
    matchMode: MATCH_MODE,
    isMatchModeSupported: isMatchModeSupported.value,
    runFieldRequirements: runFieldRequirements.value,
    barPeriod: barPeriod.value,
    exchangeId: exchangeId.value,
    instrumentId: instrumentId.value,
    startTradingDay: startTradingDay.value,
    endTradingDay: endTradingDay.value,
    initialCapitalText: initialCapitalText.value,
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
      strategyId: selectedStrategyId.value,
      strategyVersionId: selectedVersionId.value,
      matchMode: MATCH_MODE,
      isMatchModeSupported: isMatchModeSupported.value,
      runFieldRequirements: runFieldRequirements.value,
      barPeriod: barPeriod.value,
      exchangeId: exchangeId.value,
      instrumentId: instrumentId.value,
      startTradingDay: startTradingDay.value,
      endTradingDay: endTradingDay.value,
      initialCapitalText: initialCapitalText.value,
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

// 换版本 (或换策略) 就把参数控件重置成该版本的默认值: 沿用上一个版本的输入, 会把只在旧版本里
// 存在的取值带进来, 而后端对未声明的参数是直接 400.
watch(descriptors, (nextDescriptors) => {
  parameterInputs.value = createInitialParameterInputs(nextDescriptors);
});

async function loadStrategyDetail(strategyId: string): Promise<void> {
  strategyDetail.value = null;
  selectedVersionId.value = '';
  loadErrorMessage.value = null;

  if (!strategyId) {
    return;
  }

  isLoadingVersions.value = true;

  try {
    const detail = await fetchStrategyDetail(strategyId);

    strategyDetail.value = detail;
    // 版本号倒序, 故第一个就是最新版本 —— 这也是「缺省最新」的含义.
    selectedVersionId.value = detail.versions[0]?.id ?? '';
  } catch (error) {
    loadErrorMessage.value =
      error instanceof ApiError ? error.detail : '加载策略版本失败';
  } finally {
    isLoadingVersions.value = false;
  }
}

function handleStrategyChange(): void {
  hasAttemptedSubmit.value = false;
  void loadStrategyDetail(selectedStrategyId.value);
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
              v-model="startTradingDay"
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
              v-model="endTradingDay"
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
              v-model="initialCapitalText"
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
              v-model="barPeriod"
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
              v-model="exchangeId"
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
              v-model="instrumentId"
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
