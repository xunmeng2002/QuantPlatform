<script setup lang="ts">
/**
 * 版面卡片: 全站"一个区块长什么样"的唯一定义.
 *
 * 本批之前 `rounded-lg border border-line bg-surface` 这一串在 17 处出现、9 种变体, 内边距更有
 * p-3/p-4/p-5/p-6 四种并存. 抽成组件的全部价值在"改一处, 全站跟着变".
 *
 * 卡片只靠**描边**, 不叠 `shadow-sm`: 描边与极浅阴影同时上会互相削弱, 边缘看着发糊. 阴影留给真正
 * 浮起来的东西 (下拉 / 弹窗 / toast, 见 `style.css` 里那几档 `--el-box-shadow*`).
 *
 * 两个 prop 是为了不把语义元素硬掰成 `section`:
 *   - `tag`: 卡片本体渲染成什么元素. 信息栏是 `dl` (它的 `grid` 类必须落在**根**上), 深色输出
 *     面板是 `pre`, 表单分组是 `fieldset` (它的 `legend` 必须是直接子节点) —— 一律套 `<section>`
 *     会把语义丢掉.
 *   - `isBodyPadded`: 少数区块自带内边距 (内部还有一层带滚动的面板), 关掉它.
 *
 * 正文包裹层**只在有标题时**出现: 标题行要横贯整卡 (它的 `border-b` 顶到圆角处), 正文才缩进,
 * 一个元素做不到两件事. 因此 `dl`/`pre` 这类元素只能用无标题形态 —— 把 `dt` 套进 `div` 会让
 * `<dl>` 失去直接子元素, 那是无效 HTML.
 */

import { computed, useSlots } from 'vue';

const props = withDefaults(
  defineProps<{
    title?: string;
    description?: string;
    tag?: string;
    isBodyPadded?: boolean;
  }>(),
  {
    title: undefined,
    description: undefined,
    tag: 'section',
    isBodyPadded: true,
  },
);

const slots = useSlots();

const hasHeader = computed(
  () => props.title !== undefined || slots['header-actions'] !== undefined,
);
</script>

<template>
  <component
    :is="tag"
    class="rounded-lg border border-line bg-surface"
    :class="!hasHeader && isBodyPadded ? 'p-5' : undefined"
  >
    <div
      v-if="hasHeader"
      class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3"
    >
      <div class="min-w-0">
        <h2
          v-if="title"
          class="text-sm font-semibold text-slate-700"
        >
          {{ title }}
        </h2>
        <p
          v-if="description"
          class="mt-0.5 text-xs text-slate-500"
        >
          {{ description }}
        </p>
      </div>

      <slot name="header-actions" />
    </div>

    <div
      v-if="hasHeader"
      :class="isBodyPadded ? 'p-5' : undefined"
    >
      <slot />
    </div>
    <slot v-else />
  </component>
</template>
