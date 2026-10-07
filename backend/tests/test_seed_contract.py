"""种子库产物与引擎列序契约的一致性.

**产物级**断言: 真按当前 catalog 生成一个文件, 再逐表比对 `PRAGMA table_info` 的列名、次序、
个数与类型, 并断言文件里恰有这三张表.

只比 ORM 的声明序是不够的 —— 把平台自造的 `Id` 声明到最前面时, 那种测试照样全绿 (它比的是
模型与自己, 两处同源), 而引擎会把它读成 `ExchangeId`. 引擎按列**下标**绑定: 错位不报错, 只是
把一行数据的每一列都读串, 而回测照跑完、指标照出数. 故这里必须看真文件.

后两组断言把契约与 ORM 焊在一起: 契约改变而模型没跟上时, 生成会抛 `LookupError` 或 `KeyError`
(一条难归因的 500), 而这两组测试会先红在测试里.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import pytest_asyncio

from app.catalog.database import PlatformDatabase
from app.config import SEED_DATABASE_FILENAME, PlatformSettings
from app.reference_data.seed_contract import SEED_TABLES, SeedTable
from app.reference_data.seed_database import (
    SEED_TABLE_MODELS,
    generate_run_seed_database,
)

from .helpers import DEFAULT_COMMISSION_GROUP_ID


SQLITE_INTERNAL_TABLE_NAME_PREFIX = "sqlite_"

GENERATED_SEED_DATABASE_DIRECTORY_NAME = "generated-seed-database"


def _read_generated_table_names(database_path: Path) -> list[str]:
    """文件里的表名, 排除 SQLite 自建的内部表."""

    with closing(sqlite3.connect(database_path)) as connection:
        return sorted(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if not row[0].startswith(SQLITE_INTERNAL_TABLE_NAME_PREFIX)
        )


def _read_generated_columns(database_path: Path, table_name: str) -> list[tuple[str, str]]:
    """文件里这张表的 (列名, 类型) 按序. 类型统一小写后比对."""

    with closing(sqlite3.connect(database_path)) as connection:
        return [
            (row[1], str(row[2]).lower())
            for row in connection.execute(f'PRAGMA table_info("{table_name}")')
        ]


def _read_generated_primary_key(database_path: Path, table_name: str) -> tuple[str, ...]:
    """文件里这张表的主键列按序.

    `PRAGMA table_info` 的第六列 (`pk`) 是该列在主键里的**序号** (1 起), 不是布尔值 —— 排序
    要按它排, 不能按列在表里的位置排.
    """

    with closing(sqlite3.connect(database_path)) as connection:
        primary_key_rows = [
            row
            for row in connection.execute(f'PRAGMA table_info("{table_name}")')
            if row[5]
        ]

        return tuple(row[1] for row in sorted(primary_key_rows, key=lambda row: row[5]))


@pytest_asyncio.fixture
async def generated_seed_database_path(
    database: PlatformDatabase, platform_settings: PlatformSettings
) -> Path:
    """按当前 catalog 生成的一份种子库 (未交付给任何一轮, 只是一个临时文件).

    经 `generate_run_seed_database` 而不是直接调写文件那个私有函数: 这一条路径才是产品真正走的
    那条 (读 catalog → 展开三级费率 → 写文件), 列的取值也真的来自 catalog.

    合约给空表: 本文件验的是**产物形状** (三张表、列序、主键), 而列序与行内容无关; 行内容与
    三级展开由 `test_seed_database.py` 与 `test_rate_expansion.py` 覆盖, 这里不重复造一套费率.
    """

    destination = (
        platform_settings.runs_root
        / GENERATED_SEED_DATABASE_DIRECTORY_NAME
        / SEED_DATABASE_FILENAME
    )

    async with database.session_scope() as session:
        await generate_run_seed_database(
            session, destination, DEFAULT_COMMISSION_GROUP_ID, ()
        )

    return destination


def test_the_contract_and_the_model_table_cover_the_same_table_names() -> None:
    """契约的三张表与 `SEED_TABLE_MODELS` 覆盖同一批表名.

    少一对就是静默漏掉一整张表: 生成时 `read_seed_rows` 会去 `SEED_TABLE_MODELS[...]` 取模型,
    取不到即 `KeyError`, 而那一句 `KeyError` 与"有人加了一张引擎表"之间没有可读的因果链.
    """

    assert sorted(SEED_TABLE_MODELS) == sorted(
        table.table_name for table in SEED_TABLES
    )


def test_the_file_holds_exactly_the_three_engine_tables(
    generated_seed_database_path: Path,
) -> None:
    """文件里恰有三张引擎表, 没有任何平台表.

    `Users` / `Runs` 那类平台表若被一起写进来, 引擎不会反对 —— 它只挑自己认识的表读; 但种子库
    文件是给引擎的, 往里塞平台表等于把平台的内库结构抄送到另一个进程读得到的地方.
    """

    assert _read_generated_table_names(generated_seed_database_path) == sorted(
        table.table_name for table in SEED_TABLES
    )


@pytest.mark.parametrize("table", SEED_TABLES, ids=lambda table: table.table_name)
def test_each_table_matches_the_contract_column_for_column(
    generated_seed_database_path: Path, table: SeedTable
) -> None:
    """列名、列序、列数、列类型逐列相符.

    这是本文件的核心断言: 引擎按列下标绑定, 列序就是契约. 多一列在前面 (平台自造的 `Id` 恰好
    会落在那里) 等于把整行数据往后挪一格, 而引擎不报错.
    """

    assert _read_generated_columns(
        generated_seed_database_path, table.table_name
    ) == [(column.name, column.sqlite_type) for column in table.columns]

    assert _read_generated_primary_key(
        generated_seed_database_path, table.table_name
    ) == table.primary_key_columns


@pytest.mark.parametrize("table", SEED_TABLES, ids=lambda table: table.table_name)
def test_every_contract_column_exists_on_its_orm_model(table: SeedTable) -> None:
    """契约的每一列在对应 ORM 模型上都有同名的列.

    生成与播种都按**列名**配对 (`seed_database.engine_attribute_names`), 配不上时抛
    `LookupError` —— 那会表现为一次 500. 这一组断言把那个失败提前到测试里, 并且指出是哪张表
    哪一列, 而不是留到"某次保存报了个内部错误".

    只断言**存在**, 不断言次序: 两边都是按列名取属性, 模型把引擎面列摆在什么位置不影响产物.
    """

    model_column_names = {
        column.name for column in SEED_TABLE_MODELS[table.table_name].__mapper__.columns
    }

    assert [
        column_name
        for column_name in table.column_names()
        if column_name not in model_column_names
    ] == []
