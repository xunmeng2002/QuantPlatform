"""引擎自检端点.

三个条件各自可独立不成立, 且不成立时提交回测只会得到一个难以归因的启动失败, 故这里逐个
制造缺失再断言判据.
"""

from __future__ import annotations

from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import ADMIN_REQUIRED_DETAIL
from app.catalog.database import PlatformDatabase
from app.config import PlatformSettings
from app.main import UNEXPECTED_ERROR_DETAIL
from app.routers.health import EngineHealthResponse
from app.services.engine_probe import (
    ENGINE_RUNTIME_FILENAMES,
    ENGINE_VERSION_FILENAME,
    VERSION_DIGEST_PREFIX,
    interpreter_tag,
    read_engine_version,
)

from .conftest import TEST_BASE_URL, TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME
from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    bearer_headers,
    create_user_record,
    login,
)


HEALTH_PATH = "/api/health"
BINDING_FILENAME_PREFIX = "QuantTrading."
# 后缀取**本平台**解释器允许的那一份, 不写死 `.pyd`: 探针按 `EXTENSION_SUFFIXES` 判文件名, 写死
# 就等于把这些用例绑死在 Windows 上 —— 换到 Linux 跑, 探针找不到夹具造出来的那个文件, 于是
# "引擎齐备 → ready" 之类的正向用例全红, 而红的理由是夹具自己造了一份假引擎.
BINDING_FILENAME = f"{BINDING_FILENAME_PREFIX}{EXTENSION_SUFFIXES[0].removeprefix('.')}"
HEALTH_MEMBER_USERNAME = "health-probing-member"


@pytest_asyncio.fixture
async def health_token(client: AsyncClient) -> str:
    return await login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)


@pytest_asyncio.fixture
async def member_token(
    client: AsyncClient, database: PlatformDatabase
) -> str:
    """建一个普通账号并登录, 用于验证非管理员的拒绝路径."""

    await create_user_record(database, HEALTH_MEMBER_USERNAME)

    return await login(client, HEALTH_MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD)


def _settings(application: FastAPI) -> PlatformSettings:
    return application.state.settings


def _write_binding(engine_root: Path, filename: str) -> str:
    """放一个扩展模块空文件, 并返回其文件名.

    文件名由调用方给全: 默认那个是**本平台**的形态, 而"别的 ABI"与"少了后缀"正是几条反向用例
    要造的东西, 故不在这里替调用方拼后缀.
    """

    engine_root.mkdir(parents=True, exist_ok=True)
    (engine_root / filename).touch()

    return filename


def _write_runtime_libraries(engine_root: Path) -> None:
    engine_root.mkdir(parents=True, exist_ok=True)

    for filename in ENGINE_RUNTIME_FILENAMES:
        (engine_root / filename).touch()


async def _read_health(client: AsyncClient, token: str) -> EngineHealthResponse:
    response = await client.get(HEALTH_PATH, headers=bearer_headers(token))

    assert response.status_code == 200, response.text

    return EngineHealthResponse.model_validate(response.json())


async def test_health_requires_authentication(client: AsyncClient) -> None:
    """端点要报绝对路径与缺失的 DLL 名, 对匿名调用者开放等于泄漏内部布局."""

    response = await client.get(HEALTH_PATH)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_health_is_closed_to_a_signed_in_non_admin(
    client: AsyncClient, member_token: str
) -> None:
    """普通用户能做的动作里没有一项需要这份内部布局清单."""

    response = await client.get(HEALTH_PATH, headers=bearer_headers(member_token))

    assert response.status_code == 403
    assert response.json()["detail"] == ADMIN_REQUIRED_DETAIL
    assert "engine" not in response.text.lower()


async def test_health_reports_ready_when_engine_is_complete(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    settings = _settings(application)

    _write_binding(settings.engine_root, BINDING_FILENAME)
    _write_runtime_libraries(settings.engine_root)

    health = await _read_health(client, health_token)

    assert health.ready is True
    assert health.engine_root_exists is True
    assert health.missing_runtime_filenames == []
    assert health.python_binding_filename is not None
    assert health.runs_root_writable is True


async def test_health_reports_the_engine_version_it_will_launch(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    """"跑的是哪一版"与"能不能跑"是两件事: 前者即使在后者的判据齐备时也未必是人读得懂的版本号."""

    settings = _settings(application)

    _write_binding(settings.engine_root, BINDING_FILENAME)
    _write_runtime_libraries(settings.engine_root)

    health = await _read_health(client, health_token)

    assert health.engine_version == read_engine_version(settings.engine_root)
    assert health.engine_version.startswith(VERSION_DIGEST_PREFIX)


async def test_health_prefers_a_declared_engine_version_over_the_digest(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    """引擎包自己带版本号时以它为准——那才是精确口径, 摘要是盖不住整个包的."""

    settings = _settings(application)

    _write_binding(settings.engine_root, BINDING_FILENAME)
    _write_runtime_libraries(settings.engine_root)
    (settings.engine_root / ENGINE_VERSION_FILENAME).write_text(
        "build-2026.09.26\n", encoding="utf-8"
    )

    health = await _read_health(client, health_token)

    assert health.engine_version == "build-2026.09.26"


async def test_health_reports_an_empty_engine_version_when_there_is_nothing_to_read(
    client: AsyncClient, health_token: str
) -> None:
    """空串是"不知道", 不是错误: 这个端点的其余判据照常给出, 由调用方分辨."""

    health = await _read_health(client, health_token)

    assert health.engine_version == ""
    assert health.ready is False


async def test_health_reports_not_ready_without_the_extension_module(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    settings = _settings(application)

    _write_runtime_libraries(settings.engine_root)

    health = await _read_health(client, health_token)

    assert health.ready is False
    assert health.python_binding_filename is None
    assert health.engine_root_exists is True


@pytest.mark.parametrize("foreign_interpreter_tag", ["cp39", "cp310", "cp312"])
async def test_health_rejects_a_binding_built_for_another_interpreter(
    client: AsyncClient,
    application: FastAPI,
    health_token: str,
    foreign_interpreter_tag: str,
) -> None:
    """扩展模块带的是**本平台本解释器**的 ABI 标签, 别的标签一律不可用; 按文件名判, 不靠 import 试错."""

    settings = _settings(application)

    # 这里刻意造一个 Windows 形态的假模块: 它只是"不是本解释器 ABI"的一个具体样本, 判据是"名字
    # 落不进本平台的 `EXTENSION_SUFFIXES`", 故这条在 Linux 上跑同样成立.
    foreign_binding_name = f"{BINDING_FILENAME_PREFIX}{foreign_interpreter_tag}-win_amd64.pyd"
    _write_binding(settings.engine_root, foreign_binding_name)
    _write_runtime_libraries(settings.engine_root)

    health = await _read_health(client, health_token)

    assert health.ready is False
    assert health.python_binding_filename is None


async def test_health_reports_a_binding_missing_only_its_suffix(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    settings = _settings(application)

    settings.engine_root.mkdir(parents=True, exist_ok=True)
    (settings.engine_root / f"{BINDING_FILENAME_PREFIX}{interpreter_tag()}").touch()

    health = await _read_health(client, health_token)

    assert health.python_binding_filename is None


async def test_health_lists_every_missing_runtime_library(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    settings = _settings(application)

    _write_binding(settings.engine_root, BINDING_FILENAME)
    settings.engine_root.mkdir(parents=True, exist_ok=True)
    (settings.engine_root / ENGINE_RUNTIME_FILENAMES[0]).touch()

    health = await _read_health(client, health_token)

    assert health.ready is False
    assert health.missing_runtime_filenames == list(ENGINE_RUNTIME_FILENAMES[1:])


async def test_health_reports_a_runs_root_that_cannot_be_created(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    """运行根被一个同名文件占位时无法建目录, 该判定必须为假而非抛错."""

    settings = _settings(application)

    _write_binding(settings.engine_root, BINDING_FILENAME)
    _write_runtime_libraries(settings.engine_root)
    settings.runs_root.parent.mkdir(parents=True, exist_ok=True)
    settings.runs_root.touch()

    health = await _read_health(client, health_token)

    assert health.ready is False
    assert health.runs_root_writable is False


async def test_health_probe_creates_the_runs_root(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    """运行根本就该存在, 探针顺带把它建出来."""

    settings = _settings(application)

    assert settings.runs_root.is_dir() is False

    await _read_health(client, health_token)

    assert settings.runs_root.is_dir() is True


async def test_health_reports_the_interpreter_it_ran_under(
    client: AsyncClient, health_token: str
) -> None:
    health = await _read_health(client, health_token)

    assert health.interpreter_tag == interpreter_tag()
    assert health.python_version.startswith(f"{interpreter_tag().removeprefix('cp')[0]}.")


async def test_unexpected_failure_is_reported_without_leaking_internals(
    application: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兜底 500 只回固定文案: 堆栈、路径与库结构一律不出现."""

    token = await login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)

    def _raise_unexpectedly(session: object, user_id: str) -> None:
        raise RuntimeError(r"C:\Gitee\QuantPlatform\backend\data\catalog.db 打不开")

    monkeypatch.setattr("app.auth.dependencies.load_user", _raise_unexpectedly)

    transport = ASGITransport(app=application, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url=TEST_BASE_URL) as probing_client:
        response = await probing_client.get(
            "/api/auth/me", headers=bearer_headers(token)
        )

    assert response.status_code == 500
    assert response.json() == {"detail": UNEXPECTED_ERROR_DETAIL}
    assert "Traceback" not in response.text
    assert "catalog.db" not in response.text
    assert "RuntimeError" not in response.text
