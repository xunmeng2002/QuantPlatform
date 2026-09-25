"""测试夹具.

每个测试用独立的临时目录与独立的库文件, 互不干扰, 也不碰仓里真实的 runs/ 与 users/.
客户端经 ASGI 直连并走完整 lifespan, 故建表与播种这两步与线上是同一条路径.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.catalog.database import PlatformDatabase
from app.config import PlatformSettings
from app.main import create_application


TEST_BASE_URL = "http://testserver"
TEST_JWT_SECRET_KEY = "test-only-secret-key-never-used-outside-tests"
TEST_ADMIN_USERNAME = "platform-admin"
TEST_ADMIN_PASSWORD = "admin-table-password"
TEST_ACCESS_TOKEN_EXPIRE_MINUTES = 60
TEST_MAX_CONCURRENT_RUNS = 2
TEST_RUN_TIMEOUT_SECONDS = 60
TEST_HTTP_PORT = 8000


@pytest.fixture
def platform_settings(tmp_path: Path) -> PlatformSettings:
    """指向临时目录的配置."""

    return PlatformSettings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        engine_root=tmp_path / "engine",
        runs_root=tmp_path / "runs",
        user_library_root=tmp_path / "users",
        jwt_secret_key=TEST_JWT_SECRET_KEY,
        access_token_expire_minutes=TEST_ACCESS_TOKEN_EXPIRE_MINUTES,
        max_concurrent_runs=TEST_MAX_CONCURRENT_RUNS,
        run_timeout_seconds=TEST_RUN_TIMEOUT_SECONDS,
        initial_admin_username=TEST_ADMIN_USERNAME,
        initial_admin_password=TEST_ADMIN_PASSWORD,
        http_host="127.0.0.1",
        http_port=TEST_HTTP_PORT,
    )


@pytest_asyncio.fixture
async def application(platform_settings: PlatformSettings) -> AsyncIterator[FastAPI]:
    """已跑完 lifespan 的应用实例."""

    built_application = create_application(platform_settings)

    async with built_application.router.lifespan_context(built_application):
        yield built_application


@pytest_asyncio.fixture
async def client(application: FastAPI) -> AsyncIterator[AsyncClient]:
    """经 ASGI 直连的 HTTP 客户端."""

    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url=TEST_BASE_URL) as http_client:
        yield http_client


@pytest.fixture
def database(application: FastAPI) -> PlatformDatabase:
    """应用持有的数据库门面, 供夹具直接落库造数据."""

    return application.state.database
