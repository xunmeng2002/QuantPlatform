"""建表之外的**补列**动作.

`Base.metadata.create_all` 只建不存在的表, 已存在的表**一列都不改**: 加了模型列而库里没有,
第一次写入就撞上"表 X 没有列 Y", 现场是一句 SQLite 的英文报错, 极难归因到"忘了重建库".

**这不是迁移框架.** 只处理"新增列"一种变更, 且只处理能安全补上的那些; 改类型、删列、加约束
一律不做——它们在 SQLite 上要重建整张表, 代价与风险不该由这个模块承担, 遇到就记一条 warning
说明"需重建库"(`catalog/database.py` 的模块说明即此约定).

**列的 DDL 不在这里另写一份**: 类型、可空性、默认值都从模型声明渲染 (`CreateColumn`), 否则
模型与实际库会各有一个真相, 而它们迟早漂移——本仓已把同类的"两处真相"记成缺陷
(`platform-plan.md` §12.20 的表名清单).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import MetaData, inspect, literal, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import Column, CreateColumn


logger = logging.getLogger(__name__)

ALTER_TABLE_ADD_COLUMN_TEMPLATE = "ALTER TABLE {table} ADD COLUMN {column}"

RENDERABLE_DEFAULT_TYPES = (str, int, float, bool)

MISSING_DEFAULT_WARNING = (
    "表 %s 缺列 %s, 但该列是 NOT NULL 且没有可渲染的默认值, 无法安全补上"
    " (SQLite 拒绝在非空表上加没有默认值的 NOT NULL 列). 该列**未补**, 需要重建库,"
    " 或给该列声明一个标量默认值."
)


def add_missing_columns(connection: Connection, metadata: MetaData) -> list[str]:
    """把 `metadata` 声明了、而实际表结构里没有的列补上, 返回补上的 `表.列` 清单.

    幂等: 没有缺列时什么都不做. 表不存在时跳过而不是去建它——建表是 `create_all` 的职责,
    两边都建就成了两个写入者.
    """

    inspector = inspect(connection)
    added_columns: list[str] = []

    for table in sorted(metadata.sorted_tables, key=lambda table: table.name):
        if not inspector.has_table(table.name):
            continue

        existing_column_names = {
            column_info["name"] for column_info in inspector.get_columns(table.name)
        }

        for column in table.columns:
            if column.name in existing_column_names:
                continue

            statement = _build_add_column_statement(connection, table.name, column)

            if statement is None:
                logger.warning(MISSING_DEFAULT_WARNING, table.name, column.name)
                continue

            connection.execute(text(statement))
            added_columns.append(f"{table.name}.{column.name}")

    return added_columns


def _build_add_column_statement(
    connection: Connection, table_name: str, column: Column[Any]
) -> str | None:
    """渲染一条 `ALTER TABLE ... ADD COLUMN`; 该列补不上时回 None."""

    rendered_column = str(CreateColumn(column).compile(dialect=connection.dialect))
    default_clause = _render_default_clause(connection, column)

    if default_clause is not None:
        rendered_column = f"{rendered_column} {default_clause}"
    elif not column.nullable and column.server_default is None:
        return None

    quoted_table_name = connection.dialect.identifier_preparer.quote(table_name)

    return ALTER_TABLE_ADD_COLUMN_TEMPLATE.format(
        table=quoted_table_name, column=rendered_column
    )


def _render_default_clause(connection: Connection, column: Column[Any]) -> str | None:
    """从模型的 Python 侧默认值渲染 `DEFAULT <literal>`; 渲染不出来时回 None.

    值经 `literal(...).compile(literal_binds=True)` 渲染, 不自己拼引号: 拼接是"值里有什么
    字符"这件事的一个隐藏前提, 而这里的值将来可能来自别处.

    服务端默认值不在此渲染——`CreateColumn` 已经把它写在列定义里了, 再补一次会得到两个
    `DEFAULT`. 可调用默认值 (`default=utc_now` 一类) 也回 None: 那是个 Python 函数, 不是能
    落进 DDL 的字面量, 假装它能落进去只会得到一个语义不同的默认值.
    """

    if column.server_default is not None:
        return None

    default_value = getattr(column.default, "arg", None)

    if not isinstance(default_value, RENDERABLE_DEFAULT_TYPES):
        return None

    rendered_value = literal(default_value).compile(
        dialect=connection.dialect, compile_kwargs={"literal_binds": True}
    )

    return f"DEFAULT {rendered_value}"
