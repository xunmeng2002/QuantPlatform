// @vitest-environment jsdom
/**
 * PaginationToolbar 的契约用例.
 *
 * 两条要钉的:
 *
 * - **emit 顺序**: 改每页时必须先 `update:limit` 再 `update:offset`(0). 页面是靠 `update:offset`
 *   触发重新取数的, 顺序反了那次请求带的还是旧的页大小, 表现是「改了每页但一页还是十条」——
 *   不报错、只是数不对, 属于最难发现的那一类. 顺序是**跨事件**的, `wrapper.emitted()` 分键之后
 *   看不出来, 所以在父组件侧按到达先后记一笔.
 * - **翻页原样透传**: `PaginationBar` 上抛的 offset 不许被这一层改写 (它只对「改每页」有自己的
 *   意见), 否则翻页会静默跳到别处.
 */

import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';

import PaginationBar from './PaginationBar.vue';
import PaginationToolbar from './PaginationToolbar.vue';

function mountToolbar() {
  const arrivalOrder: string[] = [];
  const onUpdateLimit = vi.fn((nextLimit: number) => {
    arrivalOrder.push(`update:limit(${nextLimit})`);
  });
  const onUpdateOffset = vi.fn((nextOffset: number) => {
    arrivalOrder.push(`update:offset(${nextOffset})`);
  });

  const wrapper = mount(PaginationToolbar, {
    props: { total: 120, offset: 20, limit: 10 },
    // 键名必须带冒号: 模板里的 `@update:limit` 编译出来就是 `"onUpdate:limit"`. 写成驼峰的
    // `onUpdateLimit` 不会被 Vue 的 emit 查到 (它按 `toHandlerKey(camelize(event))` 找), 于是
    // 监听器根本不挂 —— 用例会以「一次都没调到」的形式红掉, 而不是静默通过.
    attrs: { 'onUpdate:limit': onUpdateLimit, 'onUpdate:offset': onUpdateOffset },
  });

  return { wrapper, arrivalOrder, onUpdateLimit, onUpdateOffset };
}

describe('PaginationToolbar', () => {
  it('改每页: 先上抛 update:limit 再上抛 update:offset(0)', async () => {
    const { wrapper, arrivalOrder, onUpdateLimit, onUpdateOffset } = mountToolbar();

    // 直接驱动 ElSelect 的 change: 真去点开它的下拉再选一项, 测的是 EP 自己.
    wrapper.findComponent({ name: 'ElSelect' }).vm.$emit('change', 50);
    await wrapper.vm.$nextTick();

    expect(arrivalOrder).toEqual(['update:limit(50)', 'update:offset(0)']);
    expect(onUpdateLimit).toHaveBeenCalledTimes(1);
    expect(onUpdateOffset).toHaveBeenCalledTimes(1);
  });

  it('翻页: PaginationBar 上抛的 offset 原样透传', async () => {
    const { wrapper, onUpdateOffset } = mountToolbar();

    wrapper.findComponent(PaginationBar).vm.$emit('update:offset', 30);
    await wrapper.vm.$nextTick();

    expect(onUpdateOffset).toHaveBeenCalledWith(30);
  });

  it('三个数原样交给 PaginationBar, 不再自己算一遍', () => {
    const { wrapper } = mountToolbar();
    const paginationBar = wrapper.findComponent(PaginationBar);

    expect(paginationBar.props('total')).toBe(120);
    expect(paginationBar.props('offset')).toBe(20);
    expect(paginationBar.props('limit')).toBe(10);
  });
});
