"""随平台发布的三份初始化 CSV, 在某张表为空时播种一次.

**这不是导入功能**. 三份 CSV 是代码资产 (跟着版本走, 落在 `backend/reference_seed/`), 只在
"这张表还是空的"时用一次, 此后再不相干: 数据住在 catalog 里, 日常维护走管理页面. 之所以要
它, 是因为一个全空的 `Products` 表会让每一轮回测都缺品种乘数, 而平台自己无从知道 A 股的
交易所与三位代码是什么.

故**空表视作未初始化**. 想让某张表一直空着, 就把那份 CSV 只留表头 —— 这是刻意的空起点, 不是
漏配. 反过来, 把一张表删空并重启, 会被 CSV 重新填回; 要彻底空掉它, 得同时改这份 CSV.

CSV 的读取规则逐条对齐 `QuantTrading/makeseeddb.py`: `utf-8-sig` (容忍 BOM)、表头必须逐字
等于引擎列序 (否则报错而不是静默错位)、空行跳过、数值空单元按 0、字符列原样.
"""

import asyncio
import csv
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.database import PlatformDatabase
from ..catalog.models import Base
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


async def _read_missing_references(
    session: AsyncSession, table: SeedTable, model: type[Base], attribute_names: tuple[str, ...],
    rows: list[tuple[object, ...]]
) -> list[tuple[str, str]]:
    """这些行里, 有没有指向别的表、而那一行并不存在的外键值; 回 (被指向的表名, "列=值") 的列表.

    初始化 CSV 里只有一处这样的引用 (费率明细的组号指向 `CommissionGroups`), 但它足够典型: 那张表
    非空就整个不播, 于是库里的组可能压根不含 CSV 引的那个号. 真到插入那一刻才撞外键的话, 整个播种
    事务连别的表一起回滚, 而用户只看见日志里一行 ERROR —— 下次重启重来一遍, 永远如此. 故先问一句.

    走 SQLAlchemy 的外键元数据而不是写死表名: 这一处引用是模型自己声明的, 再写一份就多一处会漂移的
    真相. 列名那两侧各取各的 —— 报给用户的话语是引擎那套列名 (`CommissionGroupId`), 与同一句里的
    表名同源.
    """

    missing_references = []

    for foreign_key in model.__table__.foreign_keys:
        local_attribute_name = model.__mapper__.get_property_by_column(foreign_key.parent).key
        local_column_name = table.column_names()[attribute_names.index(local_attribute_name)]

        referenced_values = {
            row[attribute_names.index(local_attribute_name)] for row in rows
        }

        if not referenced_values:
            continue

        existing_values = set(
            (
                await session.execute(
                    select(foreign_key.column).where(
                        foreign_key.column.in_(referenced_values)
                    )
                )
            ).scalars()
        )

        missing_references.extend(
            (foreign_key.column.table.name, f"{local_column_name}={value}")
            for value in sorted(referenced_values - existing_values)
        )

    return missing_references


async def ensure_initial_reference_data(database: PlatformDatabase) -> None:
    """逐表检查, **该表为空**才播种. 幂等: 非空的表一概不动.

    一次启动只提交一次: 三张表在同一事务里落库, 中途出错就一张都不留 —— 半播的库更难解释
    (哪几行是初始化带的、哪几行是人工录的, 事后分不出来).

    **一处例外**: 这份 CSV 指向库里的行, 而那一行不存在时 (费率明细的组号 → 库里管理员自建的组),
    只跳过这张表并报警, 不连累别的表 —— 那是**这一张表**与库里已有数据不合, 而别的表并没有错.

    **但"引用的行"若来自本次刚播下去的那张表**, 就回到上面那条整体回滚: 那意味着随包的那几份 CSV
    自相矛盾 (费率引了组 5, 而组表只定义到组 1), 属"这份包有问题", 与表头不对同一类 —— 只是跳过的话,
    一张静默空着的费率表会让每一轮都按零费用算钱.
    """

    async with database.session_scope() as session:
        seeded_row_counts: dict[str, int] = {}

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

            missing_references = await _read_missing_references(
                session, table, model, attribute_names, rows
            )

            if missing_references:
                if any(
                    referenced_table_name in seeded_row_counts
                    for referenced_table_name, _ in missing_references
                ):
                    raise ValueError(
                        f"{table.table_name}.csv 引用了本次播种没有的键: "
                        + ", ".join(
                            f"{referenced_table_name} 的 {reference}"
                            for referenced_table_name, reference in missing_references
                        )
                    )

                logger.warning(
                    "初始化 CSV 的 %s 指向库里没有的行 (%s), 这张表保持原样",
                    table.table_name,
                    ", ".join(reference for _, reference in missing_references),
                )

                continue

            for row in rows:
                session.add(
                    model(
                        id=generate_identifier(),
                        **dict(zip(attribute_names, row)),
                    )
                )

            # 逐表冲一次盘. 费率明细对组有外键, 而本仓不用 `relationship()`, 于是 SQLAlchemy
            # 排不出这两张表的插入次序 (它按映射名排, `BaseCommissions` 恰好排在
            # `CommissionGroups` 前面). 冲一次就把"组已落库"这件事钉在"明细开始插入"之前 ——
            # 费率那两份 CSV 都带数据, 这一步是必须的, 不是留白.
            await session.flush()

            seeded_row_counts[table.table_name] = len(rows)

        if not seeded_row_counts:
            return

        await session.commit()

        logger.info(
            "基础数据已按初始化 CSV 播种: %s",
            ", ".join(
                f"{table_name}({row_count} 行)"
                for table_name, row_count in seeded_row_counts.items()
            ),
        )
