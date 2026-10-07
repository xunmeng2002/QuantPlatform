// @vitest-environment jsdom
/**
 * 品种面板: 列表、建单、删除三件事, 以及三条容易悄悄坏掉的约束.
 *
 * 三条约束都是**看代码看不出来、点一下就看得出来**的那一类:
 *
 * - 表单没填全时提交键必须是灰的. 判"填全了吗"与拼请求体共用同一个函数 (`buildPayload`), 若谁
 *   把这两处拆开, 症状是"按钮亮着但点了没反应";
 * - 数值框的默认值要真的进请求体 (每手乘数 1 / 最小变动 0.01): 少一项后端会按 schema 默认值收下,
 *   于是**界面上少了哪一列, 响应里完全看不出来**;
 * - 删除必须先问一句. 这里是唯一一处会把数据抹掉的动作, 而抹掉的是引擎算钱要用的乘数.
 *
 * 建单与删除都断言**发出去的请求体/路径**, 而不只断言界面上多了或少了那一行: 后者在"界面自己
 * 乐观地加了一行、请求却没发"时同样会绿.
 *
 * `confirmAction` 被打桩成"用户点了确认": `ElMessageBox` 是真弹的, 在 jsdom 里驱动它要点 DOM 上
 * 的按钮, 而那时用例测的是 EP 自己的弹窗, 与这一页的契约无关.
 */

import { flushPromises, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PageResponse, Product, ProductPayload } from '../api/types';
import { DEFAULT_PAGE_SIZE } from '../api/types';
import { confirmAction } from '../composables/use-feedback';
import ReferenceProductPanel from './ReferenceProductPanel.vue';

vi.mock('../composables/use-feedback', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../composables/use-feedback')>();

  return { ...actual, confirmAction: vi.fn(async () => true) };
});

const PRODUCTS_PATH = '/api/reference-data/products';

const MAOTAI_PRODUCT: Product = {
  id: 'product-maotai',
  exchange_id: 'SSE',
  product_id: '600519',
  product_name: '贵州茅台',
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

interface RecordedRequest {
  method: string;
  pathname: string;
  body: unknown;
}

let listedProducts: Product[];
let recordedRequests: RecordedRequest[];

function jsonResponse(responseBody: unknown, status = 200): Response {
  return new Response(JSON.stringify(responseBody), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 一个够用的后端: 列表按 offset/limit 切, 建单把新行收进去, 删单把它拿掉. */
function stubFetch(): void {
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const [pathname = '', search = ''] = url.split('?');
    const query = new URLSearchParams(search);
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;

    recordedRequests.push({ method, pathname, body });

    if (pathname === PRODUCTS_PATH && method === 'GET') {
      const offset = Number(query.get('offset') ?? 0);
      const limit = Number(query.get('limit') ?? DEFAULT_PAGE_SIZE);
      const page: PageResponse<Product> = {
        total: listedProducts.length,
        offset,
        limit,
        records: listedProducts.slice(offset, offset + limit),
      };

      return Promise.resolve(jsonResponse(page));
    }

    if (pathname === PRODUCTS_PATH && method === 'POST') {
      const payload = body as ProductPayload;
      const createdProduct: Product = {
        ...MAOTAI_PRODUCT,
        ...payload,
        id: `product-${listedProducts.length + 1}`,
      };

      listedProducts = [...listedProducts, createdProduct];

      return Promise.resolve(jsonResponse(createdProduct, 201));
    }

    if (method === 'DELETE') {
      const deletedId = pathname.slice(`${PRODUCTS_PATH}/`.length);

      listedProducts = listedProducts.filter((product) => product.id !== deletedId);

      return Promise.resolve(jsonResponse({ message: '品种已删除' }));
    }

    throw new Error(`用例没预料到这次请求: ${method} ${url}`);
  });
}

function mountPanel(): VueWrapper {
  return mount(ReferenceProductPanel);
}

/** 给某个 ElSelect 换取值. 直接改它内层 input 的 value 不提交 —— 选项是被选出来的, 不是打进去的. */
function selectOption(wrapper: VueWrapper, selectId: string, value: string): void {
  const select = wrapper
    .findAllComponents({ name: 'ElSelect' })
    .find((candidate) => candidate.props('id') === selectId);

  if (select === undefined) {
    throw new Error(`面板里没有 id 为 ${selectId} 的下拉框`);
  }

  select.vm.$emit('update:modelValue', value);
}

/** 拉起的列表接口之外, 还要等 `onMounted` 里那次取数与表格重绘. */
async function mountAndLoad(): Promise<VueWrapper> {
  const wrapper = mountPanel();

  await flushPromises();

  return wrapper;
}

beforeEach(() => {
  listedProducts = [MAOTAI_PRODUCT];
  recordedRequests = [];
  stubFetch();
  vi.mocked(confirmAction).mockResolvedValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ReferenceProductPanel', () => {
  it('列出已有品种, 并把品种类型翻成中文', async () => {
    const wrapper = await mountAndLoad();

    expect(wrapper.text()).toContain('SSE');
    expect(wrapper.text()).toContain('600519');
    expect(wrapper.text()).toContain('股票');
    expect(recordedRequests).toEqual([
      {
        method: 'GET',
        pathname: PRODUCTS_PATH,
        body: undefined,
      },
    ]);
  });

  it('一条都没有时给空态, 而不是一张空表', async () => {
    listedProducts = [];

    const wrapper = await mountAndLoad();

    expect(wrapper.text()).toContain('还没有品种');
  });

  it('表单没填全时提交键是灰的, 填全才亮', async () => {
    const wrapper = await mountAndLoad();
    const submitButton = wrapper.get('button[type="submit"]');

    expect(submitButton.attributes('disabled')).toBeDefined();

    await wrapper.get('#product-id').setValue('600519');
    await wrapper.get('#product-name').setValue('贵州茅台');
    selectOption(wrapper, 'product-exchange-id', 'SSE');
    await flushPromises();

    expect(submitButton.attributes('disabled')).toBeUndefined();
  });

  it('建单: 请求体带着六个数字框的默认值, 成功后再取一次列表', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    await wrapper.get('#product-id').setValue('600519');
    await wrapper.get('#product-name').setValue('贵州茅台');
    selectOption(wrapper, 'product-exchange-id', 'SSE');
    // 提交事件挂在 `<form>` 上 (`@submit.prevent`), 不在那个按钮上 —— 对按钮派发 submit 是没有
    // 监听者的, 而用例会以"一个请求都没发"的形式红掉.
    await wrapper.get('form').trigger('submit');
    await flushPromises();

    expect(recordedRequests).toContainEqual({
      method: 'POST',
      pathname: PRODUCTS_PATH,
      body: {
        exchange_id: 'SSE',
        product_id: '600519',
        product_name: '贵州茅台',
        product_class: 6,
        volume_multiple: 1,
        price_tick: 0.01,
        max_market_order_volume: 0,
        min_market_order_volume: 0,
        max_limit_order_volume: 0,
        min_limit_order_volume: 0,
        session_name: '',
      },
    });

    // 建完后重新取列表: 不重取的话, 新行要等用户自己刷新才看得见.
    expect(recordedRequests.at(-1)).toEqual({
      method: 'GET',
      pathname: PRODUCTS_PATH,
      body: undefined,
    });
  });

  it('删除: 先问一句, 确认后才发请求, 页面跟着重取', async () => {
    const wrapper = await mountAndLoad();
    recordedRequests = [];

    const deleteButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '删除');

    expect(deleteButton).toBeDefined();

    await deleteButton?.trigger('click');
    await flushPromises();

    expect(confirmAction).toHaveBeenCalledTimes(1);
    expect(recordedRequests[0]).toEqual({
      method: 'DELETE',
      pathname: `${PRODUCTS_PATH}/${MAOTAI_PRODUCT.id}`,
      body: undefined,
    });
    expect(wrapper.text()).toContain('还没有品种');
  });
});
