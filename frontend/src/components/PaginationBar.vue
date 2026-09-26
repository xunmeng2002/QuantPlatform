<script setup lang="ts">
/**
 * 分页条.
 *
 * 只看 `total` / `offset` / `limit` 三个数 (后端 `PageResponse` 恰好就是这三个), 不认识具体
 * 资源. 上一页 / 下一页之外不做跳页: 运行数上千之后才需要, 那时再说 —— 所以 `layout` 必须显式
 * 钉成 `prev, next`. EP 的默认 layout 里还带 pager / jumper / total, 放着不管会多出页码按钮、
 * 跳页输入框, 以及**第二条**条数文案 (EP 自己的措辞, 与下面那句重复).
 *
 * `prev-text` / `next-text` 给的是按钮上的文字: 不传的话 EP 只画一个箭头图标.
 *
 * 对外契约不变 —— 上抛的仍是 **offset, 不是页码**.
 */

import { computed } from 'vue';
import { ElPagination } from 'element-plus';

const props = defineProps<{
  total: number;
  offset: number;
  limit: number;
}>();

const emit = defineEmits<{
  'update:offset': [offset: number];
}>();

const firstVisibleIndex = computed(() =>
  props.total === 0 ? 0 : props.offset + 1,
);
const lastVisibleIndex = computed(() =>
  Math.min(props.offset + props.limit, props.total),
);

/**
 * el-pagination 的页码从 1 起, 本组件的 offset 从 0 起 —— 这里是两者**唯一**的换算处.
 *
 * 用 `v-model:current-page` 而不是单向绑定: EP 内部持有页码 ref, 只传 prop 不回写会让两边脱节
 * (它对「传了 current-page 却没有更新监听」还有一条 dev 期告警).
 */
const currentPage = computed({
  get: () => (props.limit > 0 ? Math.floor(props.offset / props.limit) + 1 : 1),
  set: (page: number) => emit('update:offset', (page - 1) * props.limit),
});
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-3 py-3 text-sm text-slate-600">
    <span>
      第 {{ firstVisibleIndex }}–{{ lastVisibleIndex }} 条, 共 {{ total }} 条
    </span>
    <ElPagination
      v-model:current-page="currentPage"
      layout="prev, next"
      prev-text="上一页"
      next-text="下一页"
      :total="total"
      :page-size="limit"
    />
  </div>
</template>
