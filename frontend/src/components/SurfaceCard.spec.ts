// @vitest-environment jsdom
/**
 * SurfaceCard 的契约用例.
 *
 * 最要紧的两条:
 *
 * - **无标题时正文必须是根的直接子元素**. 信息栏是 `<dl>`、输出面板是 `<pre>`, 多包一层 `div`
 *   会让 `<dl>` 失去直接子元素 —— 那是无效 HTML, 而且不报错. 有标题时才包一层, 因为标题行要横贯
 *   整卡 (它的下边框顶到圆角处), 正文得单独缩进.
 * - `class` 必须**透传到根**: `LoginView` 靠 `class="mx-auto mt-16 max-w-sm"` 收窄登录卡片, 透传
 *   一断, 登录页那张卡就铺满 1280px.
 */

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import SurfaceCard from './SurfaceCard.vue';

describe('SurfaceCard', () => {
  it('无标题时无标题行, 内边距落在根上, 正文是根的直接子元素', () => {
    const wrapper = mount(SurfaceCard, { slots: { default: '<p>正文</p>' } });

    expect(wrapper.find('h2').exists()).toBe(false);
    expect(wrapper.classes()).toContain('p-5');
    expect(wrapper.element.children).toHaveLength(1);
    expect(wrapper.element.children[0]?.tagName).toBe('P');
  });

  it('有标题时渲染标题行, 正文缩进在内层', () => {
    const wrapper = mount(SurfaceCard, {
      props: { title: '版本清单' },
      slots: { default: '<p>正文</p>' },
    });

    expect(wrapper.find('h2').text()).toBe('版本清单');
    expect(wrapper.element.children).toHaveLength(2);
    expect(wrapper.element.children[1]?.querySelector('p')?.textContent).toBe('正文');
  });

  it('description 渲染在标题下面的小字行', () => {
    const wrapper = mount(SurfaceCard, {
      props: { title: '上传新版本', description: '支持 .py 与 .zip' },
    });

    expect(wrapper.find('h2').text()).toBe('上传新版本');
    expect(wrapper.element.children[0]?.querySelector('p')?.textContent).toBe('支持 .py 与 .zip');
  });

  it('tag 决定卡片本体元素, 卡片外观一并落在它身上', () => {
    const wrapper = mount(SurfaceCard, {
      props: { tag: 'dl' },
      slots: { default: '<dt>策略</dt><dd>双均线</dd>' },
    });

    expect(wrapper.element.tagName).toBe('DL');
    expect(wrapper.classes()).toContain('rounded-lg');
    expect(wrapper.classes()).toContain('shadow-sm');
  });

  it('isBodyPadded 为 false 时根上不带内边距', () => {
    const wrapper = mount(SurfaceCard, {
      props: { isBodyPadded: false },
      slots: { default: '<pre>标准输出</pre>' },
    });

    expect(wrapper.classes()).not.toContain('p-5');
  });

  it('header-actions 插槽在标题行里', () => {
    const wrapper = mount(SurfaceCard, {
      props: { title: '授权名单' },
      slots: { 'header-actions': '<button type="button">刷新</button>' },
    });

    expect(wrapper.find('h2').text()).toBe('授权名单');
    expect(wrapper.element.children[0]?.querySelector('button')?.textContent).toBe('刷新');
  });

  it('class 透传到根', () => {
    const wrapper = mount(SurfaceCard, {
      attrs: { class: 'mx-auto mt-16 max-w-sm' },
      slots: { default: '<p>正文</p>' },
    });

    expect(wrapper.classes()).toContain('max-w-sm');
    expect(wrapper.classes()).toContain('rounded-lg');
  });
});
