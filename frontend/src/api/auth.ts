/** 登录与当前用户. 令牌的持久化与附加以 session store / client 为准, 这里只发请求. */

import { request } from './client';
import type { AccessTokenResponse, User } from './types';

export function login(username: string, password: string): Promise<AccessTokenResponse> {
  return request<AccessTokenResponse>('/auth/login', {
    method: 'POST',
    body: { username, password },
  });
}

export function fetchCurrentUser(): Promise<User> {
  return request<User>('/auth/me');
}
