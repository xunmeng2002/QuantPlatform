/**
 * 可见策略的缓存 (id → 策略).
 *
 * 运行列表与运行详情里只有 `strategy_id`, 而界面上要显示策略名 —— 没有这份缓存, 列表里就是
 * 一串 32 位 hex. 后端没有「按 id 批量取策略名」的端点, 故一次把可见策略全部取回来, 之后查表.
 */

import { computed, ref } from 'vue';
import { defineStore } from 'pinia';

import { fetchStrategies } from '../api/strategies';
import { MAXIMUM_PAGE_SIZE } from '../api/types';
import type { Strategy } from '../api/types';

/**
 * 翻页上限.
 *
 * 兼作死循环的保险: 后端若把 `total` 报得比实际记录数大, `offset >= total` 永远不成立,
 * 没有这个上限就会一直请求下去.
 */
const MAXIMUM_CATALOG_PAGE_COUNT = 20;

export const useStrategyCatalogStore = defineStore('strategy-catalog', () => {
  const strategies = ref<Strategy[]>([]);
  const hasLoaded = ref(false);
  const isLoading = ref(false);

  const strategiesById = computed(
    () => new Map(strategies.value.map((strategy) => [strategy.id, strategy])),
  );

  /** 供列表页直接调用: 已经载过就不重复请求. */
  async function ensureLoaded(): Promise<void> {
    if (hasLoaded.value || isLoading.value) {
      return;
    }

    await refresh();
  }

  async function refresh(): Promise<void> {
    isLoading.value = true;

    try {
      const collectedStrategies: Strategy[] = [];
      let offset = 0;

      for (let pageIndex = 0; pageIndex < MAXIMUM_CATALOG_PAGE_COUNT; pageIndex += 1) {
        const page = await fetchStrategies(offset, MAXIMUM_PAGE_SIZE);

        collectedStrategies.push(...page.records);
        offset += page.records.length;

        if (page.records.length === 0 || offset >= page.total) {
          break;
        }
      }

      strategies.value = collectedStrategies;
      hasLoaded.value = true;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * 策略名, 查不到就回落到 id.
   *
   * 授权被撤销后该策略不再可见, 而历史运行仍指着它 —— 那时显示 id 比显示空白有用.
   */
  function nameFor(strategyId: string): string {
    return strategiesById.value.get(strategyId)?.name ?? strategyId;
  }

  return { strategies, hasLoaded, isLoading, ensureLoaded, refresh, nameFor };
});
