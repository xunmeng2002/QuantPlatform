"""结果库的两条读端点: 逐日权益与明细分页表.

读的是**引擎写的结果库** (`BackTest_<RunId>.db`), 故夹具必须建一个**真的** SQLite 文件——
P4 的产物用例可以把 `.db` 当成 16 字节的占位文本, 那是因为它只比字节; 这里要真的执行查询.

边界分四组: **归属** (只有提交人读得到, 与 P4 的产物用例同构)、**什么时候能读** (运行中一律
409: 引擎写库没有 `busy_timeout`, 边写边读会拿到半句)、**读什么** (表名白名单、只读连接)、
以及**库在哪** (路径必须落在作业目录之内, 而 `DbPath` 是引擎侧写的、可被策略伪造).

夹具里的表与列都是引擎的真实名字, 但**列是子集** (只建用例要用的那些): 真 schema 由
`test_real_engine_acceptance` 那一轮真回测兜底, 这里要守的是读路径.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.catalog.database import PlatformDatabase
from app.catalog.enums import RunStatus
from app.catalog.models import RunModel, UserModel
from app.catalog.pagination import DEFAULT_PAGE_SIZE
from app.config import PlatformSettings
from app.errors import ResourceNotFoundError
from app.routers import runs as runs_router
from app.routers.runs import RESULT_DATABASE_MISSING_MESSAGE, RESULT_NOT_READY_MESSAGE
from app.services.result_database import (
    RESULT_TABLE_NAMES,
    RESULT_TABLE_NOT_FOUND_MESSAGE,
    CapitalPointRecord,
    open_result_database_readonly,
    read_result_table_page,
)

from .helpers import (
    DEFAULT_MEMBER_PASSWORD,
    bearer_headers,
    create_run_record,
    create_strategy_record,
    create_strategy_version_record,
    create_user_record,
    login,
    update_record_by_id,
)
from .run_helpers import job_directory


RUNS_PATH = "/api/runs"
RUN_OWNER_USERNAME = "result-owner"
OTHER_MEMBER_USERNAME = "result-outsider"
STRATEGY_NAME = "result-grid"

ACCOUNT_ID = "A0000000001"
DATABASE_FILENAME_TEMPLATE = "BackTest_{run_id}.db"
DATABASE_RELATIVE_PATH_TEMPLATE = "./BackTest_{run_id}.db"


@dataclass(frozen=True)
class ResultTableFixture:
    """一张结果表的形状与内容: 列定义 (列名 + 类型) 与整齐的行.

    类型只为贴近引擎的 DDL (SQLite 只按亲和性存值), 列名才是断言的对象.
    """

    column_definitions: tuple[tuple[str, str], ...]
    rows: tuple[tuple[object, ...], ...]

    @property
    def column_names(self) -> list[str]:
        return [column_name for column_name, _ in self.column_definitions]


RESULT_TABLE_FIXTURES: dict[str, ResultTableFixture] = {
    "Capital": ResultTableFixture(
        column_definitions=(
            ("TradingDay", "char(16)"),
            ("AccountId", "char(32)"),
            ("AccountType", "int"),
            ("Balance", "double"),
            ("PreBalance", "double"),
            ("Available", "double"),
        ),
        # 第一行是引擎的种子行 (存入初始资金, 权益与可用都是初始资金), 末行创新高:
        # 权益曲线的判据 (首点 = 初始资金, 末点 = 最后一行) 就落在这四行上.
        rows=(
            ("20240101", ACCOUNT_ID, 0, 1000000.0, 1000000.0, 1000000.0),
            ("20240102", ACCOUNT_ID, 0, 999000.5, 1000000.0, 998500.5),
            ("20240103", ACCOUNT_ID, 0, 998000.0, 999000.5, 997500.0),
            ("20240104", ACCOUNT_ID, 0, 1000500.0, 998000.0, 1000000.0),
        ),
    ),
    "Trade": ResultTableFixture(
        column_definitions=(
            ("TradingDay", "char(16)"),
            ("AccountId", "char(32)"),
            ("ExchangeId", "char(8)"),
            ("InstrumentId", "char(32)"),
            ("TradeId", "char(64)"),
            ("Direction", "int"),
            ("Price", "double"),
            ("Volume", "bigint"),
        ),
        # 7 行是给分页用的: 3 + 3 + 1 三页正好把"不重不漏"试出来.
        rows=(
            ("20240101", ACCOUNT_ID, "SSE", "600000", "T0001", 1, 10.5, 100),
            ("20240101", ACCOUNT_ID, "SSE", "600000", "T0002", -1, 10.6, 200),
            ("20240102", ACCOUNT_ID, "SSE", "600001", "T0003", 1, 11.0, 300),
            ("20240102", ACCOUNT_ID, "SSE", "600001", "T0004", -1, 11.25, 400),
            ("20240103", ACCOUNT_ID, "SSE", "600002", "T0005", 1, 12.0, 500),
            ("20240103", ACCOUNT_ID, "SSE", "600002", "T0006", -1, 12.5, 600),
            ("20240104", ACCOUNT_ID, "SSE", "600003", "T0007", 1, 13.0, 700),
        ),
    ),
    "Order": ResultTableFixture(
        column_definitions=(
            ("TradingDay", "char(16)"),
            ("AccountId", "char(32)"),
            ("ExchangeId", "char(8)"),
            ("InstrumentId", "char(32)"),
            ("OrderId", "int"),
            ("Price", "double"),
            ("Volume", "bigint"),
        ),
        rows=(
            ("20240101", ACCOUNT_ID, "SSE", "600000", 1, 10.5, 100),
            ("20240102", ACCOUNT_ID, "SSE", "600001", 2, 11.0, 300),
            ("20240103", ACCOUNT_ID, "SSE", "600002", 3, 12.0, 500),
        ),
    ),
    "Position": ResultTableFixture(
        column_definitions=(
            ("TradingDay", "char(16)"),
            ("AccountId", "char(32)"),
            ("ExchangeId", "char(8)"),
            ("InstrumentId", "char(32)"),
            ("PosiDirection", "int"),
            ("PositionVolume", "bigint"),
        ),
        rows=(
            ("20240101", ACCOUNT_ID, "SSE", "600000", 1, 100),
            ("20240102", ACCOUNT_ID, "SSE", "600000", 1, 300),
        ),
    ),
    "PositionDetail": ResultTableFixture(
        column_definitions=(
            ("TradingDay", "char(16)"),
            ("AccountId", "char(32)"),
            ("ExchangeId", "char(8)"),
            ("InstrumentId", "char(32)"),
            ("TradeId", "char(64)"),
            ("OpenDate", "char(16)"),
            ("OpenPrice", "double"),
            ("CloseVolume", "bigint"),
        ),
        rows=(
            ("20240101", ACCOUNT_ID, "SSE", "600000", "T0001", "20240101", 10.5, 100),
            ("20240102", ACCOUNT_ID, "SSE", "600000", "T0003", "20240102", 11.0, 300),
        ),
    ),
}

UNLISTED_TABLE_NAMES = (
    "BarMarketData",
    "users",
    "sqlite_master",
    'Capital"; DROP TABLE "Trade"; --',
)

OUTSIDE_JOB_DIRECTORY_PATHS = (
    "../{outsider_run_id}/BackTest_{outsider_run_id}.db",
    "C:/Windows/win.ini",
)


@dataclass(frozen=True)
class ResultBoard:
    """两份结果库: 一份属于成员, 一份属于别人.

    别人那一份必须**真的存在且可读**: 越权与我们自己的路径校验若指向一个不存在的文件, 404
    就分不清是哪一条生效了.
    """

    member: UserModel
    member_token: str
    outsider: UserModel
    outsider_token: str
    member_run: RunModel
    outsider_run: RunModel


def result_database_filenames(run_id: str) -> tuple[str, str]:
    """(盘上的文件名, 落库的相对路径). 两者分开命名: 一个是文件系统的事, 一个是 `DbPath` 列的事."""

    return (
        DATABASE_FILENAME_TEMPLATE.format(run_id=run_id),
        DATABASE_RELATIVE_PATH_TEMPLATE.format(run_id=run_id),
    )


def result_database_file(settings: PlatformSettings, run_id: str) -> Path:
    file_name, _ = result_database_filenames(run_id)

    return job_directory(settings, run_id) / file_name


def expected_equity_points() -> list[dict[str, object]]:
    """权益曲线应有的三个字段, 由夹具里的 `Capital` 行派生 (不另抄一份)."""

    table = RESULT_TABLE_FIXTURES["Capital"]
    column_indexes = {
        column_name: index for index, column_name in enumerate(table.column_names)
    }

    return [
        {
            "trading_day": row[column_indexes["TradingDay"]],
            "balance": row[column_indexes["Balance"]],
            "available": row[column_indexes["Available"]],
        }
        for row in table.rows
    ]


def expected_table_records(table_name: str) -> list[dict[str, object]]:
    """一张结果表应有的全部行 (列名 → 值), 同样由夹具派生."""

    table = RESULT_TABLE_FIXTURES[table_name]

    return [dict(zip(table.column_names, row)) for row in table.rows]


def write_result_database(settings: PlatformSettings, run_id: str) -> None:
    """在作业目录里建一个真的结果库, 表名列名与引擎一致 (列是子集)."""

    database_file = result_database_file(settings, run_id)
    database_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database_file) as connection:
        for table_name, fixture in RESULT_TABLE_FIXTURES.items():
            column_definitions = ", ".join(
                f"{column_name} {column_type}"
                for column_name, column_type in fixture.column_definitions
            )
            connection.execute(
                f'CREATE TABLE "{table_name}"({column_definitions})'
            )

            placeholders = ", ".join("?" for _ in fixture.column_definitions)
            connection.executemany(
                f'INSERT INTO "{table_name}" VALUES ({placeholders})', fixture.rows
            )


@pytest_asyncio.fixture
async def result_board(
    client: AsyncClient, database: PlatformDatabase, platform_settings: PlatformSettings
) -> ResultBoard:
    member = await create_user_record(database, RUN_OWNER_USERNAME)
    outsider = await create_user_record(database, OTHER_MEMBER_USERNAME)
    strategy = await create_strategy_record(database, member, STRATEGY_NAME)
    version = await create_strategy_version_record(database, strategy, member)

    member_run = await create_run_record(
        database, member, strategy, version, status=RunStatus.SUCCEEDED
    )
    outsider_run = await create_run_record(
        database, outsider, strategy, version, status=RunStatus.SUCCEEDED
    )

    for run in (member_run, outsider_run):
        write_result_database(platform_settings, run.id)

    _, member_relative_path = result_database_filenames(member_run.id)
    _, outsider_relative_path = result_database_filenames(outsider_run.id)

    # `DbPath` 列是引擎写进 `result.json` 的值, 正常只在收尾时才有; 这里手工补上,
    # 否则每一条用例都只能测到"没有结果库"那一条 404.
    await update_record_by_id(
        database,
        RunModel,
        member_run.id,
        lambda run: setattr(run, "db_path", member_relative_path),
    )
    await update_record_by_id(
        database,
        RunModel,
        outsider_run.id,
        lambda run: setattr(run, "db_path", outsider_relative_path),
    )

    return ResultBoard(
        member=member,
        member_token=await login(client, RUN_OWNER_USERNAME, DEFAULT_MEMBER_PASSWORD),
        outsider=outsider,
        outsider_token=await login(
            client, OTHER_MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD
        ),
        member_run=member_run,
        outsider_run=outsider_run,
    )


def _equity_path(run_id: str) -> str:
    return f"{RUNS_PATH}/{run_id}/equity"


def _table_path(run_id: str, table_name: str) -> str:
    return f"{RUNS_PATH}/{run_id}/tables/{table_name}"


async def test_the_equity_series_returns_every_trading_day_in_order(
    client: AsyncClient, result_board: ResultBoard
) -> None:
    """权益曲线的判据: 逐日一行, 按交易日升序, 首点即初始资金, 末点即最后一行.

    「曲线与实测数据点吻合」这条验收, 在接口这一侧的落点就是这三个字段逐行对上——图上画的
    就是这条序列, 故这里对全量相等断言, 而不只对首末两点.
    """

    response = await client.get(
        _equity_path(result_board.member_run.id),
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["run_id"] == result_board.member_run.id
    assert payload["points"] == expected_equity_points()
    assert payload["points"][0]["balance"] == 1000000.0


async def test_the_trade_table_page_returns_its_columns_and_rows(
    client: AsyncClient, result_board: ResultBoard
) -> None:
    """表头由 `columns` 给出且顺序即权威顺序, 行按列序落成对象.

    界面上不写死列: `Order` 有 33 列, 写死就是 33 处会漂移的真相.
    """

    response = await client.get(
        _table_path(result_board.member_run.id, "Trade"),
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["table"] == "Trade"
    assert payload["columns"] == RESULT_TABLE_FIXTURES["Trade"].column_names
    assert payload["total"] == len(RESULT_TABLE_FIXTURES["Trade"].rows)
    assert payload["offset"] == 0
    assert payload["limit"] == DEFAULT_PAGE_SIZE
    assert payload["records"] == expected_table_records("Trade")


async def test_every_whitelisted_table_is_reachable_by_its_engine_name(
    client: AsyncClient, result_board: ResultBoard
) -> None:
    """白名单里的 5 张表都要读得出来 —— 其中 `Order` 是 SQLite 保留字.

    这条用例就是"表名忘了加引号"会转红的那条: 裸写 `FROM Order` 是语法错, 接口会 500.
    """

    for table_name in RESULT_TABLE_NAMES:
        response = await client.get(
            _table_path(result_board.member_run.id, table_name),
            headers=bearer_headers(result_board.member_token),
        )

        assert response.status_code == 200, table_name

        payload = response.json()

        assert payload["columns"] == RESULT_TABLE_FIXTURES[table_name].column_names
        assert payload["records"] == expected_table_records(table_name)


async def test_paging_advances_without_repeating_or_skipping_records(
    client: AsyncClient, result_board: ResultBoard
) -> None:
    """7 行按 3 行一页走完, 三页合起来恰好是全部行且次序不变.

    "不重不漏"要靠一个**唯一的次序**才成立 (排序键含并列时, SQLite 的顺序未定义), 故这里
    连行的次序一起断言.
    """

    collected: list[dict[str, object]] = []

    for offset in (0, 3, 6):
        response = await client.get(
            _table_path(result_board.member_run.id, "Trade"),
            params={"offset": offset, "limit": 3},
            headers=bearer_headers(result_board.member_token),
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["total"] == 7
        assert payload["offset"] == offset
        assert payload["limit"] == 3

        collected.extend(payload["records"])

    assert collected == expected_table_records("Trade")


@pytest.mark.parametrize("table_name", UNLISTED_TABLE_NAMES)
async def test_a_table_outside_the_whitelist_is_not_found(
    client: AsyncClient, result_board: ResultBoard, table_name: str
) -> None:
    """未收录的表名一律 404, 且**什么都没被执行**.

    `BarMarketData` 是引擎真有、但本轮界面没开的一张表; 其余三个分别是"平台自己的库"、
    SQLite 的元数据表、以及一次拼接尝试. 后者跑完必须连一行都没少.
    """

    response = await client.get(
        _table_path(result_board.member_run.id, table_name),
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == RESULT_TABLE_NOT_FOUND_MESSAGE

    intact = await client.get(
        _table_path(result_board.member_run.id, "Trade"),
        headers=bearer_headers(result_board.member_token),
    )

    assert intact.status_code == 200
    assert intact.json()["total"] == len(RESULT_TABLE_FIXTURES["Trade"].rows)


@pytest.mark.parametrize("table_name", UNLISTED_TABLE_NAMES)
def test_a_rejected_table_name_never_opens_the_database(
    platform_settings: PlatformSettings, table_name: str
) -> None:
    """表名不合法时**连库都不该打开**.

    白名单不只是一句 404: 打开连接这个动作本身就该在放行之后. 判据是**拿一个不存在的文件
    路径去试**——若校验跑到开连接之后, 这里会以"打不开库"的面目报错, 而不是"结果表不存在".
    """

    missing_database_file = (
        platform_settings.runs_root / "no-such-run" / "BackTest_no-such-run.db"
    )

    with pytest.raises(ResourceNotFoundError):
        read_result_table_page(missing_database_file, table_name, 0, 1)


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
async def test_a_run_that_has_not_finished_has_no_readable_results(
    client: AsyncClient,
    result_board: ResultBoard,
    database: PlatformDatabase,
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
) -> None:
    """运行中(排队也算)一律 409, 且**不碰结果库**.

    引擎写库用的 `SqliteWrapper` 没设 `busy_timeout`, 边写边读要么拿到 SQLITE_BUSY, 要么拿到
    半份数据; "结束"的信号恒为进程退出. 这条用例同时钉住顺序: 状态判定在读库之前.
    """

    opened_files: list[Path] = []

    def _record_open(database_file: Path) -> list[CapitalPointRecord]:
        opened_files.append(database_file)

        return []

    monkeypatch.setattr(runs_router, "read_capital_series", _record_open)
    monkeypatch.setattr(runs_router, "read_result_table_page", _record_open)

    await update_record_by_id(
        database,
        RunModel,
        result_board.member_run.id,
        lambda run: setattr(run, "status", status.value),
    )

    equity_response = await client.get(
        _equity_path(result_board.member_run.id),
        headers=bearer_headers(result_board.member_token),
    )

    assert equity_response.status_code == 409
    assert equity_response.json()["detail"] == RESULT_NOT_READY_MESSAGE

    table_response = await client.get(
        _table_path(result_board.member_run.id, "Trade"),
        headers=bearer_headers(result_board.member_token),
    )

    assert table_response.status_code == 409
    assert table_response.json()["detail"] == RESULT_NOT_READY_MESSAGE
    assert opened_files == []


async def test_another_users_result_database_is_out_of_reach(
    client: AsyncClient, result_board: ResultBoard
) -> None:
    """非归属人读别人的权益与明细一律 404 (不是 403: 403 会确认这个 id 存在)."""

    outsider_headers = bearer_headers(result_board.outsider_token)

    equity_response = await client.get(
        _equity_path(result_board.member_run.id), headers=outsider_headers
    )
    table_response = await client.get(
        _table_path(result_board.member_run.id, "Trade"), headers=outsider_headers
    )

    assert equity_response.status_code == 404
    assert table_response.status_code == 404


async def test_result_reads_reject_an_anonymous_caller(
    client: AsyncClient, result_board: ResultBoard
) -> None:
    """匿名访问两条端点都是 401 (缺令牌时由 Starlette 层挡下, 响应体里没有 detail)."""

    equity_response = await client.get(_equity_path(result_board.member_run.id))
    table_response = await client.get(_table_path(result_board.member_run.id, "Trade"))

    assert equity_response.status_code == 401
    assert table_response.status_code == 401


async def test_a_run_without_a_result_database_is_not_found(
    client: AsyncClient, result_board: ResultBoard, database: PlatformDatabase
) -> None:
    """`DbPath` 为空 (失败的轮没写出 `result.json`) 时 404 而不是拿作业目录当库打开.

    空串拼出来的路径**就是作业目录本身**, 那种"打开"会以另一种面目失败.
    """

    await update_record_by_id(
        database,
        RunModel,
        result_board.member_run.id,
        lambda run: setattr(run, "db_path", ""),
    )

    response = await client.get(
        _equity_path(result_board.member_run.id),
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == RESULT_DATABASE_MISSING_MESSAGE


async def test_a_missing_database_file_is_not_found(
    client: AsyncClient, result_board: ResultBoard, database: PlatformDatabase
) -> None:
    """路径合法但文件不在 (被人工清掉) → 404, 不是 500."""

    await update_record_by_id(
        database,
        RunModel,
        result_board.member_run.id,
        lambda run: setattr(run, "db_path", "./BackTest_no-such-run.db"),
    )

    response = await client.get(
        _table_path(result_board.member_run.id, "Trade"),
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == RESULT_DATABASE_MISSING_MESSAGE


@pytest.mark.parametrize("outside_template", OUTSIDE_JOB_DIRECTORY_PATHS)
async def test_a_database_path_outside_the_job_directory_is_not_found(
    client: AsyncClient,
    result_board: ResultBoard,
    database: PlatformDatabase,
    outside_template: str,
) -> None:
    """`DbPath` 可以指向作业目录之外, 那种路径一律 404.

    这不是假想: `DbPath` 由引擎写进 `result.json`, 而策略是任意 Python 且与引擎同进程——
    它完全可以把这个值写成 `../<别人的 RunId>/BackTest_....db`. 故路径按不可信输入处理,
    与产物下载同一套解析后的包含检查 (字符串判 `..` 挡不住符号链接与大小写).
    """

    outside_path = outside_template.format(
        outsider_run_id=result_board.outsider_run.id
    )

    await update_record_by_id(
        database,
        RunModel,
        result_board.member_run.id,
        lambda run: setattr(run, "db_path", outside_path),
    )

    response = await client.get(
        _equity_path(result_board.member_run.id),
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == RESULT_DATABASE_MISSING_MESSAGE


async def test_a_run_without_a_job_directory_is_not_found(
    client: AsyncClient, result_board: ResultBoard, database: PlatformDatabase
) -> None:
    """作业目录被清掉时也是 404: 结果库在目录里, 目录不在就无从谈起."""

    await update_record_by_id(
        database,
        RunModel,
        result_board.member_run.id,
        lambda run: setattr(run, "workspace_path", "no-such-directory"),
    )

    response = await client.get(
        _equity_path(result_board.member_run.id),
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "params", [{"offset": -1}, {"limit": 0}, {"limit": 101}, {"limit": "many"}]
)
async def test_invalid_paging_parameters_are_rejected(
    client: AsyncClient, result_board: ResultBoard, params: dict[str, object]
) -> None:
    """分页参数越界或不是整数一律 422 (上限与房规的 `MAXIMUM_PAGE_SIZE` 同值)."""

    response = await client.get(
        _table_path(result_board.member_run.id, "Trade"),
        params=params,
        headers=bearer_headers(result_board.member_token),
    )

    assert response.status_code == 422


async def test_the_result_database_opens_read_only(
    result_board: ResultBoard, platform_settings: PlatformSettings
) -> None:
    """连接是只读的, 由 SQLite 自己强制.

    把"只读"钉成断言而不是注释: 写操作必须报 `attempt to write a readonly database`.
    少了 `?mode=ro`, 这条就会以"写成功了"的面目转红——而那等于给一条读端点配了写权限.
    """

    database_file = result_database_file(platform_settings, result_board.member_run.id)

    with open_result_database_readonly(database_file) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE probe_write(value int)")
