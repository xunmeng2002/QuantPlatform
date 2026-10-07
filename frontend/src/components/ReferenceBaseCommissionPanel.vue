<script setup lang="ts">
/**
 * 费率明细: 引擎算钱时按 (手续费组, 交易所, 合约, 方向) 去查的那张表.
 *
 * 一条规则可以按**合约 / 品种 / 交易所**三级设置, 而表里没有"作用域"这一列 —— 它由「合约格」的
 * 取值承载 (空 = 交易所级, 已登记的品种码 = 品种级, 其余 = 合约级). 这个表单因此把"作用域"做成
 * 一个显式选择器, 再按它换出下面那一格该填什么; 读法只有一处, 见 `domain/rate-scope.ts`.
 *
 * **为什么能按品种 / 交易所设置**: 引擎自己查不到这两级 (它的查找键里没有品种, 成交也不带品种),
 * 是平台在**每一轮运行前**把三级规则摊成具体合约的行, 写进该轮的作业目录
 * (`backend/app/reference_data/rate_expansion.py`). 故这一页录的是"规则", 引擎读到的仍是"一行
 * 一个合约" —— 通配的语义由平台负责展开掉, 展开时命中哪一级就整行照搬, 不做字段级合并.
 *
 * 十项费率的**含义与算法**照抄引擎 (`src/Settlement/CommissionCalculator.cpp::CalcTradeFee`):
 *   佣金 = 成交金额 × 「按金额」那项 + 成交数量 × 「按数量」那项   ← 两项**相加**, 不是二选一
 *   印花税 = 成交金额 × 对应那项
 *   过户费 = 成交金额 × 对应那项
 * 下限 / 上限只作用于佣金 (`ClampCommission`), 印花税与过户费按法定费率实收; 下限或上限填 0
 * 表示这一侧不设限, 不是"封到 0".
 *
 * 方向是键的一列而不是"选哪几列算": 同一合约的买与卖各占一行, 取到行之后再按开平标志决定用开仓
 * 列还是平仓列 —— 印花税只在卖出侧收. 与作用域同理, **方向也有一档平台自己的通配**: 双向 (`-1`)
 * 表示买卖共用这一套费率, 展开时被摊成买、卖两行; 它只在平台侧存在, 引擎那张表里永远只有 0 与 1.
 * 于是每一侧都是"要么这一格有规则, 要么退到同格里那条双向的规则", 不会退回**另一个方向**的行.
 */

import { computed, onMounted, reactive, ref } from 'vue';
import {
  ElButton,
  ElInput,
  ElInputNumber,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
} from 'element-plus';

import { collectAllPages } from '../api/pagination';
import {
  createBaseCommission,
  deleteBaseCommission,
  fetchBaseCommissions,
  fetchProducts,
  updateBaseCommission,
} from '../api/reference-data';
import {
  BOTH_DIRECTIONS,
  EXCHANGE_SUGGESTIONS,
  MAXIMUM_INSTRUMENT_ID_LENGTH,
  RATE_DIRECTIONS,
  RATE_SCOPES,
} from '../api/types';
import type {
  BaseCommission,
  BaseCommissionPayload,
  Product,
  RateDirection,
  RateScope,
} from '../api/types';
import { describeApiFailure } from '../composables/use-feedback';
import { useReferenceTable } from '../composables/use-reference-table';
import { ABSENT_PLACEHOLDER, formatDateTime, formatDecimal } from '../domain/format';
import {
  describeCommissionDirection,
  describeCommissionDirectionPhrase,
} from '../domain/labels';
import {
  describeRateScope,
  describeRateScopeTarget,
  resolveRateScope,
} from '../domain/rate-scope';
import ContentSkeleton from './ContentSkeleton.vue';
import EmptyNotice from './EmptyNotice.vue';
import ErrorBanner from './ErrorBanner.vue';
import PaginationToolbar from './PaginationToolbar.vue';
import StatusBadge from './StatusBadge.vue';
import SurfaceCard from './SurfaceCard.vue';

/**
 * 十项费率在本页的次序 = 后端 `BaseCommissionWriteRequest` 的字段序 = 引擎的列序.
 *
 * 写成一张表而不是十段并排的模板: 十段里每一段只差字段名与中文标签, 而那正是最容易在复制中
 * 错位的地方 —— 抄错一列, 界面上就是"看起来都对, 费率却是别人的".
 */
const RATE_FIELD_KEYS = [
  'open_by_money',
  'close_by_money',
  'open_by_volume',
  'close_by_volume',
  'open_stamp_tax_by_money',
  'close_stamp_tax_by_money',
  'open_transfer_fee_by_money',
  'close_transfer_fee_by_money',
  'min_commission',
  'max_commission',
] as const satisfies readonly (keyof BaseCommissionPayload)[];

type RateFieldKey = (typeof RATE_FIELD_KEYS)[number];

interface RateFieldDefinition {
  key: RateFieldKey;
  label: string;
}

const RATE_FIELD_DEFINITIONS: readonly RateFieldDefinition[] = [
  { key: 'open_by_money', label: '开仓佣金 (按金额)' },
  { key: 'close_by_money', label: '平仓佣金 (按金额)' },
  { key: 'open_by_volume', label: '开仓佣金 (按数量)' },
  { key: 'close_by_volume', label: '平仓佣金 (按数量)' },
  { key: 'open_stamp_tax_by_money', label: '开仓印花税' },
  { key: 'close_stamp_tax_by_money', label: '平仓印花税' },
  { key: 'open_transfer_fee_by_money', label: '开仓过户费' },
  { key: 'close_transfer_fee_by_money', label: '平仓过户费' },
  { key: 'min_commission', label: '佣金下限' },
  { key: 'max_commission', label: '佣金上限' },
];

interface BaseCommissionForm {
  // 组号没有默认值, 理由同手续费组那一页: 它是引擎找组的那个数字.
  commissionGroupId: number | undefined;
  scope: RateScope;
  exchangeId: string;
  /**
   * 合约级时是合约代码, 品种级时是从下拉框里选中的品种码, 交易所级时**恒为空串**.
   *
   * 三种作用域共用这一个字段而不是各存一个: 提交时它就是「合约格」, 而"这一格该填什么"由作用域
   * 决定 —— 分开存反而会出现"作用域切了, 另一个字段还留着上一次的值"那种说不清谁算数的状态.
   */
  instrumentId: string;
  direction: RateDirection;
  rates: Record<RateFieldKey, number | undefined>;
}

function createZeroRates(): Record<RateFieldKey, number> {
  return {
    open_by_money: 0,
    close_by_money: 0,
    open_by_volume: 0,
    close_by_volume: 0,
    open_stamp_tax_by_money: 0,
    close_stamp_tax_by_money: 0,
    open_transfer_fee_by_money: 0,
    close_transfer_fee_by_money: 0,
    min_commission: 0,
    max_commission: 0,
  };
}

function createEmptyForm(): BaseCommissionForm {
  return {
    commissionGroupId: undefined,
    // 默认合约级: 三级里最具体、也最少歧义的那一档 —— 品种级的代码必须先登记过品种, 而交易所级
    // 会默默接住所有没单独录过的合约.
    scope: 'contract',
    exchangeId: '',
    instrumentId: '',
    // 默认双向: 佣金与过户费本来就买卖同数, 只有印花税分侧 —— 从双向起, 多数规则一步到位; 要分
    // 侧的在下面那一格改成买或卖.
    direction: BOTH_DIRECTIONS,
    // 十项**从 0 起**而不是从空起: "这一侧不收这项费用"本身就是一个决定 (印花税只在卖出侧收, 买
    // 入那一行的印花税就该是 0), 从空起等于逼用户挨个敲十个 0 —— 而敲漏一个就提交不了.
    rates: createZeroRates(),
  };
}

const form = reactive<BaseCommissionForm>(createEmptyForm());

/**
 * 品种下拉框的选项: 已登记的 (交易所, 品种码) 对.
 *
 * 判据是**登记与否**而不是"长度像不像品种码" —— 品种表是平台唯一知道这件事的地方, 而后端
 * `_ensure_instrument_scope_is_known` 也照着同一张表守门: 品种级那一格填一个没登记过的短码会被
 * 400 拦下. 下拉框是让这一步不必靠用户记住代码.
 *
 * 这份清单**只喂下拉框**: 一行费率的作用域由合约格本身判 (`domain/rate-scope.ts`), 不查这里 ——
 * 那份判据得在选项还没取回来时也说得通.
 */
interface ProductRateOption {
  /** 下拉框的取值 `<交易所>/<品种码>`. 与 `label` 同形, 但取值是拿来查的, 标签是拿来看的. */
  value: string;
  label: string;
  exchangeId: string;
  productId: string;
}

const productOptions = ref<ProductRateOption[]>([]);
const isLoadingProductOptions = ref(true);
const productOptionsErrorMessage = ref<string | null>(null);

function buildProductRateOption(product: Product): ProductRateOption {
  return {
    value: `${product.exchange_id}/${product.product_id}`,
    label: `${product.exchange_id} / ${product.product_id}`,
    exchangeId: product.exchange_id,
    productId: product.product_id,
  };
}

async function loadProductOptions(): Promise<void> {
  isLoadingProductOptions.value = true;
  productOptionsErrorMessage.value = null;

  try {
    const products = await collectAllPages(fetchProducts);

    productOptions.value = products.map(buildProductRateOption);
  } catch (error) {
    productOptionsErrorMessage.value = describeApiFailure(error, '加载品种列表失败');
  } finally {
    isLoadingProductOptions.value = false;
  }
}

/**
 * 品种下拉框的取值.
 *
 * 取值是拼出来的 `<交易所>/<品种码>`, 而**交易所那一格要跟着带出来**: 只选一个 `600` 是不够的,
 * 同一个品种码在别的交易所是另一个品种. 反过来, 选中之后交易所框就锁住 —— 手改一个与品种对不上
 * 的交易所会造出一行"SSE 下录了 SHFE 的 rb", 后端只能把它当成一个没登记过的短码拦下.
 */
const selectedProductOption = computed<string>({
  get: () =>
    form.scope === 'product' && form.instrumentId !== ''
      ? `${form.exchangeId}/${form.instrumentId}`
      : '',
  set: (optionValue: string) => {
    const selectedOption = productOptions.value.find(
      (productOption) => productOption.value === optionValue,
    );

    // 取值只可能来自本下拉框的选项; 查不到说明选项在选中与回调之间被换过 —— 那时不动比按一个空
    // 取值改写表单好.
    if (selectedOption === undefined) {
      return;
    }

    form.exchangeId = selectedOption.exchangeId;
    form.instrumentId = selectedOption.productId;
  },
});

/**
 * 换作用域时清掉合约格.
 *
 * 一格三种含义, 留着上一级的值等于让用户提交一行"作用域写着品种、格子装着 `600519`"的规则 ——
 * 那正是"不另存一列"想避免的自相矛盾. 交易所那一格不清: 三级都挂在同一个交易所下, 留着省一次
 * 输入; 品种级时它还会被选中的品种覆写.
 */
function changeScope(nextScope: RateScope): void {
  form.scope = nextScope;
  form.instrumentId = '';
}

/**
 * 表单填全了吗.
 *
 * 十项费率本身**允许为 0**, 但不允许为空: 清空一个数字框 (`undefined`) 与填 0 是两件事, 让前者
 * 按 0 提交会把"还没想好这一项"悄悄写成"这一项不收".
 *
 * 合约格跟着作用域判: 交易所级时**合法为空** (空串就是这一级的取值, 不是漏填), 另两级必须有着落.
 */
const isFormComplete = computed(
  () =>
    form.commissionGroupId !== undefined &&
    form.exchangeId.trim() !== '' &&
    (form.scope === 'exchange' || form.instrumentId.trim() !== '') &&
    RATE_FIELD_KEYS.every((key) => form.rates[key] !== undefined),
);

/**
 * 十项费率收成一份填满的请求体.
 *
 * `?? 0` 这一句是**类型收口**, 不是兜底: 上面那个判断已经保证没有空项了, 只是 TS 看不见这一层
 * 关联. 走到这里还有空项, 那只可能是判断被改坏了, 而按 0 提交与拒绝提交在这里等价 —— 都是把
 * 一个不该发生的状态挡在外面.
 */
function readFilledRates(): Record<RateFieldKey, number> {
  const filledRates = createZeroRates();

  for (const key of RATE_FIELD_KEYS) {
    filledRates[key] = form.rates[key] ?? 0;
  }

  return filledRates;
}

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
} = useReferenceTable<BaseCommission, BaseCommissionPayload>({
  fetchPage: fetchBaseCommissions,
  createRecord: createBaseCommission,
  updateRecord: updateBaseCommission,
  deleteRecord: deleteBaseCommission,

  buildPayload() {
    if (!isFormComplete.value) {
      return null;
    }

    const rates = readFilledRates();

    return {
      commission_group_id: form.commissionGroupId ?? 0,
      exchange_id: form.exchangeId.trim(),
      // 交易所级的合约格就是空串 —— 后端按它认出这一级, 不是"漏填了合约".
      instrument_id: form.scope === 'exchange' ? '' : form.instrumentId.trim(),
      direction: form.direction,
      ...rates,
    };
  },

  resetForm() {
    Object.assign(form, createEmptyForm());
  },

  describeRecord(baseCommission) {
    return (
      `${baseCommission.commission_group_id} 号组下 ` +
      `${describeRateScopeTarget(baseCommission.exchange_id, baseCommission.instrument_id)} ` +
      `的${describeCommissionDirectionPhrase(baseCommission.direction)}费率`
    );
  },

  describeDeleteQuestion(baseCommission) {
    return (
      `确定删除 ${baseCommission.commission_group_id} 号组下 ` +
      `${describeRateScopeTarget(baseCommission.exchange_id, baseCommission.instrument_id)} ` +
      `的${describeCommissionDirectionPhrase(baseCommission.direction)}费率? ` +
      '此后这一档落到更宽的规则上 (同格里还有双向那条的话就落到它) —— 都没有的话整笔取不到费率, 三列费用按 0 计.'
    );
  },

  loadFailureMessage: '加载费率明细失败',
  submitFailureMessage: '保存费率明细失败',
  deleteFailureMessage: '删除费率明细失败',
});

/** 点「编辑」把那一行的取值装进表单; 表单住的地方与新建是同一个. */
function beginEditing(baseCommission: BaseCommission): void {
  form.commissionGroupId = baseCommission.commission_group_id;
  // 作用域从合约格读回来, 而不是另存一列 —— 库里的那一格是唯一真相.
  form.scope = resolveRateScope(baseCommission.instrument_id);
  form.exchangeId = baseCommission.exchange_id;
  form.instrumentId = baseCommission.instrument_id;
  // 读侧收的是裸数字 (理由见 `BaseCommission.direction`), 这里的断言只是编译期收口: 库里出现三档
  // 之外的取值时 (只可能来自导入), 那个数字原样进表单, 而下拉框的选项只有 -1 / 0 / 1 —— 不把它
  // 假装成某一档, 用户就看得见"这一行的方向不在选项里".
  form.direction = baseCommission.direction as RateDirection;
  const existingRates = createZeroRates();

  for (const field of RATE_FIELD_DEFINITIONS) {
    existingRates[field.key] = baseCommission[field.key];
  }

  form.rates = existingRates;

  markRowAsEditing(baseCommission);
}

onMounted(() => {
  void refresh();
  void loadProductOptions();
});
</script>

<template>
  <div>
    <SurfaceCard
      class="mb-6"
      :title="
        editingRecord === null
          ? '新建费率规则'
          : `编辑 ${editingRecord.commission_group_id} 号组下 ${describeRateScopeTarget(editingRecord.exchange_id, editingRecord.instrument_id)} 的${describeCommissionDirectionPhrase(editingRecord.direction)}费率`
      "
      description="组号 + 交易所 + 代码 + 方向是引擎的查找键, 逐字对上才取得到费率; 按品种或交易所录的规则由平台在每轮运行前展开成具体合约"
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
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="base-commission-group-id"
            >手续费组号</label>
            <ElInputNumber
              id="base-commission-group-id"
              v-model="form.commissionGroupId"
              :min="0"
              :controls="false"
              class="w-full"
            />
            <span class="text-xs text-slate-400">
              组必须先建出来, 否则存不进去
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="base-commission-scope"
            >作用域</label>
            <ElSelect
              id="base-commission-scope"
              :model-value="form.scope"
              @update:model-value="changeScope"
            >
              <ElOption
                v-for="scope in RATE_SCOPES"
                :key="scope"
                :label="describeRateScope(scope).label"
                :value="scope"
              />
            </ElSelect>
            <span class="text-xs text-slate-400">
              更具体的一级遮住更宽的一级
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="base-commission-exchange-id"
            >交易所</label>
            <ElSelect
              id="base-commission-exchange-id"
              v-model="form.exchangeId"
              filterable
              allow-create
              default-first-option
              placeholder="如 SSE"
              :disabled="form.scope === 'product'"
            >
              <ElOption
                v-for="exchangeId in EXCHANGE_SUGGESTIONS"
                :key="exchangeId"
                :label="exchangeId"
                :value="exchangeId"
              />
            </ElSelect>
            <span class="text-xs text-slate-400">
              {{ form.scope === 'product' ? '随选中的品种带出' : '交易所级即整个交易所' }}
            </span>
          </div>

          <div
            v-if="form.scope === 'contract'"
            class="flex flex-col gap-1"
          >
            <label
              class="text-sm font-medium text-slate-700"
              for="base-commission-instrument-id"
            >合约代码</label>
            <ElInput
              id="base-commission-instrument-id"
              v-model="form.instrumentId"
              type="text"
              :maxlength="MAXIMUM_INSTRUMENT_ID_LENGTH"
              placeholder="如 600519"
            />
            <span class="text-xs text-slate-400">
              逐字匹配, 不做前缀
            </span>
          </div>

          <div
            v-else-if="form.scope === 'product'"
            class="flex flex-col gap-1"
          >
            <label
              class="text-sm font-medium text-slate-700"
              for="base-commission-product-code"
            >品种</label>
            <ElSelect
              id="base-commission-product-code"
              v-model="selectedProductOption"
              filterable
              :loading="isLoadingProductOptions"
              :disabled="isLoadingProductOptions"
              placeholder="如 SSE / 600"
            >
              <ElOption
                v-for="productOption in productOptions"
                :key="productOption.value"
                :label="productOption.label"
                :value="productOption.value"
              />
            </ElSelect>
            <span class="text-xs text-slate-400">
              必须先在「品种」页登记过, 这一档才认得出来
            </span>
          </div>

          <div
            v-else
            class="flex flex-col gap-1"
          >
            <span class="text-sm font-medium text-slate-700">合约代码</span>
            <span class="text-sm text-slate-500">
              交易所级不填代码, 它就是这个交易所下所有合约的兜底
            </span>
          </div>

          <div class="flex flex-col gap-1">
            <label
              class="text-sm font-medium text-slate-700"
              for="base-commission-direction"
            >方向</label>
            <ElSelect
              id="base-commission-direction"
              v-model="form.direction"
            >
              <ElOption
                v-for="direction in RATE_DIRECTIONS"
                :key="direction"
                :label="describeCommissionDirection(direction)"
                :value="direction"
              />
            </ElSelect>
            <span class="text-xs text-slate-400">
              双向: 买卖共用这一套费率 (多数费率本来就买卖同数). 印花税只在卖出侧收, 双向会让买入
              也收 —— 要区分就把它拆成买、卖两行
            </span>
          </div>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div
            v-for="field in RATE_FIELD_DEFINITIONS"
            :key="field.key"
            class="flex flex-col gap-1"
          >
            <label
              class="text-sm font-medium text-slate-700"
              :for="`base-commission-${field.key}`"
            >{{ field.label }}</label>
            <ElInputNumber
              :id="`base-commission-${field.key}`"
              v-model="form.rates[field.key]"
              :min="0"
              :step="0.000001"
              :controls="false"
              class="w-full"
            />
          </div>
        </div>

        <p class="text-xs text-slate-400">
          费率填的是「比率」不是金额: 按金额的那几项是成交金额的倍数 (万分之一写作 0.0001,
          可以写到小数点后六位, 列表也按六位显示),
          按数量的那几项是每一股 / 每一手多少钱; 佣金是这两类相加出来的, 不是二选一.
          下限与上限只管佣金, 填 0 表示这一侧不设限; 印花税与过户费按成交金额算, 不参与封底封顶.
        </p>

        <div>
          <ElButton
            type="primary"
            native-type="submit"
            :loading="isSubmitting"
            :disabled="!isFormComplete || isSubmitting"
          >
            {{ editingRecord === null ? '新建费率规则' : '保存本行' }}
          </ElButton>
        </div>
      </form>
    </SurfaceCard>

    <!-- 品种选项是这一页的**辅助输入**: 取不到它不影响已有的费率行读出来, 故它单独一条失败横幅,
         不与列表那条混在一起 —— 混在一起会让人以为"费率读不出来". -->
    <ErrorBanner
      :message="productOptionsErrorMessage"
      @retry="loadProductOptions()"
    />

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
      message="还没有费率规则"
      hint="一行只管一档; 少一档, 那一笔成交的手续费就是 0"
    />

    <template v-else>
      <div class="overflow-x-auto rounded-lg border border-line">
        <ElTable
          :data="records"
          class="min-w-max"
        >
          <ElTableColumn
            label="组号"
            align="right"
          >
            <template #default="{ row }">
              {{ row.commission_group_id }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="作用域">
            <template #default="{ row }">
              <StatusBadge
                v-bind="describeRateScope(resolveRateScope(row.instrument_id))"
              />
            </template>
          </ElTableColumn>

          <ElTableColumn label="交易所">
            <template #default="{ row }">
              {{ row.exchange_id }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="代码">
            <template #default="{ row }">
              {{ row.instrument_id || ABSENT_PLACEHOLDER }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="方向">
            <template #default="{ row }">
              {{ describeCommissionDirection(row.direction) }}
            </template>
          </ElTableColumn>

          <ElTableColumn
            v-for="field in RATE_FIELD_DEFINITIONS"
            :key="field.key"
            :label="field.label"
            align="right"
          >
            <template #default="{ row }">
              {{ formatDecimal(row[field.key]) }}
            </template>
          </ElTableColumn>

          <ElTableColumn label="录入时间">
            <template #default="{ row }">
              <span class="whitespace-nowrap">{{ formatDateTime(row.created_at) }}</span>
            </template>
          </ElTableColumn>

          <ElTableColumn
            label="操作"
            align="right"
          >
            <template #default="{ row }">
              <!-- `row` 是 el-table 自己的行泛型, 断言成 `BaseCommission` 才能喂给下面那两个函数
                   —— 声明处 `:data` 已经保证它就是. -->
              <ElButton
                size="small"
                :disabled="deletingRecordId !== null"
                @click="beginEditing(row as BaseCommission)"
              >
                编辑
              </ElButton>
              <ElButton
                size="small"
                type="danger"
                plain
                :disabled="deletingRecordId !== null"
                :loading="deletingRecordId === row.id"
                @click="removeRecord(row as BaseCommission)"
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
