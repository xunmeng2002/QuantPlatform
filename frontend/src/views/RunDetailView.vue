<script setup lang="ts">
/**
 * 运行详情: 指标、权益曲线与明细表、参数、引擎输出与产物下载, 以及取消.
 *
 * 未结束时每 2 秒刷新一次. 「引擎判定」与「状态」分开显示: 前者是引擎在自己那份 result.json 里
 * 写的结论, 后者是宿主对进程的观察 (退出码、有没有被杀), 两者不一致时正是最该看见的信息.
 *
 * 指标行由 `metricSections` 一次性算好: 这些列是引擎结果文件的镜像, 逐行写死在模板里的话, 加一列
 * 就要改三处对齐.
 *
 * 反馈分流: 两个 `ErrorBanner` 各管各的 —— `errorMessage` 是页面级加载失败, `artifactErrorMessage`
 * 是产物清单加载失败, 两者都带重试、都留在页上. 用户**动作**的得失走 toast (b 类): 取消运行、下载
 * 产物都成败各弹一次 —— 下载成功的 toast 不是多余的, 浏览器自己的下载提示在窗口最底下, 大文件还要
 * 等一会儿才出现, 「点了有没有反应」这句话得在这里回. 特别注意 `artifactErrorMessage` 是**被轮询
 * 驱动**的 (每 2 秒 `loadArtifacts` 清一次), 整条转成 toast 会让一次失败的清单加载每 2 秒弹一次.
 */

import { computed, onMounted, ref, watch } from 'vue';
import { ElAlert, ElButton } from 'element-plus';
import { RouterLink } from 'vue-router';

import { cancelRun as cancelRunRequest, fetchRunArtifacts, fetchRunArtifactBlob, fetchRunDetail } from '../api/runs';
import type { JobArtifact, RunDetail } from '../api/types';
import ArtifactList from '../components/ArtifactList.vue';
import ContentSkeleton from '../components/ContentSkeleton.vue';
import EmptyNotice from '../components/EmptyNotice.vue';
import EquityChartPanel from '../components/EquityChartPanel.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import ResultTablePanel from '../components/ResultTablePanel.vue';
import StatusBadge from '../components/StatusBadge.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import {
  confirmAction,
  describeApiFailure,
  showFailureToast,
  showSuccessToast,
} from '../composables/use-feedback';
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
/** 页面级加载失败 (c 类). 取消失败走 toast, 不再往这里写. */
const errorMessage = ref<string | null>(null);
/** 产物清单加载失败 (c 类). 下载失败走 toast, 不再往这里写. */
const artifactErrorMessage = ref<string | null>(null);
const isLoading = ref(true);
/** 取消期间禁用那个按钮 (弹窗里的忙碌态没有了, 挪到这里). */
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
    errorMessage.value = describeApiFailure(error, '加载运行详情失败');
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
    artifactErrorMessage.value = describeApiFailure(error, '加载产物清单失败');
  }
}

async function refreshRun(): Promise<void> {
  await Promise.all([loadRunDetail(), loadArtifacts()]);
}

async function downloadArtifact(artifact: JobArtifact): Promise<void> {
  // 这里**不碰** `artifactErrorMessage`: 清单加载失败与这一次下载是两件事, 顺手清掉前者等于
  // 把一条还成立的信息抹了.
  downloadingPath.value = artifact.relative_path;

  try {
    const artifactBlob = await fetchRunArtifactBlob(props.id, artifact.relative_path);
    const filename = artifactFilename(artifact.relative_path);

    saveBlobAsFile(artifactBlob, filename);
    showSuccessToast(`已开始下载 ${filename}.`);
  } catch (error) {
    showFailureToast(describeApiFailure(error, '下载失败'));
  } finally {
    downloadingPath.value = null;
  }
}

async function cancelRunWithConfirmation(): Promise<void> {
  const isConfirmed = await confirmAction({
    title: '取消这次运行',
    message: '引擎进程会被终止, 该轮将标记为已中断。已写出的产物会保留。',
    confirmLabel: '取消运行',
    isDangerous: true,
  });

  if (!isConfirmed) {
    return;
  }

  isCancelling.value = true;

  try {
    await cancelRunRequest(props.id);
    // 后端的取消是异步的 (先杀进程再回写状态), 故文案是"已请求"而不是"已取消"; 下面的 refreshRun
    // 会立刻把状态拉回来 —— 那一刻它多半还是 running, 这正是为什么文案不能写死成"已停止".
    showSuccessToast('已请求取消这次运行.');
    await refreshRun();
  } catch (error) {
    showFailureToast(describeApiFailure(error, '取消失败'));
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
    <PageHeader title="运行详情">
      <template #leading>
        <RouterLink
          class="text-sm text-brand hover:underline"
          :to="{ name: 'runs' }"
        >
          ← 运行列表
        </RouterLink>
      </template>

      <template #badges>
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
      </template>

      <template #actions>
        <ElButton
          v-if="run && !isTerminal"
          type="danger"
          plain
          :loading="isCancelling"
          @click="cancelRunWithConfirmation"
        >
          {{ isCancelling ? '取消中…' : '取消运行' }}
        </ElButton>
      </template>
    </PageHeader>

    <ErrorBanner
      :message="errorMessage"
      @retry="refreshRun"
    />

    <ContentSkeleton v-if="isLoading" />

    <template v-else-if="run">
      <!-- 引擎自己写在 result.json 里的错误号与错误文案. 只给结论不给下一步, 故不 closable:
           关掉它不会让这一轮从"引擎报错"变成别的. -->
      <ElAlert
        v-if="run.error_msg"
        class="mb-4"
        type="warning"
        :closable="false"
        :title="`引擎错误 ${run.error_id}: ${run.error_msg}`"
      />

      <SurfaceCard
        v-for="metricSection in metricSections"
        :key="metricSection.title"
        class="mb-6"
        :title="metricSection.title"
      >
        <dl class="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
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
      </SurfaceCard>

      <SurfaceCard
        class="mb-6"
        title="提交的参数与引擎配置"
      >
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
      </SurfaceCard>

      <SurfaceCard
        class="mb-6"
        title="作业目录"
      >
        <dl class="space-y-1 text-xs">
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
      </SurfaceCard>

      <!-- 结果库那两节只在轮结束之后挂载: 引擎写库时读会拿到半份文件, 故后端对未结束的轮回
           409, 这里干脆不请求. 挂载时机由既有的 `watch(isTerminal)` 负责——轮一翻成终态, 组件
           挂载即取数, 不必再等一次 2 秒轮询. -->
      <template v-if="isTerminal">
        <EquityChartPanel
          :run-id="props.id"
          class="mb-6"
        />
        <ResultTablePanel
          :run-id="props.id"
          class="mb-6"
        />
      </template>

      <!-- 这一条不是错误, 是"还没到能看的时候" —— 用 info 而不是 warning, 免得跟上面的引擎错误
           抢同一眼. 它会被下面每 2 秒一次的轮询重渲染, 但没有动画, 不会闪. -->
      <ElAlert
        v-else
        class="mb-6"
        type="info"
        :closable="false"
        title="运行结束后可查看权益曲线与明细表"
      />

      <SurfaceCard
        class="mb-6"
        :title="`产物 (${artifacts.length})`"
      >
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
      </SurfaceCard>

      <SurfaceCard title="输出尾部">
        <div class="grid gap-4 lg:grid-cols-2">
          <div>
            <p class="mb-1 text-xs text-slate-500">
              stdout
            </p>
            <!-- 深底配浅色边框本来就怪: `border-line` 是给浅色卡片用的, 这里换深一档的灰. -->
            <pre class="max-h-80 overflow-auto rounded-lg border border-slate-700 bg-slate-900 p-3 text-xs text-slate-100">{{ run.stdout_tail || '(空)' }}</pre>
          </div>
          <div>
            <p class="mb-1 text-xs text-slate-500">
              stderr
            </p>
            <pre class="max-h-80 overflow-auto rounded-lg border border-slate-700 bg-slate-900 p-3 text-xs text-slate-100">{{ run.stderr_tail || '(空)' }}</pre>
          </div>
        </div>
      </SurfaceCard>
    </template>
  </section>
</template>
