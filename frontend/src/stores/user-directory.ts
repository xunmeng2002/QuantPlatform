/**
 * 受限用户目录的缓存 (id → 显示名).
 *
 * 授权的两端都只有 id (`StrategyGrantResponse` 只有 `grantee_user_id`), 界面要显示名字就必须
 * 查目录. 目录是一次一次查出来的, 故把**见过的**条目都留下来: 已选中的被授权人未必出现在当前
 * 这次搜索的结果里.
 *
 * 目录只含**启用中且显示名非空**的账号, 故一个停用过的被授权人查不到 —— 那时 `displayNameFor`
 * 回落到显示 id. 这是已知缺口 (见 PROGRESS.md), 不在这一批里修.
 */

import { ref } from 'vue';
import { defineStore } from 'pinia';

import { fetchUserDirectory } from '../api/users';
import { MAXIMUM_PAGE_SIZE } from '../api/types';
import type { UserDirectoryEntry } from '../api/types';

export const useUserDirectoryStore = defineStore('user-directory', () => {
  const entriesById = ref(new Map<string, string>());
  const searchMatches = ref<UserDirectoryEntry[]>([]);
  const isLoading = ref(false);

  /** 搜索并记住结果. 空 `query` 即列出前若干条, 用作打开选人框时的初始列表. */
  async function search(query: string): Promise<UserDirectoryEntry[]> {
    isLoading.value = true;

    try {
      const page = await fetchUserDirectory(query, 0, MAXIMUM_PAGE_SIZE);

      rememberEntries(page.records);
      searchMatches.value = page.records;

      return page.records;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * 已知的那些 id 里还有没名字的, 就拉一页目录补齐.
   *
   * 只拉一页 (上限 100 条): 补不全的那些回落成显示 id, 与停用账号是同一条已知缺口. 多拉几页
   * 换来的只是"更少见地显示 hex", 不值得让详情页多发几次请求.
   */
  async function ensureKnown(userIds: string[]): Promise<void> {
    const unknownUserIds = userIds.filter(
      (userId) => !entriesById.value.has(userId),
    );

    if (unknownUserIds.length === 0) {
      return;
    }

    await search('');
  }

  function rememberEntries(entries: UserDirectoryEntry[]): void {
    for (const entry of entries) {
      entriesById.value.set(entry.id, entry.display_name);
    }
  }

  function displayNameFor(userId: string): string {
    return entriesById.value.get(userId) ?? userId;
  }

  return {
    searchMatches,
    isLoading,
    search,
    ensureKnown,
    rememberEntries,
    displayNameFor,
  };
});
