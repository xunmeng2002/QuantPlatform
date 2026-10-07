"""初始化 CSV 的解析与播种.

三份 CSV 只在"这张表还是空的"时用一次, 故两条纪律最要紧: **空表才播** (重跑不能把人工录的
数据抹掉), 以及**表头必须逐字等于契约列序** (差一列就整体错位, 而错位在库里看不出来).

这里的库不走 `application` 夹具: 那个夹具的 lifespan 会从**真实的**那三份 CSV 播种, 而本模块
要验的正是"播种怎么读 CSV" —— 那份 CSV 长什么样、什么时候播, 得由用例自己说了算.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.catalog.database import PlatformDatabase
from app.catalog.models import (
    BaseCommissionModel,
    CommissionGroupModel,
    ProductModel,
)
from app.config import PlatformSettings
from app.reference_data import initial_rows
from app.reference_data.initial_rows import (
    convert_csv_cell,
    ensure_initial_reference_data,
    read_initial_rows,
)
from app.reference_data.seed_contract import (
    BASE_COMMISSION_TABLE,
    COMMISSION_GROUP_TABLE,
    PRODUCT_TABLE,
    SeedColumn,
    SeedTable,
)
from app.reference_data.seed_database import SEED_TABLE_MODELS, engine_attribute_names


PRODUCT_CSV_ROWS = [
    ["SSE", "600519", "贵州茅台", "6", "1", "0.01", "100", "1", "200", "1", ""]
]

COMMISSION_GROUP_CSV_ROWS = [["1", "默认组"], ["2", "股票组"]]

# 两处空单元是刻意的: 数值列空着要读成 0, 字符列空着要留成空串.
BASE_COMMISSION_CSV_ROWS = [
    [
        "1",
        "SSE",
        "600519",
        "1",
        "0.0003",
        "0.0004",
        "",
        "",
        "0.0005",
        "0.0006",
        "",
        "",
        "5",
        "100",
    ]
]


@pytest.fixture
def seed_csv_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把初始化 CSV 目录换成临时目录, 由各用例自己往里写文件."""

    directory = tmp_path / "reference-seed"

    directory.mkdir()

    monkeypatch.setattr(initial_rows, "REFERENCE_SEED_CSV_DIRECTORY", directory)

    return directory


@pytest_asyncio.fixture
async def empty_database(platform_settings: PlatformSettings) -> AsyncIterator[PlatformDatabase]:
    """一个建好了表、一行数据都没有的库."""

    database = PlatformDatabase(platform_settings.database_url)

    await database.initialize()

    try:
        yield database
    finally:
        await database.close()


def _write_csv(directory: Path, table: SeedTable, rows: list[list[str]]) -> None:
    """按契约的表头写一份 CSV, 后面接给定各行."""

    lines = [",".join(table.column_names())] + [",".join(row) for row in rows]

    (directory / f"{table.table_name}.csv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


async def _read_catalog_rows(
    database: PlatformDatabase, table: SeedTable
) -> list[tuple[object, ...]]:
    """把库里这张表读出来, 列序按契约.

    走生产用的那套列名对照, 而不是在测试里另写一份: 另写一份的话, 列名一旦对不上, 这里会跟着
    一起错, 于是"错位"这件事在测试里恰好看不见.
    """

    model = SEED_TABLE_MODELS[table.table_name]

    attribute_names = engine_attribute_names(table, model)

    async with database.session_scope() as session:
        result = await session.execute(
            select(*(getattr(model, attribute_name) for attribute_name in attribute_names))
        )

        return [tuple(row) for row in result.all()]


def test_convert_csv_cell_reads_a_blank_number_as_zero_and_keeps_blank_text_blank() -> None:
    """空数值单元按 0, 空字符单元留成空串.

    字符列不能跟着补零: `SessionName` 的 `""` 是"这个品种没有额外交易时段", 而 `"0"` 会是另一个
    品种的名字.
    """

    assert convert_csv_cell(SeedColumn("SessionName", "char(32)"), "") == ""
    assert convert_csv_cell(SeedColumn("OpenByVolume", "double"), "  ") == 0.0
    assert convert_csv_cell(SeedColumn("VolumeMultiple", "int"), "") == 0
    assert convert_csv_cell(SeedColumn("PriceTick", "double"), "0.01") == 0.01
    assert convert_csv_cell(SeedColumn("VolumeMultiple", "int"), "10") == 10


def test_read_initial_rows_rejects_a_header_that_is_not_the_contract(tmp_path: Path) -> None:
    """表头与契约列序不符时报错, 不按位置硬塞.

    少一列 (例如新加了 `SessionName`) 而照旧按下标灌, 结果是一列整体前移, 而那样出来的库只会
    表现为"某些品种的乘数不对", 归因不到这份文件上.
    """

    csv_path = tmp_path / "Product.csv"

    csv_path.write_text(
        "ExchangeId,ProductId,ProductName\nSSE,600519,贵州茅台\n", encoding="utf-8"
    )

    with pytest.raises(ValueError):
        read_initial_rows(csv_path, PRODUCT_TABLE)


async def test_an_empty_table_is_seeded_from_the_csv(
    seed_csv_directory: Path, empty_database: PlatformDatabase
) -> None:
    """三张表都空着时, 各自按 CSV 播种, 取值按列类型转好.

    两处空单元穿过这一整条路径后应当变成 `0.0` 与 `""`: 这是"CSV 文本 → 库里取值"的转换在起
    作用, 而不是把字符串原样塞进去.
    """

    _write_csv(seed_csv_directory, PRODUCT_TABLE, PRODUCT_CSV_ROWS)
    _write_csv(seed_csv_directory, COMMISSION_GROUP_TABLE, COMMISSION_GROUP_CSV_ROWS)
    _write_csv(seed_csv_directory, BASE_COMMISSION_TABLE, BASE_COMMISSION_CSV_ROWS)

    await ensure_initial_reference_data(empty_database)

    assert await _read_catalog_rows(empty_database, PRODUCT_TABLE) == [
        ("SSE", "600519", "贵州茅台", 6, 1, 0.01, 100, 1, 200, 1, "")
    ]

    assert await _read_catalog_rows(empty_database, COMMISSION_GROUP_TABLE) == [
        (1, "默认组"),
        (2, "股票组"),
    ]

    assert await _read_catalog_rows(empty_database, BASE_COMMISSION_TABLE) == [
        (
            1,
            "SSE",
            "600519",
            1,
            0.0003,
            0.0004,
            0.0,
            0.0,
            0.0005,
            0.0006,
            0.0,
            0.0,
            5.0,
            100.0,
        )
    ]


async def test_a_table_that_already_has_rows_is_left_alone(
    seed_csv_directory: Path, empty_database: PlatformDatabase
) -> None:
    """非空的表一概不动 —— 人工改过的取值不会被初始化 CSV 冲掉.

    这是这个模块存在的全部理由的另一半: 若每启动一次就照 CSV 重灌一遍, 那这份 CSV 就不是"初始
    值"而是"权威值", 用户在管理页面上的每一次修改都会在下一次重启时消失.
    """

    _write_csv(seed_csv_directory, PRODUCT_TABLE, PRODUCT_CSV_ROWS)
    _write_csv(seed_csv_directory, COMMISSION_GROUP_TABLE, COMMISSION_GROUP_CSV_ROWS)
    _write_csv(seed_csv_directory, BASE_COMMISSION_TABLE, [])

    await ensure_initial_reference_data(empty_database)

    async with empty_database.session_scope() as session:
        product = await session.scalar(
            select(ProductModel).where(ProductModel.product_id == "600519")
        )

        product.product_name = "人工改过的名字"

        await session.commit()

    await ensure_initial_reference_data(empty_database)

    assert await _read_catalog_rows(empty_database, PRODUCT_TABLE) == [
        ("SSE", "600519", "人工改过的名字", 6, 1, 0.01, 100, 1, 200, 1, "")
    ]

    assert len(
        await _read_catalog_rows(empty_database, COMMISSION_GROUP_TABLE)
    ) == 2


async def test_a_bad_header_leaves_every_table_untouched(
    seed_csv_directory: Path, empty_database: PlatformDatabase
) -> None:
    """某一份 CSV 的表头不对时, 前面已经播进去的表也要一起回滚.

    三张表在同一事务里落库就是为了这个: 半播的库事后分不出哪几行是初始化带的、哪几行是人工录
    的, 而报错信息只指向 CSV, 与那半库数据对不上号.

    坏的这份刻意放在**最后**一张表上 (契约次序是 Product → CommissionGroup →
    BaseCommission): 放前头的话, 后面两张表根本还没轮上, "什么都没留下"是白来的.
    """

    _write_csv(seed_csv_directory, PRODUCT_TABLE, PRODUCT_CSV_ROWS)
    _write_csv(seed_csv_directory, COMMISSION_GROUP_TABLE, COMMISSION_GROUP_CSV_ROWS)

    (seed_csv_directory / "BaseCommission.csv").write_text(
        "CommissionGroupId,ExchangeId,InstrumentId\n1,SSE,600519\n", encoding="utf-8"
    )

    with pytest.raises(ValueError):
        await ensure_initial_reference_data(empty_database)

    async with empty_database.session_scope() as session:
        product_row_count = len((await session.execute(select(ProductModel))).all())
        group_row_count = len((await session.execute(select(CommissionGroupModel))).all())
        rate_row_count = len((await session.execute(select(BaseCommissionModel))).all())

    assert (product_row_count, group_row_count, rate_row_count) == (0, 0, 0)
