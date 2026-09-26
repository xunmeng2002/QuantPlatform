<script setup lang="ts">
/**
 * 策略详情: 版本清单、新版本上传、授权编辑器与软删.
 *
 * 三个写操作 (传新版本 / 改授权 / 删除) **只对归属人显示**: 被授权人拿到的是同一份详情, 但授权
 * 列表是空数组 (后端有意如此, 不是 403), 给他一个点了必然 403 的按钮才是更糟的界面.
 *
 * manifest 的每个版本都留一份可展开的**参数预览** —— 提交表单是按它生成的, 在这里就能看出来
 * 下一轮回测会长出哪些控件.
 *
 * 三个写操作都走 `use-feedback.ts`: 删除与保存授权是表单之外的页面级动作 (b 类), 成败都弹 toast;
 * 上传新版本的成功提示也在这一层 (`handleVersionUploaded`) —— 它那一支失败留在上传表单自己身上.
 */

import { computed, onMounted, ref } from 'vue';
import { ElButton, ElOption, ElSelect } from 'element-plus';
import { RouterLink, useRouter } from 'vue-router';

import { deleteStrategy, fetchStrategyDetail, replaceStrategyGrants } from '../api/strategies';
import { GRANT_PERMISSIONS } from '../api/types';
import type {
  GrantPermission,
  StrategyDetail,
  StrategyGrantPayload,
  StrategyVersion,
  UserDirectoryEntry,
} from '../api/types';
import ContentSkeleton from '../components/ContentSkeleton.vue';
import DirectoryPicker from '../components/DirectoryPicker.vue';
import EmptyNotice from '../components/EmptyNotice.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import PageHeader from '../components/PageHeader.vue';
import StatusBadge from '../components/StatusBadge.vue';
import StrategyVersionUploadForm from '../components/StrategyVersionUploadForm.vue';
import SurfaceCard from '../components/SurfaceCard.vue';
import {
  confirmAction,
  describeApiFailure,
  showFailureToast,
  showSuccessToast,
} from '../composables/use-feedback';
import { formatDateTime } from '../domain/format';
import { describeGrantPermission, describeStrategyVisibility } from '../domain/labels';
import { deriveParameterDescriptors, parseStrategyManifest } from '../domain/manifest';
import type { ParameterDescriptor } from '../domain/manifest';
import { useSessionStore } from '../stores/session';
import { useUserDirectoryStore } from '../stores/user-directory';

const props = defineProps<{ id: string }>();

const router = useRouter();
const session = useSessionStore();
const directoryStore = useUserDirectoryStore();

const strategyDetail = ref<StrategyDetail | null>(null);
/** 只剩页面级加载失败 (c 类); 删除失败已经改走 toast —— 两件事共用一个 ref 会让提示互相覆盖. */
const errorMessage = ref<string | null>(null);
/** 只剩「这个人已经在名单里了」这一条就地提示; 保存授权的得失走 toast. */
const grantErrorMessage = ref<string | null>(null);
const isLoading = ref(true);
const isSavingGrants = ref(false);
/** 删除期间禁用右上角那个按钮 (弹窗里的「处理中…」没有了, 忙碌态挪到这里). */
const isDeleting = ref(false);
const grantDraft = ref<StrategyGrantPayload[]>([]);

const strategy = computed(() => strategyDetail.value?.strategy ?? null);

interface VersionRow {
  version: StrategyVersion;
  descriptors: ParameterDescriptor[];
  manifestProblem: string | null;
}

/**
 * 每个版本的 manifest 只解析一次.
 *
 * 模板里逐处调用「解析这个版本」看着更直接, 但一次渲染会解析四五遍 (长度、有没有坏、参数字段),
 * 而且这三处判断分散在模板里很难保证彼此一致.
 */
const versionRows = computed<VersionRow[]>(() =>
  (strategyDetail.value?.versions ?? []).map((version) => {
    const manifestResult = parseStrategyManifest(version.manifest_json);

    return {
      version,
      descriptors: manifestResult.ok
        ? deriveParameterDescriptors(manifestResult.manifest)
        : [],
      manifestProblem: manifestResult.ok ? null : manifestResult.message,
    };
  }),
);

const isOwner = computed(
  () =>
    strategy.value !== null &&
    session.currentUser !== null &&
    strategy.value.owner_user_id === session.currentUser.id,
);

/** 草稿与已保存的名单只要有一处不同就算脏; 顺序无关, 故先归一化再比. */
const isGrantDraftDirty = computed(() => {
  const savedGrants = strategyDetail.value?.grants ?? [];

  if (savedGrants.length !== grantDraft.value.length) {
    return true;
  }

  const savedSignature = new Map(
    savedGrants.map((grant) => [grant.grantee_user_id, grant.permission_type]),
  );

  return grantDraft.value.some(
    (draftGrant) =>
      savedSignature.get(draftGrant.grantee_user_id) !== draftGrant.permission_type,
  );
});

async function loadStrategyDetail(): Promise<void> {
  // 只有"还没有东西可看"时才是首屏加载. 传完新版本也要走这里, 那时页面上已有整份详情, 把整页
  // 换成骨架屏等于让一次成功的上传闪一下灰条 (同 `/runs` 那条"轮询不换表格"的判据).
  isLoading.value = strategyDetail.value === null;
  errorMessage.value = null;

  try {
    const detail = await fetchStrategyDetail(props.id);

    strategyDetail.value = detail;
    grantDraft.value = detail.grants.map((grant) => ({
      grantee_user_id: grant.grantee_user_id,
      permission_type: grant.permission_type,
    }));
    await directoryStore.ensureKnown([
      detail.strategy.owner_user_id,
      ...detail.grants.map((grant) => grant.grantee_user_id),
    ]);
  } catch (error) {
    errorMessage.value = describeApiFailure(error, '加载策略详情失败');
  } finally {
    isLoading.value = false;
  }
}

async function handleVersionUploaded(): Promise<void> {
  showSuccessToast('新版本已上传.');
  await loadStrategyDetail();
}

function addGrant(entry: UserDirectoryEntry): void {
  grantErrorMessage.value = null;

  if (grantDraft.value.some((grant) => grant.grantee_user_id === entry.id)) {
    grantErrorMessage.value = `${entry.display_name} 已经在授权名单里了.`;

    return;
  }

  directoryStore.rememberEntries([entry]);
  grantDraft.value = [
    ...grantDraft.value,
    { grantee_user_id: entry.id, permission_type: 'read' },
  ];
}

function removeGrant(granteeUserId: string): void {
  grantDraft.value = grantDraft.value.filter(
    (grant) => grant.grantee_user_id !== granteeUserId,
  );
}

/**
 * 名单是数组, 元素不能直接 `v-model`, 故由模板传「谁」、事件传「改成什么」两半拼起来.
 *
 * 第二参是 `ElSelect` 的 `@change` 载荷 —— 它是**值本身**, 不是 DOM 事件 (原手写 `<select>` 那份
 * 代码读的是 `event.target.value`). 照抄旧签名会在运行时炸.
 */
function changeGrantPermission(
  granteeUserId: string,
  permission: GrantPermission,
): void {
  grantDraft.value = grantDraft.value.map((grant) =>
    grant.grantee_user_id === granteeUserId
      ? { grantee_user_id: grant.grantee_user_id, permission_type: permission }
      : grant,
  );
}

async function saveGrants(): Promise<void> {
  grantErrorMessage.value = null;
  isSavingGrants.value = true;

  try {
    strategyDetail.value = await replaceStrategyGrants(props.id, grantDraft.value);
    showSuccessToast('授权名单已保存.');
  } catch (error) {
    // 保存授权是表单之外的页面级动作 (b 类): 失败走 toast, 不再占 `grantErrorMessage` —— 那个 ref
    // 留给「这个人已经在名单里了」这条就地提示, 两件事共用会让后者被覆盖掉.
    showFailureToast(describeApiFailure(error, '保存授权失败'));
  } finally {
    isSavingGrants.value = false;
  }
}

async function deleteStrategyWithConfirmation(): Promise<void> {
  const isConfirmed = await confirmAction({
    title: '删除这个策略',
    message: '策略会被标记为已删除, 不再出现在列表与提交页; 已有的运行记录仍指向它。这一步不可撤销。',
    confirmLabel: '删除策略',
    isDangerous: true,
  });

  if (!isConfirmed) {
    return;
  }

  isDeleting.value = true;

  try {
    await deleteStrategy(props.id);
    // 先弹再跳: toast 挂在 body 上, 不随路由重建, 于是它跟着用户落到列表页 ——
    // 那句话的读者正好在「那一条不见了」的那一页上.
    showSuccessToast('策略已删除.');
    await router.push({ name: 'strategies' });
  } catch (error) {
    // 失败留在本页: 策略还在, 页面也还在, 用户能重试.
    showFailureToast(describeApiFailure(error, '删除失败'));
  } finally {
    isDeleting.value = false;
  }
}

onMounted(() => {
  void loadStrategyDetail();
});
</script>

<template>
  <section>
    <PageHeader :title="strategy?.name ?? '策略详情'">
      <template #leading>
        <RouterLink
          class="text-sm text-brand hover:underline"
          :to="{ name: 'strategies' }"
        >
          ← 策略列表
        </RouterLink>
      </template>

      <template #badges>
        <StatusBadge
          v-if="strategy"
          v-bind="describeStrategyVisibility(strategy.visibility_type)"
        />
      </template>

      <template #actions>
        <ElButton
          v-if="isOwner"
          type="danger"
          plain
          :loading="isDeleting"
          @click="deleteStrategyWithConfirmation"
        >
          {{ isDeleting ? '删除中…' : '删除策略' }}
        </ElButton>
      </template>
    </PageHeader>

    <ErrorBanner
      :message="errorMessage"
      @retry="loadStrategyDetail"
    />

    <ContentSkeleton v-if="isLoading" />

    <template v-else-if="strategy">
      <!-- 无 title 的卡片: 主体是 slot 的唯一子节点, 故 tag="dl" 合法 (dl 的直接子节点只能是
           div/dt/dd —— 让 SurfaceCard 自己在中间插一层 div 就非法了). -->
      <SurfaceCard
        tag="dl"
        class="mb-6 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2"
      >
        <div class="flex gap-2">
          <dt class="w-20 shrink-0 text-slate-500">
            策略 ID
          </dt>
          <dd class="break-all text-slate-700">
            {{ strategy.id }}
          </dd>
        </div>
        <div class="flex gap-2">
          <dt class="w-20 shrink-0 text-slate-500">
            归属人
          </dt>
          <dd class="break-all text-slate-700">
            {{ directoryStore.displayNameFor(strategy.owner_user_id) }}
          </dd>
        </div>
        <div class="flex gap-2">
          <dt class="w-20 shrink-0 text-slate-500">
            创建时间
          </dt>
          <dd class="text-slate-700">
            {{ formatDateTime(strategy.created_at) }}
          </dd>
        </div>
        <div class="flex gap-2">
          <dt class="w-20 shrink-0 text-slate-500">
            更新时间
          </dt>
          <dd class="text-slate-700">
            {{ formatDateTime(strategy.updated_at) }}
          </dd>
        </div>
        <div
          v-if="strategy.description"
          class="flex gap-2 sm:col-span-2"
        >
          <dt class="w-20 shrink-0 text-slate-500">
            说明
          </dt>
          <dd class="text-slate-700">
            {{ strategy.description }}
          </dd>
        </div>
      </SurfaceCard>

      <SurfaceCard
        class="mb-6"
        :title="`版本 (${versionRows.length})`"
      >
        <EmptyNotice
          v-if="versionRows.length === 0"
          message="这个策略还没有版本"
          hint="下面的「上传新版本」提交第一份源码与 manifest"
        />

        <ul
          v-else
          class="space-y-3"
        >
          <li
            v-for="(versionRow, versionIndex) in versionRows"
            :key="versionRow.version.id"
            class="rounded-md border border-line p-4"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-sm font-medium text-slate-800">v{{ versionRow.version.version_no }}</span>
                <StatusBadge
                  v-if="versionIndex === 0"
                  label="最新"
                  tone="progress"
                />
                <span class="text-xs text-slate-500">{{ versionRow.version.entry_filename }}</span>
                <span class="text-xs text-slate-400">/ {{ versionRow.version.config_filename }}</span>
              </div>
              <span class="text-xs text-slate-400">{{ formatDateTime(versionRow.version.uploaded_at) }}</span>
            </div>

            <p class="mt-1 text-xs text-slate-400">
              内容指纹 {{ versionRow.version.source_hash.slice(0, 12) }}
            </p>

            <details class="mt-3">
              <summary class="cursor-pointer text-xs text-brand">
                参数预览
              </summary>

              <p
                v-if="versionRow.manifestProblem"
                class="mt-2 text-xs text-amber-700"
              >
                {{ versionRow.manifestProblem }} — 该版本无法生成提交表单.
              </p>

              <p
                v-else-if="versionRow.descriptors.length === 0"
                class="mt-2 text-xs text-slate-400"
              >
                该版本的 manifest 没有声明任何参数.
              </p>

              <table
                v-else
                class="mt-2 w-full text-xs"
              >
                <thead class="text-left text-slate-500">
                  <tr>
                    <th class="py-1 pr-3 font-medium">
                      键
                    </th>
                    <th class="py-1 pr-3 font-medium">
                      标签
                    </th>
                    <th class="py-1 pr-3 font-medium">
                      类型
                    </th>
                    <th class="py-1 pr-3 font-medium">
                      缺省值
                    </th>
                    <th class="py-1 pr-3 font-medium">
                      范围 / 选项
                    </th>
                    <th class="py-1 font-medium">
                      分组
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-line">
                  <tr
                    v-for="descriptor in versionRow.descriptors"
                    :key="descriptor.key"
                  >
                    <td class="py-1 pr-3 font-mono text-slate-700">
                      {{ descriptor.key }}
                    </td>
                    <td class="py-1 pr-3 text-slate-700">
                      {{ descriptor.label }}
                    </td>
                    <td class="py-1 pr-3 text-slate-500">
                      {{ descriptor.type }}{{ descriptor.isRequired ? ' · 必填' : '' }}
                    </td>
                    <td class="py-1 pr-3 text-slate-500">
                      {{ descriptor.isRequired ? '—' : String(descriptor.defaultValue) }}
                    </td>
                    <td class="py-1 pr-3 text-slate-500">
                      <template v-if="descriptor.options.length > 0">
                        {{ descriptor.options.map((option) => option.label).join(' / ') }}
                      </template>
                      <template v-else-if="descriptor.minimum !== null || descriptor.maximum !== null">
                        {{ descriptor.minimum ?? '—' }} ~ {{ descriptor.maximum ?? '—' }}
                      </template>
                      <template v-else>
                        —
                      </template>
                    </td>
                    <td class="py-1 text-slate-500">
                      {{ descriptor.group || '—' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </details>
          </li>
        </ul>
      </SurfaceCard>

      <SurfaceCard
        v-if="isOwner"
        class="mb-6"
        title="上传新版本"
      >
        <StrategyVersionUploadForm
          :strategy-id="props.id"
          @uploaded="handleVersionUploaded"
        />
      </SurfaceCard>

      <SurfaceCard
        v-if="isOwner"
        class="mb-6"
        :title="`授权 (${grantDraft.length})`"
        description="保存时整体替换名单: 从下面移除的人, 保存后就失去了这份策略."
      >
        <ErrorBanner
          :message="grantErrorMessage"
          :is-retry-visible="false"
        />

        <EmptyNotice
          v-if="grantDraft.length === 0"
          message="还没有授权给任何人"
          hint="下面的目录里按显示名搜人, 选中后加入名单"
        />

        <ul
          v-else
          class="mb-4 divide-y divide-line"
        >
          <li
            v-for="grant in grantDraft"
            :key="grant.grantee_user_id"
            class="flex flex-wrap items-center justify-between gap-2 py-2"
          >
            <span class="text-sm text-slate-700">
              {{ directoryStore.displayNameFor(grant.grantee_user_id) }}
            </span>
            <div class="flex items-center gap-2">
              <!-- el-select 默认铺满容器, 在 flex 行里会把「移除」挤出去, 故给一个显式宽度.
                   载荷是值不是事件 (见 `changeGrantPermission` 的说明). -->
              <ElSelect
                :model-value="grant.permission_type"
                class="w-28"
                @change="changeGrantPermission(grant.grantee_user_id, $event)"
              >
                <ElOption
                  v-for="permission in GRANT_PERMISSIONS"
                  :key="permission"
                  :label="describeGrantPermission(permission)"
                  :value="permission"
                />
              </ElSelect>
              <ElButton
                type="danger"
                plain
                size="small"
                @click="removeGrant(grant.grantee_user_id)"
              >
                移除
              </ElButton>
            </div>
          </li>
        </ul>

        <DirectoryPicker @select="addGrant" />

        <div class="mt-4 flex items-center gap-3">
          <ElButton
            type="primary"
            :loading="isSavingGrants"
            :disabled="isSavingGrants || !isGrantDraftDirty"
            @click="saveGrants"
          >
            {{ isSavingGrants ? '保存中…' : '保存授权' }}
          </ElButton>
          <span
            v-if="isGrantDraftDirty"
            class="text-xs text-amber-700"
          >有未保存的改动</span>
          <span
            v-else
            class="text-xs text-slate-400"
          >与已保存的名单一致</span>
        </div>
      </SurfaceCard>
    </template>
  </section>
</template>
