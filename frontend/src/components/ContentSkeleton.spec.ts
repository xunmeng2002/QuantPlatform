// @vitest-environment jsdom
/**
 * ContentSkeleton 的契约用例.
 *
 * 两条:
 *
 * - `role="status"` 与 `aria-label` 必须在**包裹层**, 而且不许落在 `el-skeleton` 自己身上. 它把
 *   `$attrs` 同时合并到加载分支与真实内容分支的根元素上, 挂上去的话 `loading` 一翻 false, 这个角色
 *   就跟着跑到真实内容上. 而 `el-skeleton` 自身没有任何 aria 属性 —— 只给 `role` 不给标签, 播报是
 *   空的, 那比它替掉的"加载中…"三个字还退了一步.
 * - `is-animated` 这个类也在钉: `style.css` 的 `prefers-reduced-motion` 块正是按
 *   `.el-skeleton.is-animated .el-skeleton__item` 关掉流光动画的, 类名一变那条规则就静默失效.
 */

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import ContentSkeleton from './ContentSkeleton.vue';

describe('ContentSkeleton', () => {
  it('role="status" 与 aria-label 在包裹层, el-skeleton 上没有', () => {
    const wrapper = mount(ContentSkeleton);

    expect(wrapper.element.getAttribute('role')).toBe('status');
    expect(wrapper.element.getAttribute('aria-label')).toBe('加载中');
    expect(wrapper.find('.el-skeleton').attributes('role')).toBeUndefined();
  });

  it('默认五行, rows 可覆盖', () => {
    expect(mount(ContentSkeleton).findAll('.el-skeleton__paragraph')).toHaveLength(5);
    expect(
      mount(ContentSkeleton, { props: { rows: 2 } }).findAll('.el-skeleton__paragraph'),
    ).toHaveLength(2);
  });

  it('带 is-animated, 骨架屏的流光才是开的', () => {
    const wrapper = mount(ContentSkeleton);

    expect(wrapper.find('.el-skeleton').classes()).toContain('is-animated');
  });
});
