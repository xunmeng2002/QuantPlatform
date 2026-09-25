"""启动恢复: 上一届留下的未完成运行由**下一次启动**一次结清.

恢复的判据不是"进程还在不在", 而是"后端换了一届". 后端既已重启, 上一届里跑的作业就都不属于任何
活着的执行者了——故这里不探活、也不按进程号杀孤儿 (Windows 上 `os.kill(pid, 0)` 不是探活而是
**杀进程**, 而 PID 又会被积极复用, 探活这条路本身就走不通).

用例的形态由这条性质决定: 库里得先有"上一届没跑完的行", 再起"这一届". 这些行**只能在一届都不跑
的时候塞进去**——库里有一个 `queued` 而调度器醒着的话, 它会在几秒内被认领、真的跑起来, 于是
"启动时仍是未完成"这个前提就没了 (而这条竞争是时间相关的, 写不出稳定断言). 故落行直接经一个
数据库门面, 不经应用.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel
from app.config import PlatformSettings
from app.clock import utc_now
from app.scheduler.recovery import RECOVERY_MESSAGE

from .helpers import (
    create_run_record,
    create_strategy_record,
    create_strategy_version_record,
    create_user_record,
    update_record_by_id,
)
from .run_helpers import (
    count_run_rows,
    job_directory,
    read_run_record,
    running_application,
    run_directory_names,
)


STRATEGY_NAME = "桩策略"

PREVIOUS_HOSTNAME = "previous-generation-host"
RUNNING_RUNNER_PID = 4242
QUEUED_RUNNER_PID = None

UNFINISHED_RUN_COUNT = 2
BASELINE_TRADE_COUNT = 84
BASELINE_BALANCE = 998951.4506464996
BASELINE_DURATION_MS = 1200

# 恢复一概不碰的列: 它们此刻不是"0 / 没耗时 / 失败", 而是"没有结论". 写成 0 会让详情页显示一个
# 看起来正常的数字, 而那一轮根本没跑完.
COLUMNS_RECOVERY_MUST_LEAVE_ALONE = (
    "exit_code",
    "is_success",
    "duration_ms",
    "trade_count",
    "balance",
)

SPAN_FILENAME = "span.txt"
STDOUT_FILENAME = "stdout.txt"


@asynccontextmanager
async def _stopped_platform_database(
    settings: PlatformSettings,
) -> AsyncIterator[PlatformDatabase]:
    """一个指向同一份库文件、但**没有调度器在跑**的数据库门面.

    两届共用同一份 settings 与同一个库文件, 故两届之间不留任何内存状态——那正是重启的定义.
    建表与线上同一条路径 (`initialize()`), 起这一届时它已存在, `create_all` 不再改动.
    """

    database = PlatformDatabase(settings.database_url)

    await database.initialize()

    try:
        yield database
    finally:
        await database.close()


async def _seed_unfinished_runs(
    database: PlatformDatabase, settings: PlatformSettings
) -> tuple[str, str]:
    """造一行 `queued` + 一行 `running`, 并给后者建好作业目录.

    两行的 `RunnerPid` 取得不同: 恢复**原样保留**这一列 (它是"当时是谁在跑"的唯一证据), 若恢复
    顺手清空了它, 两行都会变成 NULL, 看不出是哪一列被动过.
    """

    owner = await create_user_record(database, "recovery-owner")
    strategy = await create_strategy_record(database, owner, STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, owner)

    queued_run = await create_run_record(
        database, owner, strategy, version, status=RunStatus.QUEUED
    )
    running_run = await create_run_record(
        database, owner, strategy, version, status=RunStatus.RUNNING
    )

    await update_record_by_id(
        database, RunModel, queued_run.id, _make_the_row_unfinished
    )

    # 运行中那行 = 未完成的一行 + "确实起过进程"的痕迹.
    await update_record_by_id(
        database, RunModel, running_run.id, _mark_as_running_on_the_previous_generation
    )

    # 作业目录真的建出来: "恢复后没有新目录被创建"这条断言要靠一个**已存在**的目录才有意义
    # (空目录树里断言"没有新目录"恒真).
    job_directory(settings, running_run.id).mkdir(parents=True)

    return queued_run.id, running_run.id


def _make_the_row_unfinished(run: RunModel) -> None:
    """把 `create_run_record` 造出的终态行抹回"没跑完"的样子.

    那个夹具是按"跑完了"造的 (指标列都带基线值), 而真实的未完成行上这些列是 NULL. 不抹的话,
    "恢复不许碰指标列"这条断言会因为**夹具自己填了值**而恒真——它测到的是夹具, 不是恢复.
    """

    run.exit_code = None
    run.duration_ms = None
    run.is_success = None
    run.finished_at = None
    run.error_msg = None
    run.trade_count = None
    run.order_count = None
    run.balance = None


def _mark_as_running_on_the_previous_generation(run: RunModel) -> None:
    """把一行写成"上一届确实起过进程"的样子."""

    _make_the_row_unfinished(run)

    run.started_at = utc_now()
    run.runner_pid = RUNNING_RUNNER_PID
    run.hostname = PREVIOUS_HOSTNAME


async def _seed_finished_run(database: PlatformDatabase) -> str:
    """造一行已经跑完的成功运行 (只落库, 不起进程).

    恢复的判据是**状态列**, 不是这一轮有没有真的跑过; 故这里只需要一行的终态取值.
    """

    owner = await create_user_record(database, "finished-owner")
    strategy = await create_strategy_record(database, owner, STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, owner)

    finished_run = await create_run_record(
        database,
        owner,
        strategy,
        version,
        status=RunStatus.SUCCEEDED,
        trade_count=BASELINE_TRADE_COUNT,
        balance=BASELINE_BALANCE,
    )

    await update_record_by_id(
        database, RunModel, finished_run.id, _mark_as_successfully_finished
    )

    return finished_run.id


def _mark_as_successfully_finished(run: RunModel) -> None:
    """把一行写成"跑完了、有结论"的样子."""

    run.started_at = utc_now()
    run.finished_at = utc_now()
    run.duration_ms = BASELINE_DURATION_MS
    run.exit_code = 0
    run.is_success = True
    run.error_msg = ""
    run.hostname = PREVIOUS_HOSTNAME


async def test_restarting_the_backend_interrupts_every_unfinished_run(
    platform_settings: PlatformSettings,
) -> None:
    """重启后: 两行都 `interrupted`, 结束时间填上, 文案是恢复文案, 进程号原样保留."""

    async with _stopped_platform_database(platform_settings) as previous_database:
        queued_run_id, running_run_id = await _seed_unfinished_runs(
            previous_database, platform_settings
        )

    async with running_application(platform_settings) as current_application:
        current_database = current_application.state.database

        for run_id in (queued_run_id, running_run_id):
            recovered_run = await read_run_record(current_database, run_id)

            assert recovered_run.status == RunStatus.INTERRUPTED.value
            assert recovered_run.finished_at is not None
            assert recovered_run.error_msg == RECOVERY_MESSAGE

            for untouched_column in COLUMNS_RECOVERY_MUST_LEAVE_ALONE:
                assert getattr(recovered_run, untouched_column) is None, (
                    f"{untouched_column} 被恢复写上了值"
                )

        queued_run = await read_run_record(current_database, queued_run_id)
        running_run = await read_run_record(current_database, running_run_id)

        # `RunnerPid` 是"当时是谁在跑"的唯一证据, 恢复不许清掉它.
        assert queued_run.runner_pid == QUEUED_RUNNER_PID
        assert running_run.runner_pid == RUNNING_RUNNER_PID
        assert running_run.started_at is not None


async def test_recovery_does_not_start_anything(
    platform_settings: PlatformSettings,
) -> None:
    """恢复只改库: 不起进程, 不建目录, 也不凭空多出行来.

    防的是"顺手把恢复写成续跑": 上一届的作业目录可能只建了一半, 续跑会读到一个残缺的作业环境,
    而 `result.json` 的缺席意味着平台无从判断那一轮跑到了哪一步. 故这一版一律标中断, 续跑留待
    日后.

    判据是运行根下的目录名集合**逐字不变**, 且没有新行被创建——恢复若顺手提交了续跑, 两者至少
    坏一样.
    """

    async with _stopped_platform_database(platform_settings) as previous_database:
        await _seed_unfinished_runs(previous_database, platform_settings)

    directories_before = run_directory_names(platform_settings)

    assert directories_before

    async with running_application(platform_settings) as current_application:
        current_database = current_application.state.database

        assert await count_run_rows(current_database) == UNFINISHED_RUN_COUNT
        assert run_directory_names(platform_settings) == directories_before

        for directory_name in directories_before:
            job_path = job_directory(platform_settings, directory_name)

            # 桩的 span.txt / stdout.txt 是进程自己写的, 故它们的存在就是"有进程真的跑过"的
            # 证据: 恢复路径若顺手续跑, 这些目录里会多出文件.
            assert not (job_path / SPAN_FILENAME).exists(), directory_name
            assert not (job_path / STDOUT_FILENAME).exists(), directory_name


async def test_a_run_that_already_finished_is_left_alone(
    platform_settings: PlatformSettings,
) -> None:
    """已落终态的行不归恢复管: 状态、结束时间、文案一个字段都不许动.

    恢复的条件更新若漏掉 `WHERE Status IN ('queued','running')`, 它会把**上一次跑完的成功轮**
    改写成"被中断"——而库里没有任何症状会指向恢复那段代码.
    """

    async with _stopped_platform_database(platform_settings) as previous_database:
        finished_run_id = await _seed_finished_run(previous_database)

    async with running_application(platform_settings) as current_application:
        reread_run = await read_run_record(
            current_application.state.database, finished_run_id
        )

        assert reread_run.status == RunStatus.SUCCEEDED.value
        assert reread_run.error_msg == ""
        assert reread_run.trade_count == BASELINE_TRADE_COUNT
        assert reread_run.balance == BASELINE_BALANCE
        assert reread_run.duration_ms == BASELINE_DURATION_MS
        assert reread_run.is_success is True
        assert reread_run.exit_code == 0
