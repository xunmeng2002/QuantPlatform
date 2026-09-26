/**
 * 钉住我们**传给 element-plus 的 options 形状**: 成功/失败的时长与可关性是产品决策, 「确定/取消」
 * 与危险动作不吃 Enter 是行为契约, 这些不碰 DOM 也断言得动, 故本文件用默认的 node 环境.
 *
 * 它**不**验证 locale —— 整个 element-plus 都被替换掉了, 断的是「我们传了什么」而不是「EP 怎么
 * 兜底」. 「按钮文案会不会变英文」那件事靠读产物 (ElConfigProvider 把 locale 写进模块级
 * globalConfig) 与手工验收, 不靠这份 spec, 别在这里声称钉住了它.
 *
 * 「成功短、失败长且必须手动关」是全批价值最高的一对断言: 那条拍板否则无处可测. 断言用**完整
 * 对象**而不是 objectContaining —— 多传一个不存在的选项 (例如 `max`, 它不是 ElMessage 的
 * per-call 选项) 会被它当场抓出来.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import {
  confirmAction,
  describeApiFailure,
  showFailureToast,
  showSuccessToast,
} from './use-feedback';

// vi.mock 会被提升到 import 之前, 直接引用外部常量会撞上"尚未初始化"; vi.hoisted 让这两个桩
// 先于 mock 工厂存在.
const { messageSpy, confirmSpy } = vi.hoisted(() => ({
  messageSpy: vi.fn(),
  confirmSpy: vi.fn(),
}));

vi.mock('element-plus', () => ({
  ElMessage: messageSpy,
  ElMessageBox: { confirm: confirmSpy },
}));

beforeEach(() => {
  vi.clearAllMocks();
});

/** 取最近一次 `ElMessageBox.confirm(message, title, options)` 的第三个实参. */
function readLastConfirmOptions(): Record<string, unknown> {
  const lastConfirmCall = confirmSpy.mock.calls.at(-1);

  if (lastConfirmCall === undefined) {
    throw new Error('ElMessageBox.confirm 没被调用过, 先看上面的用例是否通过');
  }

  return lastConfirmCall[2] as Record<string, unknown>;
}

describe('showSuccessToast', () => {
  it('两秒自灭, 不带关闭键', () => {
    showSuccessToast('已创建账号 zhang.');

    expect(messageSpy).toHaveBeenCalledWith({
      type: 'success',
      message: '已创建账号 zhang.',
      duration: 2000,
    });
  });
});

describe('showFailureToast', () => {
  it('不自动消失, 可手动关, 同句合并计数', () => {
    showFailureToast('建号失败');

    expect(messageSpy).toHaveBeenCalledWith({
      type: 'error',
      message: '建号失败',
      duration: 0,
      showClose: true,
      grouping: true,
    });
  });
});

describe('confirmAction', () => {
  it('危险动作: 中文文案 + warning + 确认键挂 danger 类 + 不吃回车', async () => {
    confirmSpy.mockResolvedValueOnce('confirm');

    await expect(
      confirmAction({
        title: '删除策略',
        message: '删除后不可恢复, 确认删除?',
        confirmLabel: '删除策略',
        isDangerous: true,
      }),
    ).resolves.toBe(true);

    expect(confirmSpy).toHaveBeenCalledWith(
      '删除后不可恢复, 确认删除?',
      '删除策略',
      expect.objectContaining({
        confirmButtonText: '删除策略',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
        autofocus: false,
      }),
    );
  });

  it('非危险动作: 保留 autofocus, 不染红也不加 warning 图标', async () => {
    confirmSpy.mockResolvedValueOnce('confirm');

    await confirmAction({ title: '重置为默认值', message: '丢弃当前填写?' });

    const options = readLastConfirmOptions();
    expect(options.confirmButtonText).toBe('确定');
    expect(options.cancelButtonText).toBe('取消');
    expect(options.autofocus).toBe(true);
    // 「没传」只能这样断言: 拿 undefined 去做 objectContaining 匹配, 缺失键与显式 undefined
    // 在匹配器里是同一个结果, 钉不住"这段键整个不存在".
    expect(options).not.toHaveProperty('type');
    expect(options).not.toHaveProperty('confirmButtonClass');
  });

  it('取消返回 false 且不外抛 —— reject 的载荷是字符串 "cancel" 不是 Error', async () => {
    confirmSpy.mockRejectedValueOnce('cancel');

    await expect(confirmAction({ title: '删除策略', message: '确认删除?' })).resolves.toBe(
      false,
    );
  });

  it('前一个弹窗未收起时, 第二次点击直接返回 false 且不再开一个', async () => {
    let resolveFirstConfirm: (action: string) => void = () => {};
    confirmSpy.mockReturnValueOnce(
      new Promise<string>((resolve) => {
        resolveFirstConfirm = resolve;
      }),
    );

    const firstConfirm = confirmAction({ title: '删除策略', message: '确认删除?' });
    await expect(
      confirmAction({ title: '删除策略', message: '确认删除?' }),
    ).resolves.toBe(false);
    expect(confirmSpy).toHaveBeenCalledTimes(1);

    resolveFirstConfirm('confirm');
    await expect(firstConfirm).resolves.toBe(true);
  });
});

describe('describeApiFailure', () => {
  it('ApiError 取服务端 detail', () => {
    expect(describeApiFailure(new ApiError(400, '该登录名已被占用.'), '建号失败')).toBe(
      '该登录名已被占用.',
    );
  });

  it('其他抛出物用调用方给的兜底文案', () => {
    expect(describeApiFailure(new TypeError('Failed to fetch'), '建号失败')).toBe('建号失败');
    expect(describeApiFailure('cancel', '建号失败')).toBe('建号失败');
  });
});
