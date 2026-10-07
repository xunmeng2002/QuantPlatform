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

from .helpers import SignedInAccount, create_signed_in_account
from .quote_hub_stub import build_covered_component


TEST_BASE_URL = "http://testserver"
TEST_JWT_SECRET_KEY = "test-only-secret-key-never-used-outside-tests"
TEST_ADMIN_USERNAME = "platform-admin"
TEST_ADMIN_PASSWORD = "admin-table-password"
TEST_ACCESS_TOKEN_EXPIRE_MINUTES = 60
TEST_MAX_CONCURRENT_RUNS = 2
TEST_RUN_TIMEOUT_SECONDS = 60
TEST_HTTP_PORT = 8000
TEST_SESSION_FILE_CONTENT = '{"Sessions": []}'
TEST_MARKET_DATA_SUBDIRECTORY = "Bar"

RUN_OWNER_USERNAME = "run-owner"


@pytest.fixture
def engine_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """两个引擎侧输入的真实落位: 行情根 (目录) 与会话表 (文件).

    会话表**必须真的落一个文件**: 提交侧在 `session_file_path` 缺失时直接 400, 不建它的话
    每一条提交用例都会死在"会话表文件不存在"上, 而与它想测的东西毫无关系.

    **种子库不在这里**: 它按轮生成、落在各轮自己的作业目录里 (见 `reference_data.seed_database`),
    盘上没有哪个固定的全局文件可以预先摆好 —— 要动它的用例得自己造一份, 见
    `test_seed_database.py`.

    行情根只建到 `Bar/` 一层: 引擎自己往下拼 `Identity=*/Year=*/*.parquet`, 而桩策略根本不
    读行情——桩读的是平台渲染的配置, 这条正是 §7.1 要测的等价性.
    """

    market_data_root = tmp_path / "market-data"
    session_file_path = tmp_path / "engine" / "Sessions.json"

    (market_data_root / TEST_MARKET_DATA_SUBDIRECTORY).mkdir(parents=True)
    session_file_path.parent.mkdir(parents=True, exist_ok=True)
    session_file_path.write_text(TEST_SESSION_FILE_CONTENT, encoding="utf-8")

    return market_data_root, session_file_path


@pytest.fixture
def quote_hub_root(tmp_path: Path) -> Path:
    """一个**替身**行情组件, 且"要跑的合约早已落地".

    必须钉死: `quote_hub_root` 的默认值是仓外的 `D:/Gitee/QuoteHub`, 而调度器用例提交的正是
    该组件库里真有的那一只合约与区间 —— 不钉的话, 每次跑测试都会去读那个 40 MB 的真库, 等于
    让测试依赖另一个仓的当前内容.
    """

    return build_covered_component(tmp_path / "quote-hub")


@pytest.fixture
def platform_settings(
    tmp_path: Path,
    engine_inputs: tuple[Path, Path],
    quote_hub_root: Path,
) -> PlatformSettings:
    """指向临时目录的配置."""

    market_data_root, session_file_path = engine_inputs

    return PlatformSettings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        engine_root=session_file_path.parent,
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
        market_data_root=market_data_root,
        session_file_path=session_file_path,
        quote_hub_root=quote_hub_root,
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


@pytest_asyncio.fixture
async def run_owner(
    database: PlatformDatabase, client: AsyncClient
) -> SignedInAccount:
    """一个已登录账号, 用来提交回测.

    放在 conftest 而不是各运行测试模块里: 提交、取消、恢复、并发四个模块都要它, 各抄一份的话
    改一处口令或用户名就得改四处, 而漏改的那一处只会在登录断言上以 401 的面目出现.
    """

    return await create_signed_in_account(database, client, RUN_OWNER_USERNAME)
