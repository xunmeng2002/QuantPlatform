/**
 * 用户管理 (管理员) 与受限用户目录 (全体登录用户).
 *
 * 两个前缀同属一个模块: 目录是用户资源的读视图, 拆成两个文件会让「用户」这个概念有两个出处.
 */

import { request } from './client';
import type {
  MessageResponse,
  PageResponse,
  User,
  UserCreatePayload,
  UserDirectoryEntry,
  UserStatus,
} from './types';

export function fetchUsers(offset: number, limit: number): Promise<PageResponse<User>> {
  return request<PageResponse<User>>('/users', { query: { offset, limit } });
}

/** 授权表单的选人来源. 只回 `id` 与 `display_name`, 故拿不到登录名也认不出同名的人. */
export function fetchUserDirectory(
  query: string,
  offset: number,
  limit: number,
): Promise<PageResponse<UserDirectoryEntry>> {
  return request<PageResponse<UserDirectoryEntry>>('/users/directory', {
    query: { query, offset, limit },
  });
}

export function createUser(payload: UserCreatePayload): Promise<User> {
  return request<User>('/users', { method: 'POST', body: payload });
}

export function updateUserStatus(
  userId: string,
  status: UserStatus,
): Promise<MessageResponse> {
  return request<MessageResponse>(`/users/${encodeURIComponent(userId)}/status`, {
    method: 'PATCH',
    body: { status },
  });
}
