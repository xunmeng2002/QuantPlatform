// @vitest-environment jsdom
/**
 * ErrorBanner 的契约用例.
 *
 * 四条都是"换库时最容易悄悄丢掉"的东西, 而且丢了都不报错:
 *
 * - `role="alert"` **只能有一处**: el-alert 的根节点自己就带这个角色 (EP 源码里硬编码), 所以包裹层
 *   上不能再加 —— 嵌套两块 live region 会重复播报. 这里钉的是"恰好一个", 而不是"在哪一层".
 * - `type="error"`: el-alert 的取值是 `primary | success | warning | error | info`, 与 el-tag 的
 *   `danger` **不同名**, 写成 danger 只会静默退回默认色.
 * - `closable=false`: 默认是开的, 而关闭只翻组件内部的可见标志 —— 父层的 message 还在, 于是留下
 *   「消息被用户关掉了但状态还说有错」这种自相矛盾的界面.
 * - 重试按钮必须在包裹层里 (el-alert 没有 action 插槽), 且 `retry` 语义不变. 它在**不传
 *   `isRetryVisible`** 时就要在 —— Vue 会把缺省的布尔 prop 落成 `false`, 那条隐式转换曾让这个按钮
 *   在每个调用点都不渲染.
 */

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import ErrorBanner from './ErrorBanner.vue';

describe('ErrorBanner', () => {
  it('message 为 null 时整条不渲染', () => {
    const wrapper = mount(ErrorBanner, { props: { message: null } });

    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it('role="alert" 恰好一处, 且是 el-alert 自己带的那个', () => {
    const wrapper = mount(ErrorBanner, { props: { message: '加载运行列表失败' } });

    expect(wrapper.findAll('[role="alert"]')).toHaveLength(1);
    expect(wrapper.find('.el-alert').attributes('role')).toBe('alert');
    expect(wrapper.element.attributes.role).toBeUndefined();
  });

  it('type 是 error 而不是 danger', () => {
    const wrapper = mount(ErrorBanner, { props: { message: '加载运行列表失败' } });

    expect(wrapper.find('.el-alert').classes()).toContain('el-alert--error');
  });

  it('文案原样呈现', () => {
    const wrapper = mount(ErrorBanner, { props: { message: '加载运行列表失败' } });

    expect(wrapper.find('.el-alert__title').text()).toBe('加载运行列表失败');
  });

  it('不可关闭', () => {
    const wrapper = mount(ErrorBanner, { props: { message: '加载运行列表失败' } });

    expect(wrapper.find('.el-alert__close-btn').exists()).toBe(false);
  });

  it('默认给一个「重试」按钮, 点了就 emit retry', async () => {
    const wrapper = mount(ErrorBanner, { props: { message: '加载运行列表失败' } });
    const retryButton = wrapper.find('button');

    expect(retryButton.text()).toBe('重试');

    await retryButton.trigger('click');

    expect(wrapper.emitted('retry')).toHaveLength(1);
  });

  it('isRetryVisible 为 false 时不出按钮, 消息照旧', () => {
    const wrapper = mount(ErrorBanner, {
      props: { message: '提交失败', isRetryVisible: false },
    });

    expect(wrapper.find('button').exists()).toBe(false);
    expect(wrapper.find('.el-alert__title').text()).toBe('提交失败');
  });

  it('retryLabel 能覆盖按钮文案', () => {
    const wrapper = mount(ErrorBanner, {
      props: { message: '加载策略版本失败', retryLabel: '重新加载版本' },
    });

    expect(wrapper.find('button').text()).toBe('重新加载版本');
  });
});
