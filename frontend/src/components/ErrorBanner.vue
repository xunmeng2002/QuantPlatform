<script setup lang="ts">
/**
 * 失败提示条.
 *
 * 每条消息都可以带一个「重试」: 本项目的失败几乎都是「点一下就好了」(令牌过期后重登、后端刚
 * 起来), 让用户自己去猜重进哪个页面是最没必要的摩擦.
 *
 * el-alert 只负责消息, 外层那个 flex 包裹层继续管布局: el-alert 只有 title / default 两个插槽,
 * 没有放操作按钮的位置, 把按钮塞进 default 会让它从「右侧垂直居中」掉到标题下面.
 *
 * 三点都写在代码里而不是留给下一次踩:
 *
 * 1. **包裹层上不能加 `role="alert"`**. el-alert 的根节点自己就带这个角色 (EP 的源码里是硬编码
 *    的 `role: "alert"`, 已在 2.14.6 的产物上核实); 外面再套一个就成了嵌套的两块 live region,
 *    播报可能重复. 曾经那版自绘的提示条需要在包裹层上自己加, 换库之后这一条要**去掉**.
 * 2. `:closable="false"` 必须显式关掉 —— 它默认开着, 而关闭只翻组件内部的可见标志, 父层的
 *    `message` 还在, 于是留下「消息被关掉了但状态还说有错」这种自相矛盾的界面.
 * 3. `isRetryVisible` 必须给出**显式的默认值 `true`**: Vue 对布尔 prop 有一条隐式转换, 传参缺省时
 *    落下来的是 `false` 而不是 `undefined`, 于是"没传"和"明确要隐藏"变成同一件事 —— 每个不传它的
 *    调用点都会静默地失去重试按钮 (本文件在 2026-09-26 之前就是这样).
 */

import { ElAlert } from 'element-plus';

withDefaults(
  defineProps<{
    message: string | null;
    retryLabel?: string;
    isRetryVisible?: boolean;
  }>(),
  { isRetryVisible: true },
);

const emit = defineEmits<{
  retry: [];
}>();
</script>

<template>
  <div
    v-if="message"
    class="mb-4 flex flex-wrap items-center justify-between gap-3"
  >
    <ElAlert
      class="min-w-0 flex-1"
      type="error"
      :title="message"
      :closable="false"
    />
    <button
      v-if="isRetryVisible"
      type="button"
      class="rounded border border-rose-400 px-2 py-1 text-xs font-medium text-rose-800 hover:bg-rose-100"
      @click="emit('retry')"
    >
      {{ retryLabel ?? '重试' }}
    </button>
  </div>
</template>
