// @vitest-environment jsdom
/**
 * 费率面板: 三级作用域的选择器, 以及它换出的那一格该怎么进请求体.
 *
 * 这一页最容易悄悄坏掉的是**作用域与合约格的对应关系**: 表里没有"作用域"这一列, 它由合约格的
 * 取值承载 (空 = 交易所级, 短码 = 品种级, 其余 = 合约级). 若界面按"作用域"填了别的值, 后端收到的
 * 就是另一档规则 —— 而回测照跑完, 只是钱算错了. 故下面逐档断言**发出去的请求体**, 而不只断言
 * 界面上多了一行.
 *
 * 品种那一格的选项来自 `/reference-data/products` (后端按同一张表守门), 故挂载时会多发一次
 * 请求; 交易所那一格在品种级时被锁住 —— 手改一个与品种对不上的交易所会造出一行自相矛盾的规则.
 *
 * **方向这一格同理有第二处"由取值承载"**: `-1` 是平台自己的通配档 (双向), `0` / `1` 才是引擎那
 * 两档. 故这里也断言发出去的请求体, 并单独钉住"默认取双向"与"列表里的双向行怎么显示" —— 后者是
 * 那句"…向费率"最容易漏改的地方 (双向已经是完整说法, 再接一个「向」就成了「双向向费率」).
 */

import { flushPromises, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BOTH_DIRECTIONS } from '../api/types';
import type {
  BaseCommission,
  BaseCommissionPayload,
  PageResponse,
  Product,
} from '../api/types';
import ReferenceBaseCommissionPanel from './ReferenceBaseCommissionPanel.vue';
import StatusBadge from './StatusBadge.vue';

const REFERENCE_DATA_PATH = '/api/reference-data';
const BASE_COMMISSIONS_PATH = `${REFERENCE_DATA_PATH}/base-commissions`;
const PRODUCTS_PATH = `${REFERENCE_DATA_PATH}/products`;

const STOCK_PRODUCT: Product = {
  id: 'product-600',
  exchange_id: 'SSE',
  product_id: '600',
  product_name: '沪市主板',
  product_class: 6,
  volume_multiple: 1,
  price_tick: 0.01,
  max_market_order_volume: 0,
  min_market_order_volume: 0,
  max_limit_order_volume: 0,
  min_limit_order_volume: 0,
  session_name: '',
  created_at: '2026-10-07T02:00:00',
  updated_at: '2026-10-07T02:00:00',
};

function buildBaseCommission(
  id: string,
  instrumentId: string,
  direction: number,
): BaseCommission {
  return {
    id,
    commission_group_id: 1,
    exchange_id: 'SSE',
    instrument_id: instrumentId,
    direction,
    open_by_money: 0.0003,
    close_by_money: 0.0003,
    open_by_volume: 0,
    close_by_volume: 0,
    open_stamp_tax_by_money: 0,
    close_stamp_tax_by_money: 0.0005,
    open_transfer_fee_by_money: 0,
    close_transfer_fee_by_money: 0.00001,
    min_commission: 5,
    max_commission: 0,
    created_at: '2026-10-07T02:00:00',
    updated_at: '2026-10-07T02:00:00',
  };
}

const CONTRACT_RATE = buildBaseCommission('rate-contract', '600519', 0);
const PRODUCT_RATE = buildBaseCommission('rate-product', '600', 1);
const EXCHANGE_RATE = buildBaseCommission('rate-exchange', '', 0);

interface RecordedRequest {
  method: string;
  pathname: string;
  body: unknown;
}

let listedProducts: Product[];
let listedBaseCommissions: BaseCommission[];
let recordedRequests: RecordedRequest[];

function jsonResponse(responseBody: unknown, status = 200): Response {
  return new Response(JSON.stringify(responseBody), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 一个够用的后端: 两个列表按 offset/limit 切, 建单把新行收进去. */
function stubFetch(): void {
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const [pathname = '', search = ''] = url.split('?');
    const query = new URLSearchParams(search);
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;

    recordedRequests.push({ method, pathname, body });

    if (method === 'GET') {
      const listedRecords = pathname === PRODUCTS_PATH
        ? listedProducts
        : pathname === BASE_COMMISSIONS_PATH
          ? listedBaseCommissions
          : null;

      if (listedRecords === null) {
        throw new Error(`用例没预料到这次请求: ${method} ${url}`);
      }

      const offset = Number(query.get('offset') ?? 0);
      const limit = Number(query.get('limit') ?? 20);
      const page: PageResponse<unknown> = {
        total: listedRecords.length,
        offset,
        limit,
        records: listedRecords.slice(offset, offset + limit),
      };

      return Promise.resolve(jsonResponse(page));
    }

    if (method === 'POST' && pathname === BASE_COMMISSIONS_PATH) {
      const createdRate: BaseCommission = {
        ...CONTRACT_RATE,
        ...(body as BaseCommissionPayload),
        id: `rate-${listedBaseCommissions.length + 1}`,
      };

      listedBaseCommissions = [...listedBaseCommissions, createdRate];

      return Promise.resolve(jsonResponse(createdRate, 201));
    }

    throw new Error(`用例没预料到这次请求: ${method} ${url}`);
  });
}

/** 面板里某个带 `id` 的组件. 找不到就抛 —— 那说明选择器写错了, 而不是"这一步可以跳过". */
function componentById(
  wrapper: VueWrapper,
  componentName: string,
  elementId: string,
): VueWrapper {
  const component = wrapper
    .findAllComponents({ name: componentName })
    .find((candidate) => candidate.props('id') === elementId);

  if (component === undefined) {
    throw new Error(`面板里没有 id 为 ${elementId} 的 ${componentName}`);
  }

  return component;
}

/**
 * 给下拉框换取值.
 *
 * 直接改它内层 input 的 value 不会提交 —— 选项是被选出来的, 不是打进去的; 而 `ElSelect` 上抛的
 * 正是 `update:modelValue`.
 */
function selectOption(
  wrapper: VueWrapper,
  selectId: string,
  value: string | number,
): void {
  componentById(wrapper, 'ElSelect', selectId).vm.$emit('update:modelValue', value);
}

/** 点某一行的操作键. 按文案找 —— 行里只有「编辑」与「删除」两个, 而表格外的那两个按键文案不同. */
async function clickRowAction(wrapper: VueWrapper, label: string): Promise<void> {
  const actionButton = wrapper
    .findAll('button')
    .find((button) => button.text() === label);

  if (actionButton === undefined) {
    throw new Error(`行里没有「${label}」这个操作键`);
  }

  await actionButton.trigger('click');
}

/** 某个下拉框是不是被锁住了. 断言"被锁住"这件事本身, 而不是它因为什么被锁. */
function isSelectDisabled(wrapper: VueWrapper, selectId: string): boolean {
  const selectProps = componentById(wrapper, 'ElSelect', selectId).props() as {
    disabled?: boolean;
  };

  return selectProps.disabled === true;
}

async function mountAndLoad(): Promise<VueWrapper> {
  const wrapper = mount(ReferenceBaseCommissionPanel);

  await flushPromises();

  return wrapper;
}

/** 填全"三格 + 组号"之外的公共部分: 组号与交易所. */
async function fillGroupAndExchange(wrapper: VueWrapper): Promise<void> {
  await wrapper.get('#base-commission-group-id').setValue('1');
  selectOption(wrapper, 'base-commission-exchange-id', 'SSE');
  await flushPromises();
}

function submittedRateRequests(): RecordedRequest[] {
  return recordedRequests.filter(
    (recorded) => recorded.method === 'POST' && recorded.pathname === BASE_COMMISSIONS_PATH,
  );
}

beforeEach(() => {
  listedProducts = [STOCK_PRODUCT];
  listedBaseCommissions = [CONTRACT_RATE, PRODUCT_RATE, EXCHANGE_RATE];
  recordedRequests = [];
  stubFetch();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ReferenceBaseCommissionPanel', () => {
  it('列出费率规则, 作用域按合约格读出来', async () => {
    const wrapper = await mountAndLoad();

    expect(
      wrapper.findAllComponents(StatusBadge).map((badge) => badge.props('label')),
    ).toEqual(['合约级', '品种级', '交易所级']);

    expect(wrapper.text()).toContain('600519');
    expect(wrapper.text()).toContain('600');
  });

  it('合约级: 组号 + 交易所 + 合约码三格填全才亮提交键', async () => {
    const wrapper = await mountAndLoad();
    const submitButton = wrapper.get('button[type="submit"]');

    expect(submitButton.attributes('disabled')).toBeDefined();

    await fillGroupAndExchange(wrapper);

    // 合约格还空着 —— 这一档必须指名一个合约, 空串在这里是"没填"而不是"交易所级".
    expect(submitButton.attributes('disabled')).toBeDefined();

    await wrapper.get('#base-commission-instrument-id').setValue('600519');
    await flushPromises();

    expect(submitButton.attributes('disabled')).toBeUndefined();
  });

  it('合约级: 请求体里是合约码与十项费率', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    await fillGroupAndExchange(wrapper);
    await wrapper.get('#base-commission-instrument-id').setValue('600519');
    // 提交事件挂在 `<form>` 上 (`@submit.prevent`), 不在那个按钮上.
    await wrapper.get('form').trigger('submit');
    await flushPromises();

    expect(submittedRateRequests()[0]?.body).toEqual({
      commission_group_id: 1,
      exchange_id: 'SSE',
      instrument_id: '600519',
      // 新建时方向默认双向 (-1): 买卖共用这一套费率, 后端展开那一步才摊成买、卖两行.
      direction: -1,
      open_by_money: 0,
      close_by_money: 0,
      open_by_volume: 0,
      close_by_volume: 0,
      open_stamp_tax_by_money: 0,
      close_stamp_tax_by_money: 0,
      open_transfer_fee_by_money: 0,
      close_transfer_fee_by_money: 0,
      min_commission: 0,
      max_commission: 0,
    });
  });

  it('十项费率按小数点后六位录入: 步进 0.000001, 六位的值原样进请求体', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    const rateInputs = wrapper
      .findAllComponents({ name: 'ElInputNumber' })
      .filter((inputNumber) =>
        String(inputNumber.props('id')).startsWith('base-commission-'),
      )
      // 组号那一格也是 ElInputNumber, 但它是个数数儿的整数 —— 不在这十项里.
      .filter((inputNumber) => inputNumber.props('id') !== 'base-commission-group-id');

    expect(rateInputs).toHaveLength(10);

    // 步进只管键盘上下键, 但它同时是"这一格认到第几位小数"的下限: 停在 0.0001, 用上下键微调
    // 0.000001 量级的费率会被抹平成四位.
    for (const inputNumber of rateInputs) {
      expect(inputNumber.props('step')).toBe(0.000001);
    }

    await fillGroupAndExchange(wrapper);
    await wrapper.get('#base-commission-instrument-id').setValue('600519');
    // 六位小数的费率是真实存在的 (0.000001 量级), 这一格得原样收、原样发.
    await wrapper.get('#base-commission-open_by_money').setValue('0.000001');
    await wrapper.get('form').trigger('submit');
    await flushPromises();

    // 请求体在录请求那一层是 `unknown` (它什么都录), 断言具体字段时才收口成费率请求体.
    const submittedRates = submittedRateRequests()[0]?.body as BaseCommissionPayload;

    expect(submittedRates.open_by_money).toBe(0.000001);
  });

  it('交易所级: 不填代码照样提交, 请求体里的合约格是空串', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    selectOption(wrapper, 'base-commission-scope', 'exchange');
    await flushPromises();
    await fillGroupAndExchange(wrapper);

    const submitButton = wrapper.get('button[type="submit"]');

    expect(submitButton.attributes('disabled')).toBeUndefined();

    await wrapper.get('form').trigger('submit');
    await flushPromises();

    // 空串就是这一级的取值: 后端按它认出"交易所级", 不是"漏填了合约".
    expect(submittedRateRequests()[0]?.body).toMatchObject({
      exchange_id: 'SSE',
      instrument_id: '',
    });
  });

  it('品种级: 选中品种带出交易所, 请求体里是品种码', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    selectOption(wrapper, 'base-commission-scope', 'product');
    await flushPromises();
    // 交易所那一格在这一档里被锁住 —— 它该由选中的品种带出来.
    expect(isSelectDisabled(wrapper, 'base-commission-exchange-id')).toBe(true);

    await wrapper.get('#base-commission-group-id').setValue('1');
    selectOption(wrapper, 'base-commission-product-code', 'SSE/600');
    await flushPromises();

    await wrapper.get('form').trigger('submit');
    await flushPromises();

    expect(submittedRateRequests()[0]?.body).toMatchObject({
      exchange_id: 'SSE',
      instrument_id: '600',
    });
  });

  it('换作用域会清掉已经填过的代码, 不让它冒充另一档', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    await fillGroupAndExchange(wrapper);
    await wrapper.get('#base-commission-instrument-id').setValue('600519');
    await flushPromises();

    selectOption(wrapper, 'base-commission-scope', 'exchange');
    await flushPromises();

    await wrapper.get('form').trigger('submit');
    await flushPromises();

    expect(submittedRateRequests()[0]?.body).toMatchObject({ instrument_id: '' });
  });

  it('方向可以选买卖两档: 选「卖」时请求体里是 1', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    await fillGroupAndExchange(wrapper);
    await wrapper.get('#base-commission-instrument-id').setValue('600519');
    selectOption(wrapper, 'base-commission-direction', 1);
    await flushPromises();

    await wrapper.get('form').trigger('submit');
    await flushPromises();

    // 0 / 1 是引擎那两档, 与 -1 一样都是**取值**, 不是三个互斥的开关.
    expect(submittedRateRequests()[0]?.body).toMatchObject({ direction: 1 });
  });

  it('列表里的双向行显示成「双向」, 编辑它时下拉框回显, 标题里不出现「双向向」', async () => {
    listedBaseCommissions = [
      buildBaseCommission('rate-both', '600519', BOTH_DIRECTIONS),
    ];

    const wrapper = await mountAndLoad();

    expect(wrapper.text()).toContain('双向');

    await clickRowAction(wrapper, '编辑');
    await flushPromises();

    // 回显的是那个取值本身 —— 下拉框一旦按"买 / 卖两项"的旧口径读它, 这一格就会空着, 而保存下去
    // 会把双向那条规则悄悄改成买或卖.
    const directionSelectProps = componentById(
      wrapper,
      'ElSelect',
      'base-commission-direction',
    ).props() as { modelValue?: number };

    expect(directionSelectProps.modelValue).toBe(BOTH_DIRECTIONS);

    expect(wrapper.text()).toContain('双向费率');
    // 「双向」已经是完整说法: 后缀那个「向」只该拼在买 / 卖后面.
    expect(wrapper.text()).not.toContain('双向向费率');
  });
});
