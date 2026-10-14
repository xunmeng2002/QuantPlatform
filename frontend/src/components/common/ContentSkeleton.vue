<script setup lang="ts">
/**
 * 页面首屏的骨架屏.
 *
 * 只给**首屏**用. 轮询刷新 (每 2 秒重取一次) 绝不能换它 —— 列表每两秒整块变灰再变回来是闪烁,
 * 比"加载中…"三个字更糟, 故那些地方继续用 `LoadingNotice`.
 *
 * `role="status"` 与 `aria-label` 挂在**外层包裹**上, 不挂 `ElSkeleton` 自己: 它把 `$attrs` 同时
 * 合并到"加载分支"与"真实内容分支"的根元素上, 翻成 false 之后这个角色会跟着跑到真实内容上去.
 * 而 `el-skeleton` 自身一个 aria 属性都没有 (EP 的 CSS 里 grep 不到), 只给 `role` 不给标签的话
 * 屏幕阅读器播报的是空 —— 那比 `LoadingNotice` 那句"加载中…"还退了一步.
 */

import { ElSkeleton } from 'element-plus';

withDefaults(defineProps<{ rows?: number }>(), { rows: 5 });
</script>

<template>
  <div
    role="status"
    aria-label="加载中"
  >
    <ElSkeleton
      :rows="rows"
      animated
    />
  </div>
</template>
