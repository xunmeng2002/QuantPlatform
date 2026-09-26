// @vitest-environment jsdom
/**
 * PageHeader 的契约用例.
 *
 * 三条都是"漏了不会报错, 只在肉眼上退一步"的东西:
 *
 * - `title` 必须落在 `<h1>` 上. 掉成 `div` 之后页面就没有标题了 (NotFoundView 在本批之前正是
 *   这样), 而屏幕阅读器与"跳到主内容"这类导航靠的就是它.
 * - 不传 `description` 时那个 `<p>` **不许渲染**: 留一个空段落, `mt-1` 会凭空多出一段间距.
 * - 三个插槽的**位置**比它们的内容重要: `leading` 在标题之前 (返回链接跑到标题后面是纯视觉回归),
 *   `badges` 在标题之后同一行. 故这里断言的是 DOM 顺序, 不是"插槽内容渲染了".
 */

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import PageHeader from './PageHeader.vue';

describe('PageHeader', () => {
  it('title 渲染在 h1 上', () => {
    const wrapper = mount(PageHeader, { props: { title: '回测运行' } });

    expect(wrapper.find('h1').text()).toBe('回测运行');
  });

  it('不传 description 时不渲染描述行', () => {
    const wrapper = mount(PageHeader, { props: { title: '回测运行' } });

    expect(wrapper.find('p').exists()).toBe(false);
  });

  it('description 渲染在标题下方', () => {
    const wrapper = mount(PageHeader, {
      props: { title: '新建回测', description: '选好策略与版本后提交' },
    });

    expect(wrapper.find('p').text()).toBe('选好策略与版本后提交');
  });

  it('leading 在标题之前, badges 在标题之后', () => {
    const wrapper = mount(PageHeader, {
      props: { title: '策略详情' },
      slots: {
        leading: '<a href="/strategies">策略列表</a>',
        badges: '<span class="visibility-badge">仅自己可见</span>',
        actions: '<button type="button">删除策略</button>',
      },
    });

    const renderedHtml = wrapper.html();
    expect(renderedHtml.indexOf('策略列表')).toBeLessThan(renderedHtml.indexOf('<h1'));
    expect(renderedHtml.indexOf('<h1')).toBeLessThan(renderedHtml.indexOf('visibility-badge'));
    expect(wrapper.find('button').text()).toBe('删除策略');
  });
});
