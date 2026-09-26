// @vitest-environment jsdom
/**
 * ParameterForm 的输入链回归用例.
 *
 * 钉死的是 2026-09-26 那个静默 bug: 模板里写成 `@update:model-value="createParameterUpdater(descriptor.key)"`
 * 时, Vue 把它当**内联语句**编译成 `($event) => createParameterUpdater(descriptor.key)` —— 每个事件
 * 都造一个闭包再丢掉, 值永远冒不到父状态. 症状是输入框看着能打字、提交时却全是「必填」, 控制台一声不响.
 *
 * 换成 Element Plus 之后**同一个 bug 换了一副面孔**: EP 的 `change` / `input` 载荷不再是 DOM 事件
 * 而是**值本身**, 于是「照抄旧写法」会以另一种方式踩空. 第三条用例因此专门盯着契约 1 里最容易写错的
 * 那一项: 带选项的参数上抛的必须是**选项下标的字符串**.
 *
 * 写成数字时不会崩 —— `coerceParameterInput` 的第一道 `typeof rawInput !== 'string'` 直接把它判成
 * 「没填」, 界面上只是那一项永远显示「请选择」. 这正是需要用例而不是需要 review 的那类错误.
 */

import { enableAutoUnmount, mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { ElOption, ElSelect } from 'element-plus';
import { afterEach, describe, expect, it } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import type { Ref } from 'vue';

import ParameterForm from './ParameterForm.vue';
import type { ParameterDescriptor, ParameterInput } from '../domain/manifest';

// el-select 的下拉 teleport 到 document.body, 于是它会**活过**单个用例. 不拆掉就会串场:
// 后一个用例的 `document.querySelectorAll('.el-select-dropdown__item')` 会把前一个的选项也算进去.
enableAutoUnmount(afterEach);

const ACCOUNT_DESCRIPTOR: ParameterDescriptor = {
  key: 'AccountId',
  label: '引擎账号',
  type: 'string',
  isRequired: true,
  defaultValue: null,
  options: [],
  minimum: null,
  maximum: null,
  group: '',
};

const GRID_COUNT_DESCRIPTOR: ParameterDescriptor = {
  key: 'GridCount',
  label: '网格数量',
  type: 'integer',
  isRequired: false,
  defaultValue: 5,
  options: [],
  minimum: 1,
  maximum: null,
  group: '',
};

const ENGINE_KIND_DESCRIPTOR: ParameterDescriptor = {
  key: 'EngineKind',
  label: '引擎类型',
  type: 'string',
  isRequired: false,
  defaultValue: null,
  options: [
    { label: '逐笔', value: 'tick' },
    { label: 'K 线', value: 'bar' },
  ],
  minimum: null,
  maximum: null,
  group: '',
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

/** 打开下拉并取回它的选项节点 (在 document.body 里, 不在 wrapper 的子树里). */
async function openSelectDropdown(wrapper: VueWrapper): Promise<HTMLElement[]> {
  await wrapper.find('.el-select__wrapper').trigger('click');
  await nextTick();

  return Array.from(
    document.querySelectorAll<HTMLElement>('.el-select-dropdown__item'),
  );
}

describe('ParameterForm 的输入链', () => {
  it('敲进引擎账号之后, 父状态里的值是敲进去的那个', async () => {
    const { inputs, wrapper } = await mountParameterForm(
      [GRID_COUNT_DESCRIPTOR, ACCOUNT_DESCRIPTOR],
      { GridCount: '5', AccountId: '' },
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

  it('带选项的参数上抛的是选项下标的字符串', async () => {
    const { inputs, wrapper } = await mountParameterForm(
      [ENGINE_KIND_DESCRIPTOR],
      { EngineKind: '' },
    );

    const dropdownItems = await openSelectDropdown(wrapper);

    expect(dropdownItems).toHaveLength(2);

    // `:value="String(optionIndex)"` 写成 `:value="optionIndex"` 时, 这一行是唯一会红的地方:
    // 数字 `0` 与字符串 `'0'` 在 `coerceParameterInput` 里是两个完全不同的命运.
    const optionValues = wrapper
      .findComponent(ElSelect)
      .findAllComponents(ElOption)
      .map((option) => option.props('value'));

    expect(optionValues).toEqual(['0', '1']);

    dropdownItems[1].click();
    await nextTick();

    expect(inputs.value.EngineKind).toBe('1');
  });
});
