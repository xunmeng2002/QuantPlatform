<script setup lang="ts">
/**
 * 运行详情: 指标、参数、引擎输出与产物下载, 以及取消.
 *
 * 未结束时每 2 秒刷新一次. 「引擎判定」与「状态」分开显示: 前者是引擎在自己那份 result.json 里
 * 写的结论, 后者是宿主对进程的观察 (退出码、有没有被杀), 两者不一致时正是最该看见的信息.
 *
 * 指标行由 `metricSections` 一次性算好: 这些列是引擎结果文件的镜像, 逐行写死在模板里的话, 加一列
 * 就要改三处对齐.
 */

import { computed, onMounted, ref, watch } from 'vue';
import { RouterLink } from 'vue-router';

import { ApiError } from '../api/client';
import { cancelRun as cancelRunRequest, fetchRunArtifacts, fetchRunArtifactBlob, fetchRunDetail } from '../api/runs';
import type { JobArtifact, RunDetail } from '../api/types';
import ArtifactList from '../components/ArtifactList.vue';
import ConfirmDialog from '../components/ConfirmDialog.vue';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import LoadingNotice from '../components/LoadingNotice.vue';
import StatusBadge from '../components/StatusBadge.vue';
import { usePolling } from '../composables/usePolling';
import { artifactFilename, saveBlobAsFile } from '../domain/download';
import {
  formatAmount,
  formatCount,
  formatDateTime,
  formatDuration,
  formatFlag,
  formatJsonText,
  formatTradingDay,
} from '../domain/format';
import { describeEngineVerdict, describeRunStatus, isTerminalRunStatus } from '../domain/run-status';
import { useStrategyCatalogStore } from '../stores/strategy-catalog';

const props = defineProps<{ id: string }>();

const strategyCatalog = useStrategyCatalogStore();

const run = ref<RunDetail | null>(null);
const artifacts = ref<JobArtifact[]>([]);
const errorMessage = ref<string | null>(null);
const artifactErrorMessage = ref<string | null>(null);
const isLoading = ref(true);
const isCancelDialogOpen = ref(false);
const isCancelling = ref(false);
const downloadingPath = ref<string | null>(null);

interface MetricRow {
  label: string;
  value: string;
}

interface MetricSection {
  title: string;
  rows: MetricRow[];
}

const isTerminal = computed(() =>
  run.value === null ? true : isTerminalRunStatus(run.value.status),
);

const metricSections = computed<MetricSection[]>(() => {
  const runDetail = run.value;

  if (runDetail === null) {
    return [];
  }

  return [
    {
      title: '概要',
      rows: [
        { label: '运行 ID', value: runDetail.id },
        { label: '策略', value: strategyCatalog.nameFor(runDetail.strategy_id) },
        { label: '策略版本 ID', value: runDetail.strategy_version_id },
        { label: '行情模式', value: runDetail.market_data_type ?? '—' },
        { label: '提交时间', value: formatDateTime(runDetail.submitted_at) },
        { label: '开始时间', value: formatDateTime(runDetail.started_at) },
        { label: '结束时间', value: formatDateTime(runDetail.finished_at) },
        { label: '耗时', value: formatDuration(runDetail.duration_ms) },
        { label: '宿主退出码', value: formatCount(runDetail.exit_code) },
        { label: '宿主进程号', value: formatCount(runDetail.runner_pid) },
        { label: '执行主机', value: runDetail.hostname || '—' },
      ],
    },
    {
      title: '绩效指标',
      rows: [
        { label: '余额', value: formatAmount(runDetail.balance) },
        { label: '可用资金', value: formatAmount(runDetail.available) },
        { label: '交易笔数', value: formatCount(runDetail.trade_count) },
        { label: '订单笔数', value: formatCount(runDetail.order_count) },
        { label: '总手续费', value: formatAmount(runDetail.total_commission) },
        { label: '总印花税', value: formatAmount(runDetail.total_stamp_tax) },
        { label: '总过户费', value: formatAmount(runDetail.total_transfer_fee) },
      ],
    },
    {
      title: '引擎数据镜像',
      rows: [
        { label: '交易日区间', value: `${formatTradingDay(runDetail.start_trading_day)} ~ ${formatTradingDay(runDetail.end_trading_day)}` },
        { label: '最后交易日', value: formatTradingDay(runDetail.last_trading_day) },
        { label: '结果文件版本', value: formatCount(runDetail.schema_version) },
        { label: '资金账户', value: runDetail.account_id ?? '—' },
        { label: '基础数据已载入', value: formatFlag(runDetail.basic_data_loaded) },
        { label: '资金已初始化', value: formatFlag(runDetail.has_capital) },
        { label: '行情订阅数', value: formatCount(runDetail.md_subscribe_count) },
        { label: 'K 线行情数', value: formatCount(runDetail.bar_market_data_count) },
        { label: '深度行情数', value: formatCount(runDetail.depth_market_data_count) },
        { label: '合约数', value: formatCount(runDetail.instrument_count) },
        { label: '缺失手续费记录数', value: formatCount(runDetail.commission_missing_count) },
        { label: '手续费率为零的键数', value: formatCount(runDetail.commission_zero_rate_key_count) },
        { label: '手数倍数回退合约数', value: formatCount(runDetail.volume_multiple_fallback_product_count) },
        { label: '引擎错误号', value: formatCount(runDetail.error_id) },
      ],
    },
  ];
});

async function loadRunDetail(): Promise<void> {
  errorMessage.value = null;

  try {
    run.value = await fetchRunDetail(props.id);
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.detail : '加载运行详情失败';
  } finally {
    isLoading.value = false;
  }
}

async function loadArtifacts(): Promise<void> {
  artifactErrorMessage.value = null;

  try {
    const artifactList = await fetchRunArtifacts(props.id);
    artifacts.value = artifactList.artifacts;
  } catch (error) {
    // 作业目录可能还没建出来 (排队中) 或已被清掉, 这不是页面级失败: 指标照常显示.
    artifacts.value = [];
    artifactErrorMessage.value =
      error instanceof ApiError ? error.detail : '加载产物清单失败';
  }
}

async function refreshRun(): Promise<void> {
  await Promise.all([loadRunDetail(), loadArtifacts()]);
}

async function downloadArtifact(artifact: JobArtifact): Promise<void> {
  artifactErrorMessage.value = null;
  downloadingPath.value = artifact.relative_path;

  try {
    const artifactBlob = await fetchRunArtifactBlob(props.id, artifact.relative_path);
    saveBlobAsFile(artifactBlob, artifactFilename(artifact.relative_path));
  } catch (error) {
    artifactErrorMessage.value = error instanceof ApiError ? error.detail : '下载失败';
  } finally {
    downloadingPath.value = null;
  }
}

async function confirmCancellation(): Promise<void> {
  isCancelling.value = true;
  artifactErrorMessage.value = null;

  try {
    await cancelRunRequest(props.id);
    isCancelDialogOpen.value = false;
    await refreshRun();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.detail : '取消失败';
    isCancelDialogOpen.value = false;
  } finally {
    isCancelling.value = false;
  }
}

const { isPolling, start: startPolling, stop: stopPolling } = usePolling(refreshRun);

watch(isTerminal, (isFinished) => {
  if (isFinished) {
    stopPolling();
  } else {
    startPolling();
  }
});

onMounted(async () => {
  await strategyCatalog.ensureLoaded();
  await refreshRun();

  // 首次加载完才知道该不该轮询, 故这里再判一次 (watch 只对"变化"生效).
  if (!isTerminal.value) {
    startPolling();
  }
});
</script>

<template>
  <section>
    <header class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap items-center gap-3">
        <RouterLink
          class="text-sm text-brand hover:underline"
          :to="{ name: 'runs' }"
        >
          ← 运行列表
        </RouterLink>
        <h1 class="text-lg font-semibold text-slate-900">运行详情</h1>
        <StatusBadge
          v-if="run"
          v-bind="describeRunStatus(run.status)"
        />
        <StatusBadge
          v-if="run"
          v-bind="describeEngineVerdict(run.is_success)"
        />
        <span
          v-if="isPolling"
          class="text-xs text-slate-400"
        >自动刷新中</span>
      </div>

      <button
        v-if="run && !isTerminal"
        type="button"
        class="rounded border border-rose-300 px-4 py-2 text-sm font-medium text-rose-700 hover:bg-rose-50"
        :disabled="isCancelling"
        @click="isCancelDialogOpen = true"
      >
        取消运行
      </button>
    </header>

    <ErrorBanner
      :message="errorMessage"
      @retry="refreshRun"
    />

    <LoadingNotice v-if="isLoading" />

    <template v-else-if="run">
      <p
        v-if="run.error_msg"
        class="mb-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
      >
        引擎错误 {{ run.error_id }}: {{ run.error_msg }}
      </p>

      <section
        v-for="metricSection in metricSections"
        :key="metricSection.title"
        class="mb-6"
      >
        <h2 class="mb-2 text-sm font-semibold text-slate-700">
          {{ metricSection.title }}
        </h2>
        <dl class="grid gap-x-6 gap-y-2 rounded-lg border border-line bg-surface p-4 sm:grid-cols-2 lg:grid-cols-3">
          <div
            v-for="metricRow in metricSection.rows"
            :key="metricRow.label"
            class="flex justify-between gap-3 border-b border-line pb-1 last:border-b-0"
          >
            <dt class="text-xs text-slate-500">
              {{ metricRow.label }}
            </dt>
            <dd class="text-right text-xs break-all text-slate-800">
              {{ metricRow.value }}
            </dd>
          </div>
        </dl>
      </section>

      <section class="mb-6">
        <h2 class="mb-2 text-sm font-semibold text-slate-700">
          提交的参数与引擎配置
        </h2>
        <div class="grid gap-4 lg:grid-cols-2">
          <div>
            <p class="mb-1 text-xs text-slate-500">
              策略参数 (params)
            </p>
            <pre class="max-h-80 overflow-auto rounded-lg border border-line bg-surface p-3 text-xs text-slate-700">{{ formatJsonText(run.params_json) }}</pre>
          </div>
          <div>
            <p class="mb-1 text-xs text-slate-500">
              引擎配置 (BackTest.json)
            </p>
            <pre class="max-h-80 overflow-auto rounded-lg border border-line bg-surface p-3 text-xs text-slate-700">{{ formatJsonText(run.backtest_config_json) }}</pre>
          </div>
        </div>
      </section>

      <section class="mb-6">
        <h2 class="mb-2 text-sm font-semibold text-slate-700">
          作业目录
        </h2>
        <dl class="space-y-1 rounded-lg border border-line bg-surface p-4 text-xs">
          <div class="flex gap-2">
            <dt class="w-24 shrink-0 text-slate-500">
              作业目录名
            </dt>
            <dd class="break-all text-slate-700">
              {{ run.workspace_path || '—' }}
            </dd>
          </div>
          <div class="flex gap-2">
            <dt class="w-24 shrink-0 text-slate-500">
              结果库
            </dt>
            <dd class="break-all text-slate-700">
              {{ run.db_path || '—' }}
            </dd>
          </div>
          <div class="flex gap-2">
            <dt class="w-24 shrink-0 text-slate-500">
              输出目录
            </dt>
            <dd class="break-all text-slate-700">
              {{ run.dump_path || '—' }}
            </dd>
          </div>
        </dl>
      </section>

      <section class="mb-6">
        <h2 class="mb-2 text-sm font-semibold text-slate-700">
          产物 ({{ artifacts.length }})
        </h2>
        <ErrorBanner
          :message="artifactErrorMessage"
          retry-label="重新加载"
          @retry="loadArtifacts"
        />
        <ArtifactList
          v-if="artifacts.length > 0"
          :artifacts="artifacts"
          :is-downloading="downloadingPath !== null"
          :downloading-path="downloadingPath"
          @download="downloadArtifact"
        />
        <EmptyNotice
          v-else-if="!artifactErrorMessage"
          message="这个运行的作业目录里没有文件"
          hint="排队中的轮还没建出目录; 也请确认该轮至少走到了引擎启动那一步"
        />
      </section>

      <section>
        <h2 class="mb-2 text-sm font-semibold text-slate-700">
          输出尾部
        </h2>
        <div class="grid gap-4 lg:grid-cols-2">
          <div>
            <p class="mb-1 text-xs text-slate-500">
              stdout
            </p>
            <pre class="max-h-80 overflow-auto rounded-lg border border-line bg-slate-900 p-3 text-xs text-slate-100">{{ run.stdout_tail || '(空)' }}</pre>
          </div>
          <div>
            <p class="mb-1 text-xs text-slate-500">
              stderr
            </p>
            <pre class="max-h-80 overflow-auto rounded-lg border border-line bg-slate-900 p-3 text-xs text-slate-100">{{ run.stderr_tail || '(空)' }}</pre>
          </div>
        </div>
      </section>
    </template>

    <ConfirmDialog
      :is-open="isCancelDialogOpen"
      title="取消这次运行"
      message="引擎进程会被终止, 该轮将标记为已中断。已写出的产物会保留。"
      confirm-label="取消运行"
      is-dangerous
      :is-busy="isCancelling"
      @confirm="confirmCancellation"
      @cancel="isCancelDialogOpen = false"
    />
  </section>
</template>
