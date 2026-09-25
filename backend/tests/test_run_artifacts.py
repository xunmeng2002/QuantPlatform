"""作业产物的清单与下载.

产物读的是**文件系统**而不是库: 一次运行的作业目录里有什么, 由策略与引擎决定 (引擎自己写
`Dump/<RunId>/*.csv` 与 `.db`), 平台无法在入库时列全, 故这里必须按目录实况取.

两条边界在本模块里各有一组用例: **归属** (只有提交人能拿到自己的产物) 与**越界**
(路径必须落在作业目录之内). 后者比前者更容易写错——字符串判 `..` 会漏掉符号链接与大小写,
故它的断言落在"解析后的真实路径"上.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel, UserModel
from app.config import PlatformSettings
from app.routers.runs import (
    ARTIFACT_MEDIA_TYPE,
    ARTIFACT_NOT_FOUND_MESSAGE,
    JOB_DIRECTORY_MISSING_MESSAGE,
)

from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    bearer_headers,
    create_run_record,
    create_strategy_record,
    create_strategy_version_record,
    create_user_record,
    login,
    update_record_by_id,
)
from .run_helpers import job_directory


RUNS_PATH = "/api/runs"
RUN_OWNER_USERNAME = "artifact-owner"
OTHER_MEMBER_USERNAME = "artifact-outsider"
STRATEGY_NAME = "artifact-grid"

RESULT_FILENAME = "result.json"
DATABASE_FILENAME_SUFFIX = ".db"
STDOUT_FILENAME = "stdout.log"
DUMP_DIRECTORY_NAME = "Dump"
TRADE_FILENAME = "t_trade.csv"
TRADE_SUMMARY_FILENAME = "t_summary.csv"

RESULT_CONTENT = '{"RunId": "artifact-run", "TradeCount": 84}'
TRADE_CONTENT = "index,price,volume\n1,10.5,100\n2,10.6,200\n"
TRADE_SUMMARY_CONTENT = "key,value\nTradeCount,84\n"

TRAVERSAL_TO_CATALOG = "%2e%2e%2f%2e%2e%2fcatalog.db"
TRAVERSAL_TO_SIBLING_RUN = "%2e%2e%2f%2e%2e%2f{run_id}%2fresult.json"
ABSOLUTE_WINDOWS_PATH = "C:/Windows/win.ini"


@dataclass(frozen=True)
class ArtifactBoard:
    """两份作业目录: 一份属于成员, 一份属于别人.

    别人那一份必须**真的存在**: 越权用例若指向一个不存在的目录, 404 就分不清是"归属校验生效"
    还是"目录本来就不在".
    """

    member: UserModel
    member_token: str
    outsider: UserModel
    member_run: RunModel
    outsider_run: RunModel


def expected_artifact_contents(run_id: str) -> dict[str, str]:
    """一次作业目录应有的"相对路径 → 内容", 按引擎的真实布局.

    与 `write_job_files` 分开: 用例要拿它当期望值, 而写盘的函数会用同一份数据物化——
    期望值另抄一遍的话, 改一处漏一处就会变成"断言与实际永远一致"的空断言.
    """

    return {
        RESULT_FILENAME: RESULT_CONTENT,
        f"BackTest_{run_id}{DATABASE_FILENAME_SUFFIX}": "SQLite format 3\u0000",
        STDOUT_FILENAME: "engine started\n",
        f"{DUMP_DIRECTORY_NAME}/{run_id}/{TRADE_FILENAME}": TRADE_CONTENT,
        f"{DUMP_DIRECTORY_NAME}/{run_id}/{TRADE_SUMMARY_FILENAME}": TRADE_SUMMARY_CONTENT,
    }


def write_job_files(settings: PlatformSettings, run_id: str) -> None:
    """按引擎的真实布局写一份作业目录.

    一律走 `write_bytes` 而不是 `write_text`: 文本模式在 Windows 上会把 `\\n` 翻译成 `\\r\\n`,
    于是盘上的字节与用例手上的期望值不再相同——"下载到的字节与盘上一致"这条断言就会以
    "少了三个字节"的面目失败, 而看起来像端点的缺陷.
    """

    directory = job_directory(settings, run_id)

    for relative_path, content in expected_artifact_contents(run_id).items():
        artifact_path = directory / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(content.encode("utf-8"))


@pytest_asyncio.fixture
async def artifact_board(
    client: AsyncClient, database: PlatformDatabase, platform_settings: PlatformSettings
) -> ArtifactBoard:
    member = await create_user_record(database, RUN_OWNER_USERNAME)
    outsider = await create_user_record(database, OTHER_MEMBER_USERNAME)
    strategy = await create_strategy_record(database, member, STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, member)

    member_run = await create_run_record(
        database, member, strategy, version, status=RunStatus.SUCCEEDED
    )
    outsider_run = await create_run_record(
        database, outsider, strategy, version, status=RunStatus.SUCCEEDED
    )

    write_job_files(platform_settings, member_run.id)
    write_job_files(platform_settings, outsider_run.id)

    return ArtifactBoard(
        member=member,
        member_token=await login(client, RUN_OWNER_USERNAME, DEFAULT_MEMBER_PASSWORD),
        outsider=outsider,
        member_run=member_run,
        outsider_run=outsider_run,
    )


def _artifacts_path(run_id: str) -> str:
    return f"{RUNS_PATH}/{run_id}/files"


def _artifact_path(run_id: str, relative_path: str) -> str:
    return f"{_artifacts_path(run_id)}/{relative_path}"


async def test_the_artifact_list_names_every_file_in_the_job_directory(
    client: AsyncClient,
    artifact_board: ArtifactBoard,
    platform_settings: PlatformSettings,
) -> None:
    """清单要能把嵌套的 `Dump/<RunId>/*.csv` 列出来, 且路径是相对作业目录的 POSIX 形式.

    相对路径带盘符或反斜杠时, 前端拼出来的下载 URL 会指向别处 (或直接报错), 故这里对形式
    本身断言, 而不只对条数.
    """

    response = await client.get(
        _artifacts_path(artifact_board.member_run.id),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["run_id"] == artifact_board.member_run.id

    listed = {
        artifact["relative_path"]: artifact["size_bytes"]
        for artifact in payload["artifacts"]
    }

    expected = expected_artifact_contents(artifact_board.member_run.id)

    assert set(listed) == set(expected)
    assert all("\\" not in path and ":" not in path for path in listed)

    disk_directory = job_directory(platform_settings, artifact_board.member_run.id)

    for relative_path, content in expected.items():
        assert listed[relative_path] == len(content.encode("utf-8"))
        assert (disk_directory / relative_path).exists()


async def test_an_artifact_downloads_byte_for_byte(
    client: AsyncClient, artifact_board: ArtifactBoard
) -> None:
    """下载到的字节必须与盘上逐字节相同——这是"下载"这条验收的判据."""

    nested_path = f"{DUMP_DIRECTORY_NAME}/{artifact_board.member_run.id}/{TRADE_FILENAME}"

    response = await client.get(
        _artifact_path(artifact_board.member_run.id, nested_path),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert response.status_code == 200
    assert response.text == TRADE_CONTENT
    assert response.headers["content-type"] == ARTIFACT_MEDIA_TYPE


async def test_an_artifact_is_always_served_as_an_attachment(
    client: AsyncClient, artifact_board: ArtifactBoard
) -> None:
    """一律按附件下发.

    策略是用户上传的任意 Python, 它可以往自己的作业目录里写一个 `.html`; 同源内联渲染等于
    让它在平台域上执行脚本. 故这里连"是附件"这件事本身也要断言.
    """

    response = await client.get(
        _artifact_path(artifact_board.member_run.id, RESULT_FILENAME),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")


async def test_a_html_artifact_is_not_rendered_inline(
    client: AsyncClient, artifact_board: ArtifactBoard, platform_settings: PlatformSettings
) -> None:
    """同上, 但拿一个真的 `.html` 去试: 类型必须仍是通用二进制, 不能是 `text/html`."""

    html_name = "report.html"

    (
        job_directory(platform_settings, artifact_board.member_run.id) / html_name
    ).write_bytes(b"<script>alert(1)</script>")

    response = await client.get(
        _artifact_path(artifact_board.member_run.id, html_name),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert response.status_code == 200
    assert "text/html" not in response.headers["content-type"]


async def test_an_unfinished_run_still_lists_its_artifacts(
    client: AsyncClient,
    artifact_board: ArtifactBoard,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """运行中的轮也能列: 这里读的是文件系统, 不碰库里的状态, 不存在"还没收尾"的限制."""

    await update_record_by_id(
        database,
        RunModel,
        artifact_board.member_run.id,
        lambda run: setattr(run, "status", RunStatus.QUEUED.value),
    )

    response = await client.get(
        _artifacts_path(artifact_board.member_run.id),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert response.status_code == 200
    assert response.json()["artifacts"]


async def test_a_run_without_a_job_directory_is_not_found(
    client: AsyncClient, artifact_board: ArtifactBoard, database: PlatformDatabase
) -> None:
    """目录可能在构造前那一轮就失败了 (行已落库而目录从未建起), 那种轮不该 500."""

    await update_record_by_id(
        database,
        RunModel,
        artifact_board.member_run.id,
        lambda run: setattr(run, "workspace_path", "no-such-directory"),
    )

    response = await client.get(
        _artifacts_path(artifact_board.member_run.id),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == JOB_DIRECTORY_MISSING_MESSAGE


async def test_an_empty_workspace_path_never_exposes_the_runs_root(
    client: AsyncClient, artifact_board: ArtifactBoard, database: PlatformDatabase
) -> None:
    """空的工作目录名拼出来就是**运行根本身**.

    `WorkspacePath` 的列缺省是空串, 于是这个字符串一旦直拼, "在运行根之内"的判断会一路通过,
    作业目录变成 `runs/`, 调用方就能顺着 `../<别人的 RunId>/result.json` 读走别人的产物.
    本用例把那一行改成空串, 断言它取不到任何东西——**这是唯一一条能杀死那种写法的断言**.
    """

    await update_record_by_id(
        database,
        RunModel,
        artifact_board.member_run.id,
        lambda run: setattr(run, "workspace_path", ""),
    )

    listing = await client.get(
        _artifacts_path(artifact_board.member_run.id),
        headers=bearer_headers(artifact_board.member_token),
    )
    escaped = await client.get(
        _artifact_path(
            artifact_board.member_run.id,
            TRAVERSAL_TO_SIBLING_RUN.format(run_id=artifact_board.outsider_run.id),
        ),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert listing.status_code == 404
    assert listing.json()["detail"] == JOB_DIRECTORY_MISSING_MESSAGE
    assert escaped.status_code == 404


async def test_a_directory_is_not_downloadable(
    client: AsyncClient, artifact_board: ArtifactBoard
) -> None:
    """`Dump` 是目录不是文件: 放过去会变成一个读不完的响应."""

    response = await client.get(
        _artifact_path(artifact_board.member_run.id, DUMP_DIRECTORY_NAME),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == ARTIFACT_NOT_FOUND_MESSAGE


async def test_another_users_artifacts_are_out_of_reach(
    client: AsyncClient, artifact_board: ArtifactBoard
) -> None:
    """越权与不存在必须给出同一种结果: 两者分开判, 差别本身就是泄漏.

    两条端点各测一次, 且**两条都在同一份真实存在的目录上**测: 拿一个不存在的目录去测越权,
    通过的原因可能只是"目录不在".
    """

    listing = await client.get(
        _artifacts_path(artifact_board.outsider_run.id),
        headers=bearer_headers(artifact_board.member_token),
    )
    download = await client.get(
        _artifact_path(artifact_board.outsider_run.id, RESULT_FILENAME),
        headers=bearer_headers(artifact_board.member_token),
    )

    assert listing.status_code == 404
    assert download.status_code == 404
    assert listing.json()["detail"] == download.json()["detail"]


async def test_artifacts_reject_an_anonymous_caller(
    client: AsyncClient, artifact_board: ArtifactBoard
) -> None:
    listing = await client.get(_artifacts_path(artifact_board.member_run.id))
    download = await client.get(
        _artifact_path(artifact_board.member_run.id, RESULT_FILENAME)
    )

    assert listing.status_code == 401
    assert download.status_code == 401


@pytest.fixture
def traversal_targets(artifact_board: ArtifactBoard) -> list[str]:
    """三条越界路径: 相对回溯、指到别人目录、以及一条绝对的 Windows 路径.

    `%2e` 编码不能省: httpx 会在构造 URL 时按 RFC 3986 折叠掉明文 `..`, 请求于是根本没走到
    这条路由——那样断言虽然也过, 但过的是"路由不存在", 与要测的东西无关. 故本模块对每条越界
    响应都断 `产物不存在` 这句原文: 它是**这条处理器**给的, 别的 404 给不出来.
    """

    return [
        TRAVERSAL_TO_CATALOG,
        TRAVERSAL_TO_SIBLING_RUN.format(run_id=artifact_board.outsider_run.id),
        ABSOLUTE_WINDOWS_PATH,
    ]


async def test_a_traversal_attempt_is_indistinguishable_from_a_missing_artifact(
    client: AsyncClient, artifact_board: ArtifactBoard, traversal_targets: list[str]
) -> None:
    """越界一律当"产物不存在", 不回"路径非法": 后者等于确认那个路径存在."""

    for target in traversal_targets:
        response = await client.get(
            _artifact_path(artifact_board.member_run.id, target),
            headers=bearer_headers(artifact_board.member_token),
        )

        assert response.status_code == 404, target
        assert response.json()["detail"] == ARTIFACT_NOT_FOUND_MESSAGE, target
