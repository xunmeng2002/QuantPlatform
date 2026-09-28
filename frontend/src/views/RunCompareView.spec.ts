// @vitest-environment jsdom
/**
 * 多轮对比页.
 *
 * 这一页的状态**只有一处**: 地址栏上的 `?ids=`. 勾选框不过是它的另一种写法, 故这里的用例都从推送
 * 一条地址开始, 而不是先去点框 —— 点框测的是"点了以后地址对不对", 而"地址对了页面画得对不对"才是
 * 这一页的本职.
 *
 * `EquityOverlayChart` 打桩: 它内部是 ECharts, 在 jsdom 里要 `ResizeObserver` 与 canvas, 而这里
 * 要钉的四件事 (地址解析、选满上限、逐列降级、整请求 404) 全在页头、选择器与表这三层. 曲线那份
 * 造型另有 `domain/equity.spec.ts` 直接测纯函数, 不靠这一页间接覆盖.
 *
 * 表格与图**同一份造型**由 `domain/run-comparison.spec.ts` 钉住; 这里只钉它在页面上画出来了.
 */

import { flushPromises, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { MAXIMUM_COMPARISON_RUNS, fetchRunComparison, fetchRuns } from '../api/runs';
import { fetchStrategies } from '../api/strategies';
import { MAXIMUM_PAGE_SIZE } from '../api/types';
import type { RunComparison, RunComparisonEntry, RunSummary, User } from '../api/types';
import { router } from '../router';
import { useSessionStore } from '../stores/session';
import RunCompareView from './RunCompareView.vue';

vi.mock('../api/runs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/runs')>()),
  fetchRunComparison: vi.fn(),
  fetchRuns: vi.fn(),
}));
vi.mock('../api/strategies', () => ({ fetchStrategies: vi.fn() }));

const fetchRunComparisonMock = vi.mocked(fetchRunComparison);
const fetchRunsMock = vi.mocked(fetchRuns);
const fetchStrategiesMock = vi.mocked(fetchStrategies);

const TEST_TOKEN = 'test-access-token';
const STRATEGY_ID = 'strategy-1';
const STRATEGY_NAME = '网格策略';

const REGULAR_USER: User = {
  id: '22222222-2222-2222-2222-222222222222',
  username: 'trader',
  display_name: '李四',
  user_type: 'user',
  status: 'active',
  created_at: '2026-09-26T00:00:00',
};

const pinia = createPinia();
setActivePinia(pinia);

function buildSummary(runId: string, overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: runId,
    user_id: 'user-1',
    strategy_id: STRATEGY_ID,
    strategy_version_id: 'version-1',
    status: 'succeeded',
    submitted_at: '2026-09-27T02:03:04',
    started_at: '2026-09-27T02:03:10',
    finished_at: '2026-09-27T02:04:20',
    duration_ms: 70_000,
    exit_code: 0,
    is_success: true,
    market_data_type: 'Bar',
    start_trading_day: '20240102',
    end_trading_day: '20241231',
    trade_count: 84,
    order_count: 92,
    balance: 100_000,
    available: 90_000,
    total_commission: 1_234.5,
    params_json: '{}',
    error_id: null,
    error_msg: null,
    ...overrides,
  };
}

function buildEntry(
  runId: string,
  overrides: Partial<RunSummary> = {},
): RunComparisonEntry {
  return {
    summary: buildSummary(runId, overrides),
    equity_points: [
      { trading_day: '20240102', balance: 100_000, available: 90_000 },
    ],
    equity_unavailable_reason: null,
  };
}

function buildComparison(runIds: string[]): RunComparison {
  return { runs: runIds.map((runId) => buildEntry(runId)) };
}

/** 只取一页的响应: `offset` / `limit` 也是契约的一部分, 与 `total` 一起补全. */
function buildRunPage(records: RunSummary[]) {
  return { records, total: records.length, offset: 0, limit: MAXIMUM_PAGE_SIZE };
}

/** 「重新打开一个页面」: 存储与会话都清空, 且 store 跨用例存活, 这一步不能省. */
function resetSession(): void {
  window.localStorage.clear();
  useSessionStore().clear();
}

/**
 * 打开对比页.
 *
 * 身份**直接赋给 store** 而不是走登录: 守卫看到"有令牌 + 身份已就绪"就直接放行, 于是这个文件不
 * 必为它准备任何一个接口桩 (判据见 `router/index.ts` 的 `hidesHeader` 那一段).
 */
async function openComparePage(
  query: Record<string, string> = {},
): Promise<VueWrapper> {
  const session = useSessionStore();

  session.accessToken = TEST_TOKEN;
  session.currentUser = REGULAR_USER;

  // 先热身到另一个地址: push 到**当前**地址时 vue-router 直接返回 `NAVIGATION_DUPLICATED`,
  // 守卫不跑, 路由也不变, 于是"初值解析"这一类用例会假绿.
  await router.replace('/__reset');
  await router.push({ name: 'compare', query });
  await router.isReady();

  const wrapper = mount(RunCompareView, {
    global: {
      plugins: [pinia, router],
      stubs: { EquityOverlayChart: true },
    },
  });

  // 取数是 `onMounted` 里起的, 不 await 它: 挂载返回时回包还没到, 此刻断言只会看到"加载中".
  await flushPromises();

  return wrapper;
}

/** 勾选框的 DOM 真相: 选中与否、可不可点, 都不看组件内部的 props. */
function readCheckboxStates(wrapper: VueWrapper) {
  return wrapper.findAll('input[type="checkbox"]').map((checkbox) => ({
    isChecked: (checkbox.element as HTMLInputElement).checked,
    isDisabled: checkbox.attributes('disabled') !== undefined,
  }));
}

beforeEach(() => {
  // jsdom 没实现 window.scrollTo (路由的 scrollBehavior 会调它), 打桩只为不让它刷一行 stderr.
  vi.stubGlobal('scrollTo', () => undefined);
  // `vi.mock` 工厂里建的那几个 `vi.fn()` 的**调用记录**不在 `restoreMocks` 的射程内, 不清的话
  // 「没有 ids 时不该取数」会读到上一个用例的调用, 假红.
  vi.clearAllMocks();
  resetSession();

  fetchStrategiesMock.mockResolvedValue({
    records: [
      {
        id: STRATEGY_ID,
        owner_user_id: 'user-1',
        name: STRATEGY_NAME,
        description: '',
        visibility_type: 'private',
        created_at: '2026-09-26T00:00:00',
        updated_at: '2026-09-26T00:00:00',
      },
    ],
    total: 1,
    offset: 0,
    limit: MAXIMUM_PAGE_SIZE,
  });
  fetchRunsMock.mockResolvedValue(buildRunPage([]));
  fetchRunComparisonMock.mockImplementation((runIds) =>
    Promise.resolve(buildComparison(runIds)),
  );
});

describe('地址栏是唯一的状态源', () => {
  it('`?ids=` 去空白、丢空项、保序去重, 拿去重的结果取数', async () => {
    const wrapper = await openComparePage({ ids: 'run-a,,run-a,run-b' });

    expect(fetchRunComparisonMock).toHaveBeenCalledWith(['run-a', 'run-b']);

    const pageText = wrapper.text();

    expect(pageText).toContain('第 1 轮');
    expect(pageText).toContain('第 2 轮');
    expect(pageText).not.toContain('第 3 轮');
  });

  it('没有 ids 时不取数, 只请用户先选', async () => {
    const wrapper = await openComparePage();

    expect(fetchRunComparisonMock).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('还没有选择要对比的运行');
  });
});

describe('选择器', () => {
  it('选满 6 轮后其余勾选框置灰, 并说明为什么', async () => {
    const candidateRunIds = ['run-1', 'run-2', 'run-3', 'run-4', 'run-5', 'run-6', 'run-7'];

    fetchRunsMock.mockResolvedValue(
      buildRunPage(candidateRunIds.map((runId) => buildSummary(runId))),
    );

    const wrapper = await openComparePage({
      ids: candidateRunIds.slice(0, MAXIMUM_COMPARISON_RUNS).join(','),
    });
    const checkboxStates = readCheckboxStates(wrapper);

    expect(checkboxStates).toHaveLength(candidateRunIds.length);
    expect(checkboxStates.filter((state) => state.isChecked)).toHaveLength(
      MAXIMUM_COMPARISON_RUNS,
    );
    // 恰好第 7 个 (唯一没被勾上的那个) 置灰.
    expect(checkboxStates.filter((state) => state.isDisabled)).toHaveLength(1);
    expect(checkboxStates.at(-1)).toEqual({ isChecked: false, isDisabled: true });
    expect(wrapper.text()).toContain(`已达 ${MAXIMUM_COMPARISON_RUNS} 轮上限`);
  });

  it('候选列表只列终态的轮', async () => {
    fetchRunsMock.mockResolvedValue(
      buildRunPage([
        buildSummary('run-done'),
        buildSummary('run-running', { status: 'running' }),
        buildSummary('run-queued', { status: 'queued' }),
      ]),
    );

    const wrapper = await openComparePage();
    const checkboxStates = readCheckboxStates(wrapper);

    expect(checkboxStates).toHaveLength(1);
    expect(wrapper.text()).toContain('run-done');
    expect(wrapper.text()).not.toContain('run-running');
  });
});

describe('指标并列与曲线叠加', () => {
  it('同参不同 GridStep 的两轮各占一列, 参数与指标都并排', async () => {
    fetchRunComparisonMock.mockResolvedValue({
      runs: [
        buildEntry('run-coarse', {
          params_json: '{"GridStep":0.02}',
          balance: 100_000,
        }),
        buildEntry('run-fine', {
          params_json: '{"GridStep":0.01}',
          balance: 120_000,
        }),
      ],
    });

    const pageText = (await openComparePage({ ids: 'run-coarse,run-fine' })).text();

    expect(pageText).toContain('权益曲线叠加');
    expect(pageText).toContain('指标并列');
    expect(pageText).toContain('提交的参数');
    expect(pageText).toContain('0.02');
    expect(pageText).toContain('0.01');
    expect(pageText).toContain('100,000.00');
    expect(pageText).toContain('120,000.00');
  });

  it('某一轮没有曲线时只在该列表头说明, 别列不受影响', async () => {
    const unavailableReason = '结果库文件已不存在';

    fetchRunComparisonMock.mockResolvedValue({
      runs: [
        buildEntry('run-a'),
        { ...buildEntry('run-b'), equity_points: [], equity_unavailable_reason: unavailableReason },
      ],
    });

    const wrapper = await openComparePage({ ids: 'run-a,run-b' });

    expect(wrapper.text()).toContain(`曲线缺失: ${unavailableReason}`);
    expect(wrapper.text().split(`曲线缺失: ${unavailableReason}`)).toHaveLength(2);
    // 指标是另一份可用性: 曲线没了, 这一列的数照显示.
    expect(wrapper.text()).toContain('100,000.00');
  });
});

describe('整请求失败', () => {
  it('有一轮不属于你 (或不存在) 时整页报错, 不再画一张缺一列的表', async () => {
    fetchRunComparisonMock.mockRejectedValue(new ApiError(404, '运行不存在'));

    const wrapper = await openComparePage({ ids: 'run-a,run-b' });

    expect(wrapper.text()).toContain('运行不存在');
    expect(wrapper.find('table').exists()).toBe(false);
    // 选择器照旧: 换一组 id 是这一页上唯一的出路, 把它一起挡掉就只剩刷新浏览器.
    expect(wrapper.text()).toContain('选择要对比的运行');
  });
});
