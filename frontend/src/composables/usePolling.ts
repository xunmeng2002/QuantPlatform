/**
 * 定时刷新 (列表页与详情页看运行进度用).
 *
 * 两个必须做对的地方:
 *   - **组件卸载一定要停**: 忘了停的轮询会在用户离开页面之后继续打后端, 而结果再没有人看;
 *   - **上一拍没回来就跳过这一拍**: 否则后端一慢, 请求就一拍一拍地堆起来, 越堆越慢.
 */

import { onUnmounted, ref } from 'vue';
import type { Ref } from 'vue';

export const DEFAULT_POLLING_INTERVAL_MS = 2000;

export interface PollingController {
  isPolling: Ref<boolean>;
  start: () => void;
  stop: () => void;
}

export function usePolling(
  task: () => Promise<void>,
  intervalMs: number = DEFAULT_POLLING_INTERVAL_MS,
): PollingController {
  const isPolling = ref(false);
  let timerIdentifier: number | null = null;
  let isTaskRunning = false;

  async function runTaskOnce(): Promise<void> {
    if (isTaskRunning) {
      return;
    }

    isTaskRunning = true;

    try {
      await task();
    } catch {
      // 兜底: 单个请求失败不该停掉整个轮询 (那会让界面停在旧数据上而再无刷新), 也不该逃逸成
      // 未处理的 rejection. 失败本身由 task 自己反映到界面上, 它才拿得到展示用的位置.
    } finally {
      isTaskRunning = false;
    }
  }

  function start(): void {
    if (timerIdentifier !== null) {
      return;
    }

    isPolling.value = true;
    timerIdentifier = window.setInterval(() => {
      void runTaskOnce();
    }, intervalMs);
  }

  function stop(): void {
    if (timerIdentifier !== null) {
      window.clearInterval(timerIdentifier);
      timerIdentifier = null;
    }

    isPolling.value = false;
  }

  onUnmounted(stop);

  return { isPolling, start, stop };
}
