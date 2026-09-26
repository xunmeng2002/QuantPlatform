<script setup lang="ts">
/**
 * 列表页底部的分页工具条: 左边「每页」, 右边 `PaginationBar`.
 *
 * 三个列表页 (`/runs`、`/strategies`、`/users`) 的这段逐字相同, 唯一容易写错的地方是 **emit 的
 * 顺序**: 改每页时先上抛 `update:limit`, 再上抛 `update:offset`(0). 页面是靠 `update:offset`
 * 触发重新取数的, 顺序反过来的话那次请求带的还是旧的页大小 —— 界面不报错, 只是一页仍然十条.
 * 顺序是跨事件的, 由 spec 从**父组件侧**按到达先后钉住.
 *
 * 另一个坑: 改每页必须把 offset 归零. 停在第 3 页把每页从 10 改成 50, 第 3 页就已经不存在了.
 */

import { ElOption, ElSelect } from 'element-plus';

import { PAGE_SIZE_OPTIONS } from '../api/types';
import PaginationBar from './PaginationBar.vue';

const props = defineProps<{
  total: number;
  offset: number;
  limit: number;
}>();

const emit = defineEmits<{
  'update:limit': [limit: number];
  'update:offset': [offset: number];
}>();

function changePageSize(nextLimit: number): void {
  emit('update:limit', nextLimit);
  emit('update:offset', 0);
}
</script>

<template>
  <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-2 text-sm text-slate-600">
      <span>每页</span>
      <!-- 原生 select 的包裹式 <label> 在 el-select 上不成立 (它的根节点不是 <input>), 故改用
           aria-label 给控件一个有名字的可访问名: 光有旁边那两个字, 读屏是念不出来的. -->
      <ElSelect
        :model-value="props.limit"
        class="w-20"
        aria-label="每页条数"
        @change="changePageSize"
      >
        <ElOption
          v-for="pageSize in PAGE_SIZE_OPTIONS"
          :key="pageSize"
          :label="String(pageSize)"
          :value="pageSize"
        />
      </ElSelect>
    </div>

    <PaginationBar
      :total="props.total"
      :offset="props.offset"
      :limit="props.limit"
      @update:offset="emit('update:offset', $event)"
    />
  </div>
</template>
