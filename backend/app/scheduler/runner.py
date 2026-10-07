"""单作业的执行与收尾.

本模块是**唯一**写运行终态的地方. 别处只读状态, 或者只在自己的条件更新里翻转一次
`queued`→`interrupted` (取消排队中的作业) —— 终态算一次、写一次, 才不会出现两个写入者
各判一次然后分叉.

**为什么是"仲裁"而不是"判断"**: Windows 上 `terminate()` 走 `TerminateProcess`, 被杀进程的
退出码是 **1**, 与"宿主启动失败"**同码**. 单看退出码分不出"被取消"与"策略起不来", 只能靠标志
消歧; 再叠上"退出码 0 但结果文件缺失"与"退出码 3 但结果是崩溃"两处歧义, 判据就成了一组条件
而不是一个表达式. 故这里把全部条件收进一个**纯函数** `arbitrate_terminal_state`, 状态与文案
由它一次算出——分成两处算, 迟早会出现 `failed` 却写着"已取消"这种自相矛盾的行.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import update

from ..catalog.database import PlatformDatabase
from ..catalog.enums import RunStatus
from ..catalog.models import RunModel, StrategyVersionModel
from ..clock import utc_now
from ..config import PlatformSettings
from ..reference_data.rate_expansion import RunContract, describe_missing_rates
from ..reference_data.seed_database import (
    build_seed_database_staging_path,
    discard_staged_seed_database,
    generate_run_seed_database,
)
from ..services.engine_probe import is_python_binding_available
from ..services.market_data_preparation import (
    MarketDataPrepared,
    MarketDataRequest,
    MarketDataUnavailableError,
    ensure_market_data_available,
)
from ..services.run_configuration import decode_run_fields
from .engine_config import parse_configuration_object
from .output import OUTPUT_READ_CHUNK_BYTES, JobOutputCapture
from .registry import JobHandle, RunningJobRegistry
from .result import (
    RESULT_FILENAME,
    build_mirror_column_values,
    read_reported_success,
    read_result_file,
)
from .workspace import JobFileSet, build_job_directory


logger = logging.getLogger(__name__)

STDOUT_FILENAME = "stdout.txt"
STDERR_FILENAME = "stderr.txt"

TERMINATE_GRACE_SECONDS = 5
KILL_GRACE_SECONDS = 5
PUMP_DRAIN_GRACE_SECONDS = 5

MAXIMUM_ERROR_MESSAGE_LENGTH = 1000

JOB_DIRECTORY_FAILURE_MESSAGE = "作业目录构造失败"
SEED_DATABASE_FAILURE_MESSAGE = "基本数据种子库生成失败"
MISSING_RATE_FAILURE_MESSAGE_TEMPLATE = (
    "{commission_group_id} 号组下没有 {contract_rates} 的费率, 这一轮的费用会全按 0 算, "
    "故不再跑. 请到「基础数据」页补一条费率后重新提交"
)
# 冻结配置里读不出组号: 只可能是那一轮的配置文本被改坏了 (本平台的写路径总会写下它). 说"读不动"
# 而不是替它认领 1 号组——认领一个组号就是按一套不知道是谁的费率计费, 而这一轮看起来会完全正常.
UNDECODABLE_COMMISSION_GROUP_MESSAGE = (
    "这一轮的引擎配置里读不出手续费组号, 无法确定该用哪一套费率, 故不再跑"
)
MISSING_VERSION_MESSAGE = "运行引用的策略版本不存在, 无法执行"
INTERNAL_FAILURE_MESSAGE = "调度器内部异常, 运行被中止"

CANCEL_MESSAGE = "运行已被取消"
TIMEOUT_MESSAGE = "运行超出时限, 已中止"
TIMEOUT_AFTER_CANCEL_MESSAGE = "运行超出时限且已收到取消请求, 已中止"

HOST_STARTUP_FAILURE_MESSAGE = "回测宿主启动失败"
RESULT_MISSING_MESSAGE = "结果文件缺失或无法解析"
SUCCESS_WITHOUT_RESULT_MESSAGE = "宿主正常退出但没有产出结果文件"
ENGINE_FAILURE_MESSAGE = "引擎报告本轮回测失败"
# 只有这些终态下的结果文件才权威. 被取消或被超时收掉的一轮**没有"跑完"这回事**: 它的结果文件
# 要么不存在, 要么是"进程恰好已经写完、而用户在它退出前点了取消"的竞态产物. 把指标写上去会留下
# 一行 `interrupted` 带着 `trade_count` / `balance` 的记录——状态与指标各说各话, 前端无从判断该
# 显示什么. 这与 §4.3 里"收尾抢输时不得写任何指标列"是同一条理由, 只是那条走的是窄更新这条路.
STATUSES_THAT_KEEP_THE_ENGINE_REPORT = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED}
)

MISSING_BINDING_MESSAGE = (
    "提示: 引擎根下没有与当前解释器匹配的扩展模块, 引擎类策略无法加载\n"
)


@dataclass(frozen=True)
class TerminalVerdict:
    """一轮作业的终态与配套文案.

    两者由同一个函数一起算出, 不分开算: `interrupted` 配一句"宿主启动失败"这种自相矛盾的行,
    前端拿到就不知道该显示什么.
    """

    status: RunStatus
    error_message: str


def arbitrate_terminal_state(
    *,
    exit_code: int | None,
    timed_out: bool,
    cancel_requested: bool,
    run_result: Mapping[str, object] | None,
    platform_error_message: str | None,
) -> TerminalVerdict:
    """按仲裁次序算终态与文案.

    次序即优先级, 与 `job-workspace.md` §4 的表逐行对应:

    1. 超时与取消压在最前. `timeout` 压过 `interrupted`——超时是"截止时刻真的过去了"这个与
       用户无关的事实, 而取消可能落在超时之后的宽限期里; 两者都记进文案, 不丢信息.
    2. 平台侧的准备失败 (目录构造、版本缺失) 直接判失败: 那时 `exit_code` 是 None, 落到"其余"
       只会得到一句通用文案, 而这类原因恰恰是最需要说清楚的.
    3. 退出码 0 **不足以**判成功: 结果文件必须存在、可解析、且 `Success` 为真. 少了这一条,
       一个把 `result.json` 写坏的策略会被当成跑成功了.
    4. 退出码 3 是真歧义: 既可能是引擎报的失败 (结果文件在, 可镜像), 也可能是宿主崩溃
       (无结果文件). 判据是文件, 不是码.
    """

    if platform_error_message is not None:
        return TerminalVerdict(RunStatus.FAILED, platform_error_message)

    if timed_out:
        return TerminalVerdict(
            RunStatus.TIMEOUT,
            TIMEOUT_AFTER_CANCEL_MESSAGE if cancel_requested else TIMEOUT_MESSAGE,
        )

    if cancel_requested:
        return TerminalVerdict(RunStatus.INTERRUPTED, CANCEL_MESSAGE)

    if exit_code == 0:
        if run_result is None:
            return TerminalVerdict(RunStatus.FAILED, SUCCESS_WITHOUT_RESULT_MESSAGE)

        if read_reported_success(run_result) is True:
            return TerminalVerdict(RunStatus.SUCCEEDED, "")

        return TerminalVerdict(RunStatus.FAILED, ENGINE_FAILURE_MESSAGE)

    if exit_code == 1:
        return TerminalVerdict(RunStatus.FAILED, HOST_STARTUP_FAILURE_MESSAGE)

    if exit_code == 2:
        return TerminalVerdict(RunStatus.FAILED, RESULT_MISSING_MESSAGE)

    if exit_code is None:
        return TerminalVerdict(RunStatus.FAILED, INTERNAL_FAILURE_MESSAGE)

    return TerminalVerdict(RunStatus.FAILED, build_unexpected_exit_message(exit_code))


def build_unexpected_exit_message(exit_code: int) -> str:
    """未被逐条收录的退出码的文案: 只说平台看得见的事实, 不猜原因.

    退出码 3 有两条路径到这里或者到"引擎报告失败": 差别是**结果文件在不在**. 没有结果文件时
    平台看不见任何原因, 只能如实说"异常退出 (退出码 3)"; 若照抄"引擎报告本轮回测失败", 平台就
    在替引擎断言一件它看不到的事——用户照着这句话去查引擎日志, 而日志根本不存在.
    """

    return f"回测宿主异常退出 (退出码 {exit_code})"


@dataclass(frozen=True)
class LaunchContext:
    """起进程所需的全部输入, 一次性从库里读齐.

    两个配置文本取自运行行 (`BacktestConfigJson` / `ParamsJson`): 提交侧渲染时就把它们连同
    运行行一起提交了, 于是调度侧不必回头重算一遍——"库里记的"与"盘上写的"永远是同一份.

    末尾四个行情字段与组号都是从那两份文本里**解**出来的, 不是另读一列: 运行行不存它们, 存的就是
    那两份文本 (见 `run_prefill` 的同一条理由). `exchange_id` / `instrument_id` 为空串是正常的
    ——那一轮没指名合约, 行情准备会跳过.

    `commission_group_id` 解不出来时**不进这里**: 它是"这一轮按哪套费率计费"的唯一依据, 认一个
    默认组等于替用户选一套费率, 故构造上下文之前就抛 `UnrunnableJob` (见 `_load_launch_context`).

    用户的订阅周期**不在这里**: 它不影响"行情备不备得齐" (那只看数据源精度, 是个平台常量), 故
    起进程前没有任何一处要用到它.
    """

    run_id: str
    engine_configuration_text: str
    strategy_configuration_text: str
    entry_filename: str
    configuration_filename: str
    entry_source_path: Path
    started_at: datetime | None
    exchange_id: str
    instrument_id: str
    start_trading_day: str
    end_trading_day: str
    commission_group_id: int


@dataclass
class JobExecution:
    """一次作业体的可变状态, 收尾时按它算终态."""

    job_directory: Path | None = None
    process: asyncio.subprocess.Process | None = None
    output_capture: JobOutputCapture | None = None
    pump_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    exit_code: int | None = None
    platform_error_message: str | None = None
    started_at: datetime | None = None
    abandoned: bool = False


class UnrunnableJob(Exception):
    """作业在起进程之前就确定跑不成, 附一句可以直接进 `ErrorMsg` 的文案.

    文案必须是固定中文字符串: 异常自身携带的 `OSError` 消息里带着绝对路径, 经响应体回给客户端
    就是泄漏服务端目录布局与别人的用户 id.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class JobRunner:
    """执行单个作业, 并在结束时把终态写回运行行."""

    def __init__(
        self,
        settings: PlatformSettings,
        database: PlatformDatabase,
        registry: RunningJobRegistry,
    ) -> None:
        self._settings = settings
        self._database = database
        self._registry = registry

    async def execute(self, handle: JobHandle) -> None:
        """执行一个作业, 无论成败都在返回前写定终态并出表.

        出表放在 `finally`: 等待者 (取消端点) 等的是 `finished`, 而它只在出表时被唤醒. 若只在
        正常路径出表, 一个在收尾前抛异常的作业会把每个取消请求都挂满它那两秒的兜底等待.
        """

        try:
            await self._execute_job(handle)
        finally:
            await self._registry.retire(handle.run_id)

    async def _execute_job(self, handle: JobHandle) -> None:
        """作业的执行与收尾.

        三个出口各有各的收尾: 取消要先收进程再写终态 (`CancelledError` 必须原样上抛, 否则任务
        取消语义被吞掉), 其余异常按内部故障记一条 failed, 超时则由作业体那一层的
        `asyncio.timeout` 就地收掉 (见 `_run_job`). 三者都会走到 `_finalize`, 因为"这一轮到底
        算什么"只该在那一处决定.
        """

        execution = JobExecution()

        try:
            await self._run_job(handle, execution)
        except asyncio.CancelledError:
            await self._abandon(execution)
            await self._finalize(handle, execution)
            raise
        except Exception:
            logger.exception("作业执行过程中出现未预期异常")
            execution.platform_error_message = INTERNAL_FAILURE_MESSAGE
            await self._abandon(execution)

        await self._finalize(handle, execution)

    async def _run_job(self, handle: JobHandle, execution: JobExecution) -> None:
        """作业体: 读上下文 → 生种子库 → 建目录 → 备行情 → 起进程 → 等它退出.

        **作业级超时只包住最后那一段** (`_launch_and_await_exit`), 这是刻意的: 它原先包着整个
        作业体, 而行情下载一旦落进预算里, 一次长下载就会被记成 `timeout` + "运行超出时限", 把
        "行情下得慢"说成"策略跑太久". 两者对用户的指向完全不同, 不能混成一个.
        """

        try:
            launch_context = await self._load_launch_context(handle.run_id)
        except UnrunnableJob as error:
            execution.platform_error_message = error.message
            return

        if launch_context is None:
            execution.abandoned = True
            return

        execution.started_at = launch_context.started_at

        try:
            seed_database_source_path = await self._stage_run_seed_database(launch_context)
        except UnrunnableJob as error:
            execution.platform_error_message = error.message
            return
        except Exception:
            # 捕获面与启动期那两步同理 (见 `main.py` 的 `_run_startup_step`): 这一步可能抛磁盘错、
            # 库错, 也可能抛"契约与模型对不上"那种代码错, 而在**此处**它们都该以同一种方式收场
            # —— 这一轮失败, 且失败得说得清是哪一步. 截断成 500 只会把它归因成"调度器内部异常".
            logger.exception("种子库生成失败")
            execution.platform_error_message = SEED_DATABASE_FAILURE_MESSAGE
            return

        try:
            job_directory = build_job_directory(
                self._settings,
                self._build_job_file_set(launch_context, seed_database_source_path),
            )
        except OSError:
            logger.exception("作业目录构造失败")
            execution.platform_error_message = JOB_DIRECTORY_FAILURE_MESSAGE
            return
        finally:
            # 成功时那份已经被搬进作业目录 (`os.replace`), 这一句是空动作; 失败时它清掉暂存的
            # 那一份. 于是无论走哪条路, 运行根里都不会剩下这一轮的种子库.
            discard_staged_seed_database(seed_database_source_path)

        execution.job_directory = job_directory

        # 放在目录登记之后: 准备阶段的失败与 `JOB_DIRECTORY_FAILURE_MESSAGE` 走同一条路
        # (已登记句柄、进程未起、`platform_error_message` 定终态), 形状因此是同一个.
        if not await self._prepare_market_data(handle, execution, launch_context):
            return

        if handle.cancel_signal.is_set():
            # 取消落在"已认领但进程未起"这一格: 根本不起进程, 终态由仲裁表按取消算出. 这里省下
            # 的不只是一次进程——真起来的话它会在 job 目录里写出日志与库文件, 而用户已经取消了.
            logger.info("运行在起进程之前已收到取消请求, 不再启动宿主")
            return

        try:
            async with asyncio.timeout(self._settings.run_timeout_seconds) as job_timeout:
                await self._launch_and_await_exit(
                    handle, execution, launch_context, job_directory
                )
        except TimeoutError:
            # 只有**本层**的截止时刻真的到了才算超时. 内层的每一处有界等待都自己消化掉了
            # TimeoutError (超时阶梯、管道排水), 故这里若不加这一问, 日后某处漏消化一个, 就会
            # 把"起不来"写成"跑超时"——而两者的处置完全不同.
            if not job_timeout.expired():
                raise

            handle.timed_out = True
            await self._abandon(execution)

    async def _prepare_market_data(
        self, handle: JobHandle, execution: JobExecution, launch_context: LaunchContext
    ) -> bool:
        """确保这一轮要用的行情已在本地落地; 回 False 表示这一轮不该起引擎.

        失败**不抛异常**: 与 `UnrunnableJob` 走同一条路——记一句能直接进 `ErrorMsg` 的固定中文
        文案, 终态由仲裁表算成 `failed`, `ErrorId` 保持 NULL (这不是引擎报的错).

        被取消时**不设**文案, 于是仲裁表按取消把它算成 `interrupted`: 用户点了取消, 就不该收到
        一句"行情下载失败".
        """

        request = MarketDataRequest(
            exchange_id=launch_context.exchange_id,
            instrument_id=launch_context.instrument_id,
            start_trading_day=launch_context.start_trading_day,
            end_trading_day=launch_context.end_trading_day,
        )

        try:
            prepared = await ensure_market_data_available(
                self._settings, request, handle.cancel_signal
            )
        except MarketDataUnavailableError as error:
            logger.warning("运行 %s 的行情准备未通过: %s", handle.run_id, error.message)
            execution.platform_error_message = error.message
            return False

        return prepared is MarketDataPrepared.READY

    async def _launch_and_await_exit(
        self,
        handle: JobHandle,
        execution: JobExecution,
        launch_context: LaunchContext,
        job_directory: Path,
    ) -> None:
        """起进程、并发收两条输出流、等退出, 并在取消信号到达时收掉进程.

        超时由外层的作业级 `asyncio.timeout` 负责, 这里只管"取消"这一路: 两者最终都走同一个
        `_abandon`, 故升级阶梯 (terminate → kill) 只有一份实现.

        `with` 不是装饰: 两条输出流的文件句柄必须**确定性**释放. 漏掉它的话句柄会活到进程结束,
        而 Windows 上被打开的句柄会让作业目录删不掉、也改不了名——症状是"跑过几轮之后磁盘上
        那些目录再也清不掉", 与输出捕获这个模块毫无表面关联. 内存里的尾部不受影响 (读的是
        字节缓冲, 不是文件), 故收尾照常能取到 `StdoutTail`.
        """

        with JobOutputCapture(
            job_directory / STDOUT_FILENAME,
            job_directory / STDERR_FILENAME,
            self._settings.maximum_output_tail_bytes,
        ) as output_capture:
            execution.output_capture = output_capture

            await self._supervise_process(handle, execution, launch_context, job_directory)

    async def _supervise_process(
        self,
        handle: JobHandle,
        execution: JobExecution,
        launch_context: LaunchContext,
        job_directory: Path,
    ) -> None:
        """起宿主进程并在其整个生命周期内盯着它."""

        output_capture = execution.output_capture

        if output_capture is None:
            raise RuntimeError("未装好输出捕获就进入进程监督")

        self._report_missing_binding(output_capture)

        process = await asyncio.create_subprocess_exec(
            # 解释器必须显式给出: Windows 的 `CreateProcess` **不认文件关联**, 直接拿
            # `grid_strategy.py` 当可执行文件会得到 `WinError 2`（已实测）. 用 `sys.executable`
            # 还顺带把"跑作业的解释器 == health 检查的那个解释器"从约定变成结构——health 报就绪
            # 与作业跑得起来因此是同一件事.
            sys.executable,
            # 但 `argv[0]` 仍必须是**裸文件名**: 引擎日志器按 `strrchr` 找反斜杠、再取首个点之前
            # 的部分作日志名, 拿到路径就拼不出合法路径, `fopen` 失败后整个进程在启动期终止.
            launch_context.entry_filename,
            cwd=job_directory,
            env=self._build_child_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        execution.process = process

        await self._registry.attach_process(handle.run_id, process)
        await self._record_runner_pid(handle.run_id, process.pid)

        stdout_reader = process.stdout
        stderr_reader = process.stderr

        if stdout_reader is None or stderr_reader is None:
            raise RuntimeError("子进程的输出管道未建立")

        # 两条流必须并发读. Windows 管道缓冲约 64 KB, 串行读时一个往 stderr 写超过 64 KB 的
        # 策略会永久阻塞在写系统调用上, `wait()` 永不返回, 直到作业级超时才被**误判成超时**——
        # 症状是"策略明明跑完却超时", 只在输出量大时出现.
        execution.pump_tasks = [
            asyncio.create_task(_pump_output(stdout_reader, output_capture.append_stdout)),
            asyncio.create_task(_pump_output(stderr_reader, output_capture.append_stderr)),
        ]

        await self._await_process_or_cancel(handle, execution)
        await self._drain_pumps(execution)

    async def _await_process_or_cancel(
        self, handle: JobHandle, execution: JobExecution
    ) -> None:
        """等进程退出, 或等来取消信号后收掉它.

        取消[只置标志]是不够的: 进程不会因此退出, `wait()` 也不会因此返回, 作业会一直挂到自然
        退出或作业级超时——而那时算出来的终态是 `timeout`, 与用户点的"取消"完全不是一回事.
        """

        process = execution.process

        if process is None:
            raise RuntimeError("未起进程就进入等待")

        exit_waiter = asyncio.ensure_future(process.wait())
        cancel_waiter = asyncio.ensure_future(handle.cancel_signal.wait())

        try:
            completed_waiters, _ = await asyncio.wait(
                {exit_waiter, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            cancel_waiter.cancel()

        if cancel_waiter in completed_waiters:
            logger.info("运行收到取消请求, 正在收掉宿主进程")
            await self._abandon(execution)

        await exit_waiter

        execution.exit_code = process.returncode

    async def _abandon(self, execution: JobExecution) -> None:
        """收掉进程并停止读输出; 可重复调用."""

        await self._terminate_process(execution)
        await self._drain_pumps(execution)

    async def _terminate_process(self, execution: JobExecution) -> None:
        """超时/取消后的进程回收: terminate → 有界等 → kill → 有界等.

        两点必须说清:

        1. **Windows 上 `terminate()` 与 `kill()` 都是 `TerminateProcess`**, 没有 SIGTERM, 两步
           之间买不到"优雅退出". 保留两步是为了把 `wait()` **有界化**: 它可能卡在驱动调用里.
           也**不要**指望被杀进程还写完 `result.json`——取消或超时之后它一定不存在.
        2. `kill()` 之后仍等不到退出时**绝不**继续无限期等: 那会永久占住一个槽位,
           `max_concurrent_runs=1` 时表现为"从此再也不跑了". 记一条 error 就放手, 宁可留一个
           僵尸进程, 也不能让队列永久降级.
        """

        process = execution.process

        if process is None or process.returncode is not None:
            return

        process.terminate()

        if not await _wait_for_exit(process, TERMINATE_GRACE_SECONDS):
            logger.warning("宿主进程在 terminate 后仍在运行, 升级为 kill")
            process.kill()

            if not await _wait_for_exit(process, KILL_GRACE_SECONDS):
                logger.error("宿主进程在 kill 后仍未退出, 放弃等待 (可能留下僵尸进程)")
                return

        # 谁收的进程谁记退出码. 超时那一路**不是**从 `_await_process_or_cancel` 的
        # `await exit_waiter` 出来的 (那条等待已被作业级超时取消), 退出码只能在这里补上; 否则
        # "进程被平台杀掉了"这件事在行上只体现为状态, 而 `ExitCode` 是 NULL——与取消那一路
        # (同一个 `_abandon`, 却因为等过 `exit_waiter` 而记了码) 差成一个无意的形态差异.
        execution.exit_code = process.returncode

    async def _drain_pumps(self, execution: JobExecution) -> None:
        """等两条输出流读到 EOF.

        `wait()` 返回 ≠ 输出读完, 必须显式收拢: 否则会丢掉退出瞬间仍在管道里的最后几行, 而那几行
        往往正是崩溃堆栈. 兜底时限是防呆——真读不完说明管道那一侧还有活着的进程, 继续等就把本作业
        的槽位永久占住了.

        超时后**必须把两条读泵取消掉**: 它们仍会往输出文件里写, 而文件句柄就要在上一层被关掉,
        留着跑的读泵会在下一次落盘时撞上"文件已关闭"并抛出一条没人接的异常.
        """

        if not execution.pump_tasks:
            return

        try:
            async with asyncio.timeout(PUMP_DRAIN_GRACE_SECONDS):
                await asyncio.gather(*execution.pump_tasks, return_exceptions=True)
        except TimeoutError:
            logger.warning("宿主退出后输出管道仍读不完, 已放弃等待剩余输出")

            for pump_task in execution.pump_tasks:
                pump_task.cancel()

    async def _load_launch_context(self, run_id: str) -> LaunchContext | None:
        """读齐起进程所需的一切; 返回 None 表示这一行已不由本作业负责.

        "已不由本作业负责"有两种: 行不存在 (被谁删了), 或行已不是 `running` (取消抢在登记句柄
        之前把它 CAS 成了 `interrupted`). 两种都不该再碰这一行——终态已经有人写过.
        """

        async with self._database.session_scope() as session:
            run = await session.get(RunModel, run_id)

            if run is None:
                logger.error("运行记录不存在, 作业不再执行")
                return None

            if run.status != RunStatus.RUNNING.value:
                logger.info("运行在起进程之前已不是 running, 作业不再执行")
                return None

            version = await session.get(StrategyVersionModel, run.strategy_version_id)

            if version is None:
                raise UnrunnableJob(MISSING_VERSION_MESSAGE)

            decoded_fields = self._decode_launch_fields(run)

            if decoded_fields.commission_group_id is None:
                raise UnrunnableJob(UNDECODABLE_COMMISSION_GROUP_MESSAGE)

            return LaunchContext(
                run_id=run.id,
                engine_configuration_text=run.backtest_config_json,
                strategy_configuration_text=run.params_json,
                entry_filename=version.entry_filename,
                configuration_filename=version.config_filename,
                entry_source_path=(
                    self._settings.user_library_root
                    / version.storage_path
                    / version.entry_filename
                ),
                started_at=run.started_at,
                exchange_id=decoded_fields.exchange_id,
                instrument_id=decoded_fields.instrument_id,
                # 两个交易日也来自配置文本, **不能**取行上那两列: 它们在提交时并不写, 要等引擎
                # 结果回写才有值——起进程那一刻它们还是 NULL.
                start_trading_day=decoded_fields.start_trading_day,
                end_trading_day=decoded_fields.end_trading_day,
                commission_group_id=decoded_fields.commission_group_id,
            )

    def _decode_launch_fields(self, run: RunModel) -> DecodedRunFields:
        """从运行行那两份配置文本里解出起进程前要用的那几个取值.

        解的是行情准备与种子库生成两处要看的字段: 前者看交易对, 后者还看手续费组号.

        读不动**不抛**: 配置文本坏掉时引擎侧自会以它的方式失败, 而这里若提前抛, 就会把一个
        "文本坏了"说成"行情备不齐". 行情那两个字段解不出来只表现为空串, 随后由行情准备给出确切
        文案; 而手续费组号解不出来 (`None`) 会由调用点译成 `UNDECODABLE_COMMISSION_GROUP_MESSAGE`
        —— 判"要不要因此不跑"是调用点的事, 这个函数只负责如实退回取值.

        订阅周期 (`bar_period`) **不在其列**——行情准备只认数据源精度 (平台常量), 用户选的周期
        不改变"这份数据在不在", 故它不参与起进程前的任何判断.
        """

        return decode_run_fields(
            parse_configuration_object(run.id, run.backtest_config_json) or {},
            parse_configuration_object(run.id, run.params_json) or {},
        )

    def _build_job_file_set(
        self, launch_context: LaunchContext, seed_database_source_path: Path
    ) -> JobFileSet:
        """作业目录的输入清单."""

        return JobFileSet(
            run_id=launch_context.run_id,
            entry_filename=launch_context.entry_filename,
            entry_source_path=launch_context.entry_source_path,
            strategy_configuration_filename=launch_context.configuration_filename,
            strategy_configuration_text=launch_context.strategy_configuration_text,
            engine_configuration_text=launch_context.engine_configuration_text,
            seed_database_source_path=seed_database_source_path,
        )

    async def _stage_run_seed_database(self, launch_context: LaunchContext) -> Path:
        """给这一轮生成种子库, 返回它的暂存路径 (还没进作业目录).

        内容是按**这一轮那一个合约**展开出来的: 管理端存的是按合约 / 品种 / 交易所三级设置的
        规则, 而引擎的表里没有"品种"这一档的位置, 通配只能在这里展开掉 (见 `rate_expansion`).

        展开的是**这一轮冻结的那个组** (`launch_context.commission_group_id`), 与提交期那次校验
        读的是同一个取值——两处各读各的话, 会出现"校验时看 2 号组、跑起来按 1 号组生成种子库", 而
        那一轮照样跑完, 只是钱算的不是用户选的那套.

        这一格没有费率时抛 `UnrunnableJob` 而不是照跑: 引擎遇到查不到的费率不报错, 它会**跑完**
        并把缺口记进 `result.json`, 于是用户拿到一个手续费为零的回测 —— 那看起来与"策略真的一分
        钱没花"没有区别. 提交期已经拦过一次, 走到这里还缺, 只可能是规则在提交之后被改掉了.
        """

        staging_path = build_seed_database_staging_path(self._settings.runs_root)

        async with self._database.session_scope() as session:
            expansion = await generate_run_seed_database(
                session,
                staging_path,
                launch_context.commission_group_id,
                (
                    RunContract(
                        exchange_id=launch_context.exchange_id,
                        instrument_id=launch_context.instrument_id,
                    ),
                ),
            )

        if expansion.missing_rates:
            discard_staged_seed_database(staging_path)

            raise UnrunnableJob(
                MISSING_RATE_FAILURE_MESSAGE_TEMPLATE.format(
                    commission_group_id=launch_context.commission_group_id,
                    contract_rates=describe_missing_rates(expansion.missing_rates),
                )
            )

        return staging_path

    async def _record_runner_pid(self, run_id: str, process_id: int) -> None:
        """把宿主进程号写进运行行.

        条件与认领同一条 (`Status='running'`): 行若已被取消写成终态, 这行 pid 就是一条不存在的
        证据——取消发生在起进程之后时它本该留下, 发生在之前时它不该出现.
        """

        async with self._database.session_scope() as session:
            await session.execute(
                update(RunModel)
                .where(RunModel.id == run_id)
                .where(RunModel.status == RunStatus.RUNNING.value)
                .values(runner_pid=process_id)
            )
            await session.commit()

    def _build_child_environment(self) -> dict[str, str]:
        """子进程环境: 在既有 `PYTHONPATH` **之前**追加引擎根.

        追加而不是覆盖: 调用方 (或运维脚本) 设的路径里可能有引擎额外要加载的东西, 覆盖掉它不会
        报错, 只会让宿主在 `import` 阶段以退出码 1 收场.
        """

        engine_root = str(self._settings.engine_root)
        inherited_path = os.environ.get("PYTHONPATH", "")
        search_path = os.pathsep.join(
            [engine_root, *[part for part in inherited_path.split(os.pathsep) if part]]
        )

        return {**os.environ, "PYTHONPATH": search_path}

    def _report_missing_binding(self, output_capture: JobOutputCapture) -> None:
        """ABI 不匹配时写一条诊断, **绝不做成硬闸**.

        做成硬闸看着更负责, 但测试的引擎根是空目录, 那会让每一个走桩策略的用例全部失败, 整套
        测试因此废掉; 且引擎类策略起不来时本来就有退出码 1 与引擎日志可查, 这条诊断只负责把
        "日志里什么都没有"变成"日志里有一句话说为什么".
        """

        if is_python_binding_available(self._settings.engine_root):
            return

        logger.warning("引擎根下没有与当前解释器匹配的扩展模块, 引擎类策略将无法加载")
        output_capture.append_stderr(MISSING_BINDING_MESSAGE.encode("utf-8"))

    async def _finalize(self, handle: JobHandle, execution: JobExecution) -> None:
        """算定终态并写回运行行.

        收尾走**条件更新**: 取消与收尾谁先提交谁赢, 输的一方拿到 `rowcount == 0` 并原样退出——
        比锁好, 因为不依赖两处代码按同一顺序拿锁. 推论是输的那一方**不得写任何指标列**, 否则一行
        `interrupted` 会带着 `balance=…` / `trade_count=84`, 与状态自相矛盾, 前端无从判断该显示
        什么; 它只做一次不碰 `Status` 的窄更新, 补上输出与退出码.
        """

        if execution.abandoned:
            return

        run_result = (
            read_result_file(execution.job_directory / RESULT_FILENAME)
            if execution.job_directory is not None
            else None
        )

        verdict = arbitrate_terminal_state(
            exit_code=execution.exit_code,
            timed_out=handle.timed_out,
            cancel_requested=handle.cancel_signal.is_set(),
            run_result=run_result,
            platform_error_message=execution.platform_error_message,
        )

        finished_at = utc_now()
        output_capture = execution.output_capture

        terminal_values: dict[str, object] = {
            "status": verdict.status.value,
            "finished_at": finished_at,
            "duration_ms": _elapsed_milliseconds(execution.started_at, finished_at),
            "exit_code": execution.exit_code,
            "error_msg": verdict.error_message,
            "stdout_tail": output_capture.stdout_tail if output_capture else "",
            "stderr_tail": output_capture.stderr_tail if output_capture else "",
        }

        if (
            run_result is not None
            and verdict.status in STATUSES_THAT_KEEP_THE_ENGINE_REPORT
        ):
            # 镜像里含 `ErrorMsg`: 引擎自己说的原因比平台那句通用文案具体, 有就盖掉它. 这一步只对
            # "跑完了"的那些终态生效, 故被判 `interrupted` / `timeout` 的一轮保住自己的取消/超时
            # 文案——与"收尾抢输时不得写指标列"是同一条理由的两个面.
            terminal_values.update(build_mirror_column_values(run_result))

        terminal_values["error_msg"] = _truncate_error_message(
            str(terminal_values["error_msg"])
        )

        await self._write_terminal_state(handle.run_id, terminal_values)

    async def _write_terminal_state(
        self, run_id: str, terminal_values: dict[str, object]
    ) -> None:
        """条件更新写终态; 抢输了则退化成一次不碰状态的窄更新."""

        async with self._database.session_scope() as session:
            committed = await session.execute(
                update(RunModel)
                .where(RunModel.id == run_id)
                .where(RunModel.status == RunStatus.RUNNING.value)
                .values(**terminal_values)
            )

            if committed.rowcount:
                await session.commit()
                return

            logger.info("终态已被他人先写入, 本次收尾只补输出与退出码")

            await session.execute(
                update(RunModel)
                .where(RunModel.id == run_id)
                .values(
                    stdout_tail=terminal_values["stdout_tail"],
                    stderr_tail=terminal_values["stderr_tail"],
                    exit_code=terminal_values["exit_code"],
                )
            )
            await session.commit()


async def _pump_output(
    reader: asyncio.StreamReader, append_chunk: Callable[[bytes], None]
) -> None:
    """把一条输出流整条读到 EOF, 逐块交给落盘侧.

    落盘写**原始字节**: stdout.txt / stderr.txt 按引擎实际吐出的字节存, 任何编码都能事后重新
    解释; 落成解码后的文本会永久丢掉原编码, 而引擎在中文 Windows 上有 GBK 变体.
    """

    while True:
        chunk = await reader.read(OUTPUT_READ_CHUNK_BYTES)

        if not chunk:
            return

        append_chunk(chunk)


async def _wait_for_exit(
    process: asyncio.subprocess.Process, timeout_seconds: int
) -> bool:
    """有界地等进程退出; 等到了返回 True.

    每次调用都**重新**取一个 `wait()` 协程. 被取消的协程不能再次 await, 而在 3.14 上
    `Process.wait()` 每次都会新建底层 waiter (`BaseSubprocessTransport._wait` 内部新建 Future),
    故重新调用是安全的——但这条性质是版本相关的, 换成"复用同一个 wait 协程"的写法会在别的
    版本上炸成 `RuntimeError`, 故不图省事.
    """

    try:
        async with asyncio.timeout(timeout_seconds):
            await process.wait()
    except TimeoutError:
        return False

    return True


def _elapsed_milliseconds(
    started_at: datetime | None, finished_at: datetime
) -> int | None:
    """运行耗时.

    排队中被取消时 `started_at` 为 NULL, 此时耗时必须是 NULL 而不是 0——0 会被读成"瞬间跑完",
    而真相是"根本没开始跑".
    """

    if started_at is None:
        return None

    return int((finished_at - started_at).total_seconds() * 1000)


def _truncate_error_message(message: str) -> str:
    """把文案截到列宽之内.

    `ErrorMsg` 是 `String(1000)` 而 SQLite 不强制列宽: 超长的值会被静默存下, 再经响应体原样
    回给客户端. 截断放在**唯一**的写入口, 好过在每个产生文案的地方各写一次.
    """

    return message[:MAXIMUM_ERROR_MESSAGE_LENGTH]
