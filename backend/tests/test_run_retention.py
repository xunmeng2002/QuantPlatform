"""磁盘保留策略: 每个用户只留最新 N 轮, 更旧的连作业目录一起删.

两条性质决定了用例的形态.

一是**判据在行上**: 哪一行入选、哪一行被保护、哪一行是越界路径, 都是逐行看的, 故大部分用例直接
调 `prune_superseded_runs`. 换成经接口提交、等作业真跑完也测得到同一件事, 代价是每条用例起几次真
进程——而它测的不是"作业跑没跑起来".

二是**库里不能有醒着的调度器**: 一个 `queued` 行会在几秒内被认领并真的跑起来, 于是"非终态不被
删"这个前提当场消失 (与 `test_run_recovery` 同一条理由). 故落行经一个**没有调度器**的数据库门面;
只有"启动时清一次"那条用例才起应用, 而它要的正是那条装配路径.

"默认关"单独有用例: 它守的是"升级之后不该有任何东西被删".
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest_asyncio

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel, StrategyModel, StrategyVersionModel, UserModel
from app.clock import utc_now
from app.config import PlatformSettings
from app.services.run_retention import prune_superseded_runs

from .helpers import (
    create_run_record,
    create_strategy_record,
    create_strategy_version_record,
    create_user_record,
    update_record_by_id,
)
from .run_helpers import (
    job_directory,
    read_run_or_none,
    running_application,
    settings_with,
    write_job_directory,
)


STRATEGY_NAME = "retention-grid"
OWNER_USERNAME = "retention-owner"
OTHER_OWNER_USERNAME = "retention-other-owner"

# 提交次序由"第几新"折算: 序号 0 最新, 越大越旧. 用分钟级步长, 远粗于 `utc_now()` 的分辨率,
# 于是"谁更新"在库里是一眼可判的.
SUBMITTED_AT_STEP = timedelta(minutes=1)

RETAINED_RUN_COUNT = 2
TOTAL_RUN_COUNT = 5

CANARY_DIRECTORY_NAME = "retention-canary"
CANARY_FILENAME = "keep.txt"
ESCAPING_WORKSPACE_PATH = f"../{CANARY_DIRECTORY_NAME}"

RETENTION_LOG_MARKER = "保留清理"


@dataclass(frozen=True)
class RetentionBoard:
    """两个账号与一份共用策略版本: "每用户独立"那条判据要两个账号才分得开."""

    owner: UserModel
    other_owner: UserModel
    strategy: StrategyModel
    version: StrategyVersionModel


@pytest_asyncio.fixture
async def retention_database(
    platform_settings: PlatformSettings,
) -> AsyncIterator[PlatformDatabase]:
    """指向同一份库、但**没有调度器**的数据库门面.

    与 `database` 夹具的差别只有这一点. 建表走与线上同一条路径 (`initialize()`), 保留策略本身由
    各用例按需开关.
    """

    database = PlatformDatabase(platform_settings.database_url)

    await database.initialize()

    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def retention_board(
    retention_database: PlatformDatabase,
) -> RetentionBoard:
    owner = await create_user_record(retention_database, OWNER_USERNAME)
    other_owner = await create_user_record(retention_database, OTHER_OWNER_USERNAME)
    strategy = await create_strategy_record(retention_database, owner, STRATEGY_NAME)
    version = await create_strategy_version_record(retention_database, strategy, owner)

    return RetentionBoard(
        owner=owner,
        other_owner=other_owner,
        strategy=strategy,
        version=version,
    )


def retention_settings(
    platform_settings: PlatformSettings,
    retained_run_count: int,
    enabled: bool = True,
) -> PlatformSettings:
    """派生一份保留策略配置."""

    return settings_with(
        platform_settings,
        run_retention_enabled=enabled,
        retained_runs_per_user=retained_run_count,
    )


async def create_run_with_directory(
    database: PlatformDatabase,
    settings: PlatformSettings,
    board: RetentionBoard,
    sequence: int,
    owner: UserModel | None = None,
    status: RunStatus = RunStatus.SUCCEEDED,
) -> RunModel:
    """造一行运行, 并把它的作业目录真的写到盘上.

    `sequence` 是"第几新": 保留的判据是"最新的 N 轮", 得先有可比的提交次序. 同秒提交时次序落到
    主键上, 那条路径另有用例.
    """

    run = await create_run_record(
        database,
        owner if owner is not None else board.owner,
        board.strategy,
        board.version,
        status=status,
        submitted_at=utc_now() - sequence * SUBMITTED_AT_STEP,
    )

    write_job_directory(settings, run.id)

    return run


async def create_runs(
    database: PlatformDatabase,
    settings: PlatformSettings,
    board: RetentionBoard,
    run_count: int,
    owner: UserModel | None = None,
) -> list[RunModel]:
    """造 `run_count` 轮, 返回由新到旧的行 (下标即"第几新")."""

    return [
        await create_run_with_directory(
            database, settings, board, sequence, owner=owner
        )
        for sequence in range(run_count)
    ]


async def assert_runs_survive(
    database: PlatformDatabase, settings: PlatformSettings, runs: Sequence[RunModel]
) -> None:
    """这些行与它们的作业目录都还在."""

    for run in runs:
        assert await read_run_or_none(database, run.id) is not None, run.id
        assert job_directory(settings, run.id).exists(), run.id


async def assert_runs_are_gone(
    database: PlatformDatabase, settings: PlatformSettings, runs: Sequence[RunModel]
) -> None:
    """这些行与它们的作业目录都没了.

    目录一并断言: 只删行不删目录的话, 磁盘占用一点没少——而回收磁盘正是这个功能存在的理由.
    """

    for run in runs:
        assert await read_run_or_none(database, run.id) is None, run.id
        assert not job_directory(settings, run.id).exists(), run.id


async def test_nothing_is_pruned_while_the_policy_is_off(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
    caplog,
) -> None:
    """默认关就是真的什么都不做: 行、目录一个不少, 连一条日志都不留.

    保留轮数给 1 而造了 5 轮, 远在配额之外——"关着就不动"必须与"超没超"无关.

    日志也在断言之内: 清理每次作业收尾都会被调一次, 关着时若记一条"保留策略已关闭", 它会以每轮
    一条的频率刷屏, 而那正是要靠日志轮转解决的问题, 不该由这里制造.
    """

    runs = await create_runs(
        retention_database, platform_settings, retention_board, TOTAL_RUN_COUNT
    )

    caplog.set_level(logging.INFO)

    pruned_count = await prune_superseded_runs(
        retention_database,
        retention_settings(platform_settings, 1, enabled=False),
    )

    assert pruned_count == 0
    await assert_runs_survive(retention_database, platform_settings, runs)
    assert not [
        record
        for record in caplog.records
        if RETENTION_LOG_MARKER in record.getMessage()
    ]


async def test_only_the_newest_runs_are_kept(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
) -> None:
    """开 N 之后: 最新的 N 轮留下, 更旧的连作业目录一起消失, 返回值如实报数."""

    runs = await create_runs(
        retention_database, platform_settings, retention_board, TOTAL_RUN_COUNT
    )

    pruned_count = await prune_superseded_runs(
        retention_database,
        retention_settings(platform_settings, RETAINED_RUN_COUNT),
    )

    assert pruned_count == TOTAL_RUN_COUNT - RETAINED_RUN_COUNT
    await assert_runs_survive(
        retention_database, platform_settings, runs[:RETAINED_RUN_COUNT]
    )
    await assert_runs_are_gone(
        retention_database, platform_settings, runs[RETAINED_RUN_COUNT:]
    )


async def test_the_quota_is_counted_per_user(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
) -> None:
    """配额按用户各算各的: 一个用户轮数再多, 也不该动到别人的行.

    另一个账号**恰好**造 N 轮, 于是它的每一行都该原样保留. 按全局算的实现 (把两人的行排在一起数
    够 N 个就停) 会把它最新的一轮也当"更旧的"删掉, 而那种实现在只有一个用户的用例上全绿.
    """

    other_owner_runs = await create_runs(
        retention_database,
        platform_settings,
        retention_board,
        run_count=RETAINED_RUN_COUNT,
        owner=retention_board.other_owner,
    )
    owner_runs = await create_runs(
        retention_database, platform_settings, retention_board, TOTAL_RUN_COUNT
    )

    pruned_count = await prune_superseded_runs(
        retention_database,
        retention_settings(platform_settings, RETAINED_RUN_COUNT),
    )

    assert pruned_count == TOTAL_RUN_COUNT - RETAINED_RUN_COUNT
    await assert_runs_survive(
        retention_database, platform_settings, other_owner_runs
    )
    await assert_runs_are_gone(
        retention_database, platform_settings, owner_runs[RETAINED_RUN_COUNT:]
    )


async def test_a_second_sweep_has_nothing_left_to_do(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
) -> None:
    """配额为 1 时只留最新一轮; 紧接着再扫一次是 0 且不再动任何东西.

    第二次清扫是真实存在的路径 (每轮作业收尾都会触发一次), 它必须是幂等的: 库里已经没有"第 2
    新及之后"的行, 谁也不该因此被删.
    """

    runs = await create_runs(retention_database, platform_settings, retention_board, 3)
    settings = retention_settings(platform_settings, 1)

    first_count = await prune_superseded_runs(retention_database, settings)
    second_count = await prune_superseded_runs(retention_database, settings)

    assert first_count == 2
    assert second_count == 0
    await assert_runs_survive(retention_database, platform_settings, runs[:1])


async def test_unfinished_runs_are_never_pruned(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
) -> None:
    """排队与运行中的轮无论多旧都不入选: 它们正被调度器或引擎握着.

    造的是库里最旧的两行——按保留轮数它们早该没了. 判据是**状态列**, 不是"它刚好排进了最新的 N
    轮".

    这两行的作业目录也真建出来 (真实世界里排队中的轮还没有目录): 要断言的是"非终态的行**与它名
    下的东西**都没被动", 而在一棵空目录树上那种断言恒真.
    """

    unfinished_runs = [
        await create_run_with_directory(
            retention_database,
            platform_settings,
            retention_board,
            sequence=3,
            status=RunStatus.QUEUED,
        ),
        await create_run_with_directory(
            retention_database,
            platform_settings,
            retention_board,
            sequence=4,
            status=RunStatus.RUNNING,
        ),
    ]
    finished_runs = [
        await create_run_with_directory(
            retention_database, platform_settings, retention_board, sequence
        )
        for sequence in (0, 1, 2)
    ]

    pruned_count = await prune_superseded_runs(
        retention_database,
        retention_settings(platform_settings, RETAINED_RUN_COUNT),
    )

    # 候选只剩序号 2 那一行: 序号 3、4 是未完成态, 序号 0、1 在保留名额内.
    assert pruned_count == 1
    await assert_runs_survive(
        retention_database, platform_settings, [*finished_runs[:2], *unfinished_runs]
    )
    await assert_runs_are_gone(
        retention_database, platform_settings, finished_runs[2:]
    )


async def test_protected_runs_are_exempt_from_the_sweep(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
) -> None:
    """被保护的那一轮不删, 且**不占**保留名额.

    清扫由某一轮收尾触发时, 刚结束的那一轮经保护名单排除: 用户此刻看的多半就是它. "不占名额"是
    说保护一轮不会顺带把另一轮也留下——保护的是最旧那轮 (本在候选里), 被删的仍然只有它之外的那
    一个候选.
    """

    runs = await create_runs(retention_database, platform_settings, retention_board, 4)
    protected_run = runs[3]

    pruned_count = await prune_superseded_runs(
        retention_database,
        retention_settings(platform_settings, RETAINED_RUN_COUNT),
        (protected_run.id,),
    )

    assert pruned_count == 1
    await assert_runs_survive(
        retention_database, platform_settings, [runs[0], runs[1], protected_run]
    )
    await assert_runs_are_gone(retention_database, platform_settings, runs[2:3])


async def test_a_row_whose_directory_cannot_be_removed_keeps_its_row(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
    tmp_path: Path,
) -> None:
    """作业目录列越界时删不掉: 行保留, 下次清扫仍会尝试, 金丝雀一个字节都没少.

    与删除端点共用同一个删除原语, 故这一格测的正是"两条路径的 fail-closed 方向一致". 没有它的话,
    守卫判定的每一条越界都会变成"行删了、目录还在"——磁盘泄漏从此不可观测, 也没人会再去看它.
    """

    canary_directory = tmp_path / CANARY_DIRECTORY_NAME

    canary_directory.mkdir(parents=True)
    (canary_directory / CANARY_FILENAME).write_text("canary\n", encoding="utf-8")

    escaping_run = await create_run_with_directory(
        retention_database, platform_settings, retention_board, sequence=2
    )

    await update_record_by_id(
        retention_database,
        RunModel,
        escaping_run.id,
        lambda run: setattr(run, "workspace_path", ESCAPING_WORKSPACE_PATH),
    )

    newest_run = await create_run_with_directory(
        retention_database, platform_settings, retention_board, sequence=0
    )
    middle_run = await create_run_with_directory(
        retention_database, platform_settings, retention_board, sequence=1
    )

    settings = retention_settings(platform_settings, 1)

    first_count = await prune_superseded_runs(retention_database, settings)

    assert first_count == 1
    await assert_runs_are_gone(retention_database, platform_settings, [middle_run])
    await assert_runs_survive(
        retention_database, platform_settings, [newest_run, escaping_run]
    )
    assert (canary_directory / CANARY_FILENAME).exists()

    # 下一次清扫: 这一行仍在"第 2 新及之后", 于是还会被再试一次——行留着就是为了这个.
    second_count = await prune_superseded_runs(retention_database, settings)

    assert second_count == 0
    await assert_runs_survive(
        retention_database, platform_settings, [newest_run, escaping_run]
    )
    assert (canary_directory / CANARY_FILENAME).exists()


async def test_a_row_without_a_recorded_directory_is_still_pruned(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
) -> None:
    """目录列是空串的行照删: 它名下没有目录可移, 不该因此永远留在库里.

    这一格真实可达 (提交路径在极早的失败下会留下它), 而两条删除路径共用同一份可删判据
    (`ROW_DELETABLE_OUTCOMES`), 故这里断言的是那份判据本身.
    """

    directoryless_run = await create_run_with_directory(
        retention_database, platform_settings, retention_board, sequence=3
    )
    newest_run = await create_run_with_directory(
        retention_database, platform_settings, retention_board, sequence=0
    )

    await update_record_by_id(
        retention_database,
        RunModel,
        directoryless_run.id,
        lambda run: setattr(run, "workspace_path", ""),
    )

    pruned_count = await prune_superseded_runs(
        retention_database, retention_settings(platform_settings, 1)
    )

    assert pruned_count == 1
    assert await read_run_or_none(retention_database, directoryless_run.id) is None
    await assert_runs_survive(
        retention_database, platform_settings, [newest_run]
    )


async def test_the_startup_sweep_runs_after_the_recovery(
    retention_database: PlatformDatabase,
    platform_settings: PlatformSettings,
    retention_board: RetentionBoard,
) -> None:
    """启动时清一次盘, 且发生在恢复**之后**.

    上一届留下的 `queued` 行要先被恢复改写成 `interrupted` (那一刻起才算终态), 才轮得到清理收掉
    它. 次序反过来的话, 清理看到的仍是 `queued`, 按"非终态永不入选"跳过它, 它随后被标成中断并永
    远留在盘上——而库里没有任何症状会指向这个次序.

    断言"只剩最新 N 轮"就能把两种次序分开: 那个被中断的最旧轮若不在这 N 轮里, 它必须消失.
    """

    unfinished_run = await create_run_with_directory(
        retention_database,
        platform_settings,
        retention_board,
        sequence=3,
        status=RunStatus.QUEUED,
    )
    finished_runs = [
        await create_run_with_directory(
            retention_database, platform_settings, retention_board, sequence
        )
        for sequence in (0, 1, 2)
    ]

    async with running_application(
        retention_settings(platform_settings, RETAINED_RUN_COUNT)
    ) as application:
        application_database = application.state.database

        await assert_runs_survive(
            application_database,
            platform_settings,
            finished_runs[:RETAINED_RUN_COUNT],
        )
        # 序号 2 那轮在配额之外, 被中断的 queued 轮在恢复之后也在配额之外: 两条都该消失.
        await assert_runs_are_gone(
            application_database,
            platform_settings,
            [finished_runs[2], unfinished_run],
        )
