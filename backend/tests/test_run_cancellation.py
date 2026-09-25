"""取消: 排队中、运行中, 以及与收尾抢同一行的那一瞬.

三档各对应一处真实交错:

- **排队中**: 行还没被认领, 取消只需一次条件更新. 断言里最要紧的是 `duration_ms is None`——它
  从 `started_at` 算出来, 而"根本没开始跑"与"瞬间跑完"是两件事.
- **运行中**: 取消要送达 runner, 由它收掉进程再写终态. 断言进程真的死了 (`span.txt` 没有 end 行)
  与 `result.json` 不存在——后者是"陈旧结果防护生效"的证据, 少了它, 一个被取消的作业会带着上一轮
  的结果被判成功.
- **抢同一行**: 桩写完结果文件之后停一会儿才退, 取消正好落在这个窗口里. 终态只可能是二者之一,
  而**不允许出现"状态说跑完了、指标说没跑完"的行**——那正是收尾绕过 CAS 写指标的症状.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel
from app.clock import utc_now
from app.config import PlatformSettings
from app.scheduler.runner import CANCEL_MESSAGE
from app.scheduler.workspace import STAGING_DIRECTORY_PREFIX

from .helpers import (
    SignedInAccount,
    bearer_headers,
    create_run_record,
    create_signed_in_account,
    create_strategy_record,
    create_strategy_version_record,
    update_record_by_id,
)
from .run_helpers import (
    EXIT_DELAY_SECONDS_PARAMETER_KEY,
    RESULT_FILENAME,
    RUNS_PATH,
    SLEEP_SECONDS_PARAMETER_KEY,
    await_process_started,
    await_run_status,
    await_run_terminal,
    count_span_end_lines,
    create_runnable_strategy,
    job_directory,
    read_run_record,
    running_client,
    settings_with,
    submit_run,
)


STRATEGY_NAME = "桩策略"
CANCEL_CONFIRMATION_SECONDS = 2
RACE_POLL_INTERVAL_SECONDS = 0.01
SETTLED_OBSERVATION_SECONDS = 0.5

QUEUED_BLOCKER_SLEEP_SECONDS = 3
RUNNING_SLEEP_SECONDS = 30
RACE_EXIT_DELAY_SECONDS = 0.2
RUNNING_ELAPSED_LIMIT_SECONDS = 60
QUEUE_LIMIT = 1

CANCEL_MENTION = "取消"
UNKNOWN_RUN_ID = "run-id-that-was-never-issued"
STATUS_CONFLICT_MESSAGE = "运行已结束, 无法取消"

STDOUT_FILENAME = "stdout.txt"
STDERR_FILENAME = "stderr.txt"


async def cancel_run(client: AsyncClient, token: str, run_id: str):
    """请求取消一个运行, 原样返回响应."""

    return await client.post(
        f"{RUNS_PATH}/{run_id}/cancel", headers=bearer_headers(token)
    )


def assert_row_is_self_consistent(run: RunModel) -> None:
    """一行终态不得自相矛盾.

    两种矛盾各对应一处真实缺陷:

    1. **跑到一半却说跑完了**: `interrupted` 的行上带着 `trade_count` / `balance`——收尾绕过 CAS
       写指标时会留下这种行 (取消先把状态改成 `interrupted`, 收尾随后把指标盖上去).
    2. **跑完了却说被取消了**: `succeeded` 的行上带着取消文案——两个写入者各写了一半.
    """

    if run.status == RunStatus.SUCCEEDED.value:
        assert CANCEL_MENTION not in (run.error_msg or ""), run.error_msg
        assert run.trade_count == 84
        assert run.balance == 998951.4506464996
        return

    assert run.status == RunStatus.INTERRUPTED.value
    assert CANCEL_MENTION in (run.error_msg or "")
    assert run.is_success is None
    assert run.trade_count is None
    assert run.balance is None
    assert run.market_data_type is None


async def test_cancelling_a_queued_run_never_starts_it(
    platform_settings: PlatformSettings,
) -> None:
    """取消排队中的作业: 行变 `interrupted`, 耗时是 NULL, 作业目录**从未被创建**.

    耗时必须是 NULL: 它从 `started_at` 算, 而这一轮根本没开始跑——写成 0 会被读成"瞬间跑完".
    作业目录不存在则说明"构造只在作业任务里"这条成立: 端点在提交时建目录的话, 用户会看到一整个
    已经落盘的作业目录, 而他刚点的正是取消.
    """

    limited_settings = settings_with(
        platform_settings, max_concurrent_runs=QUEUE_LIMIT
    )

    async with running_client(limited_settings) as (application, client):
        database = application.state.database
        owner = await create_signed_in_account(database, client, "cancel-queued-owner")

        runnable = await create_runnable_strategy(
            database, limited_settings, owner.user, STRATEGY_NAME
        )

        blocking = await submit_run(
            client,
            owner.token,
            runnable.strategy.id,
            params={SLEEP_SECONDS_PARAMETER_KEY: QUEUED_BLOCKER_SLEEP_SECONDS},
        )

        await await_run_status(
            database,
            blocking.id,
            RunStatus.RUNNING,
            RUNNING_ELAPSED_LIMIT_SECONDS,
        )

        queued = await submit_run(client, owner.token, runnable.strategy.id)

        response = await cancel_run(client, owner.token, queued.id)

        assert response.status_code == 200
        assert response.json()["status"] == RunStatus.INTERRUPTED.value

        cancelled_run = await read_run_record(database, queued.id)

        assert cancelled_run.status == RunStatus.INTERRUPTED.value
        assert cancelled_run.started_at is None
        assert cancelled_run.duration_ms is None
        assert cancelled_run.runner_pid is None
        assert CANCEL_MENTION in cancelled_run.error_msg

        # 阻断者不受影响: 取消只作用于被点名的那一行.
        assert (await read_run_record(database, blocking.id)).status == (
            RunStatus.RUNNING.value
        )

        assert not job_directory(limited_settings, queued.id).exists()

        await await_run_terminal(
            database, blocking.id, RUNNING_ELAPSED_LIMIT_SECONDS
        )

        # 阻断者跑完之后, 被取消的那一行仍不该被认领——它是终态, 不在取件范围内.
        assert not job_directory(limited_settings, queued.id).exists()
        assert (await read_run_record(database, queued.id)).status == (
            RunStatus.INTERRUPTED.value
        )

    assert not [
        entry
        for entry in limited_settings.runs_root.iterdir()
        if entry.name.startswith(STAGING_DIRECTORY_PREFIX)
    ]


async def test_cancelling_a_run_that_has_no_handle_interrupts_it_in_place(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """"已认领但句柄还没登记"那一格: 就地写成中断, 不留进程号, 也不建作业目录.

    这是取消端点上唯一没有句柄可送达的一格 (另一格是还没被认领的 `queued`). 它真实可达: 认领与
    `registry.register` 之间隔着一个 await, 取消正好落在那里. 落在那里的作业会在起进程之前读到
    "行已不是 running"而退出, 于是**进程从未存在**——判据是 `runner_pid` 仍为 NULL, 那是"没起过
    进程"唯一留在行上的证据.

    直接造出这一格的行 (而不是去卡那个 await 宽的窗口): 窗口卡不住, 而这里要测的是端点拿到这一
    格之后的行为.
    """

    strategy = await create_strategy_record(database, run_owner.user, STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, run_owner.user)

    claimed_run = await create_run_record(
        database, run_owner.user, strategy, version, status=RunStatus.RUNNING
    )

    await update_record_by_id(
        database, RunModel, claimed_run.id, _mark_as_claimed_without_a_handle
    )

    response = await cancel_run(client, run_owner.token, claimed_run.id)

    assert response.status_code == 200
    assert response.json()["status"] == RunStatus.INTERRUPTED.value

    cancelled_run = await read_run_record(database, claimed_run.id)

    assert cancelled_run.status == RunStatus.INTERRUPTED.value
    assert CANCEL_MENTION in cancelled_run.error_msg
    assert cancelled_run.runner_pid is None
    assert cancelled_run.exit_code is None
    assert cancelled_run.is_success is None

    assert not job_directory(platform_settings, claimed_run.id).exists()


def _mark_as_claimed_without_a_handle(run: RunModel) -> None:
    """把一行写成"刚被认领、还没起进程"的样子: 开始时间有, 进程号没有."""

    run.started_at = utc_now()
    run.runner_pid = None


async def test_cancelling_a_running_run_kills_the_process(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """取消运行中的作业: 进程真的死了, 且**结果文件不存在**.

    `result.json` 不存在是"陈旧结果防护"生效的证据: 桩在启动时先删掉上一轮的结果文件, 故它的
    缺席等价于"本轮没走到写结果那一步". 少了这一步, 一个被取消的作业会带着上一轮的结果被判成
    成功——而用户看到的正是他刚取消掉的那一轮。
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(
        client,
        run_owner.token,
        runnable.strategy.id,
        params={SLEEP_SECONDS_PARAMETER_KEY: RUNNING_SLEEP_SECONDS},
    )

    await await_process_started(platform_settings, submitted.id)

    response = await cancel_run(client, run_owner.token, submitted.id)

    assert response.status_code == 200

    cancelled_run = await await_run_terminal(
        database, submitted.id, RUNNING_ELAPSED_LIMIT_SECONDS
    )

    assert cancelled_run.status == RunStatus.INTERRUPTED.value
    assert CANCEL_MENTION in cancelled_run.error_msg
    assert cancelled_run.exit_code is not None
    assert cancelled_run.duration_ms is not None
    assert cancelled_run.is_success is None

    directory = job_directory(platform_settings, submitted.id)

    assert (directory / STDOUT_FILENAME).is_file()
    assert (directory / STDERR_FILENAME).is_file()
    assert not (directory / RESULT_FILENAME).exists()

    # 进程确实被杀: 桩要睡满 30 秒才写 end 行.
    assert count_span_end_lines(platform_settings, submitted.id) == 0


async def test_a_cancel_racing_the_finalization_leaves_one_consistent_row(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """取消落在"结果文件已写出、进程还没退"那一瞬: 终态只可能是二者之一, 且行自洽.

    桩写完 `result.json` 之后停 0.2 秒才退, 于是这个窗口是**可复现**的——测试盯着盘上的结果文件
    出现, 一出现就发取消. 两个写入者 (取消端点与收尾) 都盯着同一行, 谁先提交谁赢.

    断言穷举两种终态而不是"至少是其中之一": 一个把状态与指标分开写的实现会留下**跑到一半却带着
    `trade_count` 的行**, 那种行前端无从判断该显示什么, 而它只在竞态下出现.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(
        client,
        run_owner.token,
        runnable.strategy.id,
        params={EXIT_DELAY_SECONDS_PARAMETER_KEY: RACE_EXIT_DELAY_SECONDS},
    )

    result_path = job_directory(platform_settings, submitted.id) / RESULT_FILENAME

    deadline = asyncio.get_running_loop().time() + RUNNING_ELAPSED_LIMIT_SECONDS

    while not result_path.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("桩未在时限内写出结果文件, 竞态窗口没赶上")

        await asyncio.sleep(RACE_POLL_INTERVAL_SECONDS)

    response = await cancel_run(client, run_owner.token, submitted.id)

    assert response.status_code == 200
    assert response.json()["status"] in (
        RunStatus.SUCCEEDED.value,
        RunStatus.INTERRUPTED.value,
    )

    settled_run = await await_run_terminal(
        database, submitted.id, RUNNING_ELAPSED_LIMIT_SECONDS
    )

    assert_row_is_self_consistent(settled_run)

    # 终态落定之后不许再有写入者回来改行: 等一会儿再读一次, 三个字段必须逐字相同.
    await asyncio.sleep(SETTLED_OBSERVATION_SECONDS)

    observed_again = await read_run_record(database, submitted.id)

    assert observed_again.status == settled_run.status
    assert observed_again.finished_at == settled_run.finished_at
    assert observed_again.duration_ms == settled_run.duration_ms


async def test_cancelling_someone_elses_run_is_a_404(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """非提交人取消 → 404, 且行不受影响.

    授权不延伸到他人的运行记录: 被授权人能提交回测, 不等于能看或能取消别人的运行.
    """

    outsider = await create_signed_in_account(database, client, "cancel-outsider")

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(
        client,
        run_owner.token,
        runnable.strategy.id,
        params={SLEEP_SECONDS_PARAMETER_KEY: RUNNING_SLEEP_SECONDS},
    )

    await await_run_status(
        database, submitted.id, RunStatus.RUNNING, RUNNING_ELAPSED_LIMIT_SECONDS
    )

    response = await cancel_run(client, outsider.token, submitted.id)

    assert response.status_code == 404
    assert (await read_run_record(database, submitted.id)).status == (
        RunStatus.RUNNING.value
    )

    # 收尾干净: 别把一条还在跑的作业留给下一个用例的进程表.
    assert (await cancel_run(client, run_owner.token, submitted.id)).status_code == 200


@pytest.mark.parametrize(
    "terminal_status", [RunStatus.SUCCEEDED, RunStatus.INTERRUPTED]
)
async def test_cancelling_an_already_finished_run_is_a_conflict(
    terminal_status: RunStatus,
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """取消一个已落终态的运行 → 409.

    409 而不是 400/404: 行存在且对调用方可见 (404 不成立), 请求本身合法 (400 不成立), 冲突的是
    **资源当前状态**.

    两种终态都测: 只测 `succeeded` 的话, 一个"只对成功态回 409"的实现会漏掉"被取消的行再取消
    一次"——而那正是前端双击取消按钮会发出的第二次请求.
    """

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(client, run_owner.token, runnable.strategy.id)

    if terminal_status == RunStatus.INTERRUPTED:
        await await_run_status(
            database, submitted.id, RunStatus.RUNNING, RUNNING_ELAPSED_LIMIT_SECONDS
        )

        assert (await cancel_run(client, run_owner.token, submitted.id)).status_code == 200

    finished_run = await await_run_terminal(
        database, submitted.id, RUNNING_ELAPSED_LIMIT_SECONDS
    )

    assert finished_run.status == terminal_status.value

    response = await cancel_run(client, run_owner.token, submitted.id)

    assert response.status_code == 409
    assert response.json()["detail"] == STATUS_CONFLICT_MESSAGE


async def test_cancelling_an_unknown_run_is_a_404(
    client: AsyncClient, run_owner: SignedInAccount
) -> None:
    """取消一个不存在的 id → 404."""

    response = await cancel_run(client, run_owner.token, UNKNOWN_RUN_ID)

    assert response.status_code == 404


async def test_cancelling_without_a_token_is_rejected(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    run_owner: SignedInAccount,
) -> None:
    """无令牌取消 → 401, 且行不受影响."""

    runnable = await create_runnable_strategy(
        database, platform_settings, run_owner.user, STRATEGY_NAME
    )

    submitted = await submit_run(
        client,
        run_owner.token,
        runnable.strategy.id,
        params={SLEEP_SECONDS_PARAMETER_KEY: RUNNING_SLEEP_SECONDS},
    )

    await await_run_status(
        database, submitted.id, RunStatus.RUNNING, RUNNING_ELAPSED_LIMIT_SECONDS
    )

    response = await client.post(f"{RUNS_PATH}/{submitted.id}/cancel")

    assert response.status_code == 401
    assert (await read_run_record(database, submitted.id)).status == (
        RunStatus.RUNNING.value
    )

    assert (await cancel_run(client, run_owner.token, submitted.id)).status_code == 200
