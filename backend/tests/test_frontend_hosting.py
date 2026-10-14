"""发布形态的静态托管: 构建产物在位即同源服务前端, 缺席则退回纯 API.

这一层在开发机上**默认看不见** —— 仓里没有 `frontend/dist` (它由 `npm run build` 产出, 且
已被 .gitignore 覆盖), 故每一条都得自己造一份最小产物出来. 要钉住的多数不是"能返回文件"
(那是 StaticFiles 的事), 而是三种**容易配错却不报错**的情形: 深链接被 404、打错的 API 地址
被回成一页前端、静态资源 404 被回成一页前端.
"""

from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import PlatformSettings
from app.main import create_application


FRONTEND_INDEX_CONTENT = "<!doctype html><html lang=\"zh-CN\"><body>薪火量化</body></html>"
ASSET_CONTENT = "console.log('app');"
ASSET_PATH = "/assets/index-3f2a.js"
UNKNOWN_ROUTE_PATH = "/runs/550210e792c54164ad80da998a93a6b8"
OUTSIDE_FILE_NAME = "outside.txt"
OUTSIDE_FILE_CONTENT = "engine-version.txt 的同级文件"

TEST_BASE_URL = "http://testserver"


@pytest.fixture
def frontend_dist(tmp_path: Path) -> Path:
    """一份最小可用的构建产物: `index.html` + 一个带内容哈希的资源名."""

    dist_root = tmp_path / "frontend-dist"
    (dist_root / "assets").mkdir(parents=True)
    (dist_root / "index.html").write_text(FRONTEND_INDEX_CONTENT, encoding="utf-8")
    (dist_root / ASSET_PATH.removeprefix("/")).write_text(ASSET_CONTENT, encoding="utf-8")

    return dist_root


@pytest_asyncio.fixture
async def frontend_client(platform_settings: PlatformSettings, frontend_dist: Path) -> AsyncClient:
    """挂上构建产物的应用. 只装配、不起 lifespan: 静态托管与路由匹配都不碰库."""

    settings = replace(platform_settings, frontend_dist_root=frontend_dist)

    async with AsyncClient(
        transport=ASGITransport(app=create_application(settings)), base_url=TEST_BASE_URL
    ) as client:
        yield client


async def test_the_root_serves_the_built_index(frontend_client: AsyncClient) -> None:
    response = await frontend_client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == FRONTEND_INDEX_CONTENT


async def test_a_built_asset_is_served_from_disk(frontend_client: AsyncClient) -> None:
    response = await frontend_client.get(ASSET_PATH)

    assert response.status_code == 200
    assert response.text == ASSET_CONTENT


async def test_an_unknown_page_route_falls_back_to_the_index(frontend_client: AsyncClient) -> None:
    """深链接与 F5 靠这条: 前端路由的那串地址在盘上没有对应文件."""

    response = await frontend_client.get(UNKNOWN_ROUTE_PATH)

    assert response.status_code == 200
    assert response.text == FRONTEND_INDEX_CONTENT


async def test_an_unknown_asset_path_is_not_masked_by_the_index(frontend_client: AsyncClient) -> None:
    """带扩展名的路径不回退: 产物不在就该 404, 回一页 HTML 只会让浏览器报 MIME 错."""

    response = await frontend_client.get("/assets/index-deadbeef.js")

    assert response.status_code == 404


async def test_an_unknown_api_path_stays_a_json_404(frontend_client: AsyncClient) -> None:
    """打错的 API 地址不能回成 200 的前端: 调用方会拿一段 HTML 当数据解析."""

    response = await frontend_client.get("/api/not-an-endpoint")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


async def test_api_routes_are_reached_rather_than_the_frontend(frontend_client: AsyncClient) -> None:
    """挂载点在 `/` 上, 它必须排在全部路由之后 —— 否则整个 API 都被吞掉."""

    response = await frontend_client.post("/api/auth/login", json={})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")


async def test_a_file_next_to_the_build_output_is_not_served(
    platform_settings: PlatformSettings, frontend_dist: Path
) -> None:
    """托管的是构建产物那个目录, 不是它的父目录 —— 后者会把邻居也一并暴露出去."""

    (frontend_dist.parent / OUTSIDE_FILE_NAME).write_text(OUTSIDE_FILE_CONTENT, encoding="utf-8")
    settings = replace(platform_settings, frontend_dist_root=frontend_dist)

    async with AsyncClient(
        transport=ASGITransport(app=create_application(settings)), base_url=TEST_BASE_URL
    ) as client:
        response = await client.get(f"/{OUTSIDE_FILE_NAME}")

    assert response.status_code == 404


async def test_a_missing_build_output_leaves_the_application_api_only(
    platform_settings: PlatformSettings, tmp_path: Path
) -> None:
    """开发形态: 仓里没有 dist, 同一个装配函数必须原样退回只提供 API 的应用."""

    settings = replace(platform_settings, frontend_dist_root=tmp_path / "absent-frontend-dist")

    async with AsyncClient(
        transport=ASGITransport(app=create_application(settings)), base_url=TEST_BASE_URL
    ) as client:
        response = await client.get("/")

    assert response.status_code == 404
