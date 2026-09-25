"""运行列表的筛选、排序与分页.

排序字段经白名单映射到列对象, 请求里的字符串永远不进 SQL; 未收录的取值静默回落到默认列.
因此这里既验白名单内可用, 也验白名单外(含疑似注入载荷)既不报错也不改行为.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel, StrategyModel
from app.catalog.schemas import PageResponse, RunSummaryResponse
from app.clock import utc_now

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


RUNS_PATH = "/api/runs"
MEMBER_USERNAME = "runs-member"
EARLY_BALANCE = 100.5
LATE_BALANCE = 300.25
FAILED_BALANCE = 200.75


@dataclass(frozen=True)
class RunBoard:
    """一个账号下三次取值互不相同的运行, 供筛选与排序断言区分."""

    token: str
    early_success_run: RunModel
    late_success_run: RunModel
    failed_run: RunModel
    first_strategy: StrategyModel
    second_strategy: StrategyModel


@pytest_asyncio.fixture
async def run_board(
    client: AsyncClient, database: PlatformDatabase
) -> RunBoard:
    member = await create_user_record(database, MEMBER_USERNAME)
    first_strategy = await create_strategy_record(database, member, "runs-grid-first")
    second_strategy = await create_strategy_record(database, member, "runs-grid-second")
    first_version = await create_strategy_version_record(
        database, first_strategy, member
    )
    second_version = await create_strategy_version_record(
        database, second_strategy, member
    )

    submitted_base = utc_now()

    early_success_run = await create_run_record(
        database,
        member,
        first_strategy,
        first_version,
        status=RunStatus.SUCCEEDED,
        trade_count=1,
        balance=EARLY_BALANCE,
        submitted_at=submitted_base,
    )
    late_success_run = await create_run_record(
        database,
        member,
        first_strategy,
        first_version,
        status=RunStatus.SUCCEEDED,
        trade_count=3,
        balance=LATE_BALANCE,
        submitted_at=submitted_base + timedelta(minutes=1),
    )
    failed_run = await create_run_record(
        database,
        member,
        second_strategy,
        second_version,
        status=RunStatus.FAILED,
        trade_count=2,
        balance=FAILED_BALANCE,
        submitted_at=submitted_base + timedelta(minutes=2),
    )

    return RunBoard(
        token=await login(client, MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD),
        early_success_run=early_success_run,
        late_success_run=late_success_run,
        failed_run=failed_run,
        first_strategy=first_strategy,
        second_strategy=second_strategy,
    )


async def _fetch_runs(
    client: AsyncClient, token: str, **parameters: object
) -> PageResponse[RunSummaryResponse]:
    return await fetch_page(client, RUNS_PATH, token, RunSummaryResponse, **parameters)


async def test_listing_without_parameters_returns_every_run_newest_first(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(client, run_board.token)

    assert page.total == 3
    assert record_ids(page) == [
        run_board.failed_run.id,
        run_board.late_success_run.id,
        run_board.early_success_run.id,
    ]


async def test_status_filter_narrows_to_matching_runs(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(client, run_board.token, status=RunStatus.SUCCEEDED.value)

    assert page.total == 2
    assert set(record_ids(page)) == {
        run_board.early_success_run.id,
        run_board.late_success_run.id,
    }
    assert {record.status for record in page.records} == {RunStatus.SUCCEEDED}


async def test_strategy_filter_narrows_to_that_strategy(
    client: AsyncClient, run_board: RunBoard
) -> None:
    first_page = await _fetch_runs(
        client, run_board.token, strategy_id=run_board.first_strategy.id
    )
    second_page = await _fetch_runs(
        client, run_board.token, strategy_id=run_board.second_strategy.id
    )

    assert first_page.total == 2
    assert record_ids(first_page) == [
        run_board.late_success_run.id,
        run_board.early_success_run.id,
    ]
    assert second_page.total == 1
    assert record_ids(second_page) == [run_board.failed_run.id]


async def test_status_and_strategy_filters_combine(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(
        client,
        run_board.token,
        status=RunStatus.SUCCEEDED.value,
        strategy_id=run_board.second_strategy.id,
    )

    assert page.total == 0
    assert page.records == []


async def test_sorting_by_balance_descending(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(client, run_board.token, sort_by="balance", descending=True)

    assert record_ids(page) == [
        run_board.late_success_run.id,
        run_board.failed_run.id,
        run_board.early_success_run.id,
    ]


async def test_sorting_by_trade_count_ascending(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(
        client, run_board.token, sort_by="trade_count", descending=False
    )

    assert record_ids(page) == [
        run_board.early_success_run.id,
        run_board.failed_run.id,
        run_board.late_success_run.id,
    ]


async def test_sorting_by_submitted_at_ascending_reverses_the_default(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(
        client, run_board.token, sort_by="submitted_at", descending=False
    )

    assert record_ids(page) == [
        run_board.early_success_run.id,
        run_board.late_success_run.id,
        run_board.failed_run.id,
    ]


@pytest.mark.parametrize(
    "unknown_sort_by",
    [
        "no_such_column",
        "",
        "Id",
        "password_hash",
        "balance; DROP TABLE Runs--",
        "balance DESC, Id",
        "Balance",
    ],
)
async def test_unknown_sort_column_falls_back_to_the_default_order(
    client: AsyncClient, run_board: RunBoard, unknown_sort_by: str
) -> None:
    page = await _fetch_runs(client, run_board.token, sort_by=unknown_sort_by)

    assert record_ids(page) == [
        run_board.failed_run.id,
        run_board.late_success_run.id,
        run_board.early_success_run.id,
    ]


async def test_sorting_does_not_change_the_reported_total(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(
        client, run_board.token, sort_by="balance", descending=False, limit=1
    )

    assert page.total == 3
    assert len(page.records) == 1
    assert record_ids(page) == [run_board.early_success_run.id]


async def test_offset_advances_without_changing_the_total(
    client: AsyncClient, run_board: RunBoard
) -> None:
    first_page = await _fetch_runs(client, run_board.token, offset=0, limit=2)
    second_page = await _fetch_runs(client, run_board.token, offset=2, limit=2)

    assert first_page.total == second_page.total == 3
    assert first_page.offset == 0
    assert second_page.offset == 2
    assert record_ids(first_page) == [
        run_board.failed_run.id,
        run_board.late_success_run.id,
    ]
    assert record_ids(second_page) == [run_board.early_success_run.id]


async def test_offset_past_the_end_yields_an_empty_page(
    client: AsyncClient, run_board: RunBoard
) -> None:
    page = await _fetch_runs(client, run_board.token, offset=99, limit=20)

    assert page.total == 3
    assert page.records == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"status": "no-such-status"},
        {"sort_by": "balance", "descending": "not-a-boolean"},
    ],
)
async def test_invalid_query_parameters_are_rejected(
    client: AsyncClient, run_board: RunBoard, parameters: dict[str, object]
) -> None:
    response = await client.get(
        RUNS_PATH, params=parameters, headers=bearer_headers(run_board.token)
    )

    assert response.status_code == 422


async def test_run_summary_carries_the_baseline_metrics(
    client: AsyncClient, run_board: RunBoard
) -> None:
    """金额按 float64 原样往返, 不被定点化截断——与引擎逐位一致是验收判据."""

    page = await _fetch_runs(client, run_board.token, sort_by="balance", descending=True)

    assert page.records[0].balance == LATE_BALANCE
    assert page.records[0].trade_count == 3


async def test_reading_a_run_detail_exposes_the_full_mirrored_row(
    client: AsyncClient, run_board: RunBoard
) -> None:
    response = await client.get(
        f"{RUNS_PATH}/{run_board.early_success_run.id}",
        headers=bearer_headers(run_board.token),
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["id"] == run_board.early_success_run.id
    assert payload["status"] == RunStatus.SUCCEEDED.value
    assert payload["balance"] == EARLY_BALANCE
    assert payload["workspace_path"] == run_board.early_success_run.workspace_path
    assert "basic_data_loaded" in payload
    assert "commission_zero_rate_key_count" in payload
