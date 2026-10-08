"""策略上传、版本留档与软删除.

上传是唯一把外部内容写进盘与库的入口, 故这里逐条钉住三件事: 落盘的字节与上传的一致、版本判重
看的是**源码加配置模板**这一对、以及两份文件名的约束——入口名是启动期致命的 `argv[0]`, 配置名是
策略按硬编码名字 `open()` 的那份文件, 两个都由上传方给定, 故都得逐条挡住.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI, status
from httpx import AsyncClient, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message

from app.catalog.database import PlatformDatabase
from app.catalog.enums import StrategyVisibility
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
from app.routers.strategies import (
    CONFIGURATION_NOT_UTF8_MESSAGE,
    CONFIGURATION_TOO_LARGE_MESSAGE,
    EMPTY_CONFIGURATION_MESSAGE,
    EMPTY_SOURCE_MESSAGE,
    MAXIMUM_SOURCE_BYTES,
    MISSING_UPLOAD_FILENAME_MESSAGE,
    SOURCE_TOO_LARGE_MESSAGE,
    STRATEGY_DELETED_MESSAGE,
    STRATEGY_NAME_TAKEN_MESSAGE,
)
from app.strategy_configuration import (
    CONFIGURATION_FILENAME_SUFFIX,
    CONFIGURATION_NOT_AN_OBJECT_MESSAGE,
    ENTRY_FILENAME_SUFFIX,
    MAXIMUM_CONFIGURATION_BYTES,
    MAXIMUM_CONFIGURATION_KEY_COUNT,
    MAXIMUM_CONFIGURATION_KEY_LENGTH,
    MAXIMUM_FILENAME_LENGTH,
)

from .helpers import (
    STRATEGIES_PATH,
    SignedInAccount,
    bearer_headers,
    create_signed_in_account,
    create_strategy_record,
    create_strategy_version_record,
    fetch_page,
    read_strategy_detail,
    read_strategy_status_code,
)


OWNER_USERNAME = "strategy-owner"
OTHER_USERNAME = "strategy-other-owner"

STRATEGY_NAME = "成对网格"
ENTRY_FILENAME = "grid_entry.py"
CONFIG_FILENAME = "GridConfig.json"

#: 一份像样的配置模板: 四种 JSON 取值各来一个, 好让"取值类型决定控件形态"这件事有真样本.
#: 其中 `NestedRule` 与 `OptionalWindow` 是平台渲染不出控件的那两类——它们必须**原样透传**.
CONFIGURATION_TEMPLATE: dict[str, object] = {
    "GridStep": 10.0,
    "GridCount": 5,
    "UseLimitPrice": True,
    "GridLabel": "第一组",
    "NestedRule": {"Levels": [1, 2, 3], "Fallback": None},
    "OptionalWindow": None,
}

CONFIGURATION_TEXT = json.dumps(CONFIGURATION_TEMPLATE, ensure_ascii=False, indent=2)

STRATEGY_SOURCE = """\
import sys


def main() -> int:
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

SECOND_STRATEGY_SOURCE = b"import sys\n\n\nsys.exit(0)\n"

VALUE_MARKER = "marker-that-must-not-be-echoed"

UPLOAD_BOUNDARY = "probe-upload-boundary"


def build_multipart_body(parts: dict[str, tuple[str | None, str]]) -> bytes:
    """手搓一份 multipart 体, 分部按 `{"字段名": (文件名或 None, 内容)}` 给.

    走 `httpx` 的 `files=` 表达不出 `filename=""`——它会把这个分部**降级成普通字段** (实测:
    `Content-Disposition` 里干脆没有 `filename` 那一项), 于是请求在框架层就被拒, 根本轮不到
    处理函数. 要测的正是"客户端发得出、高层 API 却写不出"的那种体, 故自己拼.
    """

    lines: list[str] = []

    for field_name, (filename, content) in parts.items():
        disposition = f'Content-Disposition: form-data; name="{field_name}"'

        if filename is not None:
            disposition = f'{disposition}; filename="{filename}"'

        lines.append(f"--{UPLOAD_BOUNDARY}\r\n{disposition}\r\n\r\n{content}\r\n")

    lines.append(f"--{UPLOAD_BOUNDARY}--\r\n")

    return "".join(lines).encode("utf-8")


@pytest_asyncio.fixture
async def owner_account(
    client: AsyncClient, database: PlatformDatabase
) -> SignedInAccount:
    return await create_signed_in_account(database, client, OWNER_USERNAME)


@pytest_asyncio.fixture
async def other_account(
    client: AsyncClient, database: PlatformDatabase
) -> SignedInAccount:
    return await create_signed_in_account(database, client, OTHER_USERNAME)


def build_configuration_text(
    template: dict[str, object] | None = None,
) -> str:
    return json.dumps(
        CONFIGURATION_TEMPLATE if template is None else template,
        ensure_ascii=False,
    )


async def post_strategy(
    client: AsyncClient,
    token: str,
    *,
    configuration_text: str | None = None,
    source_bytes: bytes = STRATEGY_SOURCE.encode("utf-8"),
    name: str = STRATEGY_NAME,
    entry_filename: str = ENTRY_FILENAME,
    configuration_filename: str = CONFIG_FILENAME,
    **form_overrides: str,
) -> Response:
    """经接口上传一个新策略.

    两个分部的文件名**就是载荷**: 平台拿它们当作业目录里的文件名, 故这里默认给的是"作者在策略
    源码里硬编码的那个名字", 而不是浏览器随手编的名字.
    """

    resolved_configuration_text = (
        build_configuration_text() if configuration_text is None else configuration_text
    )

    return await client.post(
        STRATEGIES_PATH,
        data={"name": name, **form_overrides},
        files={
            "source": (entry_filename, source_bytes, "text/x-python"),
            "configuration": (
                configuration_filename,
                resolved_configuration_text.encode("utf-8"),
                "application/json",
            ),
        },
        headers=bearer_headers(token),
    )


async def post_strategy_version(
    client: AsyncClient,
    token: str,
    strategy_id: str,
    *,
    configuration_text: str | None = None,
    source_bytes: bytes = STRATEGY_SOURCE.encode("utf-8"),
) -> Response:
    """给既有策略上传一个版本."""

    resolved_configuration_text = (
        build_configuration_text() if configuration_text is None else configuration_text
    )

    return await client.post(
        f"{STRATEGIES_PATH}/{strategy_id}/versions",
        files={
            "source": (ENTRY_FILENAME, source_bytes, "text/x-python"),
            "configuration": (
                CONFIG_FILENAME,
                resolved_configuration_text.encode("utf-8"),
                "application/json",
            ),
        },
        headers=bearer_headers(token),
    )


async def post_raw_upload(
    client: AsyncClient,
    token: str,
    *,
    entry_filename: str | None,
    configuration_filename: str,
) -> Response:
    """按**线上原样的字节**发一次上传, 绕开 `httpx` 的 multipart 编码器.

    编码器会替客户端"规整"文件名: 空名降级成普通字段, 引号与控制字符被抹掉. 于是它写不出的那些
    体, 恰恰是最该测的那些. 体的拼法见 `build_multipart_body`.

    `entry_filename=None` 与空串在**拼出来的那几个字节**里是两件事: 空串仍是"带了 `filename`
    项、只是值为空", Starlette 照样把该分部按**文件**解析; 整个 `filename` 项缺失时, 它才降级成
    **普通字段**, 于是作为 `str` 撞上 `UploadFile` 的注解, 在框架层就被拒——轮不到处理函数.
    """

    body = build_multipart_body(
        {
            "name": (None, STRATEGY_NAME),
            "source": (entry_filename, STRATEGY_SOURCE),
            "configuration": (configuration_filename, build_configuration_text()),
        }
    )

    return await client.post(
        STRATEGIES_PATH,
        content=body,
        headers={
            **bearer_headers(token),
            "Content-Type": f"multipart/form-data; boundary={UPLOAD_BOUNDARY}",
        },
    )


async def create_strategy(client: AsyncClient, token: str) -> StrategyDetailResponse:
    """上传一个策略并解出详情, 供后续步骤复用."""

    response = await post_strategy(client, token)

    assert response.status_code == 201, response.text

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


async def test_both_uploads_land_on_disk_byte_for_byte(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """两份原文都按上传的字节落盘, 平台一个字都不改.

    这是"上传的配置即模板"的前提: 作者照着自己在版本目录里看到的文件核对, 必须与提交页的参数
    区逐键对得上. 任何"顺手格式化一下"都会让这个前提悄悄失效.
    """

    detail = await create_strategy(client, owner_account.token)
    version = detail.versions[0]

    directory = version_directory(
        platform_settings, owner_account.user_id, detail.strategy.id, version.version_no
    )

    assert (directory / ENTRY_FILENAME).read_bytes() == STRATEGY_SOURCE.encode("utf-8")
    assert (directory / CONFIG_FILENAME).read_text(encoding="utf-8") == (
        build_configuration_text()
    )


async def test_the_stored_filenames_are_the_uploaded_part_filenames(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """文件名由**上传方**给定, 平台照抄, 不另起名字.

    这两个名字各有下游: 入口名会成为 job 目录里的裸文件名与 `argv[0]`, 配置名是策略硬编码
    `open()` 的那个名字 (见 `job-workspace.md`). 平台换一个名字, 症状是"跑起来了但读不到配置".
    """

    detail = await create_strategy(client, owner_account.token)

    directory = version_directory(
        platform_settings,
        owner_account.user_id,
        detail.strategy.id,
        detail.versions[0].version_no,
    )

    assert sorted(path.name for path in directory.iterdir()) == sorted(
        [ENTRY_FILENAME, CONFIG_FILENAME]
    )

    assert detail.versions[0].entry_filename == ENTRY_FILENAME
    assert detail.versions[0].config_filename == CONFIG_FILENAME


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

    assert len(
        (await read_strategy_detail(client, owner_account.token, detail.strategy.id)).versions
    ) == 1


async def test_changing_only_the_configuration_mints_a_new_version(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """同一份源码配不同配置模板是一份新版本.

    判重若只看源码, 这条路径要么被判成"内容未变"而吞掉, 要么撞上唯一约束——两者都逼着用户
    "改参数必须连源码一起改".
    """

    detail = await create_strategy(client, owner_account.token)

    changed_configuration = build_configuration_text(
        {**CONFIGURATION_TEMPLATE, "GridStep": 25.0}
    )

    response = await post_strategy_version(
        client,
        owner_account.token,
        detail.strategy.id,
        configuration_text=changed_configuration,
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

    reread = await read_strategy_detail(client, owner_account.token, detail.strategy.id)

    assert reread.strategy.updated_at > detail.strategy.updated_at


async def test_reusing_a_version_does_not_claim_the_strategy_was_updated(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """没有新内容落库, "最近变更"就不该往前动."""

    detail = await create_strategy(client, owner_account.token)

    await post_strategy_version(client, owner_account.token, detail.strategy.id)

    reread = await read_strategy_detail(client, owner_account.token, detail.strategy.id)

    assert reread.strategy.updated_at == detail.strategy.updated_at


async def test_the_stored_configuration_is_the_uploaded_text_verbatim(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """落盘的配置与上传的**逐字节**相同——平台不补默认值、不重排键、不改取值类型.

    提交页的参数区直接由这份文本渲染, 故任何归一化都会让"我上传的 JSON"与"表单上看到的"对不
    上; 而作者核对时看的是前者, 提交的是后者.
    """

    detail = await create_strategy(client, owner_account.token)

    directory = version_directory(
        platform_settings,
        owner_account.user_id,
        detail.strategy.id,
        detail.versions[0].version_no,
    )
    stored_text = (directory / CONFIG_FILENAME).read_text(encoding="utf-8")

    assert stored_text == build_configuration_text()
    assert json.loads(stored_text) == CONFIGURATION_TEMPLATE

    # 逐键相等且**取值类型不变**: `10.0` 不许变成 `10`, `True` 不许变成 `1`.
    stored_template = json.loads(stored_text)

    for key_name, template_value in CONFIGURATION_TEMPLATE.items():
        assert stored_template[key_name] == template_value, key_name
        assert type(stored_template[key_name]) is type(template_value), key_name


async def test_the_detail_endpoint_exposes_the_stored_configuration(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """版本视图里带着配置全文: 提交页要按它的键生成参数控件.

    期望值取**盘上那份**而不是请求体, 两者的一致性由上一条管; 这一条只管"接口把库里的快照原样
    交出去了", 两条各查一件事.
    """

    detail = await create_strategy(client, owner_account.token)
    version = detail.versions[0]

    assert version.configuration_json == build_configuration_text()
    assert json.loads(version.configuration_json) == CONFIGURATION_TEMPLATE


async def test_a_pre_change_version_exposes_a_null_configuration(
    client: AsyncClient,
    database: PlatformDatabase,
    owner_account: SignedInAccount,
) -> None:
    """改形态之前落的版本, 配置是 NULL —— 详情页必须照样出得来.

    这一列若写成非可选, 每一个旧版本的详情都会被响应模型拒掉而变成 500: 用户看到的不是"这个版本
    作废了, 请重新上传", 而是"我的策略打不开了".
    """

    strategy = await create_strategy_record(database, owner_account.user, STRATEGY_NAME)

    await create_strategy_version_record(
        database, strategy, owner_account.user, configuration_json=None
    )

    response = await client.get(
        f"{STRATEGIES_PATH}/{strategy.id}",
        headers=bearer_headers(owner_account.token),
    )

    assert response.status_code == 200, response.text

    exposed_versions = [
        StrategyVersionResponse.model_validate(version_row)
        for version_row in response.json()["versions"]
    ]

    assert [version.configuration_json for version in exposed_versions] == [None]


async def test_template_values_the_form_cannot_render_survive_verbatim(
    client: AsyncClient,
    owner_account: SignedInAccount,
    platform_settings: PlatformSettings,
) -> None:
    """控件渲染不出来的那几类取值 (对象、数组、`null`) 一个不丢, 也不被"顺手"改掉.

    平台不是按声明渲染的, 所以它**没有**一份"这个键我管不了"的名单可查; 于是保证只剩一条:
    整份文本原样留存. 少了这条, 一个带嵌套配置的策略上传后参数区少一块, 而提交出去的配置里
    那个键已经没了——策略那边看到的是"配置缺键", 归因要翻到版本目录才想得明白.
    """

    detail = await create_strategy(client, owner_account.token)

    directory = version_directory(
        platform_settings,
        owner_account.user_id,
        detail.strategy.id,
        detail.versions[0].version_no,
    )
    stored_template = json.loads(
        (directory / CONFIG_FILENAME).read_text(encoding="utf-8")
    )

    assert stored_template["NestedRule"] == {"Levels": [1, 2, 3], "Fallback": None}
    assert stored_template["OptionalWindow"] is None
    assert "OptionalWindow" in stored_template


@pytest.mark.parametrize(
    ("entry_filename", "configuration_filename"),
    [
        (f"nested/directory/entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"..\\entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        ("entry.txt", CONFIG_FILENAME),
        ("  entry.py", CONFIG_FILENAME),
        (".", CONFIG_FILENAME),
        (f"con{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"com1{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"lpt9{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"grid:entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"grid|entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"grid?entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"grid<entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"grid>entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f'grid"entry{ENTRY_FILENAME_SUFFIX}', CONFIG_FILENAME),
        (f"grid*entry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        (f"grid\tentry{ENTRY_FILENAME_SUFFIX}", CONFIG_FILENAME),
        ("x" * (MAXIMUM_FILENAME_LENGTH + 1) + ENTRY_FILENAME_SUFFIX, CONFIG_FILENAME),
        (ENTRY_FILENAME, f"Grid:Config{CONFIGURATION_FILENAME_SUFFIX}"),
        (ENTRY_FILENAME, "GridConfig"),
        (ENTRY_FILENAME, "sub/GridConfig.json"),
        # 引擎与平台都会往作业目录根部写这三个名字; 配置与它们重名会**覆盖掉**其中之一, 不报错.
        (ENTRY_FILENAME, "BackTest.json"),
        (ENTRY_FILENAME, "Sessions.json"),
        (ENTRY_FILENAME, "result.json"),
    ],
)
async def test_a_filename_that_cannot_be_a_bare_argv0_is_rejected(
    client: AsyncClient,
    owner_account: SignedInAccount,
    entry_filename: str,
    configuration_filename: str,
) -> None:
    """带分隔符的 `argv[0]` 会让引擎日志器打不开文件, 宿主在启动期终止; 堵在上传口最省事.

    配置名多两条约束: 后缀必须是 `.json`, 且不得与引擎、平台写进同一个目录的文件重名——重名的
    后果是**静默覆盖**, 谁后写谁赢.

    经 `build_multipart_body` 直发而不是走 `post_strategy`: 引号与控制字符这类取值会被 `httpx`
    的编码器抹掉, 用它反而测不到想测的东西.
    """

    response = await post_raw_upload(
        client,
        owner_account.token,
        entry_filename=entry_filename,
        configuration_filename=configuration_filename,
    )

    assert response.status_code == 400, response.text


async def test_an_upload_part_without_a_filename_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """`filename` 是空的分部照样能发出, 而文件名是**载荷**: 没有它这次上传就不完整.

    兜底在这里而不是在校验函数里: 空名在"路径分隔符/非法字符/后缀"那几条上都查不出问题, 会一路
    走到落盘, 落成一个名字是空串的文件.
    """

    response = await post_raw_upload(
        client,
        owner_account.token,
        entry_filename="",
        configuration_filename=CONFIG_FILENAME,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == MISSING_UPLOAD_FILENAME_MESSAGE


async def test_a_part_that_is_not_a_file_still_gets_a_json_422(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """分部不带 `filename` 时框架回 422, 而那条回执**必须是 JSON**.

    `UploadFile` 的校验器对收到的普通字段抛 `ValueError`, 该异常对象会原样躺进校验错误的 `ctx`;
    处理器直接 `JSONResponse(content=...)` 的话, 序列化在 `json.dumps` 那里抛 `TypeError`, 于是
    用户拿到 500, 且真实原因被一句"服务器内部错误"盖掉.
    """

    response = await post_raw_upload(
        client,
        owner_account.token,
        entry_filename=None,
        configuration_filename=CONFIG_FILENAME,
    )

    assert response.status_code == 422, response.text
    assert "ValueError" not in response.text
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "configuration_text",
    [
        "{not json",
        "[1, 2, 3]",
        '"just a string"',
        "42",
        "null",
        "",
    ],
)
async def test_a_configuration_that_is_not_a_json_object_is_rejected(
    client: AsyncClient,
    owner_account: SignedInAccount,
    configuration_text: str,
) -> None:
    """顶层必须是一个对象.

    数组或标量照样会被原样写进策略配置, 而它在提交页渲染不出任何控件——症状是"参数区是空的,
    提交也能过", 用户对着一个没有参数的表单点提交, 然后收到一句策略抛的 `TypeError`.
    """

    response = await post_strategy(
        client, owner_account.token, configuration_text=configuration_text
    )

    assert response.status_code == 400, response.text


async def test_a_malformed_configuration_is_named_and_located(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy(
        client, owner_account.token, configuration_text='{\n  "GridStep": ,\n}'
    )

    assert response.status_code == 400

    detail = response.json()["detail"]

    assert "策略配置不合法" in detail
    assert "第 2 行" in detail


@pytest.mark.parametrize(
    "configuration_text",
    [
        build_configuration_text({f"Key{index}": index for index in range(MAXIMUM_CONFIGURATION_KEY_COUNT + 1)}),
        build_configuration_text({"K" * (MAXIMUM_CONFIGURATION_KEY_LENGTH + 1): 1}),
        build_configuration_text({"": 1}),
    ],
)
async def test_a_configuration_the_form_cannot_render_is_rejected(
    client: AsyncClient,
    owner_account: SignedInAccount,
    configuration_text: str,
) -> None:
    """形状约束只有三条, 且都与"表单能不能渲染"直接相关: 一个键一个控件, 键名是那格控件的唯一
    标识——键太多、键名太长、键名为空, 都会让参数区渲染不出来或渲染出两块分不清的格子.

    取值范围与类型一律不看: 那是策略自己的事.
    """

    response = await post_strategy(
        client, owner_account.token, configuration_text=configuration_text
    )

    assert response.status_code == 400, response.text


async def test_a_configuration_that_is_not_utf8_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """非 UTF-8 的配置两份读者都读不动, 早一点说清楚好过留一句"策略报告说读不到配置"."""

    response = await client.post(
        STRATEGIES_PATH,
        data={"name": STRATEGY_NAME},
        files={
            "source": (ENTRY_FILENAME, STRATEGY_SOURCE.encode("utf-8"), "text/x-python"),
            "configuration": (
                CONFIG_FILENAME,
                '{"GridStep": 10}'.encode("gbk") + b"\xff\xfe",
                "application/json",
            ),
        },
        headers=bearer_headers(owner_account.token),
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == CONFIGURATION_NOT_UTF8_MESSAGE


async def test_an_oversized_configuration_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """键名受限而取值不受限, 故撑爆配置靠一个长字符串取值即可."""

    oversized_configuration = build_configuration_text(
        {"GridStep": "x" * MAXIMUM_CONFIGURATION_BYTES}
    )

    response = await post_strategy(
        client, owner_account.token, configuration_text=oversized_configuration
    )

    assert response.status_code == 400
    assert response.json()["detail"] == CONFIGURATION_TOO_LARGE_MESSAGE


async def test_an_empty_configuration_is_rejected(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    response = await post_strategy(client, owner_account.token, configuration_text="")

    assert response.status_code == 400
    assert response.json()["detail"] == EMPTY_CONFIGURATION_MESSAGE


@pytest.mark.parametrize(
    "configuration_text",
    [
        f'{{"{VALUE_MARKER}": }}',
        build_configuration_text({VALUE_MARKER * 20: 1}),
    ],
)
async def test_configuration_errors_never_echo_the_configuration_text(
    client: AsyncClient,
    owner_account: SignedInAccount,
    configuration_text: str,
) -> None:
    """报错只回位置与原因: 回显原文等于把整份配置抄进响应体与接入层日志, 而配置里可能写着
    账户号一类的东西.

    两个分支各一例: 解析失败那条只回行列号 (`_parse_json_object` 刻意不带原文), 键名过长那条
    只回一句固定文案 (`_check_configuration_keys` 不把键名放进消息). 两条都会经手响应体, 只
    覆盖一条的话, 另一条上的回显改动不会被发现.
    """

    response = await post_strategy(
        client, owner_account.token, configuration_text=configuration_text
    )

    assert response.status_code == 400, response.text
    assert VALUE_MARKER * 20 not in response.text
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

    两份原文各有一道自己的闸, 它们都在处理函数里: 挡得住内存, 挡不住磁盘——它们执行时整个
    multipart 体早已被解析完, 超出的部分已经落进磁盘临时文件. 外面这道按 `Content-Length` 在
    路由之前回 413.

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


async def test_a_legal_upload_at_both_size_limits_is_accepted(
    client: AsyncClient, owner_account: SignedInAccount
) -> None:
    """体积闸的余量必须装得下**两份原文各自的合法上限**.

    只钉"超限被拒"是不够的: 把余量改小到装不下"源码 1 MB + 配置 64 KB"这个**允许**的组合, 全部
    既有测试照样是绿的, 而用户正常上传会莫名拿到 413. 体积闸有两个方向, 这里钉的是另一头——
    四次变异检查全在"改大/摘掉"那一侧, 恰好漏掉它.

    填充落在**取值**上而不是键名上: 键名有 64 字符的上限, 键数有 200 的上限, 拿它们去凑 64 KB
    会先被配置形状校验拒掉, 那测的就不是体积闸了.
    """

    configuration_padding = MAXIMUM_CONFIGURATION_BYTES - len(
        build_configuration_text({"GridStep": ""}).encode("utf-8")
    )
    sized_configuration = build_configuration_text(
        {"GridStep": "x" * configuration_padding}
    )
    sized_source = b"#" * MAXIMUM_SOURCE_BYTES

    assert len(sized_configuration.encode("utf-8")) == MAXIMUM_CONFIGURATION_BYTES
    assert len(sized_source) + len(sized_configuration.encode("utf-8")) <= (
        MAXIMUM_REQUEST_BODY_BYTES
    )

    response = await post_strategy(
        client,
        owner_account.token,
        configuration_text=sized_configuration,
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
        data={"name": STRATEGY_NAME},
        files={
            "source": (ENTRY_FILENAME, b"pass\n", "text/x-python"),
            "configuration": (
                CONFIG_FILENAME,
                b"{}",
                "application/json",
            ),
        },
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
        await read_strategy_status_code(client, owner_account.token, detail.strategy.id)
        == 404
    )

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
    assert (directory / CONFIG_FILENAME).is_file()


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
