"""磁盘保留策略: 每个用户只留最新 N 轮, 更旧的连作业目录一起删.

默认**关** (见 `PlatformSettings.run_retention_enabled`). 它删掉的是历史轮的结果库明细、对比曲线
与逐字复现要读的东西, 不可逆, 而且第一次打开就会把存量历史一次收干净——这件事只能由人明确要求,
不能靠一个默认值替人决定.

与 `DELETE /api/runs/{id}` **共用** `remove_run_directory`: 守卫、日志、fail-closed 的方向, 以及
"什么算删干净了" (`ROW_DELETABLE_OUTCOMES`) 因此只有一份. 两条路径各写一套删除逻辑的话, 迟早有
一份会先漏掉守卫.

**先删目录、后删行**, 与端点同一条纪律: 行是那个目录唯一的句柄 (目录名就是 `RunId`, 而知道它的
只有这一列), 行先没了就等于把一次磁盘泄漏变成永久且不可观测的. 反过来, 目录删掉了而进程在提交
前被打断, 留下的只是一行指向空目录的记录——下一次清扫幂等地把它收掉.

**不引入锁文件或心跳**: 单实例是调度器的硬前提 (见 `scheduler/recovery.py`). 加锁不会让多实例变
安全, 只会把"多开了一个进程"从"删错"变成"看着有保护其实没有".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence

from sqlalchemy import delete, select

from ..catalog.database import PlatformDatabase
from ..catalog.enums import TERMINAL_RUN_STATUSES
from ..catalog.models import RunModel
from ..config import PlatformSettings
from .run_storage import ROW_DELETABLE_OUTCOMES, remove_run_directory


logger = logging.getLogger(__name__)


async def prune_superseded_runs(
    database: PlatformDatabase,
    settings: PlatformSettings,
    protected_run_ids: Iterable[str] = (),
) -> int:
    """按保留轮数清理各用户的旧运行, 返回真正删掉的行数.

    关着时返回 0 且**不写任何日志**: 它每次作业收尾都会被调一次, 一条"保留策略已关闭"的日志会
    以每轮一条的频率刷屏, 而那正是要靠"日志轮转"解决的问题.

    `protected_run_ids` 里的行一律不入选, 且**不占**保留名额: 清扫由某一轮收尾触发时, 那一轮经
    这里排除——用户此刻正在看的就是它.

    一次扫完、不设每轮上限: 第一次打开保留策略时, 要的正是把存量历史一口气收干净.
    """

    if not settings.run_retention_enabled:
        return 0

    superseded_runs = await _select_superseded_runs(
        database, settings, frozenset(protected_run_ids)
    )

    removable_run_ids: list[str] = []

    for run in superseded_runs:
        removal = await asyncio.to_thread(remove_run_directory, settings, run)

        if removal in ROW_DELETABLE_OUTCOMES:
            removable_run_ids.append(run.id)
        else:
            logger.warning(
                "保留清理无法移除运行 %s 的作业目录: %s", run.id, removal.value
            )

    if not removable_run_ids:
        return 0

    await _delete_run_rows(database, removable_run_ids)

    logger.info(
        "保留清理移除了 %d 轮历史运行 (每用户保留最新 %d 轮)",
        len(removable_run_ids),
        settings.retained_runs_per_user,
    )

    return len(removable_run_ids)


async def _select_superseded_runs(
    database: PlatformDatabase,
    settings: PlatformSettings,
    protected_run_ids: frozenset[str],
) -> list[RunModel]:
    """取每个用户"第 N+1 新及之后"的终态运行.

    **非终态永不入选**——这是"清理与在飞的运行是什么关系"的结构性答案, 而不是"刚好没选中": 排队
    与运行中的轮正被调度器或引擎握着, 它们的行此刻就不在候选集合里.

    排序键除 `SubmittedAt` 还要带 `Id`: 同秒提交的行按主键确定化, 与 `run_prefill` 同一口径.
    `offset(retained_runs_per_user)` 就是"第 N+1 新及之后", 不需要窗口函数.

    会话在这里就关掉, 返回的是**游离对象**: 接下来逐个目录做文件操作, 可能持续很久, 而 SQLite
    的读事务会挡住写入. 把一次清扫横在调度器的认领更新前面, 是没必要的代价.
    """

    superseded_runs: list[RunModel] = []

    async with database.session_scope() as session:
        run_owner_ids = list(
            await session.scalars(
                select(RunModel.user_id)
                .where(RunModel.status.in_(TERMINAL_RUN_STATUSES))
                .distinct()
            )
        )

        for run_owner_id in run_owner_ids:
            candidate_query = (
                select(RunModel)
                .where(RunModel.user_id == run_owner_id)
                .where(RunModel.status.in_(TERMINAL_RUN_STATUSES))
                .order_by(RunModel.submitted_at.desc(), RunModel.id.desc())
                .offset(settings.retained_runs_per_user)
            )

            if protected_run_ids:
                candidate_query = candidate_query.where(
                    RunModel.id.not_in(protected_run_ids)
                )

            superseded_runs.extend(await session.scalars(candidate_query))

    return superseded_runs


async def _delete_run_rows(
    database: PlatformDatabase, run_ids: Sequence[str]
) -> None:
    """按主键批量删行.

    用 `delete()` 语句而不是把 ORM 对象逐个 `session.delete()`: 清扫手上的是上一个会话留下的游
    离对象, 而批量删除只认主键, 不必把它们重新绑回会话.
    """

    async with database.session_scope() as session:
        await session.execute(delete(RunModel).where(RunModel.id.in_(run_ids)))

        await session.commit()
