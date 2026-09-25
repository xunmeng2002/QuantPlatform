"""引擎自检端点.

三个条件各自可独立不成立, 且不成立时提交回测只会得到一个难以归因的启动失败, 故这里逐个
制造缺失再断言判据.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import ADMIN_REQUIRED_DETAIL
from app.catalog.database import PlatformDatabase
from app.config import PlatformSettings
from app.main import UNEXPECTED_ERROR_DETAIL
from app.routers.health import (
    ENGINE_RUNTIME_FILENAMES,
    EngineHealthResponse,
    _interpreter_tag,
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
BINDING_FILENAME_SUFFIX = ".pyd"
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


def _write_binding(engine_root: Path, interpreter_tag: str) -> str:
    """放一个 ABI 标签匹配的扩展模块空文件, 并返回其文件名."""

    filename = f"{BINDING_FILENAME_PREFIX}{interpreter_tag}-win_amd64{BINDING_FILENAME_SUFFIX}"
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

    _write_binding(settings.engine_root, _interpreter_tag())
    _write_runtime_libraries(settings.engine_root)

    health = await _read_health(client, health_token)

    assert health.ready is True
    assert health.engine_root_exists is True
    assert health.missing_runtime_filenames == []
    assert health.python_binding_filename is not None
    assert health.runs_root_writable is True


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
    """扩展模块是 cp311-win_amd64, 解释器小版本不匹配即不可用; 按 ABI 标签判, 不靠 import 试错."""

    settings = _settings(application)

    _write_binding(settings.engine_root, foreign_interpreter_tag)
    _write_runtime_libraries(settings.engine_root)

    health = await _read_health(client, health_token)

    assert health.ready is False
    assert health.python_binding_filename is None


async def test_health_reports_a_binding_missing_only_its_suffix(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    settings = _settings(application)

    settings.engine_root.mkdir(parents=True, exist_ok=True)
    (settings.engine_root / f"{BINDING_FILENAME_PREFIX}{_interpreter_tag()}").touch()

    health = await _read_health(client, health_token)

    assert health.python_binding_filename is None


async def test_health_lists_every_missing_runtime_library(
    client: AsyncClient, application: FastAPI, health_token: str
) -> None:
    settings = _settings(application)

    _write_binding(settings.engine_root, _interpreter_tag())
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

    _write_binding(settings.engine_root, _interpreter_tag())
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

    assert health.interpreter_tag == _interpreter_tag()
    assert health.python_version.startswith(f"{_interpreter_tag().removeprefix('cp')[0]}.")


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
