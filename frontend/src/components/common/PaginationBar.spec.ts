// @vitest-environment jsdom
/**
 * 分页条的契约用例 (契约 2).
 *
 * 整个组件对外只有**一个 emit**, 而它上抛的是 **offset (从 0 起)** 而不是页码 —— el-pagination
 * 的页码从 1 起, 两者的换算全在 `currentPage` 那个 computed 里, 两个方向各钉一条. off-by-one
 * 最爱的就是这种地方: 换错之后界面照样翻页, 只是每页的首条会重复或漏掉一条.
 *
 * 另外钉一条**行为**而不只是换算: 后端返回的条数不足一页时, 上一页/下一页仍要在, 只是禁用
 * (今天就是"始终在, 只是禁用"). EP 的 `pager` 在只有一页时会整个收起来, prev/next 会不会跟着
 * 藏是另一回事, 故用 `hideOnSinglePage` 的默认值把它钉住.
 */

import { mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import PaginationBar from './PaginationBar.vue';

function mountPagination(total: number, offset: number, limit: number): VueWrapper {
  return mount(PaginationBar, { props: { total, offset, limit } });
}

describe('PaginationBar', () => {
  it('下一页上抛 offset + limit', async () => {
    const wrapper = mountPagination(100, 0, 20);

    await wrapper.find('.btn-next').trigger('click');

    expect(wrapper.emitted('update:offset')).toEqual([[20]]);
  });

  it('上一页上抛 offset - limit', async () => {
    const wrapper = mountPagination(100, 40, 20);

    await wrapper.find('.btn-prev').trigger('click');

    expect(wrapper.emitted('update:offset')).toEqual([[20]]);
  });

  it('首页的上一页禁用', () => {
    const wrapper = mountPagination(100, 0, 20);

    expect(wrapper.find('.btn-prev').attributes('disabled')).toBeDefined();
    expect(wrapper.find('.btn-next').attributes('disabled')).toBeUndefined();
  });

  it('末页的下一页禁用', () => {
    const wrapper = mountPagination(100, 80, 20);

    expect(wrapper.find('.btn-next').attributes('disabled')).toBeDefined();
    expect(wrapper.find('.btn-prev').attributes('disabled')).toBeUndefined();
  });

  it('只有一页时两个按钮仍在, 只是都禁用', () => {
    const wrapper = mountPagination(5, 0, 20);

    expect(wrapper.find('.btn-prev').exists()).toBe(true);
    expect(wrapper.find('.btn-next').exists()).toBe(true);
    expect(wrapper.find('.btn-prev').attributes('disabled')).toBeDefined();
    expect(wrapper.find('.btn-next').attributes('disabled')).toBeDefined();
  });

  it('按钮上写的是中文而不是只剩图标', () => {
    const wrapper = mountPagination(100, 20, 20);

    expect(wrapper.find('.btn-prev').text()).toBe('上一页');
    expect(wrapper.find('.btn-next').text()).toBe('下一页');
  });

  it('条数文案仍是自己的那一句 (EP 的 total 没被拉进来)', () => {
    const wrapper = mountPagination(100, 40, 20);

    expect(wrapper.text()).toContain('第 41–60 条, 共 100 条');
  });

  it('空结果集不出现"第 1–0 条"', () => {
    const wrapper = mountPagination(0, 0, 20);

    expect(wrapper.text()).toContain('第 0–0 条, 共 0 条');
  });
});
