// @vitest-environment jsdom
/**
 * 提交页的两个**与策略无关**的下拉: 回测合约与手续费组.
 *
 * 这一页的其余部分各有各的用例 (运行级字段在 `domain/run-form`, 参数区在
 * `domain/strategy-configuration`), 这里只钉**一件曾经踩过的事**: 合约五千多条, 而
 * `el-select` 的 `persistent` 默认为真, 它的下拉内容在挂载期就渲染 —— 于是进页面后主线程被冻住
 * 若干秒, 冻的正是"点策略那一格"的第一次点击 (jsdom 里实测五千余项要十几秒).
 *
 * 故合约那一组量的**不是**"选项对不对", 而是两件必须同时成立的事: 选项数据一条不少地交给下拉框,
 * 而落进 DOM 的只有可视区那几行. 判据把 `ElSelectV2` 换回 `ElSelect` + `v-for` 就会红.
 *
 * 手续费组那一组量的则是**空列表**: 全新安装的组表就是空的 (`reference_seed/CommissionGroup.csv`
 * 只有表头), 而"要选一个组"是提交的硬前提 —— 界面那时必须禁用下拉并给出可行动的那句话, 而不是留
 * 一个什么都不说的空下拉.
 */

import { flushPromises, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { ElSelect, ElSelectV2 } from 'element-plus';
import { createPinia, setActivePinia } from 'pinia';
import { defineComponent, h } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchMarketDataContracts } from '../api/market-data';
import { fetchCommissionGroupOptions } from '../api/reference-data';
import { fetchStrategies } from '../api/strategies';
import { MAXIMUM_PAGE_SIZE } from '../api/types';
import type {
  CommissionGroupOption,
  MarketDataContract,
  MarketDataContractList,
} from '../api/types';
import RunSubmitView from './RunSubmitView.vue';

vi.mock('vue-router', () => ({
  RouterLink: defineComponent({ render: () => h('a') }),
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock('../api/market-data', () => ({
  fetchMarketDataContracts: vi.fn(),
  fetchMarketDataCoverage: vi.fn(),
}));
vi.mock('../api/reference-data', () => ({
  fetchCommissionGroupOptions: vi.fn(),
}));
vi.mock('../api/runs', () => ({ submitRun: vi.fn() }));
vi.mock('../api/run-templates', () => ({
  createRunTemplate: vi.fn(),
  fetchRunTemplates: vi.fn(),
}));
vi.mock('../api/strategies', () => ({
  fetchStrategies: vi.fn(),
  fetchStrategyDetail: vi.fn(),
  fetchLastSubmittedParameters: vi.fn(),
}));

/**
 * 五千二百零七条, 与真机上 `GET /api/market-data/contracts` 的返回量同量级
 * (`backend/app/services/quote_hub.read_tradable_contracts` 在本机实测就是这个数).
 */
const CONTRACT_COUNT = 5207;

/**
 * 落进 DOM 的选项行数上限.
 *
 * 可视区 274px / 每行 34px ≈ 8 行, 加缓冲区实测 16 行; 取 64 是为了让判据盯住的是"有没有界",
 * 而不是钉死某个 Element Plus 版本的缓冲策略.
 */
const MAXIMUM_RENDERED_OPTION_COUNT = 64;

function buildContracts(count: number): MarketDataContract[] {
  return Array.from({ length: count }, (_, index) => ({
    code: `sh.${String(600000 + index).padStart(6, '0')}`,
    exchange_id: 'SSE',
    instrument_id: String(600000 + index).padStart(6, '0'),
    display_name: `测试标的 ${index}`,
  }));
}

/** 页面要能画出表单, 就得至少有一个策略 —— 一条都没有时它画的是 `EmptyNotice`. */
function stubStrategyCatalog(): void {
  vi.mocked(fetchStrategies).mockResolvedValue({
    records: [
      {
        id: 'strategy-1',
        owner_user_id: 'user-1',
        name: '网格策略',
        description: '',
        visibility_type: 'private',
        created_at: '2026-09-27T00:00:00',
        updated_at: '2026-09-27T00:00:00',
      },
    ],
    total: 1,
    offset: 0,
    limit: MAXIMUM_PAGE_SIZE,
  });
}

/** 手续费组选项这一路: 一页装得下, 故 `total` 等于条数, `collectAllPages` 取一次就收尾. */
function stubCommissionGroupOptions(options: CommissionGroupOption[]): void {
  vi.mocked(fetchCommissionGroupOptions).mockResolvedValue({
    records: options,
    total: options.length,
    offset: 0,
    limit: MAXIMUM_PAGE_SIZE,
  });
}

/** 挂上提交页并等三路挂载期的请求都回完 (策略目录、合约清单、手续费组选项). */
async function mountSubmitView(
  contractList: MarketDataContractList,
  commissionGroups: CommissionGroupOption[] = [],
) {
  vi.mocked(fetchMarketDataContracts).mockResolvedValue(contractList);
  stubCommissionGroupOptions(commissionGroups);

  const pinia = createPinia();
  setActivePinia(pinia);

  const wrapper = mount(RunSubmitView, { global: { plugins: [pinia] } });

  await flushPromises();

  return wrapper;
}

/** 页面上那一个手续费组下拉. 按 `id` 找而不是按次序: 这一页有好几个下拉框, 找错等于白测. */
function findCommissionGroupSelect(wrapper: VueWrapper) {
  const commissionGroupSelect = wrapper
    .findAllComponents(ElSelect)
    .find((candidate) => candidate.props('id') === 'run-commission-group');

  if (commissionGroupSelect === undefined) {
    throw new Error('页面上没有手续费组下拉');
  }

  return commissionGroupSelect;
}

beforeEach(() => {
  vi.clearAllMocks();
  stubStrategyCatalog();
});

describe('回测合约下拉', () => {
  it('五千多条选项全部交给下拉框, 但只把可视区那几行落进 DOM', async () => {
    const contracts = buildContracts(CONTRACT_COUNT);
    const wrapper = await mountSubmitView({
      available: true,
      reason: '',
      contracts,
    });

    const contractSelect = wrapper.findComponent(ElSelectV2);

    expect(contractSelect.exists()).toBe(true);
    expect(contractSelect.props('options')).toHaveLength(CONTRACT_COUNT);

    // 打开的是**合约那一个**下拉框, 不是页面上第一个: 这一页有五个下拉框, 打开错的等于白测.
    await contractSelect.find('.el-select__wrapper').trigger('click');
    await flushPromises();

    // 查整个 document 而不是 wrapper: 下拉内容 teleport 到 body 上, 不在组件自己的子树里
    // (与 `el-select` 那版同一个道理, 只是那时我们数的是它铺了多少个 `<li>`).
    const renderedOptionCount = document.querySelectorAll(
      '.el-select-dropdown__item',
    ).length;

    // 本机实测 16 行. 下界取 0 是排除"菜单压根没渲染, 于是这条判据白过"; 上界给到 64 是留出
    // Element Plus 改可视区缓冲的余地 —— 它再翻两番也到不了五千.
    expect(renderedOptionCount).toBeGreaterThan(0);
    expect(renderedOptionCount).toBeLessThan(MAXIMUM_RENDERED_OPTION_COUNT);
  });

  it('合约清单不在位时下拉框禁用, 且退回的是被禁用的下拉而不是自由文本', async () => {
    const wrapper = await mountSubmitView({
      available: false,
      reason: '行情组件不在位',
      contracts: [],
    });

    const contractSelect = wrapper.findComponent(ElSelectV2);

    expect(contractSelect.props('disabled')).toBe(true);
    expect(contractSelect.props('options')).toEqual([]);
    expect(wrapper.text()).toContain('行情组件不在位');
  });
});

describe('手续费组下拉', () => {
  /** 合约清单这里不参与判据, 给一份"在位但没有合约"的, 免得五千多条拖慢这一组用例. */
  const contractList: MarketDataContractList = {
    available: true,
    reason: '',
    contracts: [],
  };

  it('选项文案是「组号 · 组名」, 且不预选任何一组', async () => {
    const wrapper = await mountSubmitView(contractList, [
      { commission_group_id: 2, commission_group_name: '宽费率' },
      { commission_group_id: 3, commission_group_name: '窄费率' },
    ]);

    const commissionGroupSelect = findCommissionGroupSelect(wrapper);

    expect(commissionGroupSelect.props('disabled')).toBe(false);
    // 不预选: 只有"上次提交的参数 / 保存过的模板"里带了组号时才回填, 否则空着等用户选. 组号决定
    // 这一轮按哪一套费率计费, 替用户认领一个等于替他选了一套不知道是谁的费率.
    expect(commissionGroupSelect.props('modelValue')).toBeNull();

    await commissionGroupSelect.find('.el-select__wrapper').trigger('click');
    await flushPromises();

    const renderedLabels = Array.from(
      document.querySelectorAll('.el-select-dropdown__item'),
    ).map((option) => option.textContent?.trim());

    // 组号在前: 计费只认组号, 组名只是给人看的.
    expect(renderedLabels).toContain('2 · 宽费率');
    expect(renderedLabels).toContain('3 · 窄费率');
  });

  it('一个组都没建时下拉禁用, 并给出下一步该做什么', async () => {
    const wrapper = await mountSubmitView(contractList, []);

    const commissionGroupSelect = findCommissionGroupSelect(wrapper);

    expect(commissionGroupSelect.props('disabled')).toBe(true);
    expect(commissionGroupSelect.props('modelValue')).toBeNull();
    // 空下拉配一句"还没有组"是**可行动**的 (下一步是找管理员建组); 什么都不说, 用户只会以为页面坏了.
    expect(wrapper.text()).toContain('还没有手续费组');
  });
});
