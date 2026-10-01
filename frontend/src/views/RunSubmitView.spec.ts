// @vitest-environment jsdom
/**
 * 提交页的合约下拉.
 *
 * 这一页的其余部分各有各的用例 (运行级字段在 `domain/run-form`, 参数区在
 * `domain/strategy-configuration`), 这里只钉**一件曾经踩过的事**: 合约五千多条, 而
 * `el-select` 的 `persistent` 默认为真, 它的下拉内容在挂载期就渲染 —— 于是进页面后主线程被冻住
 * 若干秒, 冻的正是"点策略那一格"的第一次点击 (jsdom 里实测五千余项要十几秒).
 *
 * 故这里量的**不是**"选项对不对", 而是两件必须同时成立的事: 选项数据一条不少地交给下拉框, 而
 * 落进 DOM 的只有可视区那几行. 判据把 `ElSelectV2` 换回 `ElSelect` + `v-for` 就会红.
 */

import { flushPromises, mount } from '@vue/test-utils';
import { ElSelectV2 } from 'element-plus';
import { createPinia, setActivePinia } from 'pinia';
import { defineComponent, h } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchMarketDataContracts } from '../api/market-data';
import { fetchStrategies } from '../api/strategies';
import { MAXIMUM_PAGE_SIZE } from '../api/types';
import type { MarketDataContract, MarketDataContractList } from '../api/types';
import RunSubmitView from './RunSubmitView.vue';

vi.mock('vue-router', () => ({
  RouterLink: defineComponent({ render: () => h('a') }),
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock('../api/market-data', () => ({
  fetchMarketDataContracts: vi.fn(),
  fetchMarketDataCoverage: vi.fn(),
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

/** 挂上提交页并等两路挂载期的请求都回完 (策略目录与合约清单). */
async function mountSubmitView(
  contractList: MarketDataContractList,
) {
  vi.mocked(fetchMarketDataContracts).mockResolvedValue(contractList);

  const pinia = createPinia();
  setActivePinia(pinia);

  const wrapper = mount(RunSubmitView, { global: { plugins: [pinia] } });

  await flushPromises();

  return wrapper;
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

    // 打开的是**合约那一个**下拉框, 不是页面上第一个: 这一页有四个下拉框, 打开错的等于白测.
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
