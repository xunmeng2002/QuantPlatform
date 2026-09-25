"""策略上传、版本留档与软删除.

上传是唯一把外部内容写进盘与库的入口, 故这里逐条钉住三件事: 落盘的字节与上传的一致、
版本判重看的是源码加 manifest 这一对、以及 manifest 里的文件名约束 (它是启动期致命的
`argv[0]` 约束的入口).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI, status
from httpx import AsyncClient, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message

from app.catalog.database import PlatformDatabase
from app.catalog.enums import MarketDataType, StrategyVisibility
from app.catalog.schemas import (
    MAXIMUM_STRATEGY_DESCRIPTION_LENGTH,
    MAXIMUM_STRATEGY_NAME_LENGTH,
    StrategyDetailResponse,
    StrategyResponse,
    StrategyVersionResponse,
)
from app.config import PlatformSettings
from app.main import (
    INVALID_CONTENT_LENGTH_DETAIL,
    MAXIMUM_REQUEST_BODY_BYTES,
    REQUEST_TOO_LARGE_DETAIL,
)
from app.manifest import (
    CONFIG_FILENAME_SUFFIX,
    ENTRY_FILENAME_SUFFIX,
    MANIFEST_INVALID_MESSAGE,
    MANIFEST_TOO_LARGE_MESSAGE,
    MAXIMUM_FILENAME_LENGTH,
    MAXIMUM_MANIFEST_BYTES,
)
from app.routers.strategies import (
    EMPTY_SOURCE_MESSAGE,
    MAXIMUM_SOURCE_BYTES,
    SOURCE_TOO_LARGE_MESSAGE,
    STRATEGY_DELETED_MESSAGE,
    STRATEGY_NAME_TAKEN_MESSAGE,
)

from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    bearer_headers,
    create_user_record,
    fetch_page,
    login,
)


STRATEGIES_PATH = "/api/strategies"

OWNER_USERNAME = "strategy-owner"
OTHER_USERNAME = "strategy-other-owner"

STRATEGY_NAME = "成对网格"
ENTRY_FILENAME = "grid_entry.py"
CONFIG_FILENAME = "GridConfig.json"
CLIENT_SIDE_PART_FILENAME = "whatever-the-browser-called-it.py"
MANIFEST_FILENAME = "manifest.json"

PARAMETER_LIST = [
    {"key": "GridStep", "label": "网格步长", "type": "number", "default": 10.0},
    {"key": "GridCount", "label": "网格层数", "type": "integer", "default": 5},
]

STRATEGY_SOURCE = """\
import sys


def main() -> int:
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

SECOND_STRATEGY_SOURCE = b"import sys\n\n\nsys.exit(0)\n"

REMOVED = object()

VALUE_MARKER = "marker-that-must-not-be-echoed"


@dataclass(frozen=True)
class SignedInAccount:
    """一个已登录账号. 留下 user_id 是为了直接核对盘上的归属目录."""

    user_id: str
    token: str


def manifest_payload(**overrides: object) -> str:
    """一份合法 manifest 的 JSON 文本, 按 overrides 逐键覆盖.

    传 REMOVED 表示把该键整个删掉, 用于制造"缺了某个必需键"的样例.
    """

    manifest: dict[str, object] = {
        "entry_filename": ENTRY_FILENAME,
        "config_filename": CONFIG_FILENAME,
        "supported_match_modes": [MarketDataType.BAR.value],
        "params": PARAMETER_LIST,
    }

    for key, value in overrides.items():
        if value is REMOVED:
            manifest.pop(key, None)
        else:
            manifest[key] = value

    return json.dumps(manifest, ensure_ascii=False)


@pytest_asyncio.fixture
async def owner_account(
    client: AsyncClient, database: PlatformDatabase
) -> SignedInAccount:
    user = await create_user_record(database, OWNER_USERNAME)

    return SignedInAccount(
        user_id=user.id,
        token=await login(client, OWNER_USERNAME, DEFAULT_MEMBER_PASSWORD),
    )


@pytest_asyncio.fixture
async def other_account(
    client: AsyncClient, database: PlatformDatabase
) -> SignedInAccount:
    user = await create_user_record(database, OTHER_USERNAME)

    return SignedInAccount(
        user_id=user.id,
        token=await login(client, OTHER_USERNAME, DEFAULT_MEMBER_PASSWORD),
    )


async def post_strategy(
    client: AsyncClient,
    token: str,
    *,
    manifest_text: str | None = None,
    source_bytes: bytes = STRATEGY_SOURCE.encode("utf-8"),
    name: str = STRATEGY_NAME,
    **form_overrides: str,
) -> Response:
    """经接口上传一个新策略."""

    form = {
        "manifest": manifest_payload() if manifest_text is None else manifest_text,
        "name": name,
        **form_overrides,
    }

    return await client.post(
        STRATEGIES_PATH,
        data=form,
        files={"source": (CLIENT_SIDE_PART_FILENAME, source_bytes, "text/x-python")},
        headers=bearer_headers(token),
    )


async def post_strategy_version(
    client: AsyncClient,
    token: str,
    strategy_id: str,
    *,
    manifest_text: str | None = None,
    source_bytes: bytes = STRATEGY_SOURCE.encode("utf-8"),
) -> Response:
    """给既有策略上传一个版本."""

    return await client.post(
        f"{STRATEGIES_PATH}/{strategy_id}/versions",
        data={"manifest": manifest_payload() if manifest_text is None else manifest_text},
        files={"source": (CLIENT_SIDE_PART_FILENAME, source_bytes, "text/x-python")},
        headers=bearer_headers(token),
    )


async def create_strategy(client: AsyncClient, token: str) -> StrategyDetailResponse:
    """上传一个策略并解出详情, 供后续步骤复用."""

    response = await post_strategy(client, token)

    assert response.status_code == 201, response.text

    return StrategyDetailResponse.model_validate(response.json())


async def reread_strategy(
    client: AsyncClient, token: str, strategy_id: str
) -> StrategyDetailResponse:
    response = await client.get(
        f"{STRATEGIES_PATH}/{strategy_id}", headers=bearer_headers(token)
    )

    assert response.status_code == 200, response.text

    return StrategyDetailResponse.model_validate(response.json())


def version_directory(
    settings: PlatformSettings, owner_user_id: str, strategy_id: str, version_no: int
) -> Path:
    """版本目录的期望路径, 按 platform-plan.md §7.4 的布局独立写出.

    刻意不复用服务端的构造函数: 复用等于用实现验证实现, 布局偏了也照样绿.
    """

    return (
        settings.user_library_root
        / owner_user_id
        / "strategies"
        / strategy_id
        / str(version_no)
    )


def strategy_directory(
    settings: PlatformSettings, owner_user_id: str, strategy_id: str
) -> Path:
    """一个策略在策略库里的目录."""

    return settings.user_library_root / owner_user_id / "strategies" / strategy_id


async def test_upload_creates_the_strategy_and_its_first_version(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy(client, owner_account.token)

    assert response.status_code == 201, response.text

    detail = StrategyDetailResponse.model_validate(response.json())

    assert detail.strategy.name == STRATEGY_NAME
    assert detail.strategy.owner_user_id == owner_account.user_id
    assert detail.strategy.visibility_type is StrategyVisibility.PRIVATE
    assert detail.grants == []
    assert len(detail.versions) == 1
    assert detail.versions[0].version_no == 1
    assert detail.versions[0].entry_filename == ENTRY_FILENAME
    assert detail.versions[0].config_filename == CONFIG_FILENAME


async def test_uploaded_source_lands_on_disk_byte_for_byte(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    detail = await create_strategy(client, owner_account.token)
    version = detail.versions[0]

    directory = version_directory(
        platform_settings, owner_account.user_id, detail.strategy.id, version.version_no
    )

    assert (directory / ENTRY_FILENAME).read_bytes() == STRATEGY_SOURCE.encode("utf-8")
    assert (directory / MANIFEST_FILENAME).is_file()


async def test_the_stored_entry_filename_comes_from_the_manifest_not_the_upload(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """入口文件名即 job 目录里的裸文件名与 argv[0], 故只能由 manifest 决定.

    浏览器给的分部文件名由客户端说了算, 拿它当文件名等于把 argv[0] 交给上传方.
    """

    detail = await create_strategy(client, owner_account.token)

    directory = version_directory(
        platform_settings,
        owner_account.user_id,
        detail.strategy.id,
        detail.versions[0].version_no,
    )

    assert (directory / ENTRY_FILENAME).is_file()
    assert not (directory / CLIENT_SIDE_PART_FILENAME).exists()


async def test_uploaded_strategy_shows_up_in_the_owners_list(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    await create_strategy(client, owner_account.token)

    page = await fetch_page(
        client, STRATEGIES_PATH, owner_account.token, StrategyResponse
    )

    assert [record.name for record in page.records] == [STRATEGY_NAME]


async def test_a_second_version_gets_the_next_version_number(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    detail = await create_strategy(client, owner_account.token)

    response = await post_strategy_version(
        client,
        owner_account.token,
        detail.strategy.id,
        source_bytes=SECOND_STRATEGY_SOURCE,
    )

    assert response.status_code == 201, response.text

    version = StrategyVersionResponse.model_validate(response.json())

    assert version.version_no == 2

    for version_no in (1, 2):
        assert (
            version_directory(
                platform_settings, owner_account.user_id, detail.strategy.id, version_no
            )
            / ENTRY_FILENAME
        ).is_file()


async def test_identical_content_reuses_the_version_instead_of_minting_a_new_one(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    detail = await create_strategy(client, owner_account.token)

    response = await post_strategy_version(client, owner_account.token, detail.strategy.id)

    assert response.status_code == 201, response.text
    assert StrategyVersionResponse.model_validate(response.json()).version_no == 1

    assert len((await reread_strategy(
        client, owner_account.token, detail.strategy.id
    )).versions) == 1


async def test_changing_only_the_manifest_mints_a_new_version(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """同一份源码配不同 manifest 是一份新版本.

    判重若只看源码, 这条路径要么被判成"内容未变"而吞掉, 要么撞上唯一约束——两者都逼着用户
    "改参数必须连源码一起改".
    """

    detail = await create_strategy(client, owner_account.token)

    changed_manifest = manifest_payload(
        params=[
            *PARAMETER_LIST,
            {"key": "VolumePerGrid", "type": "integer", "default": 1},
        ]
    )

    response = await post_strategy_version(
        client, owner_account.token, detail.strategy.id, manifest_text=changed_manifest
    )

    assert response.status_code == 201, response.text
    assert StrategyVersionResponse.model_validate(response.json()).version_no == 2


async def test_the_same_source_under_another_strategy_is_a_separate_version(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """判重的作用域是单个策略: 两个策略各有一份同样的源码, 各占各自的 1 号版本."""

    first = await create_strategy(client, owner_account.token)

    second_response = await post_strategy(client, owner_account.token, name="另一个策略")

    assert second_response.status_code == 201, second_response.text

    second = StrategyDetailResponse.model_validate(second_response.json())

    assert second.strategy.id != first.strategy.id
    assert second.versions[0].version_no == 1
    assert second.versions[0].id != first.versions[0].id


async def test_rotating_the_strategy_marks_it_as_updated(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    detail = await create_strategy(client, owner_account.token)

    await post_strategy_version(
        client,
        owner_account.token,
        detail.strategy.id,
        source_bytes=SECOND_STRATEGY_SOURCE,
    )

    reread = await reread_strategy(client, owner_account.token, detail.strategy.id)

    assert reread.strategy.updated_at > detail.strategy.updated_at


async def test_reusing_a_version_does_not_claim_the_strategy_was_updated(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """没有新内容落库, "最近变更"就不该往前动."""

    detail = await create_strategy(client, owner_account.token)

    await post_strategy_version(client, owner_account.token, detail.strategy.id)

    reread = await reread_strategy(client, owner_account.token, detail.strategy.id)

    assert reread.strategy.updated_at == detail.strategy.updated_at


async def test_parameter_entries_the_platform_does_not_know_yet_survive_verbatim(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """params 的类型枚举与校验规则尚未定案, 故此刻必须原样留存, 定案后不必回头改写已传的 manifest."""

    detail = await create_strategy(client, owner_account.token)

    directory = version_directory(
        platform_settings,
        owner_account.user_id,
        detail.strategy.id,
        detail.versions[0].version_no,
    )
    stored = json.loads((directory / MANIFEST_FILENAME).read_text(encoding="utf-8"))

    assert stored["params"] == PARAMETER_LIST


@pytest.mark.parametrize(
    "manifest_overrides",
    [
        {"entry_filename": f"nested/directory/entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"..\\entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": "entry.txt"},
        {"entry_filename": "  entry.py"},
        {"entry_filename": ""},
        {"entry_filename": "."},
        {"entry_filename": f"con{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"com1{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"lpt9{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"grid:entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"grid|entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"grid?entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"grid<entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"grid>entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f'grid"entry{ENTRY_FILENAME_SUFFIX}'},
        {"entry_filename": f"grid*entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"grid\tentry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": "x" * (MAXIMUM_FILENAME_LENGTH + 1) + ".py"},
        {"config_filename": f"Grid:Config{CONFIG_FILENAME_SUFFIX}"},
        {"config_filename": "GridConfig"},
        {"config_filename": "BackTest.json"},
        {"config_filename": "Sessions.json"},
        {"config_filename": "result.json"},
        {"config_filename": "sub/GridConfig.json"},
    ],
)
async def test_a_filename_that_cannot_be_a_bare_argv0_is_rejected(
    client: AsyncClient,
    owner_account: SignedInAccount,
    manifest_overrides: dict[str, str],
) -> None:
    """带分隔符的 argv[0] 会让引擎日志器打不开文件, 宿主在启动期终止; 堵在上传口最省事."""

    response = await post_strategy(
        client, owner_account.token, manifest_text=manifest_payload(**manifest_overrides)
    )

    assert response.status_code == 400, response.text


@pytest.mark.parametrize(
    "manifest_overrides",
    [
        {"supported_match_modes": REMOVED},
        {"supported_match_modes": []},
        {"supported_match_modes": ["Quote"]},
        {"supported_match_modes": [MarketDataType.BAR.value, MarketDataType.TICK.value,
                                   MarketDataType.BAR.value]},
        {"params": [{"key": "GridStep"}, {"key": "GridStep"}]},
        {"params": [{"key": "", "type": "number"}]},
        {"params": [{"label": "没有键"}]},
        {"params": ["GridStep"]},
        {"entry_filename": REMOVED},
        {"config_filename": REMOVED},
        {"unexpected_top_level_key": "anything"},
    ],
)
async def test_a_structurally_invalid_manifest_is_rejected(
    client: AsyncClient,
    owner_account: SignedInAccount,
    manifest_overrides: dict[str, object],
) -> None:
    response = await post_strategy(
        client, owner_account.token, manifest_text=manifest_payload(**manifest_overrides)
    )

    assert response.status_code == 400, response.text


async def test_a_missing_supported_match_mode_is_named_in_the_error(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """缺模式正是 P2 的验收项之一: 引擎在 Bar 模式下遇到漏写 on_bar 的策略会静默 0 成交."""

    response = await post_strategy(
        client,
        owner_account.token,
        manifest_text=manifest_payload(supported_match_modes=REMOVED),
    )

    assert response.status_code == 400
    assert "supported_match_modes" in response.json()["detail"]


async def test_a_malformed_manifest_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy(client, owner_account.token, manifest_text="{not json")

    assert response.status_code == 400
    assert MANIFEST_INVALID_MESSAGE in response.json()["detail"]


async def test_an_oversized_manifest_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """label 是参数项上的未知键, 长度不受限, 正好用来撑爆 manifest."""

    oversized_manifest = manifest_payload(
        params=[{"key": "GridStep", "label": "x" * MAXIMUM_MANIFEST_BYTES}]
    )

    response = await post_strategy(
        client, owner_account.token, manifest_text=oversized_manifest
    )

    assert response.status_code == 400
    assert response.json()["detail"] == MANIFEST_TOO_LARGE_MESSAGE


@pytest.mark.parametrize(
    "manifest_overrides",
    [
        {"entry_filename": f"{VALUE_MARKER}/entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"{VALUE_MARKER}:entry{ENTRY_FILENAME_SUFFIX}"},
        {"entry_filename": f"{VALUE_MARKER}.txt"},
        {"entry_filename": f" {VALUE_MARKER}{ENTRY_FILENAME_SUFFIX}"},
        {"config_filename": f"{VALUE_MARKER}|Grid{CONFIG_FILENAME_SUFFIX}"},
        {"params": [{"key": f"{VALUE_MARKER}!"}]},
        {"unexpected_top_level_key": VALUE_MARKER},
    ],
)
async def test_manifest_errors_never_echo_the_field_values(
    client: AsyncClient,
    owner_account: SignedInAccount,
    manifest_overrides: dict[str, object],
) -> None:
    """报错只回位置与原因: 回显值等于把整份 manifest 抄进响应体与接入层日志.

    守住这条的是 `_describe_validation_errors` **只取 `loc` 与 `msg`**——pydantic 交出来的
    报错项里**是带 `input` 的** (实测: 未知顶层键、参数 key 不合模式、合法字符检查失败三条
    都带), 所以"没人会把值抄出去"并不自动成立, 全靠那个函数不取它.

    取值因此刻意撒在**两类分支上**: 我们自己写的那几条 (分隔符、非法字符、后缀、首尾空白)
    与 pydantic 自带的那几条。两类都会经手那个函数, 只覆盖一类的话, 另一类上的回显改动
    不会被发现。
    """

    response = await post_strategy(
        client, owner_account.token, manifest_text=manifest_payload(**manifest_overrides)
    )

    assert response.status_code == 400, response.text
    assert VALUE_MARKER not in response.text


async def test_an_unparsable_source_is_rejected_before_it_reaches_the_disk(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """语法错误留到运行期只会以"宿主退出码 1"的面目出现, 归因要翻 stdout."""

    response = await post_strategy(
        client, owner_account.token, source_bytes=b"def main(:\n    pass\n"
    )

    assert response.status_code == 400
    assert "无法解析" in response.json()["detail"]
    assert not platform_settings.user_library_root.exists()


async def test_an_empty_source_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy(client, owner_account.token, source_bytes=b"")

    assert response.status_code == 400
    assert response.json()["detail"] == EMPTY_SOURCE_MESSAGE


async def test_an_oversized_source_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy(
        client, owner_account.token, source_bytes=b"#" * (MAXIMUM_SOURCE_BYTES + 1)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == SOURCE_TOO_LARGE_MESSAGE


async def test_a_request_body_beyond_the_cap_is_rejected_before_it_is_parsed(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """两道闸各管一段, 这一条钉的是外面那道.

    源码那道闸在处理函数里, 挡得住内存, 挡不住磁盘: 它执行时整个 multipart 体早已被解析完,
    超出的部分已经落进磁盘临时文件. 外面这道按 `Content-Length` 在路由之前回 413.

    413 而非 400 是关键判据: 两者都能说明"被拒了", 但只有 413 才是路由之前那道闸给的——
    拿到 400 就意味着请求已经被解析过一遍了, 那时限多大都已经晚了.
    """

    response = await post_strategy(
        client,
        owner_account.token,
        source_bytes=b"#" * MAXIMUM_REQUEST_BODY_BYTES,
    )

    assert response.status_code == 413
    assert response.json()["detail"] == REQUEST_TOO_LARGE_DETAIL
    assert not platform_settings.user_library_root.exists()


def build_upload_scope(declared_content_length: bytes) -> dict[str, object]:
    """手搓一个 multipart 上传请求的 ASGI scope."""

    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": STRATEGIES_PATH,
        "raw_path": STRATEGIES_PATH.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [
            (b"content-length", declared_content_length),
            (b"content-type", b"multipart/form-data; boundary=probe"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


OVERSIZED_CONTENT_LENGTH = str(MAXIMUM_REQUEST_BODY_BYTES + 1).encode()


@pytest.mark.parametrize(
    ("declared_content_length", "expected_status", "expected_detail"),
    [
        (
            OVERSIZED_CONTENT_LENGTH + b" ",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            REQUEST_TOO_LARGE_DETAIL,
        ),
        (
            OVERSIZED_CONTENT_LENGTH + b"\t",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            REQUEST_TOO_LARGE_DETAIL,
        ),
        (
            OVERSIZED_CONTENT_LENGTH + b"\x00",
            status.HTTP_400_BAD_REQUEST,
            INVALID_CONTENT_LENGTH_DETAIL,
        ),
        (b"\xb2", status.HTTP_400_BAD_REQUEST, INVALID_CONTENT_LENGTH_DETAIL),
        (b"9" * 5000, status.HTTP_400_BAD_REQUEST, INVALID_CONTENT_LENGTH_DETAIL),
    ],
)
async def test_a_malformed_content_length_cannot_skip_the_body_guard(
    application: FastAPI,
    declared_content_length: bytes,
    expected_status: int,
    expected_detail: str,
) -> None:
    """成帧层按 OWS 裁掉首尾空白后解析该值, 但 ASGI scope 里放的是**未裁剪的原文**.

    拿 `str.isdigit()` 当守卫就漏在这: `"1179649 "` 的 `isdigit()` 为假, 闸整条跳过, 请求被
    完整解析、超限部分落进磁盘临时文件——正是这道闸要防的事. 一个尾随空格就够了, 比记录在案的
    分块编码遗留项便宜得多. NUL 更别扭: `isdigit()` 为假而 `int()` 抛异常, 旧写法下是把异常
    抛穿用户中间件、绕过项目的 JSON 错误契约.

    经 ASGI scope 直接构造, 不走 httpx: 要测的正是**客户端发得出、框架层看不出**的那种畸形
    取值. 顺带钉住"体一个字节都不读"——判据是 `call_next` 从未被走到 (被路由到的请求, 哪怕
    不读体, 也会让 `receive` 走一次). `detail` 也一并钉住, 否则分不清这个 400 是守卫给的
    还是别处给的.
    """

    receive_call_count = 0

    async def receive() -> Message:
        nonlocal receive_call_count
        receive_call_count += 1

        return {"type": "http.request", "body": b"", "more_body": False}

    sent_messages: list[Message] = []

    async def send(message: Message) -> None:
        sent_messages.append(message)

    await application(build_upload_scope(declared_content_length), receive, send)

    response_start = next(
        message
        for message in sent_messages
        if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )

    assert response_start["status"] == expected_status
    assert json.loads(response_body)["detail"] == expected_detail
    assert receive_call_count == 0


async def test_a_legal_upload_at_the_source_and_manifest_limits_is_accepted(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """体积闸的余量必须装得下合法上限.

    只钉"超限被拒"是不够的: 把余量改小到装不下"源码 1 MB + manifest 64 KB"这个**允许**的
    组合, 全部既有测试照样是绿的, 而用户正常上传会莫名拿到 413. 体积闸有两个方向, 这里钉的是
    另一头——四次变异检查全在"改大/摘掉"那一侧, 恰好漏掉它.
    """

    manifest_padding = MAXIMUM_MANIFEST_BYTES - len(
        manifest_payload(params=[{"key": "GridStep", "label": ""}]).encode("utf-8")
    )
    sized_manifest = manifest_payload(
        params=[{"key": "GridStep", "label": "x" * manifest_padding}]
    )
    sized_source = b"#" * MAXIMUM_SOURCE_BYTES

    assert len(sized_manifest.encode("utf-8")) == MAXIMUM_MANIFEST_BYTES
    assert len(sized_source) + len(sized_manifest.encode("utf-8")) <= (
        MAXIMUM_REQUEST_BODY_BYTES
    )

    response = await post_strategy(
        client,
        owner_account.token,
        manifest_text=sized_manifest,
        source_bytes=sized_source,
        description="d" * MAXIMUM_STRATEGY_DESCRIPTION_LENGTH,
    )

    assert response.status_code == 201, response.text
    assert len(response.json()["strategy"]["description"]) == (
        MAXIMUM_STRATEGY_DESCRIPTION_LENGTH
    )


async def test_the_same_name_under_one_owner_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    await create_strategy(client, owner_account.token)

    response = await post_strategy(client, owner_account.token)

    assert response.status_code == 409
    assert response.json()["detail"] == STRATEGY_NAME_TAKEN_MESSAGE


async def test_the_same_name_under_another_owner_is_allowed(
    client: AsyncClient, owner_account: SignedInAccount, other_account: SignedInAccount
) -> None:
    """策略名只在归属人内部唯一: 甲取的名字不该挡住乙."""

    await create_strategy(client, owner_account.token)

    response = await post_strategy(client, other_account.token)

    assert response.status_code == 201, response.text


async def test_a_name_differing_only_by_surrounding_space_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """不归一化的话, "网格" 与 "网格 " 会各占一个策略, 而界面上看是同一个."""

    await create_strategy(client, owner_account.token)

    response = await post_strategy(client, owner_account.token, name=f"  {STRATEGY_NAME}  ")

    assert response.status_code == 409


async def test_a_blank_name_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy(client, owner_account.token, name="   ")

    assert response.status_code == 400


@pytest.mark.parametrize(
    "form_overrides",
    [
        {"name": ""},
        {"name": "x" * (MAXIMUM_STRATEGY_NAME_LENGTH + 1)},
        {"description": "x" * 501},
        {"visibility_type": "everyone"},
    ],
)
async def test_out_of_range_metadata_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount, form_overrides: dict[str, str]
) -> None:
    response = await post_strategy(client, owner_account.token, **form_overrides)

    assert response.status_code == 422


async def test_an_anonymous_upload_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        STRATEGIES_PATH,
        data={"manifest": manifest_payload(), "name": STRATEGY_NAME},
        files={"source": (CLIENT_SIDE_PART_FILENAME, b"pass\n", "text/x-python")},
    )

    assert response.status_code == 401


async def test_a_version_cannot_be_added_to_another_owners_strategy(
    client: AsyncClient, owner_account: SignedInAccount, other_account: SignedInAccount
) -> None:
    detail = await create_strategy(client, owner_account.token)

    response = await post_strategy_version(
        client, other_account.token, detail.strategy.id
    )

    assert response.status_code == 404


async def test_a_version_cannot_be_added_to_an_absent_strategy(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy_version(
        client, owner_account.token, "no-such-strategy-identifier"
    )

    assert response.status_code == 404


async def test_a_rejected_upload_never_touches_the_disk(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    detail = await create_strategy(client, owner_account.token)

    response = await post_strategy(client, owner_account.token)

    assert response.status_code == 409

    assert [
        path.name
        for path in strategy_directory(
            platform_settings, owner_account.user_id, detail.strategy.id
        ).iterdir()
    ] == ["1"]


async def test_a_database_failure_rolls_the_written_version_directory_back(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """落库失败必须把刚落成的版本目录撤掉.

    孤儿目录会占住那个版本号, 使下一次同号重试的整目录改名失败——一次瞬时故障就此变成
    永久故障.
    """

    detail = await create_strategy(client, owner_account.token)

    async def _fail_to_commit(session: AsyncSession) -> None:
        raise IntegrityError("simulated", None, RuntimeError("simulated"))

    monkeypatch.setattr(AsyncSession, "commit", _fail_to_commit)

    response = await post_strategy_version(
        client,
        owner_account.token,
        detail.strategy.id,
        source_bytes=SECOND_STRATEGY_SOURCE,
    )

    assert response.status_code == 409

    assert [
        path.name
        for path in strategy_directory(
            platform_settings, owner_account.user_id, detail.strategy.id
        ).iterdir()
    ] == ["1"]


async def test_the_owner_can_delete_a_strategy(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    detail = await create_strategy(client, owner_account.token)

    response = await client.delete(
        f"{STRATEGIES_PATH}/{detail.strategy.id}",
        headers=bearer_headers(owner_account.token),
    )

    assert response.status_code == 200
    assert response.json()["message"] == STRATEGY_DELETED_MESSAGE

    assert (
        await client.get(
            f"{STRATEGIES_PATH}/{detail.strategy.id}",
            headers=bearer_headers(owner_account.token),
        )
    ).status_code == 404

    page = await fetch_page(
        client, STRATEGIES_PATH, owner_account.token, StrategyResponse
    )

    assert page.total == 0


async def test_deleting_a_strategy_twice_is_not_found(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    detail = await create_strategy(client, owner_account.token)
    path = f"{STRATEGIES_PATH}/{detail.strategy.id}"

    first = await client.delete(path, headers=bearer_headers(owner_account.token))
    repeated = await client.delete(path, headers=bearer_headers(owner_account.token))

    assert first.status_code == 200
    assert repeated.status_code == 404


async def test_deleting_a_strategy_keeps_the_version_files(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """历史运行要复现就得靠那份原文, 清掉目录会让旧结果失去可追溯的依据."""

    detail = await create_strategy(client, owner_account.token)

    directory = version_directory(
        platform_settings,
        owner_account.user_id,
        detail.strategy.id,
        detail.versions[0].version_no,
    )

    await client.delete(
        f"{STRATEGIES_PATH}/{detail.strategy.id}",
        headers=bearer_headers(owner_account.token),
    )

    assert (directory / ENTRY_FILENAME).is_file()


async def test_another_owner_cannot_delete_a_strategy(
    client: AsyncClient, owner_account: SignedInAccount, other_account: SignedInAccount
) -> None:
    detail = await create_strategy(client, owner_account.token)

    response = await client.delete(
        f"{STRATEGIES_PATH}/{detail.strategy.id}",
        headers=bearer_headers(other_account.token),
    )

    assert response.status_code == 404


async def test_deleting_a_strategy_releases_its_name(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """名字只在**在用的**策略之间唯一.

    删掉的策略在列表与详情里都看不见了, 若名字仍被占住, 用户看到的现象是"没人用这个名字,
    却说我重名", 而且不提供硬删, 没有任何接口能把它释放出来.
    """

    deleted = await create_strategy(client, owner_account.token)

    await client.delete(
        f"{STRATEGIES_PATH}/{deleted.strategy.id}",
        headers=bearer_headers(owner_account.token),
    )

    recreated = await post_strategy(client, owner_account.token)

    assert recreated.status_code == 201, recreated.text

    detail = StrategyDetailResponse.model_validate(recreated.json())

    assert detail.strategy.name == STRATEGY_NAME
    assert detail.strategy.id != deleted.strategy.id
    assert detail.versions[0].version_no == 1


async def test_deleting_a_strategy_does_not_release_another_owners_name(
    client: AsyncClient, owner_account: SignedInAccount, other_account: SignedInAccount
) -> None:
    """释放只落在归属人自己那一格: 重名判据是 (归属人, 名字), 不是全局名字.

    与上一条配对: 上一条钉"该释放的必须释放", 这一条钉"不该释放的仍要拦住"——只把索引去掉也
    能让上一条变绿.
    """

    mine = await create_strategy(client, owner_account.token)

    await create_strategy(client, other_account.token)

    await client.delete(
        f"{STRATEGIES_PATH}/{mine.strategy.id}",
        headers=bearer_headers(owner_account.token),
    )

    assert (await post_strategy(client, other_account.token)).status_code == 409
