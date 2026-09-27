"""队列、并发、超时、输出捕获与终态仲裁.

这些用例跑的是**真实进程**: 桩策略经正常上传路径落盘, 被调度器复制进作业目录, 在
`sys.executable` 下以裸文件名启动. 于是它们验的是"平台真的把作业跑起来了", 而不是"平台认为它
跑起来了".

判据一律取自进程自己落下的证据 (`span.txt` / `argv0.txt` / `result.json`) 与库里的列, 不采样
平台的内存状态: "此刻有几个 running" 这类采样在采样偏差下几乎恒真, 杀不掉任何变异.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel
from app.config import PlatformSettings
from app.scheduler.engine_config import (
    RELATIVE_DATABASE_HOST,
    RELATIVE_DUMP_PATH,
    SQLITE_DATABASE_TYPE,
)
from app.scheduler.result import RESULT_MIRROR_COLUMN_NAMES
from app.scheduler.runner import (
    HOST_STARTUP_FAILURE_MESSAGE,
    JOB_DIRECTORY_FAILURE_MESSAGE,
    RESULT_MISSING_MESSAGE,
    SUCCESS_WITHOUT_RESULT_MESSAGE,
    TIMEOUT_MESSAGE,
    build_unexpected_exit_message,
)
from app.scheduler.workspace import STAGING_DIRECTORY_PREFIX

from .helpers import SignedInAccount, create_signed_in_account, soft_delete_strategy_record
from .run_helpers import (
    ARGV0_FILENAME,
    DEFAULT_END_TRADING_DAY,
    DEFAULT_EXCHANGE_ID,
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_INSTRUMENT_ID,
    DEFAULT_START_TRADING_DAY,
    ENGINE_CONFIGURATION_FILENAME,
    EXIT_DELAY_SECONDS_PARAMETER_KEY,
    FLOOD_BYTES_PARAMETER_KEY,
    RESULT_FILENAME,
    SLEEP_SECONDS_PARAMETER_KEY,
    SPAN_FILENAME,
    STDERR_FLOOD_BYTES_PARAMETER_KEY,
    STUB_CONFIG_FILENAME,
    STUB_ENTRY_FILENAME,
    STUB_SPACE_ENTRY_FILENAME,
    await_run_status,
    await_run_terminal,
    build_stub_manifest,
    count_span_end_lines,
    create_runnable_strategy,
    job_directory,
    maximum_overlap,
    read_job_json,
    read_reported_argv0,
    read_run_record,
    read_span_intervals,
    running_client,
    settings_with,
    submit_run,
)


BEHAVIOR_PARAMETER_KEY = "Behavior"

BEHAVIOR_SUCCESS = "success"
BEHAVIOR_EXIT_WITHOUT_HOST = "exit1"
BEHAVIOR_UNREADABLE_RESULT = "exit2"
BEHAVIOR_ENGINE_REPORTED_FAILURE = "exit3_reported"
BEHAVIOR_CRASHED_WITHOUT_RESULT = "exit3_crash"
BEHAVIOR_SUCCESS_WITHOUT_RESULT = "exit0_no_result"
BEHAVIOR_FULL_MIRROR = "mirror"
BEHAVIOR_FOREIGN_RUN_ID = "wrong_run_id"
BEHAVIOR_GBK_ERROR_MESSAGE = "gbk_error_msg"

STRATEGY_NAME = "桩策略"

CONCURRENT_RUN_COUNT = 3
CONCURRENT_SLEEP_SECONDS = 2
CONCURRENT_ELAPSED_LIMIT_SECONDS = 60

TIMEOUT_SECONDS = 1
TIMEOUT_ELAPSED_LIMIT_SECONDS = 6
TIMEOUT_SLEEP_SECONDS = 30

FLOOD_BYTES = 300 * 1024
PIPE_DEADLOCK_STDERR_BYTES = 1024 * 1024
FLOOD_ELAPSED_LIMIT_SECONDS = 60

FOREIGN_RUN_ID = "run-id-the-platform-never-issued"
GBK_ERROR_MESSAGE = "引擎报告: 行情数据缺失"
REPORTED_FAILURE_ERROR_ID = 4242

# 平台按 manifest 的映射与参数声明写进策略配置的全部键. 用例把它与盘上的键集合对起来, 于是
# "渲染结果恰好等于 {映射声明的键} ∪ {请求的参数键}" 成了可验的性质.
CONFIGURATION_KEYS_WRITTEN_BY_PLATFORM = frozenset(
    {"Behavior", "SleepSeconds", "ExitDelaySeconds", "FloodBytes", "StderrFloodBytes"}
)
RUN_FIELD_CONFIGURATION_KEYS = frozenset({"ExchangeId", "InstrumentId", "BarPreces"})

MIRROR_COLUMN_BY_RESULT_KEY = dict(RESULT_MIRROR_COLUMN_NAMES)


async def _submit_and_wait(
    client: AsyncClient,
    database: PlatformDatabase,
    token: str,
    strategy_id: str,
    params: dict[str, object] | None = None,
    timeout_seconds: float = CONCURRENT_ELAPSED_LIMIT_SECONDS,
) -> RunModel:
    """提交一轮并等它落终态."""

    submitted = await submit_run(client, token, strategy_id, params=params)

    return await await_run_terminal(database, submitted.id, timeout_seconds)


@pytest.mark.parametrize(
    ("exit_delay_seconds", "sleep_seconds"),
    [(0, 0), (0, CONCURRENT_SLEEP_SECONDS)],
)
async def test_a_successful_run_is_recorded_end_to_end(
    exit_delay_seconds: float,
    sleep_seconds: float,
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """一轮跑通的作业要走完: 落 `queued` → 起进程 → 写结果 → 镜像入库 → 落 `succeeded`.

    两个参数化取值覆盖两条真实路径: 立即退出, 以及"先睡一会儿再退出"(并发用例靠的正是后者).
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={
            SLEEP_SECONDS_PARAMETER_KEY: sleep_seconds,
            EXIT_DELAY_SECONDS_PARAMETER_KEY: exit_delay_seconds,
        },
    )

    assert run.status == RunStatus.SUCCEEDED.value
    assert run.exit_code == 0
    assert run.error_msg == ""
    assert run.trade_count == 84
    assert run.balance == 998951.4506464996
    assert run.market_data_type == "Bar"
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.duration_ms is not None
    assert run.runner_pid is not None


async def test_the_job_directory_matches_the_launch_contract(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """作业目录逐项对照启动契约.

    两条断言是**启动期致命**的, 单看"跑成功了"看不出来: 入口文件名必须是裸文件名 (引擎日志器
    按 `argv[0]` 拼日志路径, 带路径就 `fopen` 失败、进程在启动期终止), 两个写路径必须是相对值
    (绝对写路径会让两个并发作业共写同一个库, 而不报任何错).
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(client, database, run_owner.token, runnable.strategy.id)

    directory = job_directory(platform_settings, run.id)

    assert run.workspace_path == run.id

    assert sorted(entry.name for entry in directory.iterdir()) == sorted(
        [
            ENGINE_CONFIGURATION_FILENAME,
            STUB_CONFIG_FILENAME,
            STUB_ENTRY_FILENAME,
            "Sessions.json",
            RESULT_FILENAME,
            "Dump",
            "stdout.txt",
            "stderr.txt",
            ARGV0_FILENAME,
            SPAN_FILENAME,
        ]
    )

    engine_configuration = read_job_json(
        platform_settings, run.id, ENGINE_CONFIGURATION_FILENAME
    )

    assert engine_configuration["RunId"] == run.id
    assert engine_configuration["MatchMode"] == 3
    assert engine_configuration["BarPreces"] == "5m"
    assert engine_configuration["DbType"] == SQLITE_DATABASE_TYPE
    assert engine_configuration["StartTradingDay"] == DEFAULT_START_TRADING_DAY
    assert engine_configuration["EndTradingDay"] == DEFAULT_END_TRADING_DAY
    assert engine_configuration["InitialCapital"] == DEFAULT_INITIAL_CAPITAL
    assert engine_configuration["MdDataPath"] == str(platform_settings.market_data_root)
    assert engine_configuration["DbInitHost"] == str(
        platform_settings.seed_database_path
    )

    for relative_path_key in ("DbHost", "DumpPath"):
        written_path = engine_configuration[relative_path_key]

        assert not Path(written_path).is_absolute(), relative_path_key
        assert ":" not in written_path, relative_path_key
        assert not written_path.startswith(("/", "\\")), relative_path_key

    assert engine_configuration["DbHost"] == RELATIVE_DATABASE_HOST
    assert engine_configuration["DumpPath"] == RELATIVE_DUMP_PATH

    assert read_reported_argv0(platform_settings, run.id) == STUB_ENTRY_FILENAME

    # 构造期的临时目录一个都不许留下: 它们会以 `.staging-<rand>` 的面目堆在运行根下, 而没有任何
    # 症状会指向"构造作业目录"那段代码.
    assert not [
        entry
        for entry in platform_settings.runs_root.iterdir()
        if entry.name.startswith(STAGING_DIRECTORY_PREFIX)
    ], "作业目录构造留下了临时目录"


async def test_the_strategy_configuration_carries_exactly_the_declared_keys(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """策略配置的键集合**恰好等于** {映射声明的键} ∪ {请求的参数键}.

    再换一组映射键名重跑一次, 输出必须跟着变. 这一条专杀"平台把策略侧的键名硬编码进渲染逻辑"
    ——只断言第一轮的话, 一组恰好撞对的常量也能过.
    """

    renamed_run_field_keys = {
        "exchange_id": "ExchangeCode",
        "instrument_id": "Symbol",
        "bar_period": "PeriodName",
    }

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(client, database, run_owner.token, runnable.strategy.id)

    written_configuration = read_job_json(
        platform_settings, run.id, STUB_CONFIG_FILENAME
    )

    assert set(written_configuration) == (
        CONFIGURATION_KEYS_WRITTEN_BY_PLATFORM | RUN_FIELD_CONFIGURATION_KEYS
    )
    assert written_configuration["ExchangeId"] == DEFAULT_EXCHANGE_ID
    assert written_configuration["InstrumentId"] == DEFAULT_INSTRUMENT_ID
    assert written_configuration["BarPreces"] == "5m"

    renamed = await create_runnable_strategy(
        database,
        platform_settings,
        run_owner.user,
        f"{STRATEGY_NAME}(改名映射)",
        manifest=build_stub_manifest(run_field_keys=renamed_run_field_keys),
    )

    renamed_run = await _submit_and_wait(
        client, database, run_owner.token, renamed.strategy.id
    )

    renamed_configuration = read_job_json(
        platform_settings, renamed_run.id, STUB_CONFIG_FILENAME
    )

    assert set(renamed_configuration) == (
        CONFIGURATION_KEYS_WRITTEN_BY_PLATFORM | set(renamed_run_field_keys.values())
    )
    assert renamed_configuration["PeriodName"] == "5m"

    # 换的只是**策略侧**的键名: 引擎那一份仍写在 `BarPreces` 上, 而两者的取值必须一致——不一致
    # 时策略收不到 bar, 表现是**静默 0 成交**.
    renamed_engine_configuration = read_job_json(
        platform_settings, renamed_run.id, ENGINE_CONFIGURATION_FILENAME
    )

    assert renamed_engine_configuration["BarPreces"] == "5m"


async def test_concurrency_never_exceeds_the_configured_limit(
    platform_settings: PlatformSettings,
) -> None:
    """并发上限靠最大重叠数判定, 不靠采样.

    三个 `sleep(2)` 的作业并发提交, 上限为 2. 判据是三份 `span.txt` 里 `[start, end]` 区间的
    最大重叠数——区间由进程自己写下, 与测试的采样时刻无关; 而"轮询到 running 数 ≤ 2"在采样偏差
    下几乎恒真, 杀不掉任何变异.
    """

    limited_settings = settings_with(platform_settings, max_concurrent_runs=2)

    executed_run_ids = []

    async with running_client(limited_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(
            database, client, "concurrency-owner"
        )

        runnable = await create_runnable_strategy(
            database, limited_settings, owner.user, STRATEGY_NAME
        )

        for _ in range(CONCURRENT_RUN_COUNT):
            submitted = await submit_run(
                client,
                owner.token,
                runnable.strategy.id,
                params={SLEEP_SECONDS_PARAMETER_KEY: CONCURRENT_SLEEP_SECONDS},
            )
            executed_run_ids.append(submitted.id)

        for run_id in executed_run_ids:
            finished_run = await await_run_terminal(
                database, run_id, CONCURRENT_ELAPSED_LIMIT_SECONDS
            )
            assert finished_run.status == RunStatus.SUCCEEDED.value

    intervals = [
        interval
        for run_id in executed_run_ids
        for interval in read_span_intervals(limited_settings, run_id)
    ]

    assert len(intervals) == CONCURRENT_RUN_COUNT
    assert maximum_overlap(intervals) == 2


async def test_a_queued_job_is_not_started_before_its_turn(
    platform_settings: PlatformSettings,
) -> None:
    """排队中的作业: 行是 `queued`, 三个"已经开始"的痕迹一个都还没有.

    作业目录也**不存在**——它由作业任务构造, 而不是提交端点反过来的话用户提交完立刻就能在一个
    "正在排队"的目录里看到文件, 而下一轮构造会撞上"目录已存在"并判失败.
    """

    limited_settings = settings_with(platform_settings, max_concurrent_runs=1)

    async with running_client(limited_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "queueing-owner")

        runnable = await create_runnable_strategy(
            database, limited_settings, owner.user, STRATEGY_NAME
        )

        blocking = await submit_run(
            client,
            owner.token,
            runnable.strategy.id,
            params={SLEEP_SECONDS_PARAMETER_KEY: CONCURRENT_SLEEP_SECONDS},
        )

        # 等阻断者**真的认领并起进程**, 排队者才处在"确定还在排队"的状态; 否则这一条测的是
        # "两个都还没被认领", 与排队语义无关.
        await await_run_status(
            database, blocking.id, RunStatus.RUNNING, CONCURRENT_ELAPSED_LIMIT_SECONDS
        )

        queued = await submit_run(client, owner.token, runnable.strategy.id)
        queued_run = await read_run_record(database, queued.id)

        assert queued_run.status == RunStatus.QUEUED.value
        assert queued_run.started_at is None
        assert queued_run.runner_pid is None
        assert not job_directory(limited_settings, queued.id).exists()

        for run_id in (blocking.id, queued.id):
            finished_run = await await_run_terminal(
                database, run_id, CONCURRENT_ELAPSED_LIMIT_SECONDS
            )
            assert finished_run.status == RunStatus.SUCCEEDED.value

    # 两条都跑完了, 但是串行的: 重叠数为 1.
    intervals = [
        interval
        for run_id in (blocking.id, queued.id)
        for interval in read_span_intervals(limited_settings, run_id)
    ]

    assert maximum_overlap(intervals) == 1


async def test_two_concurrent_jobs_do_not_share_a_database_file(
    platform_settings: PlatformSettings,
) -> None:
    """相对写路径的隔离: 两个作业各写各的库, 而两份配置里的取值**逐字相同**.

    这条只能靠并发用例验: 单作业时绝对写路径也"跑得通", 只有两个作业同时跑才会共写同一个库
    ——且那不会报任何错, 只是两边的成交互相串台. 断言取自两行各自的 `db_path`, 那是引擎真会去
    用的取值.
    """

    limited_settings = settings_with(platform_settings, max_concurrent_runs=2)

    async with running_client(limited_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "isolation-owner")

        runnable = await create_runnable_strategy(
            database, limited_settings, owner.user, STRATEGY_NAME
        )

        submitted_ids = []

        for _ in range(2):
            submitted = await submit_run(
                client,
                owner.token,
                runnable.strategy.id,
                params={SLEEP_SECONDS_PARAMETER_KEY: CONCURRENT_SLEEP_SECONDS},
            )
            submitted_ids.append(submitted.id)

        finished_runs = [
            await await_run_terminal(
                database, run_id, CONCURRENT_ELAPSED_LIMIT_SECONDS
            )
            for run_id in submitted_ids
        ]

    for finished_run in finished_runs:
        assert finished_run.db_path == RELATIVE_DATABASE_HOST

        # 同一个相对值, 在两个作业目录里指向两个不同的绝对路径——这正是隔离成立的形式.
        resolved_path = job_directory(limited_settings, finished_run.id) / (
            finished_run.db_path
        )

        assert resolved_path.parent == job_directory(limited_settings, finished_run.id)

    assert len({run.id for run in finished_runs}) == 2
    # 运行根下除了作业目录不该出现别的东西: 一个写在运行根的 `BackTest.db` 就是两个作业共用的那
    # 一个, 而它恰恰是"相对路径写成绝对"的症状.
    assert not [
        entry
        for entry in limited_settings.runs_root.iterdir()
        if entry.is_file()
    ], "运行根下出现了文件, 说明有作业把写路径解析到了作业目录之外"


async def test_the_full_output_is_kept_on_disk_and_only_the_tail_is_reported(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """全量输出落盘, 内存里只留尾部, 且尾部是文件内容的**真后缀**.

    尾部按字节截, 故起点可能落在多字节字符的续字节上——那会凭空多出一个 U+FFFD, 看着像"引擎
    输出了乱码"而原始输出完全正常. 故这里连"不以 U+FFFD 开头"一起钉住.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={FLOOD_BYTES_PARAMETER_KEY: FLOOD_BYTES},
    )

    assert run.status == RunStatus.SUCCEEDED.value

    written_stdout = (job_directory(platform_settings, run.id) / "stdout.txt").read_bytes()

    assert len(written_stdout) == FLOOD_BYTES
    assert run.stdout_tail
    assert len(run.stdout_tail.encode("utf-8")) <= (
        platform_settings.maximum_output_tail_bytes
    )
    assert not run.stdout_tail.startswith("�")
    assert written_stdout.endswith(run.stdout_tail.encode("utf-8"))


async def test_a_job_that_floods_the_other_stream_does_not_deadlock(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """只往 stderr 灌 1 MB 而 stdout 一个字不写, 作业照样跑完.

    串行读两条流的实现会挂在这里: Windows 管道缓冲约 64 KB, 桩阻塞在写系统调用上, `wait()` 永不
    返回, 直到作业级超时才被**误判成超时**——症状是"策略明明跑完却超时", 且只在输出量大时出现.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(
        client,
        run_owner.token,
        runnable.strategy.id,
        params={STDERR_FLOOD_BYTES_PARAMETER_KEY: PIPE_DEADLOCK_STDERR_BYTES},
    )

    run = await await_run_terminal(
        database, submitted.id, FLOOD_ELAPSED_LIMIT_SECONDS
    )

    assert run.status == RunStatus.SUCCEEDED.value, run.error_msg

    written_stderr = (job_directory(platform_settings, run.id) / "stderr.txt").read_bytes()

    # 那一条非阻断的 ABI 诊断也会进 stderr (测试的引擎根是空目录), 故这里断言"至少"。
    assert len(written_stderr) >= PIPE_DEADLOCK_STDERR_BYTES
    assert (job_directory(platform_settings, run.id) / "stdout.txt").read_bytes() == b""


async def test_an_entry_filename_with_a_space_survives_launch(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """入口文件名含空格也要跑得通.

    这一条依赖 `create_subprocess_exec` 的参数列表语义: 改成手工拼一行命令行, 空格处会被拆成两个
    参数, 而症状是"策略偶尔起不来", 与文件名毫无表面关联.
    """

    runnable = await create_runnable_strategy(
        database,
        platform_settings,
        run_owner.user,
        f"{STRATEGY_NAME}(带空格)",
        entry_filename=STUB_SPACE_ENTRY_FILENAME,
        config_filename=STUB_CONFIG_FILENAME,
    )

    run = await _submit_and_wait(client, database, run_owner.token, runnable.strategy.id)

    assert run.status == RunStatus.SUCCEEDED.value
    assert read_reported_argv0(platform_settings, run.id) == STUB_SPACE_ENTRY_FILENAME
    assert (job_directory(platform_settings, run.id) / STUB_SPACE_ENTRY_FILENAME).is_file()


async def test_a_failed_job_construction_does_not_starve_the_queue(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """作业目录构造失败落 `failed`, 且**随后的作业照样跑完**.

    后半句才是重点: 构造失败的那一轮若把信号量漏还, 在上限为 1 的部署下表现为"从此再也不跑了"
    ——而平台照常接收提交、照常回 201.

    构造失败靠删掉版本目录里的入口文件制造: `copyfile` 会抛 `FileNotFoundError`, 而它是**确定性**
    的 (比"运行中删目录"可靠——Windows 上打开着的句柄会让删除本身失败).
    """

    broken = await create_runnable_strategy(
        database, platform_settings, run_owner.user, f"{STRATEGY_NAME}(缺入口文件)"
    )
    healthy = await create_runnable_strategy(
        database, platform_settings, run_owner.user, f"{STRATEGY_NAME}(正常)"
    )

    entry_source_path = (
        platform_settings.user_library_root
        / broken.version.storage_path
        / broken.version.entry_filename
    )
    assert entry_source_path.is_file()

    entry_source_path.unlink()

    failed_run = await _submit_and_wait(
        client, database, run_owner.token, broken.strategy.id
    )

    assert failed_run.status == RunStatus.FAILED.value
    assert failed_run.error_msg == JOB_DIRECTORY_FAILURE_MESSAGE
    assert not job_directory(platform_settings, failed_run.id).exists()

    following_run = await _submit_and_wait(
        client, database, run_owner.token, healthy.strategy.id
    )

    assert following_run.status == RunStatus.SUCCEEDED.value
    assert job_directory(platform_settings, following_run.id).is_dir()


async def test_soft_deleting_the_strategy_does_not_stop_a_submitted_job(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """提交之后软删策略, 已提交的作业仍要跑完.

    版本目录是 append-only 的: 软删只改一行, 不碰盘. 若软删顺手删了版本目录, 一轮已经排队的回测
    会在构造作业目录时莫名失败, 而用户看到的是"刚点了删除, 排队中的回测就炸了".
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(client, run_owner.token, runnable.strategy.id)

    await soft_delete_strategy_record(database, runnable.strategy.id)

    run = await await_run_terminal(
        database, submitted.id, CONCURRENT_ELAPSED_LIMIT_SECONDS
    )

    assert run.status == RunStatus.SUCCEEDED.value


async def test_a_job_over_the_time_limit_is_killed(
    platform_settings: PlatformSettings,
) -> None:
    """超时后进程必须真的死掉, 而不只是库里写了一句 `timeout`.

    判据是 `span.txt` 里**没有 end 行**: 桩要睡满 30 秒才写它, 而时限是 1 秒. 若进程没被收掉,
    它会在几秒后把那一行补上.
    """

    limited_settings = settings_with(
        platform_settings, max_concurrent_runs=1, run_timeout_seconds=TIMEOUT_SECONDS
    )

    async with running_client(limited_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "timeout-owner")

        runnable = await create_runnable_strategy(
            database, limited_settings, owner.user, STRATEGY_NAME
        )

        run = await _submit_and_wait(
            client,
            database,
            owner.token,
            runnable.strategy.id,
            params={SLEEP_SECONDS_PARAMETER_KEY: TIMEOUT_SLEEP_SECONDS},
            timeout_seconds=TIMEOUT_ELAPSED_LIMIT_SECONDS + 10,
        )

        assert run.status == RunStatus.TIMEOUT.value
        assert run.error_msg == TIMEOUT_MESSAGE
        assert run.exit_code is not None
        assert TIMEOUT_SECONDS <= run.duration_ms / 1000 < TIMEOUT_ELAPSED_LIMIT_SECONDS

        # 被杀进程写不完结果文件: 陈旧结果防护在启动时就删掉了它, 而本轮没走到写那一步.
        assert not (job_directory(limited_settings, run.id) / RESULT_FILENAME).exists()

        assert count_span_end_lines(limited_settings, run.id) == 0


async def test_the_host_startup_failure_exit_code_is_put_on_the_row(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """退出码 1 = 宿主启动失败: 结果文件不存在, 四个镜像列一个都不许落.

    判据是 `db_path == ""`: 空串是列默认值, 而"引擎真跑过"一定会把它写成 `./BackTest_<id>.db`.
    于是"这一列非空"就是镜像落过库的证据, 比断言 `is_success is None` 更贴近事实.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_EXIT_WITHOUT_HOST},
    )

    assert run.status == RunStatus.FAILED.value
    assert run.exit_code == 1
    assert run.error_msg == HOST_STARTUP_FAILURE_MESSAGE
    assert run.is_success is None
    assert run.db_path == ""
    assert run.trade_count is None


async def test_the_unreadable_result_exit_code_is_put_on_the_row(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """退出码 2 = 结果文件缺失或不可解析。

    "不可解析"与"缺失"同码, 因为对平台来说后续动作一样: 拿不到结果就是拿不到.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_UNREADABLE_RESULT},
    )

    assert run.status == RunStatus.FAILED.value
    assert run.exit_code == 2
    assert run.error_msg == RESULT_MISSING_MESSAGE
    assert (job_directory(platform_settings, run.id) / RESULT_FILENAME).is_file()


async def test_a_reported_engine_failure_is_mirrored(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """退出码 3 **且结果文件可解析** = 引擎报告失败: 镜像要落库.

    与下一条用例成对: 两条的退出码相同, 只有文件在不在之分. 这就是"消歧靠文件不靠码"。
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_ENGINE_REPORTED_FAILURE},
    )

    assert run.status == RunStatus.FAILED.value
    assert run.exit_code == 3
    assert run.is_success is False
    assert run.error_id is not None
    assert run.market_data_type == "Bar"
    assert run.error_msg == "桩: 引擎报告本轮回测失败"


async def test_a_reported_engine_failure_without_a_result_file_is_not_mirrored(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """退出码 3 但**没有**结果文件 = 宿主崩溃: 一个镜像列都不许落.

    文案也一并钉住: 没有结果文件时平台看不见任何原因, 只能如实说"异常退出 (退出码 3)". 写成
    "引擎报告本轮回测失败" 会让平台替引擎断言一件它看不到的事——而用户照这句话去查引擎日志时,
    那份日志根本不存在 (进程崩溃时什么都没写).

    两条 3 号用例的退出码相同, 差别只在文件在不在: 这一条测的正是"消歧靠文件不靠码".
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_CRASHED_WITHOUT_RESULT},
    )

    assert run.status == RunStatus.FAILED.value
    assert run.exit_code == 3
    assert run.is_success is None
    assert run.error_id is None
    assert run.market_data_type is None
    assert run.error_msg == build_unexpected_exit_message(run.exit_code)


async def test_a_zero_exit_code_without_a_result_file_is_not_success(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """退出码 0 **不足以**判成功: 结果文件必须存在、可解析、且 `Success` 为真.

    少了这一条, 一个把结果文件写坏的策略会被当成跑成功了——而"跑成功了"是用户唯一不会复查的
    结论.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_SUCCESS_WITHOUT_RESULT},
    )

    assert run.status == RunStatus.FAILED.value
    assert run.exit_code == 0
    assert run.error_msg == SUCCESS_WITHOUT_RESULT_MESSAGE


async def test_the_result_file_is_mirrored_column_by_column(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """结果镜像落库: 桩写的每一列都要逐项出现在行上.

    `Balance` 取 `998951.4506464996` 逐位相等, 钉住的是金额列用 `Float` 而非 `Numeric`: 定点数
    会把末位那一位抹掉, 而"金额少了一位"是没人会去复查的那类差异.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_FULL_MIRROR},
    )

    written_result = read_job_json(platform_settings, run.id, RESULT_FILENAME)

    assert written_result["MissingRateKeys"], "镜像用例的结果文件必须带着长尾数组"

    for result_key, column_name in MIRROR_COLUMN_BY_RESULT_KEY.items():
        assert getattr(run, column_name) == written_result[result_key], result_key

    assert run.balance == 998951.4506464996


async def test_a_result_file_written_in_gbk_is_still_a_report(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """结果文件是 GBK 编码时, 结论仍是"引擎报告失败", 不是"结果文件不可解析".

    中文 Windows 上引擎有 GBK 变体, 而那种字节序列不是合法 UTF-8. 读文件若不宽容解码, 一个
    **引擎报告失败**会被报成**宿主崩溃**——两者的后续动作与给用户的提示完全相反, 而结果文件
    明明就在盘上.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_GBK_ERROR_MESSAGE},
    )

    assert run.status == RunStatus.FAILED.value
    assert run.exit_code == 3
    assert run.is_success is False
    assert run.error_msg == GBK_ERROR_MESSAGE
    assert run.error_id == REPORTED_FAILURE_ERROR_ID


async def test_a_result_reporting_a_foreign_run_id_still_lands_on_this_row(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """结果文件里的 `RunId` 与本行不符时**以行主键为准**, 且不新建行.

    引擎可能报回上一轮的 id. 拿结果文件的 id 去定位行, 轻则镜像写进别人的行, 重则凭空多出一行
    不属于任何提交的记录.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    run = await _submit_and_wait(
        client,
        database,
        run_owner.token,
        runnable.strategy.id,
        params={BEHAVIOR_PARAMETER_KEY: BEHAVIOR_FOREIGN_RUN_ID},
    )

    assert run.id != FOREIGN_RUN_ID
    assert run.status == RunStatus.SUCCEEDED.value
    assert run.trade_count == 84
    assert (
        read_job_json(platform_settings, run.id, RESULT_FILENAME)["RunId"]
        == FOREIGN_RUN_ID
    )
