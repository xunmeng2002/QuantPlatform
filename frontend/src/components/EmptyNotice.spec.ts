// @vitest-environment jsdom
/**
 * EmptyNotice 的契约用例.
 *
 * 最容易写错的一处是**两行放哪个插槽**: el-empty 的默认插槽不是副标题, 它渲染在描述**下方**
 * 的 `.el-empty__bottom` 里 (只在插槽存在时出现), 视觉上是第三个层级. 主句与注解分开两层之后
 * 就不再像同一句话了, 所以两条断言一起钉: 都在 `#description` 里, 且 `__bottom` 压根不存在.
 *
 * 顺带钉住插画尺寸 —— 自带的那张不压小的话, 一个空态会占掉半屏.
 */

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import EmptyNotice from './EmptyNotice.vue';

describe('EmptyNotice', () => {
  it('两行都在 description 插槽里, 不落进 __bottom', () => {
    const wrapper = mount(EmptyNotice, {
      props: { message: '还没有任何回测运行', hint: '点右上角「新建回测」提交第一次运行' },
    });

    const description = wrapper.find('.el-empty__description');

    expect(description.text()).toContain('还没有任何回测运行');
    expect(description.text()).toContain('点右上角「新建回测」提交第一次运行');
    expect(wrapper.find('.el-empty__bottom').exists()).toBe(false);
  });

  it('没有 hint 时只有一行', () => {
    const wrapper = mount(EmptyNotice, { props: { message: '还没有任何回测运行' } });

    expect(wrapper.findAll('.el-empty__description p')).toHaveLength(1);
  });

  it('两行是不同的灰阶, 不是同一档', () => {
    const wrapper = mount(EmptyNotice, {
      props: { message: '还没有任何回测运行', hint: '点右上角「新建回测」提交第一次运行' },
    });

    const lines = wrapper.findAll('.el-empty__description p');

    expect(lines[0].classes()).toContain('text-slate-600');
    expect(lines[1].classes()).toContain('text-slate-400');
  });

  it('插画被压到 72px', () => {
    const wrapper = mount(EmptyNotice, { props: { message: '还没有任何回测运行' } });

    expect(wrapper.find('.el-empty__image').attributes('style')).toContain('width: 72px');
  });
});
