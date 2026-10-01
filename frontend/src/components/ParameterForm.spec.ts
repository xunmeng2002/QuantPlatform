// @vitest-environment jsdom
/**
 * ParameterForm 的输入链回归用例.
 *
 * 钉死的是 2026-09-26 那个静默 bug: 模板里写成 `@update:model-value="createParameterUpdater(descriptor.key)"`
 * 时, Vue 把它当**内联语句**编译成 `($event) => createParameterUpdater(descriptor.key)` —— 每个事件
 * 都造一个闭包再丢掉, 值永远冒不到父状态. 症状是输入框看着能打字, 提交时却全被判成没填, 控制台一声不响.
 *
 * 换成 Element Plus 之后**同一个 bug 换了一副面孔**: EP 的 `change` / `input` 载荷不再是 DOM 事件
 * 而是**值本身**, 于是「照抄旧写法」会以另一种方式踩空. 故这里逐条盯住三种控件各自上抛的形状: 文本
 * 与数字都是**字符串**, 复选框是**布尔量** —— `coerceParameterInput` 按这个契约收值, 差一格就是
 * 静默的空值.
 */

import { enableAutoUnmount, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import type { Ref } from 'vue';

import ParameterForm from './ParameterForm.vue';
import type { ParameterDescriptor, ParameterInput } from '../domain/strategy-configuration';

// el-checkbox 一类组件会在子树上挂东西, 不拆掉就会串场: 后一个用例的选择器会把前一个的节点也算进去.
enableAutoUnmount(afterEach);

const ACCOUNT_DESCRIPTOR: ParameterDescriptor = {
  key: 'AccountId',
  type: 'string',
  defaultValue: '模板占位账号',
};

const GRID_COUNT_DESCRIPTOR: ParameterDescriptor = {
  key: 'GridCount',
  type: 'number',
  defaultValue: 5,
};

const HEDGING_DESCRIPTOR: ParameterDescriptor = {
  key: 'IsHedging',
  type: 'boolean',
  defaultValue: false,
};

/**
 * 挂载 ParameterForm, 并像页面那样把它的 `update:modelValue` 回灌成新的 prop —— 不回灌的话
 * 「改一项不冲掉另一项」根本测不出来 (第二次编辑会从陈旧的 prop 重新展开, 顺手抹掉第一次).
 *
 * 末尾那个 `nextTick` 不能省: EP 是把 `<label for>` 指向的那个 `id` 交给 `useFormItemInputId`
 * 生成的, 而它**在 `onMounted` 里才第一次求值** (`/hooks/use-form-item`). 首帧渲染出来时内层控件
 * 上还没有 `id`, 要等那次重渲染 —— 浏览器里是一瞬间, 测试里就是"找不到元素".
 */
async function mountParameterForm(
  descriptors: ParameterDescriptor[],
  initialInputs: Record<string, ParameterInput>,
): Promise<{ inputs: Ref<Record<string, ParameterInput>>; wrapper: VueWrapper }> {
  const inputs = ref<Record<string, ParameterInput>>(initialInputs);

  const host = defineComponent({
    name: 'ParameterFormHost',
    setup: () => () =>
      h(ParameterForm, {
        descriptors,
        errors: {},
        modelValue: inputs.value,
        'onUpdate:modelValue': (value: Record<string, ParameterInput>) => {
          inputs.value = value;
        },
      }),
  });

  const wrapper = mount(host, { attachTo: document.body });

  await nextTick();

  return { inputs, wrapper };
}

describe('ParameterForm 的输入链', () => {
  it('敲进引擎账号之后, 父状态里的值是敲进去的那个', async () => {
    const { inputs, wrapper } = await mountParameterForm(
      [GRID_COUNT_DESCRIPTOR, ACCOUNT_DESCRIPTOR],
      { GridCount: '5', AccountId: '模板占位账号' },
    );

    await wrapper.find('#parameter-AccountId').setValue('18511899894');

    expect(inputs.value.AccountId).toBe('18511899894');
    expect(inputs.value.GridCount).toBe('5');
  });

  it('改一项不冲掉另一项', async () => {
    const { inputs, wrapper } = await mountParameterForm(
      [GRID_COUNT_DESCRIPTOR, ACCOUNT_DESCRIPTOR],
      { GridCount: '5', AccountId: '18511899894' },
    );

    await wrapper.find('#parameter-GridCount').setValue('7');

    expect(inputs.value.GridCount).toBe('7');
    expect(inputs.value.AccountId).toBe('18511899894');
  });

  it('数值在输入框里也是字符串, 不因 type=number 变成数字', async () => {
    const { inputs, wrapper } = await mountParameterForm(
      [GRID_COUNT_DESCRIPTOR],
      { GridCount: '5' },
    );

    await wrapper.find('#parameter-GridCount').setValue('12');

    expect(inputs.value.GridCount).toBe('12');
  });

  it('复选框上抛的是布尔量本身, 不是 EP 也能给的字符串', async () => {
    const { inputs, wrapper } = await mountParameterForm(
      [HEDGING_DESCRIPTOR],
      { IsHedging: false },
    );

    await wrapper.find('#parameter-IsHedging').setValue(true);

    expect(inputs.value.IsHedging).toBe(true);
  });
});
