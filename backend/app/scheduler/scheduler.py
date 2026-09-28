"""常驻调度循环: 取槽 → 认领 → 登记句柄 → 派发.

调度层**就是 web 后端**——没有第二个进程、没有队列中间件: 行是持久记录, 循环扫的是库, 于是
"提交"只需一次 INSERT 加一次唤醒, "取消"只需一次条件更新, 而"重启后怎么办"由恢复那一条
UPDATE 回答.

**单实例是硬前提**: 两个调度循环会各自认领、各自起进程, 而条件更新只保证一行不被认领两次,
不保证系统里只有一个调度器. 故 `uvicorn --workers 2` 是明确不支持的用法 (见 recovery 模块).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update

from ..catalog.database import PlatformDatabase
from ..catalog.enums import RunStatus
from ..catalog.models import RunModel
from ..clock import utc_now
from ..config import PlatformSettings
from .registry import RunningJobRegistry
from .runner import JobRunner


logger = logging.getLogger(__name__)

IDLE_WATCHDOG_SECONDS = 5
SHUTDOWN_GRACE_SECONDS = 10

SCHEDULER_TASK_NAME = "quant-scheduler"
JOB_TASK_NAME_PREFIX = "quant-job-"
RETENTION_TASK_NAME = "quant-retention"

RetentionSweep = Callable[[str], Awaitable[None]]


class RunScheduler:
    """把库里的 `queued` 行派成作业任务, 并守住并发上限.

    并发靠**信号量**而不是"数一数现在跑了几个": 后者要先查库再判断, 判断与启动之间还有 await,
    两个循环能同时通过检查. 信号量是同步的把关点, 且它天然把"排队"这件事停在 `queued` 上——
    行没被认领, 列表页看到的就是"排队中"而不是"已开始但没进程".

    `retention_sweep` 是"某一轮落终态之后"的钩子, 参数是**刚结束的那一轮**的运行号. 默认 `None`
    即不挂任何东西, 于是不关心磁盘清理的调用方 (以及绝大多数用例) 构造出来的调度器行为不变.
    """

    def __init__(
        self,
        settings: PlatformSettings,
        database: PlatformDatabase,
        *,
        retention_sweep: RetentionSweep | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._registry = RunningJobRegistry()
        self._runner = JobRunner(settings, database, self._registry)
        self._slots = asyncio.Semaphore(settings.max_concurrent_runs)
        self._wake_signal = asyncio.Event()
        self._job_tasks: set[asyncio.Task[None]] = set()
        self._job_run_ids: dict[asyncio.Task[None], str] = {}
        self._retention_sweep = retention_sweep
        self._retention_task: asyncio.Task[None] | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = True

    @property
    def registry(self) -> RunningJobRegistry:
        """在跑的作业句柄表, 供取消端点取句柄."""

        return self._registry

    async def start(self) -> None:
        """起常驻循环. 在此之前提交的作业由第一轮派发捡起, 不需要额外的补扫."""

        if self._loop_task is not None:
            raise RuntimeError("调度器已在运行")

        _ensure_subprocess_capable_loop()

        self._stopping = False
        self._loop_task = asyncio.create_task(self._run_loop(), name=SCHEDULER_TASK_NAME)
        self._loop_task.add_done_callback(_report_loop_exit)

    async def stop(self, timeout_seconds: float = SHUTDOWN_GRACE_SECONDS) -> None:
        """停循环、等在跑的作业收尾, 最后收掉保留清理任务.

        有界: 停机时限内没跑完的作业会被放掉, 它们的行留在 `running`, 由下次启动的恢复标成
        `interrupted`. 这是设计好的路径, 不是遗漏——为了等一个三十分钟的回测而让后端停不下来,
        比让一行显示"中断"糟糕得多.

        清理任务必须在**这里**收掉 (而不是任它在后台跑): 数据库门面紧接着 `scheduler.stop()`
        就被关掉, 一个还握着会话的游离任务会在连接释放之后才用上它. 取消点可能落在两次删除之间,
        故留一次"目录已删、行还在"的半成品——那正是下一次清扫幂等收掉的那一格.
        """

        self._stopping = True
        self.wake()

        loop_task = self._loop_task
        self._loop_task = None

        if loop_task is not None:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await asyncio.gather(loop_task, return_exceptions=True)
            except TimeoutError:
                logger.warning("调度循环在停机时限内未退出, 已取消该任务")

        if not await self.wait_until_idle(timeout_seconds):
            logger.warning("停机时限内仍有作业在跑, 它们会在下次启动时被标为中断")

        await self._cancel_retention_sweep()

    async def _cancel_retention_sweep(self) -> None:
        """取消在跑的保留清理并等它退出."""

        retention_task = self._retention_task
        self._retention_task = None

        if retention_task is None:
            return

        retention_task.cancel()

        await asyncio.gather(retention_task, return_exceptions=True)

    def wake(self) -> None:
        """催一次派发.

        提交端点在 `commit()` **之后**调用它. 行是持久记录、事件只是一次推, 故事件即使丢失也不
        影响正确性: 看门狗最多让作业晚 5 秒开始.
        """

        self._wake_signal.set()

    async def wait_until_idle(self, timeout_seconds: float | None = None) -> bool:
        """等在跑的作业任务全部结束, 超时返回 False.

        判据是**作业任务集合空**而不是"库里没有 running 的行": 两者差着收尾那几步, 而"可以安全
        释放资源"要求的是前者.
        """

        try:
            async with asyncio.timeout(timeout_seconds):
                while self._job_tasks:
                    await asyncio.gather(*tuple(self._job_tasks), return_exceptions=True)
        except TimeoutError:
            return False

        return True

    async def _run_loop(self) -> None:
        """常驻循环: 先清信号 → 再扫库 → 后等唤醒.

        这个次序保证**不丢唤醒**. 反过来的话, 一次落在"扫完库"与"开始等"之间的提交就会等到
        看门狗到期才被发现——那还是好的, 没有看门狗时它是**永不处理**.
        """

        logger.info("调度器已启动, 最大并发 %d", self._settings.max_concurrent_runs)

        while not self._stopping:
            self._wake_signal.clear()

            try:
                await self._dispatch_available_jobs()
            except Exception:
                # 一次派发失败不该让常驻循环退出: 循环死于一次未处理异常的表现是"平台从此不跑
                # 任何作业", 而所有 HTTP 端点都照常返回——最坏的失败方式.
                logger.exception("调度循环的一轮派发失败, 循环继续")

            try:
                async with asyncio.timeout(IDLE_WATCHDOG_SECONDS):
                    await self._wake_signal.wait()
            except TimeoutError:
                pass

        logger.info("调度器已停止")

    async def _dispatch_available_jobs(self) -> None:
        """占用空槽并派发, 直到没有空槽或没有排队作业.

        信号量在**认领之前**取. 认领后再取会让 `running` 短暂地表示"没有进程在跑": 列表页出现
        `started_at` 已填、`runner_pid` 为空的幽灵行; 超时若从 `started_at` 起算, **排队时间被
        算进运行时长** (躺了 29 分钟的作业一启动就只剩 1 分钟); 取消还要为"running 但无句柄"
        这一格单编一套逻辑——而这一格本不该存在.
        """

        while not self._stopping:
            await self._slots.acquire()

            if self._stopping:
                self._slots.release()
                return

            run_id = await self._claim_next_run()

            if run_id is None:
                self._slots.release()
                return

            handle = await self._registry.register(run_id)
            job_task = asyncio.create_task(
                self._runner.execute(handle), name=f"{JOB_TASK_NAME_PREFIX}{run_id}"
            )
            self._job_tasks.add(job_task)
            self._job_run_ids[job_task] = run_id
            job_task.add_done_callback(self._on_job_task_done)

    async def _claim_next_run(self) -> str | None:
        """取件并**条件更新**成 `running`; 取不到或抢输了返回 None.

        条件 (`Status='queued'`) 不能省. 单循环本身不会重复认领, 但**取消会翻转状态**, 于是有
        一个真实可达的交错: 调度器 SELECT 到 X → `await` 让出 → 取消把 X 写成 `interrupted` →
        无条件 UPDATE **复活** X 并真跑满整轮. 窗口只有一个 `await` 宽, **不会有任何测试自然
        覆盖它**; 条件更新把它变成 `rowcount == 0`, 而认领失败意味着该行已不是 `queued`, 下次
        SELECT 也不会重选它——**不会空转**.

        次序键除了 `SubmittedAt` 还要带 `Id`: Windows 上 `datetime.now()` 的分辨率远粗于微秒,
        同毫秒提交的两个作业 `SubmittedAt` 可以完全相同, 只按它排序时 SQLite 的并列顺序未定义,
        表现为"偶尔插队"——而那种现象不会让任何测试变红.
        """

        async with self._database.session_scope() as session:
            candidate_id = await session.scalar(
                select(RunModel.id)
                .where(RunModel.status == RunStatus.QUEUED.value)
                .order_by(RunModel.submitted_at, RunModel.id)
                .limit(1)
            )

            if candidate_id is None:
                return None

            claim = await session.execute(
                update(RunModel)
                .where(RunModel.id == candidate_id)
                .where(RunModel.status == RunStatus.QUEUED.value)
                .values(status=RunStatus.RUNNING.value, started_at=utc_now())
            )

            await session.commit()

        if not claim.rowcount:
            logger.info("运行在认领之前已被取消, 本行放弃")
            return None

        return candidate_id

    def _on_job_task_done(self, job_task: asyncio.Task[None]) -> None:
        """作业任务结束: 还槽位, 记异常, 起一次保留清理.

        还槽位必须在**这里**, 而不是在 runner 的收尾路径上: 收尾路径有一处在异常下会跳过 (比如
        写库失败), 而槽位一旦漏还, `max_concurrent_runs=1` 时表现为"从此再也不跑了".
        """

        self._job_tasks.discard(job_task)
        self._slots.release()
        finished_run_id = self._job_run_ids.pop(job_task, None)

        if job_task.cancelled():
            logger.warning("作业任务被取消, 其运行行可能未落终态 (由启动恢复兜底)")
        else:
            error = job_task.exception()

            if error is not None:
                logger.error("作业任务以未处理异常结束", exc_info=error)

        if finished_run_id is not None:
            self._start_retention_sweep(finished_run_id)

    def _start_retention_sweep(self, finished_run_id: str) -> None:
        """让清理任务游离地跑起来, 绝不 await.

        这一行在**作业任务的完成回调**里, 而回调是事件循环同步调用的: "判"与"建"之间没有 await,
        故 `_retention_task` 这个单飞护栏在单线程事件循环里就是原子的, 不需要锁.

        `await` 它会拖住槽位释放的那条路径——清理要删目录、要连库, 让它挡在"槽位归还"前面, 等于
        让磁盘上的历史决定下一个作业什么时候开始跑.

        另起一个而不是在当前任务里做, 也因为回调在**作业任务**的收尾路径上: 在那里抛异常会把一次
        正常收尾变成"作业任务以未处理异常结束".
        """

        if self._retention_sweep is None or self._stopping:
            return

        if self._retention_task is not None:
            return

        self._retention_task = asyncio.create_task(
            self._retention_sweep(finished_run_id), name=RETENTION_TASK_NAME
        )
        self._retention_task.add_done_callback(self._on_retention_task_done)

    def _on_retention_task_done(self, retention_task: asyncio.Task[None]) -> None:
        """清理任务收尾: 放开单飞护栏, 记异常.

        放护栏必须发生, 否则第一次清扫之后再也不会有第二次. 取 `exception()` 也是必须的——不去
        取的话, 一个失败的游离任务只在进程退出时抛一句 "Task exception was never retrieved".
        """

        self._retention_task = None

        if retention_task.cancelled():
            logger.info("保留清理任务在停机时被取消, 未删完的下次再收")
            return

        error = retention_task.exception()

        if error is not None:
            logger.error("保留清理任务以未处理异常结束", exc_info=error)



def _ensure_subprocess_capable_loop() -> None:
    """确认当前事件循环能起子进程.

    `create_subprocess_exec` 在 Windows 上**只在 `ProactorEventLoop` 下可用**,
    `SelectorEventLoop` 会抛 `NotImplementedError`. 启动即报错并给出原因, 比第一次提交时抛一个
    裸的 `NotImplementedError` 好归因得多——后者看起来像"引擎起不来".
    """

    if sys.platform != "win32":
        return

    if isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
        return

    raise RuntimeError(
        "调度器需要 ProactorEventLoop: Windows 的 SelectorEventLoop 不支持子进程"
    )


def _report_loop_exit(loop_task: asyncio.Task[None]) -> None:
    """常驻循环意外结束时记一条 error.

    循环退出后平台照常接收提交、照常回 201, 只是再也不会有人执行它们——不记这一条, 现象就只能
    靠"作业一直排队中"反推.
    """

    if loop_task.cancelled():
        return

    error = loop_task.exception()

    if error is not None:
        logger.error("调度循环意外退出, 平台将不再执行新提交的作业", exc_info=error)
