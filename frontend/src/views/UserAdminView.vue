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

import { createUser, fetchUsers, updateUserStatus } from '../api/users';
import {
  DEFAULT_PAGE_SIZE,
  MAXIMUM_DISPLAY_NAME_LENGTH,
  MAXIMUM_PASSWORD_LENGTH,
  MAXIMUM_USERNAME_LENGTH,
  MINIMUM_PASSWORD_LENGTH,
  MINIMUM_USERNAME_LENGTH,
  PAGE_SIZE_OPTIONS,
  USER_TYPES,
} from '../api/types';
import type { PageResponse, User, UserStatus, UserType } from '../api/types';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import LoadingNotice from '../components/LoadingNotice.vue';
import PaginationBar from '../components/PaginationBar.vue';
import StatusBadge from '../components/StatusBadge.vue';
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
  isLoading.value = true;
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

function handlePageSizeChange(): void {
  offset.value = 0;
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
    <header class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h1 class="text-lg font-semibold text-slate-900">用户管理</h1>
      <button
        type="button"
        class="rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong"
        @click="isCreatePanelOpen = !isCreatePanelOpen"
      >
        {{ isCreatePanelOpen ? '收起建号表单' : '新建账号' }}
      </button>
    </header>

    <section
      v-if="isCreatePanelOpen"
      class="mb-6 rounded-lg border border-line bg-surface p-4"
    >
      <h2 class="mb-4 text-sm font-semibold text-slate-700">
        新建账号
      </h2>

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
            <input
              id="new-user-username"
              v-model="newUsername"
              type="text"
              autocomplete="off"
              class="rounded border border-line px-2 py-1.5 text-sm"
              :minlength="MINIMUM_USERNAME_LENGTH"
              :maxlength="MAXIMUM_USERNAME_LENGTH"
            >
            <span class="text-xs text-slate-400">
              {{ MINIMUM_USERNAME_LENGTH }}–{{ MAXIMUM_USERNAME_LENGTH }} 个字符, 不可重复
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="new-user-display-name"
            >显示名</label>
            <input
              id="new-user-display-name"
              v-model="newDisplayName"
              type="text"
              class="rounded border border-line px-2 py-1.5 text-sm"
              :maxlength="MAXIMUM_DISPLAY_NAME_LENGTH"
            >
            <span class="text-xs text-slate-400">
              必填: 授权表单里就是按它认人
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="new-user-password"
            >初始口令</label>
            <input
              id="new-user-password"
              v-model="newPassword"
              type="password"
              autocomplete="new-password"
              class="rounded border border-line px-2 py-1.5 text-sm"
              :minlength="MINIMUM_PASSWORD_LENGTH"
              :maxlength="MAXIMUM_PASSWORD_LENGTH"
            >
            <span class="text-xs text-slate-400">
              至少 {{ MINIMUM_PASSWORD_LENGTH }} 位
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="new-user-type"
            >账号类型</label>
            <select
              id="new-user-type"
              v-model="newUserType"
              class="rounded border border-line bg-surface px-2 py-1.5 text-sm"
            >
              <option
                v-for="userType in USER_TYPES"
                :key="userType"
                :value="userType"
              >
                {{ describeUserType(userType) }}
              </option>
            </select>
          </div>
        </div>

        <button
          type="submit"
          class="rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong disabled:opacity-50"
          :disabled="isCreateDisabled"
        >
          {{ isCreating ? '创建中…' : '创建账号' }}
        </button>
      </form>
    </section>

    <ErrorBanner
      :message="errorMessage"
      @retry="refreshUsers"
    />

    <!-- 建号失败 (a 类) 就地显示: 这句话的读者正是正在填这张表单的人. 改账号状态的失败不在这里. -->
    <ErrorBanner
      :message="createErrorMessage"
      :is-retry-visible="false"
    />

    <LoadingNotice v-if="isLoading" />

    <EmptyNotice
      v-else-if="users.length === 0"
      message="平台上还没有账号"
      hint="点右上角「新建账号」建第一个"
    />

    <template v-else>
      <div class="overflow-x-auto rounded-lg border border-line bg-surface">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th class="px-3 py-2 font-medium">
                显示名
              </th>
              <th class="px-3 py-2 font-medium">
                登录名
              </th>
              <th class="px-3 py-2 font-medium">
                类型
              </th>
              <th class="px-3 py-2 font-medium">
                状态
              </th>
              <th class="px-3 py-2 font-medium">
                建号时间
              </th>
              <th class="px-3 py-2 font-medium" />
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr
              v-for="user in users"
              :key="user.id"
              class="hover:bg-slate-50"
            >
              <td class="px-3 py-2 text-slate-800">
                {{ user.display_name || '—' }}
                <span
                  v-if="session.currentUser?.id === user.id"
                  class="ml-1 text-xs text-slate-400"
                >(你)</span>
              </td>
              <td class="px-3 py-2 text-slate-600">
                {{ user.username }}
              </td>
              <td class="px-3 py-2 text-slate-600">
                {{ describeUserType(user.user_type) }}
              </td>
              <td class="px-3 py-2">
                <StatusBadge v-bind="describeUserStatus(user.status)" />
              </td>
              <td class="px-3 py-2 whitespace-nowrap text-slate-600">
                {{ formatDateTime(user.created_at) }}
              </td>
              <td class="px-3 py-2 text-right">
                <button
                  type="button"
                  class="rounded border border-line px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
                  :disabled="statusChangingUser !== null"
                  @click="changeUserStatus(user)"
                >
                  {{ user.status === 'active' ? '停用' : '启用' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
        <label class="flex items-center gap-2 text-sm text-slate-600">
          每页
          <select
            v-model.number="limit"
            class="rounded border border-line bg-surface px-2 py-1 text-sm"
            @change="handlePageSizeChange"
          >
            <option
              v-for="pageSize in PAGE_SIZE_OPTIONS"
              :key="pageSize"
              :value="pageSize"
            >
              {{ pageSize }}
            </option>
          </select>
        </label>
        <PaginationBar
          :total="totalCount"
          :offset="offset"
          :limit="limit"
          @update:offset="goToOffset"
        />
      </div>
    </template>
  </section>
</template>
