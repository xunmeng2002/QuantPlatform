/**
 * 基础数据三张表共用的列表骨架: 取数、翻页、建单 / 改单、删除, 以及各自的失败留痕.
 *
 * 抽它的直接原因是三个面板各写一遍这段的话, 同样的三十来行会重复三次 (Harness §5), 而其中
 * 最容易写歪的两处都在"看不见"的地方:
 *   - **只有首屏才给骨架屏**. 建完单、翻完页都要重取, 那些时刻表格里已经有内容, 换成骨架屏只会
 *     闪一下 —— 骨架屏自带呼吸动画, 而列表刷新是不带动画的;
 *   - **`limit` 与 `offset` 的联动**. 改每页必须把 offset 归零, 否则停在第 3 页把每页从 20 改成
 *     100 时, 第 3 页早就不存在了. 这条由 `PaginationToolbar` 保证 (它先上抛 `update:limit`,
 *     再上抛 `update:offset` 0), 故这里只须老实响应 `update:offset`.
 *
 * 表单是**一份两用**: 同一个表单先当"新建", 点某一行的「编辑」后装进那一行的取值、按钮变成
 * 「保存本行」. 两套表单各写一遍的话, 日后加一列只会改到其中一处, 而那一处自己是绿的 —— 与
 * 后端"建单改单共用一份 WriteRequest"是同一条理由.
 *
 * 三条反馈通道分开, 别合并 ref (规则同 `use-feedback` 的文件头):
 *   - 加载失败 → `errorMessage`, 走列表上方的 `ErrorBanner` (c 类);
 *   - 表单提交失败 → `submitErrorMessage`, 就地留在表单里 (a 类) —— 那句话的读者正是正在填这张
 *     表单的人;
 *   - 删除是表单之外的页面级动作 (b 类) → 成败都走 toast.
 */

import { computed, ref } from 'vue';
import type { ComputedRef, Ref } from 'vue';

import type { MessageResponse, PageResponse } from '../api/types';
import { DEFAULT_PAGE_SIZE } from '../api/types';
import {
  confirmAction,
  describeApiFailure,
  showFailureToast,
  showSuccessToast,
} from './use-feedback';

/** 泛型只约束到这里: 删除要拿 `id`, toast 要拿它描述删掉的是哪一行. */
interface IdentifiedRecord {
  id: string;
}

export interface ReferenceTableOptions<
  RecordShape extends IdentifiedRecord,
  PayloadShape,
> {
  fetchPage(offset: number, limit: number): Promise<PageResponse<RecordShape>>;
  createRecord(payload: PayloadShape): Promise<RecordShape>;
  updateRecord(recordId: string, payload: PayloadShape): Promise<RecordShape>;
  deleteRecord(recordId: string): Promise<MessageResponse>;
  /**
   * 从当前表单调出请求体; **表单还没填全时回 `null`**.
   *
   * 让"填全了吗"与"请求体长什么样"共用这一个函数, 是为了不让两处判据各说各话: 一个按钮按某一
   * 组条件亮起来、提交时按另一组条件放行, 症状是"按钮亮着但点了没反应".
   */
  buildPayload(): PayloadShape | null;
  /** 清空表单. 退出编辑态时由本模块调, 故它不该顺带复位 `editingRecord`. */
  resetForm(): void;
  /** 一行数据的说法, 用于成功提示 (`已新建 …` / `已保存 …` / `已删除 …`). */
  describeRecord(record: RecordShape): string;
  describeDeleteQuestion(record: RecordShape): string;
  loadFailureMessage: string;
  submitFailureMessage: string;
  deleteFailureMessage: string;
}

export interface ReferenceTable<RecordShape extends IdentifiedRecord> {
  records: ComputedRef<RecordShape[]>;
  totalCount: ComputedRef<number>;
  isLoading: Ref<boolean>;
  errorMessage: Ref<string | null>;
  offset: Ref<number>;
  limit: Ref<number>;
  /** 非空表示正在编辑哪一行; 表单标题与提交按钮的措辞都看它. */
  editingRecord: Ref<RecordShape | null>;
  submitErrorMessage: Ref<string | null>;
  isSubmitting: Ref<boolean>;
  /** 正在删的那一行. 非空时全表按钮禁用 (防连点), 但只有它指向的那一行转圈. */
  deletingRecordId: Ref<string | null>;
  refresh(): Promise<void>;
  goToOffset(nextOffset: number): void;
  beginEditing(record: RecordShape): void;
  cancelEditing(): void;
  submitForm(): Promise<void>;
  removeRecord(record: RecordShape): Promise<void>;
}

export function useReferenceTable<
  RecordShape extends IdentifiedRecord,
  PayloadShape,
>(
  options: ReferenceTableOptions<RecordShape, PayloadShape>,
): ReferenceTable<RecordShape> {
  const page = ref<PageResponse<RecordShape> | null>(null) as Ref<PageResponse<RecordShape> | null>;
  const isLoading = ref(true);
  const errorMessage = ref<string | null>(null);

  const offset = ref(0);
  const limit = ref<number>(DEFAULT_PAGE_SIZE);

  const editingRecord = ref<RecordShape | null>(null) as Ref<RecordShape | null>;
  const submitErrorMessage = ref<string | null>(null);
  const isSubmitting = ref(false);

  const deletingRecordId = ref<string | null>(null);

  const records = computed(() => page.value?.records ?? []);
  const totalCount = computed(() => page.value?.total ?? 0);

  async function refresh(): Promise<void> {
    isLoading.value = page.value === null;
    errorMessage.value = null;

    try {
      page.value = await options.fetchPage(offset.value, limit.value);
    } catch (error) {
      errorMessage.value = describeApiFailure(error, options.loadFailureMessage);
    } finally {
      isLoading.value = false;
    }
  }

  function goToOffset(nextOffset: number): void {
    offset.value = nextOffset;
    void refresh();
  }

  function beginEditing(record: RecordShape): void {
    editingRecord.value = record;
    submitErrorMessage.value = null;
  }

  function cancelEditing(): void {
    editingRecord.value = null;
    submitErrorMessage.value = null;
    options.resetForm();
  }

  async function submitForm(): Promise<void> {
    const payload = options.buildPayload();

    if (payload === null || isSubmitting.value) {
      return;
    }

    const recordBeingEdited = editingRecord.value;

    submitErrorMessage.value = null;
    isSubmitting.value = true;

    try {
      if (recordBeingEdited === null) {
        const createdRecord = await options.createRecord(payload);

        showSuccessToast(`已新建 ${options.describeRecord(createdRecord)}.`);
        // 新行落在哪一页由它的自然键决定, 可能不在当前页; 改单则原地不动 —— 用户刚看完那一行,
        // 把他弹回第一页是没有理由的.
        offset.value = 0;
      } else {
        const updatedRecord = await options.updateRecord(
          recordBeingEdited.id,
          payload,
        );

        showSuccessToast(`已保存 ${options.describeRecord(updatedRecord)}.`);
      }

      cancelEditing();

      await refresh();
    } catch (error) {
      submitErrorMessage.value = describeApiFailure(error, options.submitFailureMessage);
    } finally {
      isSubmitting.value = false;
    }
  }

  async function removeRecord(record: RecordShape): Promise<void> {
    const isConfirmed = await confirmAction({
      title: '删除',
      message: options.describeDeleteQuestion(record),
      confirmLabel: '删除',
      isDangerous: true,
    });

    if (!isConfirmed) {
      return;
    }

    deletingRecordId.value = record.id;

    try {
      await options.deleteRecord(record.id);

      // 删掉的正是表单里那一行时, 表单得跟着退出来 —— 留着它, 下一次点「保存本行」会去打一个
      // 已经不存在的主键, 而后端回的是 404「不存在」, 用户看不出那是因为自己刚把它删了.
      if (editingRecord.value?.id === record.id) {
        cancelEditing();
      }

      showSuccessToast(`已删除 ${options.describeRecord(record)}.`);

      await refresh();
    } catch (error) {
      showFailureToast(describeApiFailure(error, options.deleteFailureMessage));
    } finally {
      deletingRecordId.value = null;
    }
  }

  return {
    records,
    totalCount,
    isLoading,
    errorMessage,
    offset,
    limit,
    editingRecord,
    submitErrorMessage,
    isSubmitting,
    deletingRecordId,
    refresh,
    goToOffset,
    beginEditing,
    cancelEditing,
    submitForm,
    removeRecord,
  };
}
