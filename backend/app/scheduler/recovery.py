"""启动恢复: 把上次进程留下的未完成运行一次结清.

一条 UPDATE 而不是逐行循环: 逐行判"这一行现在还在不在跑"需要重新探活, 而在 Windows 上探活
本身就是一个陷阱 (`os.kill(pid, 0)` **不是探活, 是杀进程**——CPython 走 `OpenProcess` +
`TerminateProcess`). 后端既已重启, 上一个进程里跑的作业就都已经不属于任何活着的执行者了.

**单实例是硬前提**: 若哪天用 `uvicorn --workers 2` 起后端, B 进程的恢复会把 A 正在跑的作业
标成 `interrupted`, 而 A 的收尾条件更新会因行已不是 `running` 而全部空转——表现为"多 worker
下作业永远不落结果". 故此处记一条 warning, 让这个前提在日志里留着痕迹.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update

from ..catalog.database import PlatformDatabase
from ..catalog.enums import RunStatus
from ..catalog.models import RunModel
from ..clock import utc_now


logger = logging.getLogger(__name__)

RECOVERY_MESSAGE = "后端重启, 运行被中断"

UNFINISHED_RUN_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)

SINGLE_INSTANCE_WARNING = (
    "启动恢复假设本后端是**唯一实例**; 多 worker 或多副本会让正在跑的作业被误标为中断"
)


async def recover_interrupted_runs(database: PlatformDatabase) -> int:
    """把所有未完成的运行标成 `interrupted`, 返回受影响行数.

    只写状态、结束时间与一句固定文案: `ExitCode` 未知故不写, `DurationMs` 留 NULL (没跑完),
    `RunnerPid` 原样保留作证据, `IsSuccess` 一概不碰——它不是"没成功", 而是"没结论".

    **不按 `RunnerPid` 杀孤儿进程**, 三条理由按强度排: 一是 PID 会被 Windows 积极复用, 崩溃到
    被守护进程拉起的间隔可能只有几秒, `taskkill /PID` 会杀错无关进程; 二是重启后父子关系已不
    存在, 库里也没有进程创建时间, 没有任何可验证的身份凭证; 三是孤儿的伤害有界——"写路径必须
    相对"这条约束把它的全部写入关在 `runs/<RunId>/` 里, 后果是资源泄漏而非正确性破坏, 且新提交
    会产生新 `RunId`、不冲突. 要把它们清掉, 用下面这条日志里成对的 `(RunId, 进程号)` 人工核对
    `tasklist /FI "PID eq <N>"` 之后再 `taskkill /T /PID <N>`.
    """

    logger.warning(SINGLE_INSTANCE_WARNING)

    finished_at = utc_now()

    async with database.session_scope() as session:
        unfinished_runs = (
            await session.execute(
                select(RunModel.id, RunModel.runner_pid).where(
                    RunModel.status.in_(UNFINISHED_RUN_STATUSES)
                )
            )
        ).all()

        if not unfinished_runs:
            return 0

        await session.execute(
            update(RunModel)
            .where(RunModel.status.in_(UNFINISHED_RUN_STATUSES))
            .values(
                status=RunStatus.INTERRUPTED.value,
                finished_at=finished_at,
                error_msg=RECOVERY_MESSAGE,
            )
        )

        await session.commit()

    for run_id, process_id in unfinished_runs:
        logger.warning("启动恢复: 运行 %s 被标为中断 (原进程号 %s)", run_id, process_id)

    return len(unfinished_runs)
