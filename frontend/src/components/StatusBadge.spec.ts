// @vitest-environment jsdom
/**
 * StatusBadge 的契约用例 (契约 3).
 *
 * 它只吃 `label` + `tone` 两个展示用的入参 —— 中文与色调的出处是 `domain/run-status.ts`, 组件里
 * 一个枚举都不认识. 于是这里钉三件事:
 *
 * 1. 五个色调与 el-tag 的五个 type **一一对上** (EP 的 type 取值集恰好也是五个, 少一个就会在
 *    界面上悄悄退化成默认色);
 * 2. 文案原样透传 —— 谁在组件里加一层翻译或映射, 这里就红;
 * 3. 文字色挂在我们自己那层 span 上, 不靠 el-tag 出厂调色板的色阶 (那套对比度只有 2.0-2.6:1).
 */

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import StatusBadge from './StatusBadge.vue';
import { describeEngineVerdict, describeRunStatus } from '../domain/run-status';
import type { StatusTone } from '../domain/run-status';

/** 逐项列出而不是从组件里 import 那张表: 用例要能独立地判定"这个映射对不对". */
const TONE_TAG_TYPES: ReadonlyArray<[StatusTone, string]> = [
  ['neutral', 'info'],
  ['progress', 'primary'],
  ['success', 'success'],
  ['danger', 'danger'],
  ['warning', 'warning'],
];

describe('StatusBadge', () => {
  it.each(TONE_TAG_TYPES)('色调 %s 落在 el-tag--%s 上', (tone, tagType) => {
    const wrapper = mount(StatusBadge, { props: { label: '示例', tone } });

    expect(wrapper.find('.el-tag').classes()).toContain(`el-tag--${tagType}`);
  });

  it('文案由 domain 决定, 组件一个字不改', () => {
    const succeeded = describeRunStatus('succeeded');
    const wrapper = mount(StatusBadge, { props: succeeded });

    expect(wrapper.text()).toBe(succeeded.label);
    expect(wrapper.text()).toBe('已成功');
  });

  it('引擎没给出结论是中性色调, 不是失败', () => {
    const silent = describeEngineVerdict(null);
    const wrapper = mount(StatusBadge, { props: silent });

    expect(wrapper.find('.el-tag').classes()).toContain('el-tag--info');
    expect(wrapper.text()).toBe('引擎未给出结论');
  });

  it('文字色在我们自己那层 span 上, 与色调一起给出', () => {
    const wrapper = mount(StatusBadge, { props: { label: '已失败', tone: 'danger' } });
    const badgeText = wrapper.find('.el-tag__content > span');

    expect(badgeText.classes()).toContain('text-rose-700');
    expect(wrapper.find('.el-tag').classes()).not.toContain('text-rose-700');
  });
});
