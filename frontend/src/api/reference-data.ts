/**
 * 回测基础数据: 品种 / 手续费组 / 费率.
 *
 * 三个资源全在 `/api/reference-data` 下, **管理端点**限管理员 —— 它们对整个平台是同一份, 没有
 * 归属概念, 而它们决定每一轮回测怎么算钱.
 *
 * 唯一例外是 `fetchCommissionGroupOptions`: 提交页要选组就得先看得见有哪些组, 故那条只要求登录
 * (普通用户有权提交回测). 它只读、只给组号与组名, 不是管理面 —— 改组的端点照旧限管理员.
 *
 * 写端点**没有"保存后还要不要同步"这一步**: 引擎读的那份种子库现在**按轮生成**, 落在各轮自己的
 * 作业目录里 (`backend/app/reference_data/seed_database.py`), 不再是盘上一个需要人工维护的全局
 * 文件. 故这里既没有状态查询, 也没有"重新生成".
 */

import { request } from './client';
import type {
  BaseCommission,
  BaseCommissionPayload,
  CommissionGroup,
  CommissionGroupOption,
  CommissionGroupPayload,
  MessageResponse,
  PageResponse,
  Product,
  ProductPayload,
} from './types';

const REFERENCE_DATA_PATH = '/reference-data';
const PRODUCTS_PATH = `${REFERENCE_DATA_PATH}/products`;
const COMMISSION_GROUPS_PATH = `${REFERENCE_DATA_PATH}/commission-groups`;
const COMMISSION_GROUP_OPTIONS_PATH = `${REFERENCE_DATA_PATH}/commission-group-options`;
const BASE_COMMISSIONS_PATH = `${REFERENCE_DATA_PATH}/base-commissions`;

export function fetchProducts(
  offset: number,
  limit: number,
): Promise<PageResponse<Product>> {
  return request<PageResponse<Product>>(PRODUCTS_PATH, { query: { offset, limit } });
}

export function createProduct(payload: ProductPayload): Promise<Product> {
  return request<Product>(PRODUCTS_PATH, { method: 'POST', body: payload });
}

export function updateProduct(
  productId: string,
  payload: ProductPayload,
): Promise<Product> {
  return request<Product>(`${PRODUCTS_PATH}/${encodeURIComponent(productId)}`, {
    method: 'PATCH',
    body: payload,
  });
}

export function deleteProduct(productId: string): Promise<MessageResponse> {
  return request<MessageResponse>(`${PRODUCTS_PATH}/${encodeURIComponent(productId)}`, {
    method: 'DELETE',
  });
}

export function fetchCommissionGroups(
  offset: number,
  limit: number,
): Promise<PageResponse<CommissionGroup>> {
  return request<PageResponse<CommissionGroup>>(COMMISSION_GROUPS_PATH, {
    query: { offset, limit },
  });
}

/**
 * 提交页选组用的选项页.
 *
 * 与其他列表函数一样只取一页: 要"全部选项"的调用方用 `collectAllPages(fetchCommissionGroupOptions)`
 * 收全 (同 `ReferenceBaseCommissionPanel` 取品种的做法). 与 `fetchCommissionGroups` 的区别不在
 * 数据, 而在**权限**: 这条只要求登录, 故提交页不必是管理员页.
 */
export function fetchCommissionGroupOptions(
  offset: number,
  limit: number,
): Promise<PageResponse<CommissionGroupOption>> {
  return request<PageResponse<CommissionGroupOption>>(COMMISSION_GROUP_OPTIONS_PATH, {
    query: { offset, limit },
  });
}

export function createCommissionGroup(
  payload: CommissionGroupPayload,
): Promise<CommissionGroup> {
  return request<CommissionGroup>(COMMISSION_GROUPS_PATH, {
    method: 'POST',
    body: payload,
  });
}

export function updateCommissionGroup(
  commissionGroupId: string,
  payload: CommissionGroupPayload,
): Promise<CommissionGroup> {
  return request<CommissionGroup>(
    `${COMMISSION_GROUPS_PATH}/${encodeURIComponent(commissionGroupId)}`,
    { method: 'PATCH', body: payload },
  );
}

export function deleteCommissionGroup(
  commissionGroupId: string,
): Promise<MessageResponse> {
  return request<MessageResponse>(
    `${COMMISSION_GROUPS_PATH}/${encodeURIComponent(commissionGroupId)}`,
    { method: 'DELETE' },
  );
}

export function fetchBaseCommissions(
  offset: number,
  limit: number,
): Promise<PageResponse<BaseCommission>> {
  return request<PageResponse<BaseCommission>>(BASE_COMMISSIONS_PATH, {
    query: { offset, limit },
  });
}

export function createBaseCommission(
  payload: BaseCommissionPayload,
): Promise<BaseCommission> {
  return request<BaseCommission>(BASE_COMMISSIONS_PATH, {
    method: 'POST',
    body: payload,
  });
}

export function updateBaseCommission(
  baseCommissionId: string,
  payload: BaseCommissionPayload,
): Promise<BaseCommission> {
  return request<BaseCommission>(
    `${BASE_COMMISSIONS_PATH}/${encodeURIComponent(baseCommissionId)}`,
    { method: 'PATCH', body: payload },
  );
}

export function deleteBaseCommission(
  baseCommissionId: string,
): Promise<MessageResponse> {
  return request<MessageResponse>(
    `${BASE_COMMISSIONS_PATH}/${encodeURIComponent(baseCommissionId)}`,
    { method: 'DELETE' },
  );
}
