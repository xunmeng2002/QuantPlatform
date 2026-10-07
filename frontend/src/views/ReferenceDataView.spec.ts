// @vitest-environment jsdom
/**
 * 基础数据页: 三个标签页, 以及 `lazy` 与 `default-value` 这两处必须显式给的取值.
 *
 * 这一页自己没有取数逻辑 (三张表的数据各由自己的面板管), 剩下值得钉的就是这两处:
 *
 * - `default-value` 必须写成一个**面板名**而不是默认的序号 `'0'`: 面板各有名字
 *   (`products` / …), 不指一次就没有任何一个匹配得上 —— 症状是三页全空, 而标签条照常渲染, 看上
 *   去像"面板没内容";
 * - 三个面板都带 `lazy`: 不加的话进页面就会同时挂载三个、同时发三个列表请求, 而用户此刻只看得到
 *   第一个 —— 两次白花的往返, 且它们的失败横幅会一起冒出来, 像是三处同时坏了.
 */

import { flushPromises, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PageResponse, Product } from '../api/types';
import ReferenceDataView from './ReferenceDataView.vue';

const REFERENCE_DATA_PATH = '/api/reference-data';
const PRODUCTS_PATH = `${REFERENCE_DATA_PATH}/products`;

let recordedRequests: string[];

function jsonResponse(responseBody: unknown, status = 200): Response {
  return new Response(JSON.stringify(responseBody), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubFetch(): void {
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const [pathname = ''] = url.split('?');

    recordedRequests.push(`${method} ${pathname}`);

    if (pathname === PRODUCTS_PATH) {
      const emptyPage: PageResponse<Product> = {
        total: 0,
        offset: 0,
        limit: 20,
        records: [],
      };

      return Promise.resolve(jsonResponse(emptyPage));
    }

    throw new Error(`用例没预料到这次请求: ${method} ${url}`);
  });
}

async function mountAndLoad(): Promise<VueWrapper> {
  const wrapper = mount(ReferenceDataView);

  await flushPromises();

  return wrapper;
}

beforeEach(() => {
  recordedRequests = [];
  stubFetch();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ReferenceDataView', () => {
  it('三个标签都在', async () => {
    const wrapper = await mountAndLoad();
    const pageText = wrapper.text();

    expect(pageText).toContain('品种');
    expect(pageText).toContain('手续费组');
    expect(pageText).toContain('费率明细');
  });

  it('先只挂默认那一页的面板, 不发另外两个列表请求', async () => {
    await mountAndLoad();

    expect(recordedRequests).toContain(`GET ${PRODUCTS_PATH}`);
    expect(recordedRequests).not.toContain(
      `GET ${REFERENCE_DATA_PATH}/commission-groups`,
    );
    expect(recordedRequests).not.toContain(
      `GET ${REFERENCE_DATA_PATH}/base-commissions`,
    );
  });
});
