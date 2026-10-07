<script setup lang="ts">
/**
 * 手续费组.
 *
 * 组号是**人工指定的整数**, 不是平台生成的自增号: 引擎每轮拿它去费率表里找组, 认的是数字. 自增的
 * 话"1 号组"是哪一行就由插入次序决定, 而用户在新建回测页看到的就是"组号 · 组名"——两组换个号而名
 * 字不动, 下一次提交就会按另一套费率算钱, 界面上却看不出任何变化. 故这里让操作员自己填, 并由唯一
 * 约束守住不重复.
 *
 * 费率明细存的是**组号**而不是本表的主键, 于是改一个还挂着明细的组的号会被外键拒掉 (那些明细
 * 会指向一个不存在的组). 那条路上后端给的是"改组号"的说法, 而不是笼统的重名 —— 见
 * `routers/reference_data.py` 的 `update_commission_group_handler`.
 */

import { onMounted, reactive } from 'vue';
import {
  ElButton,
  ElInput,
  ElInputNumber,
  ElTable,
  ElTableColumn,
} from 'element-plus';

import {
  createCommissionGroup,
  deleteCommissionGroup,
  fetchCommissionGroups,
  updateCommissionGroup,
} from '../api/reference-data';
import { MAXIMUM_COMMISSION_GROUP_NAME_LENGTH } from '../api/types';
import type { CommissionGroup, CommissionGroupPayload } from '../api/types';
import { useReferenceTable } from '../composables/use-reference-table';
import { formatDateTime } from '../domain/format';
import ContentSkeleton from './ContentSkeleton.vue';
import EmptyNotice from './EmptyNotice.vue';
import ErrorBanner from './ErrorBanner.vue';
import PaginationToolbar from './PaginationToolbar.vue';
import SurfaceCard from './SurfaceCard.vue';

interface CommissionGroupForm {
  // 组号没有默认值: 它是个决定 (引擎按这个数字找组), 替用户填一个等于替他认了一个组.
  commissionGroupId: number | undefined;
  commissionGroupName: string;
}

function createEmptyForm(): CommissionGroupForm {
  return { commissionGroupId: undefined, commissionGroupName: '' };
}

const form = reactive<CommissionGroupForm>(createEmptyForm());

const {
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
  beginEditing: markRowAsEditing,
  cancelEditing,
  submitForm,
  removeRecord,
} = useReferenceTable<CommissionGroup, CommissionGroupPayload>({
  fetchPage: fetchCommissionGroups,
  createRecord: createCommissionGroup,
  updateRecord: updateCommissionGroup,
  deleteRecord: deleteCommissionGroup,

  buildPayload() {
    const commissionGroupName = form.commissionGroupName.trim();

    if (form.commissionGroupId === undefined || commissionGroupName === '') {
      return null;
    }

    return {
      commission_group_id: form.commissionGroupId,
      commission_group_name: commissionGroupName,
    };
  },

  resetForm() {
    Object.assign(form, createEmptyForm());
  },

  describeRecord(commissionGroup) {
    return `${commissionGroup.commission_group_id} 号手续费组「${commissionGroup.commission_group_name}」`;
  },

  describeDeleteQuestion(commissionGroup) {
    return (
      `确定删除 ${commissionGroup.commission_group_id} 号手续费组` +
      `「${commissionGroup.commission_group_name}」? 下面还挂着费率明细时删不掉, ` +
      '得先把那些明细删掉 —— 引擎取不到费率就会把手续费按 0 算.'
    );
  },

  loadFailureMessage: '加载手续费组失败',
  submitFailureMessage: '保存手续费组失败',
  deleteFailureMessage: '删除手续费组失败',
});

/** 点「编辑」把那一行的取值装进表单; 表单住的地方与新建是同一个. */
function beginEditing(commissionGroup: CommissionGroup): void {
  form.commissionGroupId = commissionGroup.commission_group_id;
  form.commissionGroupName = commissionGroup.commission_group_name;

  markRowAsEditing(commissionGroup);
}

onMounted(() => {
  void refresh();
});
</script>

<template>
  <div>
    <SurfaceCard
      class="mb-6"
      :title="
        editingRecord === null
          ? '新建手续费组'
          : `编辑 ${editingRecord.commission_group_id} 号手续费组`
      "
    >
      <template #header-actions>
        <ElButton
          v-if="editingRecord !== null"
          size="small"
          @click="cancelEditing()"
        >
          取消编辑
        </ElButton>
      </template>

      <form
        class="space-y-4"
        @submit.prevent="submitForm()"
      >
        <div class="grid gap-4 sm:grid-cols-2">
          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="commission-group-id"
            >组号</label>
            <ElInputNumber
              id="commission-group-id"
              v-model="form.commissionGroupId"
              :min="0"
              :controls="false"
              class="w-full"
            />
            <span class="text-xs text-slate-400">
              引擎按这个数字找组; 现在运行的配置写死的是 1 号
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="commission-group-name"
            >名称</label>
            <ElInput
              id="commission-group-name"
              v-model="form.commissionGroupName"
              type="text"
              :maxlength="MAXIMUM_COMMISSION_GROUP_NAME_LENGTH"
              placeholder="如 A 股默认费率"
            />
          </div>
        </div>

        <span class="text-xs text-slate-400">
          还挂着费率明细的组改不了号, 也删不掉 —— 那会让那些明细指向一个不存在的组
        </span>

        <div>
          <ElButton
            type="primary"
            native-type="submit"
            :loading="isSubmitting"
            :disabled="
              form.commissionGroupId === undefined ||
              form.commissionGroupName.trim() === '' ||
              isSubmitting
            "
          >
            {{ editingRecord === null ? '新建手续费组' : '保存本行' }}
          </ElButton>
        </div>
      </form>
    </SurfaceCard>

    <ErrorBanner
      :message="errorMessage"
      @retry="refresh()"
    />

    <ErrorBanner
      :message="submitErrorMessage"
      :is-retry-visible="false"
    />

    <ContentSkeleton v-if="isLoading" />

    <EmptyNotice
      v-else-if="records.length === 0"
      message="还没有手续费组"
      hint="费率明细必须挂在某个组下面, 先建一个组"
    />

    <template v-else>
      <div class="overflow-hidden rounded-lg border border-line">
        <ElTable :data="records">
          <ElTableColumn
            label="组号"
            align="right"
          >
            <template #default="{ row }">
              {{ row.commission_group_id }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="名称">
            <template #default="{ row }">
              {{ row.commission_group_name }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="录入时间">
            <template #default="{ row }">
              <span class="whitespace-nowrap">{{ formatDateTime(row.created_at) }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn align="right">
            <template #default="{ row }">
              <!-- `row` 是 el-table 自己的行泛型, 断言成 `CommissionGroup` 才能喂给下面那两个函数
                   —— 声明处 `:data` 已经保证它就是. -->
              <ElButton
                size="small"
                :disabled="deletingRecordId !== null"
                @click="beginEditing(row as CommissionGroup)"
              >
                编辑
              </ElButton>
              <ElButton
                size="small"
                type="danger"
                plain
                :disabled="deletingRecordId !== null"
                :loading="deletingRecordId === row.id"
                @click="removeRecord(row as CommissionGroup)"
              >
                删除
              </ElButton>
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
  </div>
</template>
