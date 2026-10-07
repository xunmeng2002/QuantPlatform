"""随平台发布的三份初始化 CSV, 在某张表为空时播种一次.

**这不是导入功能**. 三份 CSV 是代码资产 (跟着版本走, 落在 `backend/reference_seed/`), 只在
"这张表还是空的"时用一次, 此后再不相干: 数据住在 catalog 里, 日常维护走管理页面. 之所以要
它, 是因为一个全空的 `Products` 表会让每一轮回测都缺品种乘数, 而平台自己无从知道 A 股的
交易所与三位代码是什么.

故**空表视作未初始化**. 想让某张表一直空着 (例如费率表, 建库时有意留空等人工录入), 就把那份
CSV 只留表头 —— 这是刻意的空起点, 不是漏配. 反过来, 把一张表删空并重启, 会被 CSV 重新填回;
要彻底空掉它, 得同时改这份 CSV.

CSV 的读取规则逐条对齐 `QuantTrading/makeseeddb.py`: `utf-8-sig` (容忍 BOM)、表头必须逐字
等于引擎列序 (否则报错而不是静默错位)、空行跳过、数值空单元按 0、字符列原样.
"""

from __future__ import annotations

import asyncio
import csv
import logging
from pathlib import Path

from sqlalchemy import func, select

from ..catalog.database import PlatformDatabase
from ..config import REFERENCE_SEED_CSV_DIRECTORY
from ..ids import generate_identifier
from .seed_contract import SEED_TABLES, SeedColumn, SeedTable
from .seed_database import SEED_TABLE_MODELS, engine_attribute_names


logger = logging.getLogger(__name__)


CSV_FILENAME_SUFFIX = ".csv"


def convert_csv_cell(column: SeedColumn, raw_value: str) -> object:
    """把 CSV 的一个单元转成该列的取值.

    空数值单元按 0: Dump 出来的 CSV 里缺列就是空串, 语义上等于不收费 / 不限制. 字符列原样
    保留 —— 空串就是空串, 补零会把 `SessionName` 变成一个假值.
    """

    if column.sqlite_type.startswith("char"):
        return raw_value

    if not raw_value.strip():
        return 0.0 if column.sqlite_type == "double" else 0

    return float(raw_value) if column.sqlite_type == "double" else int(raw_value)


def read_initial_rows(csv_path: Path, table: SeedTable) -> list[tuple[object, ...]]:
    """读一份初始化 CSV, 按契约的列序转出取值元组.

    表头不符时**报错而不是按位置硬塞**: 少一列 (例如新加了 `SessionName`) 而照旧按下标灌,
    结果是一列整体错位, 而那种库里出来的回测只会"乘数不对", 归因不到这份文件上.
    """

    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)

        try:
            header = tuple(next(reader))
        except StopIteration:
            raise ValueError(f"{csv_path} 是空文件, 连表头都没有") from None

        expected_header = table.column_names()

        if header != expected_header:
            raise ValueError(
                f"{csv_path} 的表头与 {table.table_name} 的列序不一致: "
                f"{header} != {expected_header}"
            )

        return [
            tuple(
                convert_csv_cell(column, raw_value)
                for column, raw_value in zip(table.columns, raw_row)
            )
            for raw_row in reader
            if raw_row
        ]


async def ensure_initial_reference_data(database: PlatformDatabase) -> None:
    """逐表检查, **该表为空**才播种. 幂等: 非空的表一概不动.

    一次启动只提交一次: 三张表在同一事务里落库, 中途出错就一张都不留 —— 半播的库更难解释
    (哪几行是初始化带的、哪几行是人工录的, 事后分不出来).
    """

    async with database.session_scope() as session:
        seeded_table_names = []

        for table in SEED_TABLES:
            model = SEED_TABLE_MODELS[table.table_name]

            existing_row_count = await session.scalar(
                select(func.count()).select_from(model)
            )

            if existing_row_count:
                continue

            csv_path = (
                REFERENCE_SEED_CSV_DIRECTORY / f"{table.table_name}{CSV_FILENAME_SUFFIX}"
            )

            rows = await asyncio.to_thread(read_initial_rows, csv_path, table)

            attribute_names = engine_attribute_names(table, model)

            for row in rows:
                session.add(
                    model(
                        id=generate_identifier(),
                        **dict(zip(attribute_names, row)),
                    )
                )

            # 逐表冲一次盘. 费率明细对组有外键, 而本仓不用 `relationship()`, 于是 SQLAlchemy
            # 排不出这两张表的插入次序 (它按映射名排, `BaseCommissions` 恰好排在
            # `CommissionGroups` 前面). 冲一次就把"组已落库"这件事钉在"明细开始插入"之前;
            # 当前两份 CSV 都只有表头, 这一行暂时无事可做, 但等谁往里填了数据再发现就晚了.
            await session.flush()

            seeded_table_names.append(f"{table.table_name}({len(rows)} 行)")

        if not seeded_table_names:
            return

        await session.commit()

        logger.info("基础数据已按初始化 CSV 播种: %s", ", ".join(seeded_table_names))
