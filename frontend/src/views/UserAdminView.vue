<script setup lang="ts">
/**
 * 用户管理 (管理员).
 *
 * 建号时**显示名必填** —— 受限用户目录只列显示名非空的账号 (`routers/users.py` 的
 * `list_user_directory_handler`), 留空建出来的号别人在授权表单里选不到, 表现是「点了没反应」.
 *
 * 停用管理员要过「还剩几个启用的管理员」这一关, 最后一个启用管理员被停用后端回 409; 界面不预先
 * 猜这条规则, 直接把后端的话显示出来 (规则在服务端, 前端再实现一遍就是两份会漂移的规则).
 *
 * 两条反馈通道在这里分得很清楚, 别把它们的 ref 合并:
 *   - 建号是本页第一个表单 (a 类) —— 失败**就地**留在表单上方的 `ErrorBanner` 里, 因为那句话的
 *     读者是正在填这个表单的人; 成功走 toast (账号已经建出来了, 提示条没有"下一步"可指);
 *   - 改账号状态是表单之外的页面级动作 (b 类) —— 成败都走 toast. 它原先也往建号那条 banner 上写,
 *     于是"建号失败"和"停用失败"共用一条消息位, 后者还会覆盖前者.
 */

import { computed, onMounted, ref } from 'vue';
import { ElButton, ElInput, ElOption, ElSelect, ElTable, ElTableColumn } from 'element-plus';

import { createUser, fetchUsers, updateUserStatus } from '../api/users';
import {
  DEFAULT_PAGE_SIZE,
  MAXIMUM_DISPLAY_NAME_LENGTH,
  MAXIMUM_PASSWORD_LENGTH,
  MAXIMUM_USERNAME_LENGTH,
  MINIMUM_PASSWORD_LENGTH,
  MINIMUM_USERNAME_LENGTH,
  USER_TYPES,
} from '../api/types';
import type { PageResponse, User, UserStatus, UserType } from '../api/types';
import ContentSkeleton from '../components/ContentSkeleton.vue';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import PaginationToolbar from '../components/PaginationToolbar.vue';
import StatusBadge from '../components/StatusBadge.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import {
  confirmAction,
  describeApiFailure,
  showFailureToast,
  showSuccessToast,
} from '../composables/use-feedback';
import { formatDateTime } from '../domain/format';
import { describeUserStatus, describeUserType } from '../domain/labels';
import { useSessionStore } from '../stores/session';

const session = useSessionStore();

const usersPage = ref<PageResponse<User> | null>(null);
const errorMessage = ref<string | null>(null);
/** 只管建号失败 (a 类); 改账号状态的失败走 toast. */
const createErrorMessage = ref<string | null>(null);
const isLoading = ref(true);
const isCreatePanelOpen = ref(false);

const offset = ref(0);
const limit = ref<number>(DEFAULT_PAGE_SIZE);

const newUsername = ref('');
const newPassword = ref('');
const newDisplayName = ref('');
const newUserType = ref<UserType>('user');
const isCreating = ref(false);

/**
 * 正在改状态的那一行. 既是行内按钮的忙碌态, 也是"防重复点击"的闸: 请求在飞的时候它非空, 那一行
 * 的按钮因此是禁用的.
 */
const statusChangingUser = ref<User | null>(null);

const users = computed(() => usersPage.value?.records ?? []);
const totalCount = computed(() => usersPage.value?.total ?? 0);

const isCreateDisabled = computed(
  () =>
    isCreating.value ||
    !newUsername.value.trim() ||
    newDisplayName.value.trim() === '' ||
    newPassword.value.length < MINIMUM_PASSWORD_LENGTH,
);

function isCurrentUser(user: User): boolean {
  return session.currentUser !== null && session.currentUser.id === user.id;
}

/** 停用的后果按「是不是你自己」分两说 —— 这条措辞是本页最需要读清楚的一句话. */
function describeStatusChangeConsequence(user: User, nextStatus: UserStatus): string {
  const targetName = user.display_name || user.username;

  if (nextStatus === 'active') {
    return `启用后 ${targetName} 可以立即登录.`;
  }

  return isCurrentUser(user)
    ? '这是你自己的账号. 停用后你手上的令牌立即失效, 下一个请求就会被登出, 且你再也看不到这个页面.'
    : `停用后 ${targetName} 的令牌立即失效, 无法登录; 已有的策略与运行记录不受影响.`;
}

async function refreshUsers(): Promise<void> {
  // 同 `/strategies`: 只有首屏才给骨架屏. 建号成功、改完状态、翻页都要重取, 那些时刻表格里已经有
  // 内容, 换成骨架屏只会闪 —— 骨架屏自己带动画, 而列表数据刷新是不加动画的.
  isLoading.value = usersPage.value === null;
  errorMessage.value = null;

  try {
    usersPage.value = await fetchUsers(offset.value, limit.value);
  } catch (error) {
    errorMessage.value = describeApiFailure(error, '加载用户列表失败');
  } finally {
    isLoading.value = false;
  }
}

function goToOffset(nextOffset: number): void {
  offset.value = nextOffset;
  void refreshUsers();
}

function resetCreateForm(): void {
  newUsername.value = '';
  newPassword.value = '';
  newDisplayName.value = '';
  newUserType.value = 'user';
}

async function submitCreateUser(): Promise<void> {
  if (isCreateDisabled.value) {
    return;
  }

  createErrorMessage.value = null;
  isCreating.value = true;

  try {
    const createdUser = await createUser({
      username: newUsername.value.trim(),
      password: newPassword.value,
      display_name: newDisplayName.value.trim(),
      user_type: newUserType.value,
    });

    resetCreateForm();
    offset.value = 0;
    await refreshUsers();
    // 成功但不收起表单: 连着建几个号是常态, 收起反而要多点一次.
    showSuccessToast(`已创建账号 ${createdUser.display_name}.`);
  } catch (error) {
    createErrorMessage.value = describeApiFailure(error, '建号失败');
  } finally {
    isCreating.value = false;
  }
}

async function changeUserStatus(user: User): Promise<void> {
  const nextStatus: UserStatus = user.status === 'active' ? 'disabled' : 'active';

  const isConfirmed = await confirmAction({
    title: nextStatus === 'active' ? '启用账号' : '停用账号',
    message: describeStatusChangeConsequence(user, nextStatus),
    confirmLabel: nextStatus === 'active' ? '启用' : '停用',
    isDangerous: nextStatus === 'disabled',
  });

  if (!isConfirmed) {
    return;
  }

  statusChangingUser.value = user;

  try {
    await updateUserStatus(user.id, nextStatus);
    await refreshUsers();
    showSuccessToast(
      `${user.display_name || user.username} 已${nextStatus === 'active' ? '启用' : '停用'}.`,
    );
  } catch (error) {
    // 后端那条「最后一个启用的管理员不能被停用」的规则会在这里以 409 的原文出现 —— 照抄后端的话,
    // 前端不重写一遍规则.
    showFailureToast(describeApiFailure(error, '更新账号状态失败'));
  } finally {
    statusChangingUser.value = null;
  }
}

onMounted(async () => {
  await refreshUsers();

  // 一个账号都没有时, 这里是唯一的下一步; 有账号时把版面留给列表.
  if (totalCount.value === 0) {
    isCreatePanelOpen.value = true;
  }
});
</script>

<template>
  <section>
    <PageHeader title="用户管理">
      <template #actions>
        <ElButton
          :type="isCreatePanelOpen ? 'default' : 'primary'"
          @click="isCreatePanelOpen = !isCreatePanelOpen"
        >
          {{ isCreatePanelOpen ? '收起建号表单' : '新建账号' }}
        </ElButton>
      </template>
    </PageHeader>

    <SurfaceCard
      v-if="isCreatePanelOpen"
      class="mb-6"
      title="新建账号"
    >
      <form
        class="space-y-4"
        @submit.prevent="submitCreateUser"
      >
        <div class="grid gap-4 sm:grid-cols-2">
          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="new-user-username"
            >登录名</label>
            <!-- minlength / maxlength / autocomplete 都是 el-input 声明过的 prop, 它会把它们
                 绑到内层原生 <input> 上 (已在 2.14.6 的产物上核实: 内层元素的属性表里这三个都在,
                 且排在透传的 attrs 之后, 属性值胜出). -->
            <ElInput
              id="new-user-username"
              v-model="newUsername"
              type="text"
              autocomplete="off"
              :minlength="MINIMUM_USERNAME_LENGTH"
              :maxlength="MAXIMUM_USERNAME_LENGTH"
            />
            <span class="text-xs text-slate-400">
              {{ MINIMUM_USERNAME_LENGTH }}–{{ MAXIMUM_USERNAME_LENGTH }} 个字符, 不可重复
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="new-user-display-name"
            >显示名</label>
            <ElInput
              id="new-user-display-name"
              v-model="newDisplayName"
              type="text"
              :maxlength="MAXIMUM_DISPLAY_NAME_LENGTH"
            />
            <span class="text-xs text-slate-400">
              必填: 授权表单里就是按它认人
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="new-user-password"
            >初始口令</label>
            <ElInput
              id="new-user-password"
              v-model="newPassword"
              type="password"
              autocomplete="new-password"
              :minlength="MINIMUM_PASSWORD_LENGTH"
              :maxlength="MAXIMUM_PASSWORD_LENGTH"
            />
            <span class="text-xs text-slate-400">
              至少 {{ MINIMUM_PASSWORD_LENGTH }} 位
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="new-user-type"
            >账号类型</label>
            <ElSelect
              id="new-user-type"
              v-model="newUserType"
            >
              <ElOption
                v-for="userType in USER_TYPES"
                :key="userType"
                :label="describeUserType(userType)"
                :value="userType"
              />
            </ElSelect>
          </div>
        </div>

        <ElButton
          type="primary"
          native-type="submit"
          :loading="isCreating"
          :disabled="isCreateDisabled"
        >
          {{ isCreating ? '创建中…' : '创建账号' }}
        </ElButton>
      </form>
    </SurfaceCard>

    <ErrorBanner
      :message="errorMessage"
      @retry="refreshUsers"
    />

    <!-- 建号失败 (a 类) 就地显示: 这句话的读者正是正在填这张表单的人. 改账号状态的失败不在这里. -->
    <ErrorBanner
      :message="createErrorMessage"
      :is-retry-visible="false"
    />

    <ContentSkeleton v-if="isLoading" />

    <EmptyNotice
      v-else-if="users.length === 0"
      message="平台上还没有账号"
      hint="点右上角「新建账号」建第一个"
    />

    <template v-else>
      <!-- 不加 row-key: 与 /runs、/strategies 同一理由 (本表不用选中 / 展开 / 树形). 行内那个按钮
           的忙碌态绑在**具体那一行**上: `statusChangingUser` 非空时全表按钮都禁用 (防连点),
           但只有它指向的那一行转圈. -->
      <div class="overflow-hidden rounded-lg border border-line">
        <ElTable :data="users">
          <ElTableColumn label="显示名">
            <template #default="{ row }">
              <span class="text-slate-800">{{ row.display_name || '—' }}</span>
              <span
                v-if="session.currentUser?.id === row.id"
                class="ml-1 text-xs text-slate-400"
              >(你)</span>
            </template>
          </ElTableColumn>

          <ElTableColumn label="登录名">
            <template #default="{ row }">
              {{ row.username }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="类型">
            <template #default="{ row }">
              {{ describeUserType(row.user_type) }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="状态">
            <template #default="{ row }">
              <StatusBadge v-bind="describeUserStatus(row.status)" />
            </template>
          </ElTableColumn>

          <ElTableColumn label="建号时间">
            <template #default="{ row }">
              <span class="whitespace-nowrap">{{ formatDateTime(row.created_at) }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn align="right">
            <template #default="{ row }">
              <!-- `row` 的类型是 el-table 自己的行泛型 (一个宽泛的记录), 直接传给吃 `User` 的函数
                   过不了类型检查; 这里断言成 `User` —— 声明处 `:data="users"` 已经保证它就是. -->
              <ElButton
                size="small"
                :disabled="statusChangingUser !== null"
                :loading="statusChangingUser?.id === row.id"
                @click="changeUserStatus(row as User)"
              >
                {{ row.status === 'active' ? '停用' : '启用' }}
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
  </section>
</template>
