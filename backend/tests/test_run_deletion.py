"""删除一个已结束的运行: 先删作业目录, 后删行.

守卫那一组是本模块的重点. 路径穿越不靠"拒绝几个字符串"来防——判据落在**解析后的真实路径**上
(`resolve()` 之后再比 `is_relative_to`, 并要求目录名等于 `RunId`), 故用例建的是**盘上真的存在
的**目标: 运行根外的金丝雀、另一个真实运行的作业目录、一个名字对不上的嵌套目录、运行根本身.
每条都同时断言三件事: 请求失败、那些目录**还在**、那一行也还在. 只看状态码的话, 一个"先删干
净再报错"的实现照样能过.

"删不掉就保留行"同样是本模块的判据: 行是那个目录唯一的句柄 (目录名就是 `RunId`, 而知道它的只
有这一列), 报成功等于把磁盘泄漏变成看不见的.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient, Response

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel, StrategyModel, StrategyVersionModel
from app.catalog.schemas import MessageResponse
from app.catalog.visibility import RUN_NOT_FOUND_MESSAGE
from app.config import PlatformSettings
from app.routers.runs import (
    RUN_DELETED_MESSAGE,
    RUN_DIRECTORY_NOT_REMOVED_MESSAGE,
    RESULT_NOT_READY_MESSAGE,
)

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
    DUMP_DIRECTORY_NAME,
    RESULT_FILENAME,
    SLEEP_SECONDS_PARAMETER_KEY,
    await_run_status,
    await_run_terminal,
    create_runnable_strategy,
    job_directory,
    read_run_or_none,
    read_run_record,
    running_client,
    settings_with,
    submit_run,
    write_job_directory,
)


RUNS_PATH = "/api/runs"
RUN_OWNER_USERNAME = "deletion-owner"
OTHER_MEMBER_USERNAME = "deletion-outsider"
QUEUEING_OWNER_USERNAME = "deletion-queueing-owner"
STRATEGY_NAME = "deletion-grid"

CANARY_DIRECTORY_NAME = "canary"
CANARY_FILENAME = "keep.txt"
CANARY_ESCAPE_PATH = f"../{CANARY_DIRECTORY_NAME}"

UNRELATED_DIRECTORY_NAME = "unrelated"
UNRELATED_RUN_DIRECTORY_NAME = "inner"
UNRELATED_ESCAPE_PATH = f"{UNRELATED_DIRECTORY_NAME}/{UNRELATED_RUN_DIRECTORY_NAME}"

RUNS_ROOT_ESCAPE_PATH = "."

QUEUED_BLOCKER_SLEEP_SECONDS = 3
QUEUED_CASE_TIMEOUT_SECONDS = 30.0

FILE_INSTEAD_OF_DIRECTORY_CONTENT = "not a directory"


@dataclass(frozen=True)
class DeletionBoard:
    """一个成员的两个已结束运行, 加上别人名下的一个; 三者共用同一份策略版本.

    三份作业目录都要**真的落盘**: 正向用例要断言目录被移走, 越权与守卫用例要断言目录**没**被
    移走——后者若指向一个本来就不存在的目录, 失败就分不清是"守卫生效"还是"目录不在".
    """

    member: SignedInAccount
    outsider: SignedInAccount
    strategy: StrategyModel
    version: StrategyVersionModel
    member_run: RunModel
    neighbour_run: RunModel
    outsider_run: RunModel


def write_canary(directory: Path) -> Path:
    """在给定目录里落一个"还在不在"可判的金丝雀, 返回那个目录."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / CANARY_FILENAME).write_text("canary\n", encoding="utf-8")

    return directory


@pytest_asyncio.fixture
async def deletion_board(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> DeletionBoard:
    member = await create_signed_in_account(database, client, RUN_OWNER_USERNAME)
    outsider = await create_signed_in_account(database, client, OTHER_MEMBER_USERNAME)
    strategy = await create_strategy_record(database, member.user, STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, member.user)

    member_run = await create_run_record(
        database, member.user, strategy, version, status=RunStatus.SUCCEEDED
    )
    neighbour_run = await create_run_record(
        database, member.user, strategy, version, status=RunStatus.SUCCEEDED
    )
    outsider_run = await create_run_record(
        database, outsider.user, strategy, version, status=RunStatus.SUCCEEDED
    )

    for run in (member_run, neighbour_run, outsider_run):
        write_job_directory(platform_settings, run.id)

    return DeletionBoard(
        member=member,
        outsider=outsider,
        strategy=strategy,
        version=version,
        member_run=member_run,
        neighbour_run=neighbour_run,
        outsider_run=outsider_run,
    )


async def create_finished_run(
    database: PlatformDatabase,
    board: DeletionBoard,
    status: RunStatus = RunStatus.SUCCEEDED,
    run_id: str | None = None,
) -> RunModel:
    """再落一行运行 (不建目录), 供只关心"行还在不在"的用例加点变量.

    `run_id` 只在守卫用例里给: 那条"上跳一级但末段名字对得上"的越界取值要靠**运行号本身**拼出
    来, 故探针路径得在造行之前就定下来.
    """

    return await create_run_record(
        database,
        board.member.user,
        board.strategy,
        board.version,
        status=status,
        run_id=run_id,
    )


async def delete_run(client: AsyncClient, token: str, run_id: str) -> Response:
    """请求删除一轮, 原样返回响应."""

    return await client.delete(f"{RUNS_PATH}/{run_id}", headers=bearer_headers(token))


def assert_deleted(response: Response) -> None:
    """断言一次成功的删除回执.

    按 `MessageResponse` 解而不是取 `detail`: 删除成功是 200, 而 200 体的形状只有响应模型说得
    准——拿错误响应的字段名去读成功响应, 会在一个本该直接判定的地方抛 KeyError.
    """

    assert response.status_code == 200, response.text

    payload = MessageResponse.model_validate(response.json())

    assert payload.message == RUN_DELETED_MESSAGE
    assert payload.success


async def set_workspace_path(
    database: PlatformDatabase, run_id: str, workspace_path: str
) -> None:
    """把一行的作业目录列改成给定取值, 用于造出真实提交路径写不出来的那一格."""

    await update_record_by_id(
        database,
        RunModel,
        run_id,
        lambda run: setattr(run, "workspace_path", workspace_path),
    )


async def test_a_finished_run_disappears_with_its_whole_job_directory(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    deletion_board: DeletionBoard,
) -> None:
    """正向: 行没了、整棵目录树没了, 邻居一行一目录不少; 再删一次是 404."""

    target_directory = job_directory(platform_settings, deletion_board.member_run.id)
    neighbour_directory = job_directory(
        platform_settings, deletion_board.neighbour_run.id
    )

    response = await delete_run(
        client, deletion_board.member.token, deletion_board.member_run.id
    )

    assert_deleted(response)

    assert await read_run_or_none(database, deletion_board.member_run.id) is None
    assert not target_directory.exists()
    # 嵌套那一层要一起走: 顶层两个文件没了而 `Dump/<RunId>/` 留着, 正是"删了个大概"的样子.
    assert not (target_directory / DUMP_DIRECTORY_NAME).exists()

    neighbour_row = await read_run_or_none(
        database, deletion_board.neighbour_run.id
    )

    assert neighbour_row is not None
    assert neighbour_directory.exists()

    repeated = await delete_run(
        client, deletion_board.member.token, deletion_board.member_run.id
    )

    assert repeated.status_code == 404


async def test_another_members_run_is_not_found(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    deletion_board: DeletionBoard,
) -> None:
    """越权删除与删一个不存在的运行逐字不可分辨: 同状态码、同响应体, 且什么都不动."""

    outsider_directory = job_directory(
        platform_settings, deletion_board.outsider_run.id
    )

    forbidden = await delete_run(
        client, deletion_board.member.token, deletion_board.outsider_run.id
    )
    unknown = await delete_run(
        client, deletion_board.member.token, "no-such-run-identifier"
    )

    assert forbidden.status_code == 404, forbidden.text
    assert forbidden.json()["detail"] == RUN_NOT_FOUND_MESSAGE
    assert unknown.status_code == forbidden.status_code
    assert unknown.text == forbidden.text

    assert (
        await read_run_or_none(database, deletion_board.outsider_run.id)
    ) is not None
    assert outsider_directory.exists()


async def test_a_running_run_cannot_be_deleted(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    deletion_board: DeletionBoard,
) -> None:
    """运行中的轮其目录正被引擎占着, 删它要么失败要么留下半死的作业: 409 且一行一目录保留.

    这一行直接用库造 (`status='running'`): 调度器只认领 `queued`, 故它不会自己往下走, 断言因此
    不依赖任何时序.
    """

    running_run = await create_finished_run(database, deletion_board, RunStatus.RUNNING)

    write_job_directory(platform_settings, running_run.id)

    response = await delete_run(
        client, deletion_board.member.token, running_run.id
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == RESULT_NOT_READY_MESSAGE

    assert await read_run_or_none(database, running_run.id) is not None
    assert job_directory(platform_settings, running_run.id).exists()


async def test_a_queued_run_cannot_be_deleted_while_it_waits(
    platform_settings: PlatformSettings,
) -> None:
    """排队中的轮同样删不掉: 它下一刻就会被派发, 删行等于让一次在飞的派发落空.

    这一格必须**真的**处在排队态: 库里直接造一个 `queued` 行是不稳的 (调度器的看门狗 5 秒内就
    会认领它). 于是照 `test_run_scheduler` 的办法, 让唯一的槽位被一个真在跑的作业占住, 排队者
    才确定停在 `queued` 上.
    """

    limited_settings = settings_with(platform_settings, max_concurrent_runs=1)

    async with running_client(limited_settings) as (application, http_client):
        database = application.state.database
        owner = await create_signed_in_account(
            database, http_client, QUEUEING_OWNER_USERNAME
        )
        runnable = await create_runnable_strategy(
            database, limited_settings, owner.user, STRATEGY_NAME
        )

        blocking = await submit_run(
            http_client,
            owner.token,
            runnable.strategy.id,
            params={SLEEP_SECONDS_PARAMETER_KEY: QUEUED_BLOCKER_SLEEP_SECONDS},
        )

        await await_run_status(
            database, blocking.id, RunStatus.RUNNING, QUEUED_CASE_TIMEOUT_SECONDS
        )

        queued = await submit_run(http_client, owner.token, runnable.strategy.id)

        queued_row = await read_run_record(database, queued.id)

        assert queued_row.status == RunStatus.QUEUED.value

        response = await delete_run(http_client, owner.token, queued.id)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == RESULT_NOT_READY_MESSAGE

        assert await read_run_or_none(database, queued.id) is not None

        # 两条都等落终态: 停机时还有作业在跑的话, 收尾会以"停机时限内仍有作业"的警告面目出现,
        # 与这一条想测的东西混在一起.
        for run_id in (blocking.id, queued.id):
            finished_run = await await_run_terminal(database, run_id)
            assert finished_run.status == RunStatus.SUCCEEDED.value


async def test_a_directory_that_is_already_gone_does_not_block_deletion(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    deletion_board: DeletionBoard,
) -> None:
    """目录已不在 (手工清过盘、或上一次删到一半) 时照常删行: 删的是"运行"这个对象."""

    target_directory = job_directory(platform_settings, deletion_board.member_run.id)

    target_directory.rename(target_directory.with_name("moved-away-directory"))

    response = await delete_run(
        client, deletion_board.member.token, deletion_board.member_run.id
    )

    assert_deleted(response)
    assert await read_run_or_none(database, deletion_board.member_run.id) is None


async def test_a_row_without_a_recorded_directory_is_deletable(
    client: AsyncClient,
    database: PlatformDatabase,
    deletion_board: DeletionBoard,
) -> None:
    """目录列是空串的行同样删得掉: 它本来就没有目录可移, 不该被永久卡住."""

    await set_workspace_path(database, deletion_board.member_run.id, "")

    response = await delete_run(
        client, deletion_board.member.token, deletion_board.member_run.id
    )

    assert_deleted(response)
    assert await read_run_or_none(database, deletion_board.member_run.id) is None


async def test_a_workspace_path_outside_this_run_is_refused(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    deletion_board: DeletionBoard,
    tmp_path: Path,
) -> None:
    """守卫的结构性证明: 六种越界取值逐个来一遍, 每次都要求失败、探针还在、行还在.

    探针 (金丝雀、那个嵌套目录、另一个真实运行的产物) 都**真的存在**: 守卫若少一步, 它们就会被
    真的删掉——只断言状态码的话, 这组用例在一个"删完再报错"的实现上依然全绿.

    六条要能分别钉住守卫的每一步, 否则去掉某一步照样全绿. 前两条 (`../canary` 与绝对路径) 挂在
    "目录名必须等于 `RunId`"上 (它们的末段是 `canary`); `a/inner` 挂在同一步上但落在运行根**之
    内**; 第三条才是唯一钉住 `is_relative_to` 的那一步——它的末段**就是**运行号, 只有"解析后必须
    仍在运行根之内"能拦住它, 故那一条必须按运行号拼出来.
    """

    canary_directory = write_canary(tmp_path / CANARY_DIRECTORY_NAME)
    unrelated_directory = write_canary(
        platform_settings.runs_root
        / UNRELATED_DIRECTORY_NAME
        / UNRELATED_RUN_DIRECTORY_NAME
    )
    neighbour_directory = job_directory(
        platform_settings, deletion_board.neighbour_run.id
    )
    neighbour_result_path = neighbour_directory / RESULT_FILENAME

    upward_escape_run_id = "deletion-upward-escape"
    upward_escape_probe = write_canary(tmp_path / upward_escape_run_id)

    escape_cases: list[tuple[str, str, str, Path]] = [
        (
            "运行根之外",
            "deletion-canary-escape",
            CANARY_ESCAPE_PATH,
            canary_directory / CANARY_FILENAME,
        ),
        (
            "绝对路径",
            "deletion-absolute-escape",
            str(canary_directory.resolve()),
            canary_directory / CANARY_FILENAME,
        ),
        (
            "上跳一级但末段就是运行号",
            upward_escape_run_id,
            f"../{upward_escape_run_id}",
            upward_escape_probe / CANARY_FILENAME,
        ),
        (
            "名字对不上",
            "deletion-mismatched-name",
            UNRELATED_ESCAPE_PATH,
            unrelated_directory / CANARY_FILENAME,
        ),
        (
            "另一个运行的目录",
            "deletion-neighbour-escape",
            deletion_board.neighbour_run.id,
            neighbour_result_path,
        ),
        # 等于运行根本身: 放它过去等于一次请求抹掉全部历史轮, 故用根下某轮的文件当探针.
        (
            "运行根本身",
            "deletion-root-escape",
            RUNS_ROOT_ESCAPE_PATH,
            neighbour_result_path,
        ),
    ]

    for (
        escape_label,
        escape_run_id,
        workspace_path,
        survivor_probe,
    ) in escape_cases:
        escaping_run = await create_finished_run(
            database, deletion_board, run_id=escape_run_id
        )

        await set_workspace_path(database, escaping_run.id, workspace_path)

        response = await delete_run(
            client, deletion_board.member.token, escaping_run.id
        )

        assert response.status_code == 409, f"{escape_label}: {response.text}"
        assert (
            response.json()["detail"] == RUN_DIRECTORY_NOT_REMOVED_MESSAGE
        ), escape_label

        assert (
            await read_run_or_none(database, escaping_run.id) is not None
        ), escape_label
        assert survivor_probe.exists(), escape_label
        assert neighbour_directory.exists(), escape_label


async def test_a_file_where_the_directory_belongs_is_refused(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    deletion_board: DeletionBoard,
) -> None:
    """目录名上是个**文件**时删不掉: 失败路径要把异常收进 409, 而不是漏成 500.

    这一格真实可达: 作业目录名与 `RunId` 相同, 而盘上先有个同名文件 (手工放的、或上一次异常中断
    留下的), 作业构造因此失败并留下这一格.
    """

    usurped_run = await create_finished_run(database, deletion_board)
    usurped_path = job_directory(platform_settings, usurped_run.id)

    usurped_path.parent.mkdir(parents=True, exist_ok=True)
    usurped_path.write_text(FILE_INSTEAD_OF_DIRECTORY_CONTENT, encoding="utf-8")

    response = await delete_run(client, deletion_board.member.token, usurped_run.id)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == RUN_DIRECTORY_NOT_REMOVED_MESSAGE

    assert await read_run_or_none(database, usurped_run.id) is not None
    assert usurped_path.is_file()
    assert (
        usurped_path.read_text(encoding="utf-8") == FILE_INSTEAD_OF_DIRECTORY_CONTENT
    )


async def test_anonymous_callers_cannot_delete(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
    deletion_board: DeletionBoard,
) -> None:
    """没有令牌就没有删除权, 且这一行一目录都不动."""

    response = await client.delete(
        f"{RUNS_PATH}/{deletion_board.member_run.id}"
    )

    assert response.status_code == 401

    assert (
        await read_run_or_none(database, deletion_board.member_run.id)
    ) is not None
    assert job_directory(platform_settings, deletion_board.member_run.id).exists()
