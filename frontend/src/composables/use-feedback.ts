/**
 * 全站唯一的反馈出口: 两个 toast + 一个确认弹窗 + 一句错误归一化.
 *
 * 为什么是几个纯函数而不是 `useFeedback()` 钩子: 它们没有任何实例状态, 也没有需要拆卸的东西
 * (与 `usePolling` 那种返回 `{ start, stop }` 的钩子不同), 包成钩子只会让每个调用点多一行
 * setup 声明. 放在 `composables/` 而不是 `domain/`: `domain/` 是不依赖任何库的纯逻辑层,
 * 往里放一个 `element-plus` 的 import 就破了那条边界.
 *
 * 分流规则 (完整表见 `docs/platform-plan.md` 的观感层拍板表):
 *
 *   a. **表单自己的提交** (登录 / 提交回测 / 上传策略 / 上传版本 / 建号) —— 成功走这里,
 *      **失败留在表单里**, 因为错误与出错的字段同屏, 弹到右上角等于让用户去别处找;
 *   b. **表单之外的页面级动作** (删除策略 / 取消运行 / 改账号状态 / 保存授权 / 下载产物) ——
 *      成功与失败都走这里;
 *   c. **加载类** (页面加载 / 级联加载 / 产物清单) —— 一律走 `ErrorBanner` 或组件就地提示,
 *      不进这个文件.
 *
 * 一个 ref 不许同时承载 a/b 与 c 两类失败: 那样同一个失败会被报两次, 或者反过来一次都不报.
 */

import { ElMessage, ElMessageBox } from 'element-plus';

import { ApiError } from '../api/client';

/**
 * 成功: 两秒自己消失 —— 用户不需要为「成了」做任何事.
 *
 * 不传 `max`: 它不是 `ElMessage` 的 per-call 选项 (2.14.6 的 `messageProps` 里没有这一项,
 * 传了只会作为废属性漏进 DOM), 并发上限由 `App.vue` 里 `ElConfigProvider` 的
 * `:message="{ max: 3 }"` 给 —— `method.mjs:98` 只从 provider 的 `messageConfig` 读它.
 */
export function showSuccessToast(message: string): void {
  ElMessage({
    type: 'success',
    message,
    duration: 2000,
  });
}

/**
 * 失败: **不自动消失**, 必须手动关 (点 × 或按 ESC —— `ElMessage` 自己监听了 document 的
 * keydown) —— 失败信息通常要读完再决定下一步, 读慢了就没了比不弹更糟. `grouping` 让同一句
 * 重复的失败只累加计数, 不至于堆成一列.
 */
export function showFailureToast(message: string): void {
  ElMessage({
    type: 'error',
    message,
    duration: 0,
    showClose: true,
    grouping: true,
  });
}

/**
 * 把任意抛出物归一化成一句给人看的中文.
 *
 * 本批之前全仓有 20 处逐字相同的 `error instanceof ApiError ? error.detail : '…'` (Harness §5:
 * 三段以上仅参数不同即封装), 本批起只留这一处. 兜底文案仍由调用方给 —— 只有它知道刚失败的是
 * 「登录」还是「删除策略」, 那半句无法由这里造出来.
 */
export function describeApiFailure(error: unknown, fallbackMessage: string): string {
  return error instanceof ApiError ? error.detail : fallbackMessage;
}

interface ConfirmActionOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  isDangerous?: boolean;
}

/**
 * 两次点击之间不许叠出第二个弹窗: `ElMessageBox` 每调一次就 `createElement` 一个容器挂到 body
 * (没有单例保护), 而它取代的 `ConfirmDialog` 是 `v-if` —— 双击「删除策略」原本叠不出两层,
 * 换过去就会. 被挡掉的第二次点击返回 `false`, 语义就是「什么都没确认」.
 */
let isConfirmDialogOpen = false;

/**
 * 确认弹窗. 用户取消 (点取消键 / ESC / 点遮罩) 一律返回 `false`, 不抛异常.
 *
 * 中文按钮文案**显式传**, 但理由不是「函数式 API 拿不到 locale」—— 那条路是通的:
 * `ElConfigProvider` 挂载时会把 locale 写进模块级 `globalConfig` (`use-global-config.mjs:12/57`),
 * 而 MessageBox 的实例拿不到 appContext, `inject` 正好落到这个默认值, 所以不传也读得到
 * `el.messagebox.confirm` (`index.mjs:154` 的兜底). 显式传是为了让措辞成为我们自己的、可被
 * spec 钉住的契约, 不随 provider 的挂载与否漂移.
 *
 * `autofocus: !isDangerous` —— 它默认为 `true` 且聚焦**确认键** (`index.vue…:142`), 对危险动作
 * 等于让「回车」直接执行删除, 故危险动作关掉; 非危险动作保留, 键盘用户靠它一步确认.
 *
 * `confirmButtonClass: 'el-button--danger'` 够用, 不必绕 `customClass` + 覆盖 CSS 变量:
 * `el-button.css` 里 `--danger` 在字节 13830、`--primary` 最晚到 8220, 同特异性同表且后者在后,
 * danger 那组 `--el-button-*` 稳稳覆盖 primary, 连配套的红色焦点环一起.
 *
 * 行为差 (有意): 确认后弹窗立即关闭, 写进程中的忙碌态改由**触发它的那个按钮**挂 `:loading`
 * (`ConfirmDialog` 里那句「处理中…」随之消失). 遮罩点击与 ESC 关闭是新增能力, 今天只有前者.
 */
export async function confirmAction(options: ConfirmActionOptions): Promise<boolean> {
  if (isConfirmDialogOpen) {
    return false;
  }

  isConfirmDialogOpen = true;

  try {
    const isDangerous = options.isDangerous === true;

    await ElMessageBox.confirm(options.message, options.title, {
      confirmButtonText: options.confirmLabel ?? '确定',
      cancelButtonText: '取消',
      autofocus: !isDangerous,
      ...(isDangerous
        ? { type: 'warning' as const, confirmButtonClass: 'el-button--danger' }
        : {}),
    });

    return true;
  } catch {
    // 兜底: 取消与关闭都由 MessageBox 以 reject 表达 (拒绝值是个字符串 'cancel', 不是 Error),
    // 那就是本函数的「false」分支, 不是错误; 它不发起任何请求, 没有别的异常来源, 故不留日志.
    return false;
  } finally {
    isConfirmDialogOpen = false;
  }
}
