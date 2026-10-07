<script setup lang="ts">
/**
 * 品种基本数据.
 *
 * 它是引擎的"这个合约一手多少股、最小变动多少钱"的出处: 报价乘数取不到时引擎按 1 折算, 于是
 * 一整轮的资金曲线会平白差掉一个倍数, 而结果文件里只有一个 `VolumeMultipleFallbackProductCount`
 * 能看出来. 故这一页的重点不是"能录进去", 而是**录对**: 交易所与品种代码刻意的窄 (8 / 32 个
 * 字符), 越界在请求层就被拒, 不会写进库再在装载时被引擎截断.
 *
 * 品种类型收成九项下拉而不是自由文本: 那是引擎侧一个完整的枚举 (`Spark/Types.h` 的
 * `ProductClassType`), 而它决定引擎要不要为这个品种造主力合约 (只对期货造).
 */

import { computed, onMounted, reactive } from 'vue';
import {
  ElButton,
  ElInput,
  ElInputNumber,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
} from 'element-plus';

import {
  createProduct,
  deleteProduct,
  fetchProducts,
  updateProduct,
} from '../api/reference-data';
import {
  EXCHANGE_SUGGESTIONS,
  MAXIMUM_PRODUCT_ID_LENGTH,
  MAXIMUM_PRODUCT_NAME_LENGTH,
  MAXIMUM_SESSION_NAME_LENGTH,
  PRODUCT_CLASSES,
} from '../api/types';
import type { Product, ProductClass, ProductPayload } from '../api/types';
import { useReferenceTable } from '../composables/use-reference-table';
import { formatCount, formatDateTime, formatDecimal } from '../domain/format';
import { describeProductClass } from '../domain/labels';
import ContentSkeleton from './ContentSkeleton.vue';
import EmptyNotice from './EmptyNotice.vue';
import ErrorBanner from './ErrorBanner.vue';
import PaginationToolbar from './PaginationToolbar.vue';
import SurfaceCard from './SurfaceCard.vue';

/** 新建时的默认值逐列对齐后端 `ProductWriteRequest`: A 股一手一股、最小变动 0.01 元、股票. */
interface ProductForm {
  exchangeId: string;
  productId: string;
  productName: string;
  productClass: ProductClass;
  // 六个数字框收 `number | undefined` 而不是 `number | null`: `ElInputNumber` 清空时上抛的正是
  // `undefined` (`valueOnClear` 没设), 声明成 `number | null` 会在 `v-model` 的赋值那一侧过不了
  // 类型检查.
  volumeMultiple: number | undefined;
  priceTick: number | undefined;
  maxMarketOrderVolume: number | undefined;
  minMarketOrderVolume: number | undefined;
  maxLimitOrderVolume: number | undefined;
  minLimitOrderVolume: number | undefined;
  sessionName: string;
}

function createEmptyForm(): ProductForm {
  return {
    exchangeId: '',
    productId: '',
    productName: '',
    productClass: 6,
    volumeMultiple: 1,
    priceTick: 0.01,
    maxMarketOrderVolume: 0,
    minMarketOrderVolume: 0,
    maxLimitOrderVolume: 0,
    minLimitOrderVolume: 0,
    sessionName: '',
  };
}

const form = reactive<ProductForm>(createEmptyForm());

/**
 * 表单填全了吗.
 *
 * 空着的数字框 (`undefined`) 与填了 0 是两件事: 后者是一个决定 (「这个品种不受委托量限制」), 前者
 * 只是还没填. 故它与空着的代码框同等对待, 都拦在提交之前 —— 让 0 默填过去会把"没想好"写成"不限".
 */
const isFormComplete = computed(() => {
  const numericValues = [
    form.volumeMultiple,
    form.priceTick,
    form.maxMarketOrderVolume,
    form.minMarketOrderVolume,
    form.maxLimitOrderVolume,
    form.minLimitOrderVolume,
  ];

  return (
    form.exchangeId.trim() !== '' &&
    form.productId.trim() !== '' &&
    form.productName.trim() !== '' &&
    numericValues.every((value) => value !== undefined)
  );
});

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
} = useReferenceTable<Product, ProductPayload>({
  fetchPage: fetchProducts,
  createRecord: createProduct,
  updateRecord: updateProduct,
  deleteRecord: deleteProduct,

  buildPayload() {
    if (!isFormComplete.value) {
      return null;
    }

    return {
      exchange_id: form.exchangeId.trim(),
      product_id: form.productId.trim(),
      product_name: form.productName.trim(),
      product_class: form.productClass,
      volume_multiple: form.volumeMultiple ?? 0,
      price_tick: form.priceTick ?? 0,
      max_market_order_volume: form.maxMarketOrderVolume ?? 0,
      min_market_order_volume: form.minMarketOrderVolume ?? 0,
      max_limit_order_volume: form.maxLimitOrderVolume ?? 0,
      min_limit_order_volume: form.minLimitOrderVolume ?? 0,
      session_name: form.sessionName.trim(),
    };
  },

  resetForm() {
    Object.assign(form, createEmptyForm());
  },

  describeRecord(product) {
    return `品种 ${product.exchange_id} ${product.product_id}`;
  },

  describeDeleteQuestion(product) {
    return (
      `确定删除 ${product.exchange_id} ${product.product_id} ${product.product_name}? ` +
      '此后引擎不再知道它的每手乘数, 用到它的回测会按乘数 1 折算. 既有费率明细不受影响.'
    );
  },

  loadFailureMessage: '加载品种列表失败',
  submitFailureMessage: '保存品种失败',
  deleteFailureMessage: '删除品种失败',
});

/** 点「编辑」把那一行的取值装进表单; 表单住的地方与新建是同一个. */
function beginEditing(product: Product): void {
  form.exchangeId = product.exchange_id;
  form.productId = product.product_id;
  form.productName = product.product_name;
  // 读侧收的是裸数字, 而下拉框只有那九项: 库里出现枚举外的取值时 (只可能来自导入), 这一行显示
  // 在原数字上, 但拿它编辑会被归到某一个合法取值. 这是有意的 —— 那种行本来就该人工处理.
  form.productClass = product.product_class as ProductClass;
  form.volumeMultiple = product.volume_multiple;
  form.priceTick = product.price_tick;
  form.maxMarketOrderVolume = product.max_market_order_volume;
  form.minMarketOrderVolume = product.min_market_order_volume;
  form.maxLimitOrderVolume = product.max_limit_order_volume;
  form.minLimitOrderVolume = product.min_limit_order_volume;
  form.sessionName = product.session_name;

  markRowAsEditing(product);
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
          ? '新建品种'
          : `编辑 ${editingRecord.exchange_id} ${editingRecord.product_id}`
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
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-exchange-id"
            >交易所</label>
            <!-- `filterable` + `allow-create`: 给出常见交易所做建议, 但不封口 —— 引擎侧没有交易所
                 的枚举, 它只拿这几个字符去与成交记录里的对, 收成闭集反而会拦住一个它本来就认的取值. -->
            <ElSelect
              id="product-exchange-id"
              v-model="form.exchangeId"
              filterable
              allow-create
              default-first-option
              placeholder="如 SSE"
            >
              <ElOption
                v-for="exchangeId in EXCHANGE_SUGGESTIONS"
                :key="exchangeId"
                :label="exchangeId"
                :value="exchangeId"
              />
            </ElSelect>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-id"
            >品种代码</label>
            <ElInput
              id="product-id"
              v-model="form.productId"
              type="text"
              :maxlength="MAXIMUM_PRODUCT_ID_LENGTH"
            />
            <span class="text-xs text-slate-400">
              引擎按定长比较, 多一位就是另一个品种
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-name"
            >品种名称</label>
            <ElInput
              id="product-name"
              v-model="form.productName"
              type="text"
              :maxlength="MAXIMUM_PRODUCT_NAME_LENGTH"
            />
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-class"
            >品种类型</label>
            <ElSelect
              id="product-class"
              v-model="form.productClass"
            >
              <ElOption
                v-for="productClass in PRODUCT_CLASSES"
                :key="productClass"
                :label="describeProductClass(productClass)"
                :value="productClass"
              />
            </ElSelect>
            <span class="text-xs text-slate-400">
              只有期货会被引擎造出主力合约
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-volume-multiple"
            >每手乘数</label>
            <ElInputNumber
              id="product-volume-multiple"
              v-model="form.volumeMultiple"
              :min="0"
              :controls="false"
              class="w-full"
            />
            <span class="text-xs text-slate-400">
              取不到时引擎按 1 折算资金曲线
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-price-tick"
            >最小变动价位</label>
            <ElInputNumber
              id="product-price-tick"
              v-model="form.priceTick"
              :min="0"
              :step="0.01"
              :controls="false"
              class="w-full"
            />
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-max-market-volume"
            >市价单上限</label>
            <ElInputNumber
              id="product-max-market-volume"
              v-model="form.maxMarketOrderVolume"
              :min="0"
              :controls="false"
              class="w-full"
            />
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-min-market-volume"
            >市价单下限</label>
            <ElInputNumber
              id="product-min-market-volume"
              v-model="form.minMarketOrderVolume"
              :min="0"
              :controls="false"
              class="w-full"
            />
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-max-limit-volume"
            >限价单上限</label>
            <ElInputNumber
              id="product-max-limit-volume"
              v-model="form.maxLimitOrderVolume"
              :min="0"
              :controls="false"
              class="w-full"
            />
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-min-limit-volume"
            >限价单下限</label>
            <ElInputNumber
              id="product-min-limit-volume"
              v-model="form.minLimitOrderVolume"
              :min="0"
              :controls="false"
              class="w-full"
            />
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="product-session-name"
            >交易时段</label>
            <ElInput
              id="product-session-name"
              v-model="form.sessionName"
              type="text"
              :maxlength="MAXIMUM_SESSION_NAME_LENGTH"
              placeholder="留空即无额外时段"
            />
          </div>
        </div>

        <span class="text-xs text-slate-400">
          四个手数上限填 0 表示不受该方向的委托量限制
        </span>

        <div>
          <ElButton
            type="primary"
            native-type="submit"
            :loading="isSubmitting"
            :disabled="!isFormComplete || isSubmitting"
          >
            {{ editingRecord === null ? '新建品种' : '保存本行' }}
          </ElButton>
        </div>
      </form>
    </SurfaceCard>

    <ErrorBanner
      :message="errorMessage"
      @retry="refresh()"
    />

    <!-- 提交失败 (a 类) 就地留着: 读这句话的人正在填这张表单. 删除的失败不在这里, 它走 toast. -->
    <ErrorBanner
      :message="submitErrorMessage"
      :is-retry-visible="false"
    />

    <ContentSkeleton v-if="isLoading" />

    <EmptyNotice
      v-else-if="records.length === 0"
      message="还没有品种"
      hint="填好上面那张表单点「新建品种」, 引擎才认得出这个合约"
    />

    <template v-else>
      <div class="overflow-hidden rounded-lg border border-line">
        <ElTable :data="records">
          <ElTableColumn label="交易所">
            <template #default="{ row }">
              {{ row.exchange_id }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="品种代码">
            <template #default="{ row }">
              {{ row.product_id }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="名称">
            <template #default="{ row }">
              {{ row.product_name }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="类型">
            <template #default="{ row }">
              {{ describeProductClass(row.product_class) }}
            </template>
          </ElTableColumn>

          <ElTableColumn
            label="每手乘数"
            align="right"
          >
            <template #default="{ row }">
              {{ formatCount(row.volume_multiple) }}
            </template>
          </ElTableColumn>

          <ElTableColumn
            label="最小变动"
            align="right"
          >
            <template #default="{ row }">
              {{ formatDecimal(row.price_tick) }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="交易时段">
            <template #default="{ row }">
              <span class="text-slate-500">{{ row.session_name || '—' }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn label="录入时间">
            <template #default="{ row }">
              <span class="whitespace-nowrap">{{ formatDateTime(row.created_at) }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn align="right">
            <template #default="{ row }">
              <!-- `row` 是 el-table 自己的行泛型 (一个宽泛的记录), 传给吃 `Product` 的函数过不了
                   类型检查; 这里断言成 `Product` —— 声明处 `:data` 已经保证它就是 (`UserAdminView`
                   对 `User` 是同一写法). -->
              <ElButton
                size="small"
                :disabled="deletingRecordId !== null"
                @click="beginEditing(row as Product)"
              >
                编辑
              </ElButton>
              <ElButton
                size="small"
                type="danger"
                plain
                :disabled="deletingRecordId !== null"
                :loading="deletingRecordId === row.id"
                @click="removeRecord(row as Product)"
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
