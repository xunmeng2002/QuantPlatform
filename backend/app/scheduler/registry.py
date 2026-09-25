"""在跑的作业句柄表.

调度循环、取消端点与作业任务三方共享它, 故纪律只有一条: **临界区内绝不 await 慢操作**.
`register` / `get` / `request_cancel` / `retire` 全是同步字典操作, 于是取消请求与调度循环之间
不存在锁争用——取消端点在 HTTP 处理器里跑, 它的响应时间不该取决于调度循环此刻在做什么.

句柄有一条贯穿全程的**不变量**: 它在认领之后、spawn 之前入表, 直到作业任务返回之后才出表.
于是任何时刻"行是 running"都蕴含"表里有句柄", 取消端点不必为"running 但无句柄"单编一套逻辑
——那一格本不该存在.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class JobHandle:
    """一次作业执行的句柄.

    `process` 在 spawn 之前是 `None`. 句柄先于 spawn 入表是有意的: 取消落在"已认领但进程未起"
    那一格时, runner 在 spawn **之前**读一次 `cancel_signal`, 已置位则根本不起进程、直接按
    `interrupted` 收尾——既省一次进程, 又把该窗口的语义钉死, 且不会留孤儿.

    `cancel_signal` 用 `Event` 而不是 `bool`, 是因为同一件事**两个读法都要有**: spawn 之前要一次
    同步读 (`is_set()`), spawn 之后要能 `await` 它——取消落在进程已经在跑的那一格时, 只置个标志
    是没用的, `process.wait()` 不会因此返回, 作业会一直挂到自然退出或作业级超时. 用一个 `Event`
    同时满足两种读法, 好过用"标志 + 事件"两个字段去表达同一个事实.

    `finished` 表示**终态已经落库**, 而不是"进程已经退出": 取消端点等它是为了返回时行已是终态,
    前端因此不必靠轮询猜. 进程退出与终态落库之间隔着读 `result.json`、写镜像列、CAS 收尾几步,
    差着这几步返回就会给出一个"已取消但仍显示 running"的瞬间.
    """

    run_id: str
    process: asyncio.subprocess.Process | None = None
    cancel_signal: asyncio.Event = field(default_factory=asyncio.Event)
    timed_out: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class RunningJobRegistry:
    """在跑的作业句柄表.

    锁在今天其实拦不到任何交错: 临界区内没有 `await`, 事件循环不会在字典操作中途切走. 保留它
    是给"临界区内不得出现 `await`"这条纪律一个**可执行的落点**——日后有人在 `request_cancel` 里
    加一句 `await session.commit()` 时, 会撞上一把已经在那里的锁, 而不是先制造出一个只在并发下
    才现形的错.
    """

    def __init__(self) -> None:
        self._handles: dict[str, JobHandle] = {}
        self._lock = asyncio.Lock()

    async def register(self, run_id: str) -> JobHandle:
        """登记一个新句柄.

        覆盖已有同号句柄是缺陷而非正常路径 (`RunId` 唯一且认领带条件), 故记一条 error 而不静默
        替换——静默替换会把前一个作业的取消信号丢掉, 表现为"取消点了没反应".
        """

        handle = JobHandle(run_id=run_id)

        async with self._lock:
            if run_id in self._handles:
                logger.error("同一运行被重复登记, 前一个句柄已失去所有等待者")

            self._handles[run_id] = handle

        return handle

    async def attach_process(
        self, run_id: str, process: asyncio.subprocess.Process
    ) -> None:
        """把刚起的进程挂到句柄上.

        句柄按不变量必然在表里; 真取不到说明有人违反了它, 而那种情况下这个进程**已经没有人
        认领**——就地收掉, 比留一个查无此人的孤儿好.
        """

        async with self._lock:
            handle = self._handles.get(run_id)

            if handle is None:
                logger.error("句柄不在表中, 刚起的进程无人认领, 已就地终止")
                process.terminate()
                return

            handle.process = process

    async def get(self, run_id: str) -> JobHandle | None:
        """取句柄; 不在表里即返回 None."""

        async with self._lock:
            return self._handles.get(run_id)

    async def request_cancel(self, run_id: str) -> JobHandle | None:
        """置取消标志并交出句柄.

        只置标志、不代 runner 决定终态: 终态由 `runner` 的仲裁表统一算一次, 而 Windows 上被
        `terminate()` 杀掉的进程退出码是 1, 与"宿主启动失败"同码——两个写入者各判一次必然分叉.
        """

        async with self._lock:
            handle = self._handles.get(run_id)

            if handle is None:
                return None

            handle.cancel_signal.set()

        return handle

    async def retire(self, run_id: str) -> None:
        """作业任务结束后出表, 并唤醒所有等待者.

        由调度循环在任务结束回调里调用, 正常与异常路径都会走到: 若只在正常路径唤醒, 一个在收尾
        前抛异常的作业会把取消端点挂满它那两秒的兜底等待.
        """

        async with self._lock:
            handle = self._handles.pop(run_id, None)

        if handle is not None:
            handle.finished.set()
