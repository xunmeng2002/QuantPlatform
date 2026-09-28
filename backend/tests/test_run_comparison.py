"""多轮对比: 一次取多轮的镜像列与各自的权益曲线.

四条性质决定了用例的形态.

一是**指标来自镜像列, 不来自引擎结果文件**: 用例把指标直接落在行上, 曲线则建一个**真的**
结果库 (查询要真执行, 同 `test_run_results`). 这条不是风格问题——保留清理会移走旧轮的作业目录,
那时"历史轮的指标还在"必须成立, 而只有镜像列做到了这一点.

二是**列序 = 请求序**: `IN (...)` 的返回序是未定义的, 不专门定死的话界面上的列序会在驱动之间
随机; 故有一条用例把请求序倒过来发, 逐列断言 id.

三是**失败粒度分两层**: 归属与不存在**整请求 404** (越权降级成"少一列"就是可分辨信号), 而
"这一轮没有曲线"只降**那一列**, 指标照常出.

四是**同参不同 `GridStep` 两轮并列**就是本批的验收句, 故它是第一条用例, 且断言的是"两列、
指标互异、覆盖区间不同"三件事一起成立.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel, StrategyModel, StrategyVersionModel
from app.catalog.schemas import RunComparisonResponse, RunSummaryResponse
from app.catalog.visibility import RUN_NOT_FOUND_MESSAGE
from app.config import PlatformSettings
from app.routers.runs import (
    COMPARISON_EQUITY_FILE_MISSING_MESSAGE,
    COMPARISON_EQUITY_UNREADABLE_MESSAGE,
    COMPARISON_IDS_REQUIRED_MESSAGE,
    COMPARISON_TOO_MANY_RUNS_MESSAGE,
    MAXIMUM_COMPARISON_RUNS,
    RESULT_DATABASE_MISSING_MESSAGE,
    RESULT_NOT_READY_MESSAGE,
)

from .helpers import (
    SignedInAccount,
    bearer_headers,
    create_run_record,
    create_signed_in_account,
    create_strategy_record,
    create_strategy_version_record,
    update_record_by_id,
)
from .run_helpers import job_directory, open_result_database_for_writing


RUNS_PATH = "/api/runs"
COMPARE_PATH = f"{RUNS_PATH}/compare"

RUN_OWNER_USERNAME = "comparison-owner"
OTHER_MEMBER_USERNAME = "comparison-outsider"
STRATEGY_NAME = "comparison-grid"

DATABASE_FILENAME_TEMPLATE = "BackTest_{run_id}.db"
DATABASE_RELATIVE_PATH_TEMPLATE = "./BackTest_{run_id}.db"
ACCOUNT_ID = "A0000000001"

GRID_STEP_PARAMETER_KEY = "GridStep"
FINE_GRID_STEP = 0.01
COARSE_GRID_STEP = 0.05

# 两轮**只差** `GridStep`: 其余取值逐字相同, 于是"参数差在哪"在用例里是可判的.
SHARED_PARAMETERS: dict[str, object] = {"VolumeMultiple": 2, "TradeVolume": 100}

# 两轮的指标必须互异, 否则"并列"看上去与"同一轮画了两遍"一样.
FINE_GRID_METRICS: dict[str, object] = {
    "trade_count": 84,
    "order_count": 92,
    "balance": 1002500.5,
}
COARSE_GRID_METRICS: dict[str, object] = {
    "trade_count": 41,
    "order_count": 45,
    "balance": 998100.25,
}

# 覆盖区间**不同**: 并集因此比任何一条都长, 界面上"标签取并集"才有可辨的落点.
FINE_CAPITAL_ROWS: tuple[tuple[str, float, float], ...] = (
    ("20240101", 1000000.0, 1000000.0),
    ("20240102", 1004000.0, 1003000.0),
    ("20240103", 1002500.5, 1001500.0),
)
COARSE_CAPITAL_ROWS: tuple[tuple[str, float, float], ...] = (
    ("20240102", 1000000.0, 1000000.0),
    ("20240103", 998100.25, 997000.0),
    ("20240104", 999900.0, 999000.0),
)

# 外人那一轮的曲线取一组**在别处不出现**的数: 路径守卫若少一步, 它会被读进别人的响应里, 而那种
# 泄漏只能靠取值本身认出来 (状态码与列数都是对的).
OUTSIDER_CAPITAL_ROWS: tuple[tuple[str, float, float], ...] = (
    ("20240101", 777777.0, 777000.0),
    ("20240102", 888888.0, 888000.0),
)
OUTSIDER_BALANCE_TOKEN = "888888"

# 一个路径合法、但内容不是结果库的文件 (手工放错、或写到一半断电).
UNREADABLE_DATABASE_CONTENT = "这不是一份 SQLite 文件"

UNKNOWN_RUN_ID = "no-such-run-identifier"


@dataclass(frozen=True)
class ComparisonTenant:
    """一个成员、一个外人, 与两人共用的一份策略版本."""

    member: SignedInAccount
    outsider: SignedInAccount
    strategy: StrategyModel
    version: StrategyVersionModel


@dataclass(frozen=True)
class ComparisonBoard(ComparisonTenant):
    """上面那套租户数据, 加上夹具预先造好的三轮.

    继承而不是内嵌一层: 各用例要的是 `board.member.token` 这样的直读, 嵌一层会让每个用例都写
    一遍 `.tenant`. 造轮只用到租户那部分, 故 `create_compared_run` 的形参类型就声明成租户,
    `board` 照传不误.

    外人那一轮必须**真的存在且可读**: 越权与"这个 id 根本不存在"若指向同一份空数据, 整请求
    404 就分不清是哪一条生效了.
    """

    fine_run: RunModel
    coarse_run: RunModel
    outsider_run: RunModel


def parameters_with_grid_step(grid_step: float) -> str:
    """一份参数文本: 与另一轮**只差** `GridStep`."""

    return json.dumps(
        {**SHARED_PARAMETERS, GRID_STEP_PARAMETER_KEY: grid_step}, sort_keys=True
    )


def result_database_file(settings: PlatformSettings, run_id: str) -> Path:
    """一轮结果库应落的位置."""

    return job_directory(settings, run_id) / DATABASE_FILENAME_TEMPLATE.format(
        run_id=run_id
    )


def write_capital_database(
    settings: PlatformSettings,
    run_id: str,
    capital_rows: Sequence[tuple[str, float, float]],
) -> Path:
    """建一个只含 `Capital` 表的结果库, 列与引擎一致 (列是子集).

    查询要真的执行, 故这里必须是**真的** SQLite 文件: 拿一份占位文本当库, 那条用例测到的会
    是"读不出来"而不是"读出来了什么".
    """

    database_file = result_database_file(settings, run_id)
    database_file.parent.mkdir(parents=True, exist_ok=True)

    with open_result_database_for_writing(database_file) as connection:
        connection.execute(
            'CREATE TABLE "Capital"("TradingDay" char(16), "AccountId" char(32),'
            ' "Balance" double, "PreBalance" double, "Available" double)'
        )
        connection.executemany(
            'INSERT INTO "Capital" VALUES (?, ?, ?, ?, ?)',
            [
                (trading_day, ACCOUNT_ID, balance, balance, available)
                for trading_day, balance, available in capital_rows
            ],
        )

    return database_file


def expected_equity_points(
    capital_rows: Sequence[tuple[str, float, float]],
) -> list[dict[str, object]]:
    """由夹具里的 `Capital` 行派生应有的三个字段 (不另抄一份)."""

    return [
        {"trading_day": trading_day, "balance": balance, "available": available}
        for trading_day, balance, available in capital_rows
    ]


async def create_compared_run(
    database: PlatformDatabase,
    settings: PlatformSettings,
    tenant: ComparisonTenant,
    grid_step: float,
    metrics: dict[str, object],
    capital_rows: Sequence[tuple[str, float, float]] | None = None,
    status: RunStatus = RunStatus.SUCCEEDED,
    owner: SignedInAccount | None = None,
) -> RunModel:
    """造一轮参与对比的运行.

    `capital_rows` 为 `None` 时**不写结果库也不填 `DbPath`**——那一格 (还没收尾、或策略在早期就
    失败了) 正是"没有结果库"那条降级路径, 而它不是靠手工清文件造出来的.
    """

    submitting_account = owner if owner is not None else tenant.member
    run = await create_run_record(
        database,
        submitting_account.user,
        tenant.strategy,
        tenant.version,
        status=status,
        params_json=parameters_with_grid_step(grid_step),
        **metrics,
    )

    if capital_rows is not None:
        write_capital_database(settings, run.id, capital_rows)

        await update_record_by_id(
            database,
            RunModel,
            run.id,
            lambda compared_run: setattr(
                compared_run,
                "db_path",
                DATABASE_RELATIVE_PATH_TEMPLATE.format(run_id=compared_run.id),
            ),
        )

    return run


@pytest_asyncio.fixture
async def comparison_board(
    client: AsyncClient,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> ComparisonBoard:
    member = await create_signed_in_account(database, client, RUN_OWNER_USERNAME)
    outsider = await create_signed_in_account(database, client, OTHER_MEMBER_USERNAME)
    strategy = await create_strategy_record(database, member.user, STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, member.user)

    tenant = ComparisonTenant(
        member=member, outsider=outsider, strategy=strategy, version=version
    )

    fine_run = await create_compared_run(
        database,
        platform_settings,
        tenant,
        FINE_GRID_STEP,
        FINE_GRID_METRICS,
        FINE_CAPITAL_ROWS,
    )
    coarse_run = await create_compared_run(
        database,
        platform_settings,
        tenant,
        COARSE_GRID_STEP,
        COARSE_GRID_METRICS,
        COARSE_CAPITAL_ROWS,
    )
    outsider_run = await create_compared_run(
        database,
        platform_settings,
        tenant,
        FINE_GRID_STEP,
        FINE_GRID_METRICS,
        OUTSIDER_CAPITAL_ROWS,
        owner=outsider,
    )

    return ComparisonBoard(
        member=member,
        outsider=outsider,
        strategy=strategy,
        version=version,
        fine_run=fine_run,
        coarse_run=coarse_run,
        outsider_run=outsider_run,
    )


async def request_comparison(
    client: AsyncClient, token: str, comparison_run_ids: Sequence[str]
) -> Response:
    """请求一次对比, 原样返回响应."""

    return await client.get(
        COMPARE_PATH,
        params={"ids": ",".join(comparison_run_ids)},
        headers=bearer_headers(token),
    )


async def read_comparison(
    client: AsyncClient, token: str, comparison_run_ids: Sequence[str]
) -> RunComparisonResponse:
    """请求一次对比, 断言 200 再解出响应体."""

    response = await request_comparison(client, token, comparison_run_ids)

    assert response.status_code == 200, response.text

    return RunComparisonResponse.model_validate(response.json())


def returned_run_ids(comparison: RunComparisonResponse) -> list[str]:
    """响应里各列的运行号, 保持响应序."""

    return [entry.summary.id for entry in comparison.runs]


async def test_two_runs_that_differ_only_in_one_parameter_are_side_by_side(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """**验收句**: 同策略同参、只差 `GridStep` 的两轮并列, 指标互异且各带各的曲线.

    三件事一起断言才构成"并列"这件事: 两列 (而不是一轮画两遍)、指标确实不同 (而不是两块一样的
    数字)、覆盖区间不同 (并集因此是**真**并集, 界面上"缺的那天留空"才有对象).

    参数差在哪也从这一列读出来: `params_json` 是这次加宽的唯一理由——两轮的指标不同却看不出
    因为什么, 对比页就没答上来"差在哪".
    """

    comparison = await read_comparison(
        client,
        comparison_board.member.token,
        [comparison_board.fine_run.id, comparison_board.coarse_run.id],
    )

    assert returned_run_ids(comparison) == [
        comparison_board.fine_run.id,
        comparison_board.coarse_run.id,
    ]

    fine_entry, coarse_entry = comparison.runs

    fine_parameters = json.loads(fine_entry.summary.params_json)
    coarse_parameters = json.loads(coarse_entry.summary.params_json)

    assert fine_parameters[GRID_STEP_PARAMETER_KEY] == FINE_GRID_STEP
    assert coarse_parameters[GRID_STEP_PARAMETER_KEY] == COARSE_GRID_STEP
    # 除 `GridStep` 外逐键相同: "同参"这句话由这条断言顶着.
    assert {
        key: value for key, value in fine_parameters.items() if key != GRID_STEP_PARAMETER_KEY
    } == {
        key: value for key, value in coarse_parameters.items() if key != GRID_STEP_PARAMETER_KEY
    }

    assert (
        fine_entry.summary.trade_count,
        fine_entry.summary.order_count,
        fine_entry.summary.balance,
    ) == (
        FINE_GRID_METRICS["trade_count"],
        FINE_GRID_METRICS["order_count"],
        FINE_GRID_METRICS["balance"],
    )
    assert coarse_entry.summary.trade_count != fine_entry.summary.trade_count
    assert coarse_entry.summary.balance != fine_entry.summary.balance

    assert [point.model_dump() for point in fine_entry.equity_points] == (
        expected_equity_points(FINE_CAPITAL_ROWS)
    )
    assert [point.model_dump() for point in coarse_entry.equity_points] == (
        expected_equity_points(COARSE_CAPITAL_ROWS)
    )
    assert fine_entry.equity_unavailable_reason is None
    assert coarse_entry.equity_unavailable_reason is None

    fine_trading_days = {point.trading_day for point in fine_entry.equity_points}
    coarse_trading_days = {point.trading_day for point in coarse_entry.equity_points}

    assert fine_trading_days != coarse_trading_days
    assert len(fine_trading_days | coarse_trading_days) > max(
        len(fine_trading_days), len(coarse_trading_days)
    )


async def test_the_columns_follow_the_request_order(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """列的次序就是请求里那个次序, 不是库返回的次序.

    **同一对运行号按两种次序各请求一次**, 两次都要等于各自的请求序. 只发一次是不行的: `IN (...)`
    的返回序未定义 (SQLite 按主键索引取件, 而运行号是随机串), 于是"库序恰好等于请求序"有一半的
    机会成立——那样的用例只能偶然抓住一个按库序返回的实现. 两次一起断言则必然抓住: 库序对同一
    组行是固定的, 它不可能同时等于两个相反的请求序.
    """

    ascending_ids = [
        comparison_board.fine_run.id,
        comparison_board.coarse_run.id,
    ]
    descending_ids = list(reversed(ascending_ids))

    ascending_comparison = await read_comparison(
        client, comparison_board.member.token, ascending_ids
    )
    descending_comparison = await read_comparison(
        client, comparison_board.member.token, descending_ids
    )

    assert returned_run_ids(ascending_comparison) == ascending_ids
    assert returned_run_ids(descending_comparison) == descending_ids


async def test_a_repeated_id_yields_one_column(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """重复的运行号只出一列: 库里那一行只有一份, 出两列会让响应与请求对不上.

    同一轮数满 7 次仍然放行, 因为**上限按去重之后判**: 实际要画的是 1 列, 没有理由拒.
    """

    comparison = await read_comparison(
        client,
        comparison_board.member.token,
        [comparison_board.fine_run.id] * (MAXIMUM_COMPARISON_RUNS + 1),
    )

    assert returned_run_ids(comparison) == [comparison_board.fine_run.id]


async def test_more_than_the_maximum_number_of_runs_is_rejected(
    client: AsyncClient,
    comparison_board: ComparisonBoard,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """超过上限的**去重后**轮数一律 400, 且什么都不读.

    去重与否在这里是可分的: 造 `MAXIMUM_COMPARISON_RUNS + 1` 个**互不相同**的轮.
    """

    extra_run_ids = [
        (
            await create_compared_run(
                database,
                platform_settings,
                comparison_board,
                FINE_GRID_STEP,
                FINE_GRID_METRICS,
                FINE_CAPITAL_ROWS,
            )
        ).id
        for _ in range(MAXIMUM_COMPARISON_RUNS + 1)
    ]

    response = await request_comparison(
        client, comparison_board.member.token, extra_run_ids
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == COMPARISON_TOO_MANY_RUNS_MESSAGE


@pytest.mark.parametrize("raw_ids", ["", "   ", ",", ", ,", " , "])
async def test_an_empty_id_list_is_rejected(
    client: AsyncClient, comparison_board: ComparisonBoard, raw_ids: str
) -> None:
    """一个运行号都没给 (空串、只有分隔符、只有空白) 一律 400.

    切成空列表后若继续走下去, `IN ()` 会退化成"什么都不匹配", 而整请求 404 那句文案会把一次
    用法错误说成"运行不存在".
    """

    response = await client.get(
        COMPARE_PATH,
        params={"ids": raw_ids},
        headers=bearer_headers(comparison_board.member.token),
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == COMPARISON_IDS_REQUIRED_MESSAGE


async def test_the_ids_parameter_is_required(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """连 `ids` 都不带是 422 (契约里它必填), 不会走到那句 400 的文案上."""

    response = await client.get(
        COMPARE_PATH, headers=bearer_headers(comparison_board.member.token)
    )

    assert response.status_code == 422


async def test_another_members_run_makes_the_whole_request_not_found(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """混进别人的轮 → **整请求 404**, 响应里一列都没有.

    降级成"少一列"是不行的: 越权与不存在一旦留下形状上的差别, 拿一组 id 去试就能问出"这个运行
    号存不存在"——而多租户要求这两者逐字不可分辨.
    """

    response = await request_comparison(
        client,
        comparison_board.member.token,
        [comparison_board.fine_run.id, comparison_board.outsider_run.id],
    )

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": RUN_NOT_FOUND_MESSAGE}
    # 归一的那一列也不能漏出来: 只断言状态码的话, 一个"先组装再校验"的实现照样能过.
    assert comparison_board.fine_run.id not in response.text
    assert comparison_board.outsider_run.id not in response.text


async def test_an_unknown_run_id_is_indistinguishable_from_another_members(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """不认识这个运行号与"这是他提交的轮"逐字相同: 同状态码、同响应体."""

    hidden = await request_comparison(
        client,
        comparison_board.member.token,
        [comparison_board.fine_run.id, comparison_board.outsider_run.id],
    )
    unknown = await request_comparison(
        client, comparison_board.member.token, [comparison_board.fine_run.id, UNKNOWN_RUN_ID]
    )

    assert unknown.status_code == hidden.status_code
    assert unknown.text == hidden.text


def assert_only_the_curve_is_missing(
    comparison: RunComparisonResponse,
    expected_run_id: str,
    expected_reason: str,
) -> None:
    """第一列的曲线降级、它的指标与第二列**整个**完好.

    降级那一列恒为本模块的细网格那一份 (各降级用例造它时用的就是 `FINE_GRID_METRICS`), 第二列
    恒为夹具里的粗网格那一轮. 两个前提都由调用处摆好, 这里只断言结论.
    """

    degraded_entry, intact_entry = comparison.runs
    degraded_summary = degraded_entry.summary

    assert degraded_summary.id == expected_run_id
    assert degraded_entry.equity_points == []
    assert degraded_entry.equity_unavailable_reason == expected_reason

    # 指标一律来自镜像列, 与曲线读不读得出来无关——这正是保留清理之后历史轮还能对比的前提.
    assert degraded_summary.trade_count == FINE_GRID_METRICS["trade_count"]
    assert degraded_summary.order_count == FINE_GRID_METRICS["order_count"]
    assert degraded_summary.balance == FINE_GRID_METRICS["balance"]
    assert degraded_summary.params_json == parameters_with_grid_step(FINE_GRID_STEP)

    assert intact_entry.equity_unavailable_reason is None
    assert [point.model_dump() for point in intact_entry.equity_points] == (
        expected_equity_points(COARSE_CAPITAL_ROWS)
    )


async def test_an_unfinished_run_keeps_its_metrics_and_loses_only_its_curve(
    client: AsyncClient,
    comparison_board: ComparisonBoard,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """还没结束的轮进得来, 指标照出, 只是没有曲线.

    这一行直接落库 (`status='running'`): 调度器只认领 `queued`, 故它不会自己往下走, 断言不依赖
    任何时序. 引擎此刻正握着结果库, 边写边读会拿到半份数据——"还没有曲线"是**正确**的回答,
    不是缺失.
    """

    unfinished_run = await create_compared_run(
        database,
        platform_settings,
        comparison_board,
        FINE_GRID_STEP,
        FINE_GRID_METRICS,
        status=RunStatus.RUNNING,
    )

    comparison = await read_comparison(
        client,
        comparison_board.member.token,
        [unfinished_run.id, comparison_board.coarse_run.id],
    )

    assert_only_the_curve_is_missing(
        comparison, unfinished_run.id, RESULT_NOT_READY_MESSAGE
    )


async def test_a_run_without_a_result_database_loses_only_its_curve(
    client: AsyncClient,
    comparison_board: ComparisonBoard,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """`DbPath` 是空串的轮 (`result.json` 没写成功) 同样只降曲线.

    空串拼出来的路径**就是作业目录本身**, 那种"打开"会以另一种面目失败, 故它单列一档文案.
    """

    directoryless_run = await create_compared_run(
        database,
        platform_settings,
        comparison_board,
        FINE_GRID_STEP,
        FINE_GRID_METRICS,
    )

    comparison = await read_comparison(
        client,
        comparison_board.member.token,
        [directoryless_run.id, comparison_board.coarse_run.id],
    )

    assert_only_the_curve_is_missing(
        comparison, directoryless_run.id, RESULT_DATABASE_MISSING_MESSAGE
    )


async def test_a_deleted_result_database_loses_only_its_curve(
    client: AsyncClient,
    comparison_board: ComparisonBoard,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """结果库文件被移走 (手工清理、或保留策略走过) 时, 指标一个都不少.

    这一格就是"指标取镜像列"这个决定的判据: 换成"对比时重读 `result.json`"的实现, 这里会以
    "指标全空"的面目转红.
    """

    cleaned_run = await create_compared_run(
        database,
        platform_settings,
        comparison_board,
        FINE_GRID_STEP,
        FINE_GRID_METRICS,
        FINE_CAPITAL_ROWS,
    )

    result_database_file(platform_settings, cleaned_run.id).unlink()

    comparison = await read_comparison(
        client,
        comparison_board.member.token,
        [cleaned_run.id, comparison_board.coarse_run.id],
    )

    assert_only_the_curve_is_missing(
        comparison, cleaned_run.id, COMPARISON_EQUITY_FILE_MISSING_MESSAGE
    )


async def test_an_unreadable_result_database_loses_only_its_curve(
    client: AsyncClient,
    comparison_board: ComparisonBoard,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """文件在、但读不出来时也只降这一列: 一次坏库不该让整页 500.

    与单轮权益端点的差别正在这里: 那边读不出来即 404/409, 这边是"整页少一条线", 代价差着一个
    数量级.
    """

    broken_run = await create_compared_run(
        database,
        platform_settings,
        comparison_board,
        FINE_GRID_STEP,
        FINE_GRID_METRICS,
        FINE_CAPITAL_ROWS,
    )

    result_database_file(platform_settings, broken_run.id).write_text(
        UNREADABLE_DATABASE_CONTENT, encoding="utf-8"
    )

    comparison = await read_comparison(
        client,
        comparison_board.member.token,
        [broken_run.id, comparison_board.coarse_run.id],
    )

    assert_only_the_curve_is_missing(
        comparison, broken_run.id, COMPARISON_EQUITY_UNREADABLE_MESSAGE
    )


async def test_a_database_path_outside_the_job_directory_loses_only_its_curve(
    client: AsyncClient,
    comparison_board: ComparisonBoard,
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """`DbPath` 指向作业目录之外时, 那条曲线一律当"文件已不在", 且**别人的数一个都不出来**.

    这不是假想: `DbPath` 由引擎写进 `result.json`, 而策略是任意 Python 且与引擎同进程, 它完全
    可以把这个值写成 `../<别人的 RunId>/BackTest_....db`. 探针指向一个**真实存在且可读**的结果
    库, 守卫若少一步, 这里读到的就是别人的权益——而那与"文件不在"在响应形状上完全一样.
    """

    escaping_run = await create_compared_run(
        database,
        platform_settings,
        comparison_board,
        FINE_GRID_STEP,
        FINE_GRID_METRICS,
    )

    await update_record_by_id(
        database,
        RunModel,
        escaping_run.id,
        lambda run: setattr(
            run,
            "db_path",
            f"../{comparison_board.outsider_run.id}/"
            f"{DATABASE_FILENAME_TEMPLATE.format(run_id=comparison_board.outsider_run.id)}",
        ),
    )

    response = await request_comparison(
        client,
        comparison_board.member.token,
        [escaping_run.id, comparison_board.coarse_run.id],
    )

    assert response.status_code == 200, response.text
    assert OUTSIDER_BALANCE_TOKEN not in response.text

    assert_only_the_curve_is_missing(
        RunComparisonResponse.model_validate(response.json()),
        escaping_run.id,
        COMPARISON_EQUITY_FILE_MISSING_MESSAGE,
    )


async def test_the_summary_matches_the_list_view_column_for_column(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """对比里的 `summary` 与列表里那一行**逐字段相同** (含这次新加的 `params_json`).

    加宽是纯增量这件事要一条断言来顶: 两处若各有一份字段清单, 将来加一列只会加在一边, 而那种
    偏差在界面上表现为"列表里有、对比页没有".
    """

    comparison = await read_comparison(
        client, comparison_board.member.token, [comparison_board.fine_run.id]
    )

    list_response = await client.get(
        RUNS_PATH,
        params={"strategy_id": comparison_board.strategy.id},
        headers=bearer_headers(comparison_board.member.token),
    )

    assert list_response.status_code == 200, list_response.text

    listed_records = [
        RunSummaryResponse.model_validate(record)
        for record in list_response.json()["records"]
    ]
    listed_summary = next(
        record for record in listed_records if record.id == comparison_board.fine_run.id
    )

    assert comparison.runs[0].summary.model_dump() == listed_summary.model_dump()
    assert listed_summary.params_json == parameters_with_grid_step(FINE_GRID_STEP)


async def test_the_detail_view_still_carries_the_widened_column(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """详情响应里 `params_json` 还在 (它现在继承自列表视图, 不再是自己声明的那个字段)."""

    response = await client.get(
        f"{RUNS_PATH}/{comparison_board.fine_run.id}",
        headers=bearer_headers(comparison_board.member.token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["params_json"] == parameters_with_grid_step(FINE_GRID_STEP)


async def test_anonymous_callers_cannot_compare(
    client: AsyncClient, comparison_board: ComparisonBoard
) -> None:
    """没有令牌就没有对比权: 401 且不读任何结果库."""

    response = await client.get(
        COMPARE_PATH, params={"ids": comparison_board.fine_run.id}
    )

    assert response.status_code == 401
