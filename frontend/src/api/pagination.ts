/**
 * 一次把某个分页端点取完.
 *
 * 界面上有几处要的是"一个下拉框的全部选项" (可见策略、已登记品种), 而不是"某一页". 逐页拼装在
 * 前端写成循环是同一段逻辑的反复抄写, 而它最容易被抄漏的是**终止条件**: 少一个判据就是对着同
 * 一个 offset 无限请求下去.
 *
 * 这里不并发翻页: 页数不多 (上限见下), 而并发会让"取了几页"变得依赖响应次序.
 */

import { MAXIMUM_PAGE_SIZE } from './types';
import type { PageResponse } from './types';

/**
 * 翻页上限.
 *
 * 兼作死循环的保险: 后端若把 `total` 报得比实际记录数大, `offset >= total` 永远不成立, 没有这个
 * 上限就会一直请求下去.
 */
const MAXIMUM_PAGE_COUNT = 20;

export async function collectAllPages<RecordShape>(
  fetchPage: (offset: number, limit: number) => Promise<PageResponse<RecordShape>>,
): Promise<RecordShape[]> {
  const collectedRecords: RecordShape[] = [];
  let offset = 0;

  for (let pageIndex = 0; pageIndex < MAXIMUM_PAGE_COUNT; pageIndex += 1) {
    const page = await fetchPage(offset, MAXIMUM_PAGE_SIZE);

    collectedRecords.push(...page.records);
    offset += page.records.length;

    if (page.records.length === 0 || offset >= page.total) {
      break;
    }
  }

  return collectedRecords;
}
