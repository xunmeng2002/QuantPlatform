"""租户隔离: P1 的验收点.

要求的判据是"两个账号互相看不到对方的策略与运行; 越权访问返回 404 而非 403". 因此每个
断言都配了一条反证——同一个 id 归属人自己取得到, 另一个账号取不到; 否则"返回 404"也可能
只是因为这条记录压根不存在, 那样测的就不是归属校验了.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest_asyncio
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.models import RunModel, StrategyModel, StrategyVersionModel, UserModel
from app.catalog.schemas import (
    RunDetailResponse,
    RunSummaryResponse,
    StrategyDetailResponse,
    StrategyResponse,
)
from app.catalog.visibility import RUN_NOT_FOUND_MESSAGE, STRATEGY_NOT_FOUND_MESSAGE

from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    bearer_headers,
    create_run_record,
    create_strategy_record,
    create_strategy_version_record,
    create_user_record,
    fetch_page,
    login,
    record_ids,
)


STRATEGIES_PATH = "/api/strategies"
RUNS_PATH = "/api/runs"
FIRST_MEMBER_USERNAME = "tenant-first"
SECOND_MEMBER_USERNAME = "tenant-second"


@dataclass(frozen=True)
class Tenant:
    """一个账号及其全部私有资产, 用于断言彼此不可见."""

    user: UserModel
    strategy: StrategyModel
    version: StrategyVersionModel
    run: RunModel
    token: str


async def _build_tenant(
    client: AsyncClient, database: PlatformDatabase, username: str, strategy_name: str
) -> Tenant:
    user = await create_user_record(database, username)
    strategy = await create_strategy_record(database, user, strategy_name)
    version = await create_strategy_version_record(database, strategy, user)
    run = await create_run_record(database, user, strategy, version)

    return Tenant(
        user=user,
        strategy=strategy,
        version=version,
        run=run,
        token=await login(client, username, DEFAULT_MEMBER_PASSWORD),
    )


@pytest_asyncio.fixture
async def tenants(
    client: AsyncClient, database: PlatformDatabase
) -> tuple[Tenant, Tenant]:
    """两个互不相干的账号, 各有一份策略与一次运行."""

    first = await _build_tenant(client, database, FIRST_MEMBER_USERNAME, "first-grid")
    second = await _build_tenant(client, database, SECOND_MEMBER_USERNAME, "second-grid")

    return first, second


async def _list_strategy_ids(client: AsyncClient, tenant: Tenant) -> list[str]:
    page = await fetch_page(client, STRATEGIES_PATH, tenant.token, StrategyResponse)

    assert page.total == len(page.records)

    return record_ids(page)


async def _list_run_ids(client: AsyncClient, tenant: Tenant) -> list[str]:
    page = await fetch_page(client, RUNS_PATH, tenant.token, RunSummaryResponse)

    assert page.total == len(page.records)

    return record_ids(page)


async def test_each_account_sees_only_its_own_strategies(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, second = tenants

    assert await _list_strategy_ids(client, first) == [first.strategy.id]
    assert await _list_strategy_ids(client, second) == [second.strategy.id]


async def test_each_account_sees_only_its_own_runs(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, second = tenants

    assert await _list_run_ids(client, first) == [first.run.id]
    assert await _list_run_ids(client, second) == [second.run.id]


async def test_owner_reads_own_strategy_and_run(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, _ = tenants

    strategy_response = await client.get(
        f"{STRATEGIES_PATH}/{first.strategy.id}", headers=bearer_headers(first.token)
    )
    run_response = await client.get(
        f"{RUNS_PATH}/{first.run.id}", headers=bearer_headers(first.token)
    )

    assert strategy_response.status_code == 200
    assert run_response.status_code == 200

    detail = StrategyDetailResponse.model_validate(strategy_response.json())

    assert detail.strategy.id == first.strategy.id
    assert RunDetailResponse.model_validate(run_response.json()).id == first.run.id


async def test_reading_another_tenants_strategy_is_not_found_not_forbidden(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, second = tenants

    response = await client.get(
        f"{STRATEGIES_PATH}/{second.strategy.id}", headers=bearer_headers(first.token)
    )

    assert response.status_code == 404
    assert response.json()["detail"] == STRATEGY_NOT_FOUND_MESSAGE


async def test_reading_another_tenants_run_is_not_found_not_forbidden(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, second = tenants

    response = await client.get(
        f"{RUNS_PATH}/{second.run.id}", headers=bearer_headers(first.token)
    )

    assert response.status_code == 404
    assert response.json()["detail"] == RUN_NOT_FOUND_MESSAGE


async def test_another_tenants_strategy_exposes_no_existence_hint(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """不存在的 id 与别家的 id 必须给出一模一样的回应, 否则 404/403 的差别就是存在性探测."""

    first, second = tenants

    foreign_response = await client.get(
        f"{STRATEGIES_PATH}/{second.strategy.id}", headers=bearer_headers(first.token)
    )
    absent_response = await client.get(
        f"{STRATEGIES_PATH}/no-such-strategy-identifier",
        headers=bearer_headers(first.token),
    )

    assert foreign_response.status_code == absent_response.status_code == 404
    assert foreign_response.json() == absent_response.json()


async def test_another_tenants_run_exposes_no_existence_hint(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, second = tenants

    foreign_response = await client.get(
        f"{RUNS_PATH}/{second.run.id}", headers=bearer_headers(first.token)
    )
    absent_response = await client.get(
        f"{RUNS_PATH}/no-such-run-identifier", headers=bearer_headers(first.token)
    )

    assert foreign_response.status_code == absent_response.status_code == 404
    assert foreign_response.json() == absent_response.json()


async def test_another_tenants_run_cannot_be_reached_through_filters(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """按别家的策略 id 筛选自己的运行, 结果必须是空集而不是别家的运行."""

    first, second = tenants

    page = await fetch_page(
        client, RUNS_PATH, first.token, RunSummaryResponse, strategy_id=second.strategy.id
    )

    assert page.total == 0
    assert page.records == []


async def test_both_resources_require_a_token(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, _ = tenants

    strategy_response = await client.get(f"{STRATEGIES_PATH}/{first.strategy.id}")
    run_response = await client.get(f"{RUNS_PATH}/{first.run.id}")

    assert strategy_response.status_code == 401
    assert run_response.status_code == 401


async def test_strategy_detail_of_another_tenant_leaks_no_versions(
    client: AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    first, second = tenants

    response = await client.get(
        f"{STRATEGIES_PATH}/{second.strategy.id}", headers=bearer_headers(first.token)
    )

    assert second.version.id not in response.text
    assert second.run.id not in response.text
