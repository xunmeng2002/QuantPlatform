"""读引擎的结果库 (`BackTest_<RunId>.db`).

引擎把一轮运行的 17 张结果表写进作业目录下的一个 SQLite 文件, 这里是平台**唯一**读它的地方.
三条硬约束都收在这一层, 不散到路由:

  - **只读**: 连接走 `?mode=ro`, 由 SQLite 自己强制 (对同一句柄写会直接报
    `attempt to write a readonly database`);
  - **表名白名单**: `RESULT_TABLE_NAMES` 是正本, 只有它收录的名字才可能被拼进 SQL,
    且拼进去时一律加双引号 (`Order` 是 SQLite 保留字, 裸写会语法错);
  - **运行中不读**: `SqliteWrapper` 没设 `busy_timeout`, 引擎还在写时读会拿到 SQLITE_BUSY
    或半份文件, 故「这一轮已经结束」必须先由调用方判定 (见 `routers/runs.py`).

放在 services 而不是 catalog 或 scheduler: catalog 是平台自己的库, scheduler 管启动与回收,
而这里是"读一份外部产物", 与 `engine_probe` 同类 —— 本层不依赖 FastAPI, 只抛领域异常.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..errors import ResourceNotFoundError


RESULT_TABLE_NOT_FOUND_MESSAGE = "结果表不存在"

READ_ONLY_URI_SUFFIX = "?mode=ro"

CAPITAL_TABLE_NAME = "Capital"

TRADING_DAY_COLUMN_NAME = "TradingDay"

ROW_IDENTIFIER_COLUMN_NAME = "rowid"

# 界面上开放的结果表, 即 `{table}` 路径参数的白名单**正本**.
#
# 引擎的结果库共 17 张表 (正本是 `QuantTrading/src/BackTest/BackTestTableList.h`), 这里只开
# 交易与资金相关的 5 张, 其余 12 张 (含 `BarMarketData`) 一律 404. 放宽就是往这个元组里加
# 名字, 同时别忘了在前端 `api/types.ts` 的 `RESULT_TABLE_NAMES` 补一份 —— 那是本表的镜像.
RESULT_TABLE_NAMES = (
    CAPITAL_TABLE_NAME,
    "Trade",
    "Order",
    "Position",
    "PositionDetail",
)

"""结果表里能出现的标量类型: 引擎的列只有 str / int / float, 无 BLOB."""
ResultScalar = str | int | float | bool | None


@dataclass(frozen=True)
class CapitalPointRecord:
    """`Capital` 表的一行, 即某个交易日的结算权益."""

    trading_day: str
    balance: float
    available: float


@dataclass(frozen=True)
class ResultTablePage:
    """结果表的一页.

    `column_names` 由 `PRAGMA table_info` 给出, 顺序即权威顺序 —— 表头因此不必在界面上
    写死 (`Order` 有 33 列, 写死就是 33 处会漂移的真相). `total` 是全表行数而不是本页行数.
    """

    table_name: str
    column_names: list[str]
    total: int
    records: list[dict[str, ResultScalar]]


@contextmanager
def open_result_database_readonly(database_file: Path) -> Iterator[sqlite3.Connection]:
    """以只读方式打开一份结果库, 出块即关闭.

    收文件路径而不是收连接: sqlite3 的连接不能跨线程用, 而调用方要把它整个丢进
    `asyncio.to_thread`, 于是"开连接 → 查询 → 关连接"必须在同一个 worker 里完成.

    `as_uri()` 而不是字符串拼接: 运行根可能含空格或非 ASCII 字符, 由它统一做百分号转义.
    """

    connection = sqlite3.connect(
        f"{database_file.as_uri()}{READ_ONLY_URI_SUFFIX}", uri=True
    )
    connection.row_factory = sqlite3.Row

    try:
        yield connection
    finally:
        connection.close()


def read_capital_series(database_file: Path) -> list[CapitalPointRecord]:
    """逐日权益序列, 按交易日升序.

    首行是引擎的种子行 (`Deposit` = 初始资金, `Balance` = `PreBalance` = 初始权益), 故序列
    的起点就是初始权益; 末尾一行是最后一个交易日的结算权益, 与 `result.json` 的 `Balance`
    同值. **`AccountId` 不作过滤**: 本平台的轮恒为单账户 (策略只订阅一个 `AccountId`).

    `TradingDay` 是 8 字符 `YYYYMMDD` 串, 字典序即时序, 故不解析日期; 末尾的 `rowid` 只为
    让次序**唯一** (PK 含 `AccountId`, 万一将来出现多账户, 同一天的多行也有确定次序).
    """

    with open_result_database_readonly(database_file) as connection:
        rows = connection.execute(
            f"SELECT {_quote(TRADING_DAY_COLUMN_NAME)}, {_quote('Balance')}"
            f", {_quote('Available')} FROM {_quote(CAPITAL_TABLE_NAME)}"
            f" ORDER BY {_quote(TRADING_DAY_COLUMN_NAME)}, {ROW_IDENTIFIER_COLUMN_NAME}"
        ).fetchall()

    return [
        CapitalPointRecord(
            trading_day=row[0],
            balance=row[1],
            available=row[2],
        )
        for row in rows
    ]


def read_result_table_page(
    database_file: Path,
    table_name: str,
    offset: int,
    limit: int,
) -> ResultTablePage:
    """结果表的一页, 按交易日升序.

    **表名先过白名单再引用**: 校验与拼接同处一函数, 是让"先白名单、后拼接"这条在代码上
    绕不过去 —— 界面上的页签本来就来自同一份清单, 只有手敲 URL 才会走到 404 那一条.
    """

    quoted_table = _quote_whitelisted_table_name(table_name)

    with open_result_database_readonly(database_file) as connection:
        column_names = [
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        ]
        total = connection.execute(f"SELECT COUNT(*) FROM {quoted_table}").fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM {quoted_table}"
            f" ORDER BY {_quote(TRADING_DAY_COLUMN_NAME)}, {ROW_IDENTIFIER_COLUMN_NAME}"
            f" LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    return ResultTablePage(
        table_name=table_name,
        column_names=column_names,
        total=total,
        records=[dict(row) for row in rows],
    )


def _quote_whitelisted_table_name(table_name: str) -> str:
    """表名过白名单 (不在册即当"不存在"), 再包上双引号.

    双引号是必须的: `Order` 是 SQLite 保留字, 裸写直接语法错. 白名单已保证名字里不含
    双引号, 故这里不做 `""` 转义 —— 但**顺序不能反**: 先引用后校验等于把没有护栏的字符串
    送进了 SQL.
    """

    if table_name not in RESULT_TABLE_NAMES:
        raise ResourceNotFoundError(RESULT_TABLE_NOT_FOUND_MESSAGE)

    return _quote(table_name)


def _quote(identifier: str) -> str:
    """SQL 标识符引用: 双引号包裹, 内部的双引号写两遍."""

    escaped_identifier = identifier.replace('"', '""')

    return f'"{escaped_identifier}"'
