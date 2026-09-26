<script setup lang="ts">
/**
 * 策略列表 + 上传入口.
 *
 * 「上传」是折叠在同一个页面里的, 不做单独的 `/strategies/new` 路由: 上传完要看到的正是列表里
 * 多了那一条, 换页会把这一步的反馈切走.
 *
 * 归属人用目录把 id 映成显示名 —— 列表里有别人共享给我的策略, 只显示 32 位 hex 等于没显示.
 */

import { computed, onMounted, ref } from 'vue';
import { ElButton, ElTable, ElTableColumn } from 'element-plus';
import { RouterLink, useRouter } from 'vue-router';

import { fetchStrategies } from '../api/strategies';
import { DEFAULT_PAGE_SIZE } from '../api/types';
import type { PageResponse, Strategy } from '../api/types';
import ContentSkeleton from '../components/ContentSkeleton.vue';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import PaginationToolbar from '../components/PaginationToolbar.vue';
import StatusBadge from '../components/StatusBadge.vue';
import StrategyUploadForm from '../components/StrategyUploadForm.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import { describeApiFailure, showSuccessToast } from '../composables/use-feedback';
import { formatDateTime } from '../domain/format';
import { describeStrategyVisibility } from '../domain/labels';
import { useStrategyCatalogStore } from '../stores/strategy-catalog';
import { useUserDirectoryStore } from '../stores/user-directory';

const router = useRouter();
const strategyCatalog = useStrategyCatalogStore();
const directoryStore = useUserDirectoryStore();

const strategiesPage = ref<PageResponse<Strategy> | null>(null);
const errorMessage = ref<string | null>(null);
const isLoading = ref(true);
const isUploadPanelOpen = ref(false);

const offset = ref(0);
const limit = ref<number>(DEFAULT_PAGE_SIZE);

const strategies = computed(() => strategiesPage.value?.records ?? []);
const totalCount = computed(() => strategiesPage.value?.total ?? 0);

async function refreshStrategies(): Promise<void> {
  // 只有"手上还没有一页数据"才是首屏. 翻页、上传成功后重取都走这里, 那时表格已经有内容, 换成
  // 骨架屏就是"每次翻页闪一下灰条" —— 骨架屏是带动画的, 而本批的判据是"列表数据刷新不加动画".
  isLoading.value = strategiesPage.value === null;
  errorMessage.value = null;

  try {
    const page = await fetchStrategies(offset.value, limit.value);

    strategiesPage.value = page;
    // 新出现的一页里可能有没见过的归属人, 顺手补齐名字.
    await directoryStore.ensureKnown(page.records.map((record) => record.owner_user_id));
  } catch (error) {
    // 页面级 (c 类) 失败: 留在 ErrorBanner 上, 带重试. 不走 toast —— 这里没有"用户刚点的那个动作"
    // 可以回话, 而下一轮轮询/翻页还会再试.
    errorMessage.value = describeApiFailure(error, '加载策略列表失败');
  } finally {
    isLoading.value = false;
  }
}

function goToOffset(nextOffset: number): void {
  offset.value = nextOffset;
  void refreshStrategies();
}

async function handleCreated(strategyId: string): Promise<void> {
  isUploadPanelOpen.value = false;
  // 先弹再跳: toast 挂在 body 上, 不随路由重建, 于是它会跟着用户落到详情页 —— 上传的反馈正好
  // 在"东西真的在那儿"的那一页上. 失败**不在这里报**: 它就是上传表单自己的失败 (名字重复之类),
  // 留在表单里与出错的字段同屏 (a 类).
  showSuccessToast('策略已上传.');
  // 列表缓存与当前页都要失效: 新建的策略按更新时间倒序排在最前, 若停在第二页, 它在上一页.
  await strategyCatalog.refresh();
  offset.value = 0;
  await refreshStrategies();
  await router.push({ name: 'strategy-detail', params: { id: strategyId } });
}

onMounted(() => {
  void refreshStrategies();
});
</script>

<template>
  <section>
    <PageHeader title="策略">
      <template #actions>
        <ElButton
          :type="isUploadPanelOpen ? 'default' : 'primary'"
          @click="isUploadPanelOpen = !isUploadPanelOpen"
        >
          {{ isUploadPanelOpen ? '收起上传表单' : '上传策略' }}
        </ElButton>
      </template>
    </PageHeader>

    <SurfaceCard
      v-if="isUploadPanelOpen"
      class="mb-6"
      title="上传新策略"
    >
      <StrategyUploadForm @created="handleCreated" />
    </SurfaceCard>

    <ErrorBanner
      :message="errorMessage"
      @retry="refreshStrategies"
    />

    <ContentSkeleton v-if="isLoading" />

    <EmptyNotice
      v-else-if="strategies.length === 0"
      message="还没有任何策略"
      hint="点右上角「上传策略」提交第一份策略源码与 manifest"
    />

    <template v-else>
      <!-- 不加 row-key: 与 /runs 同一理由 —— 本表不用选中 / 展开 / 树形, 用不上它. 空表头就是省掉
           label (它没有默认值). 说明那一列用 show-overflow-tooltip: 原来的 `truncate` 是块级
           overflow 裁切, el-table 的单元格是表格布局, 换过来只能靠 EP 自己这套省略 + 悬浮全量. -->
      <div class="overflow-hidden rounded-lg border border-line">
        <ElTable :data="strategies">
          <ElTableColumn label="策略名">
            <template #default="{ row }">
              <span class="text-slate-800">{{ row.name }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn
            label="说明"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ row.description || '—' }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="可见性">
            <template #default="{ row }">
              <StatusBadge v-bind="describeStrategyVisibility(row.visibility_type)" />
            </template>
          </ElTableColumn>

          <ElTableColumn label="归属">
            <template #default="{ row }">
              {{ directoryStore.displayNameFor(row.owner_user_id) }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="更新时间">
            <template #default="{ row }">
              <span class="whitespace-nowrap">{{ formatDateTime(row.updated_at) }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn align="right">
            <template #default="{ row }">
              <RouterLink
                class="text-brand hover:underline"
                :to="{ name: 'strategy-detail', params: { id: row.id } }"
              >
                详情
              </RouterLink>
            </template>
          </ElTableColumn>
        </ElTable>
      </div>

      <PaginationToolbar
        v-model:limit="limit"
        :total="totalCount"
        :offset="offset"
        @update:offset="goToOffset"
      />
    </template>
  </section>
</template>
