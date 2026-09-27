"""只补新增列的小迁移.

`create_all` 对已存在的表一列都不改, 于是"加一列模型"与"库里的真实运行记录"直接冲突: 补不上
就只能重建库. 这里钉住补列本身, 以及它**拒绝**补的那些情形——拒绝得静默的话, 现场会变成一句
"表 X 没有列 Y"的英文报错, 与真正的原因隔着一层.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.catalog.migrations import add_missing_columns
from app.clock import utc_now


TABLE_NAME = "Widgets"
ABSENT_TABLE_NAME = "Gadgets"
EXISTING_ROW = ("a", "first")
ADDED_COLUMN_NAME = "EngineVersion"
DEFAULT_VALUE = ""
UNRENDERABLE_COLUMN_NAME = "Stamped"


def _declared_metadata() -> MetaData:
    """只声明 `Widgets` 的模型侧真相."""

    metadata = MetaData()

    Table(
        TABLE_NAME,
        metadata,
        Column("Id", String(32), primary_key=True),
        Column("Name", String(64), nullable=False, default=""),
        Column(ADDED_COLUMN_NAME, String(64), nullable=False, default=DEFAULT_VALUE),
    )

    return metadata


def _metadata_for_absent_table(*columns: Column[object]) -> MetaData:
    """只声明 `Gadgets` —— 用来把"补列"与"表不存在"两件事分开测."""

    metadata = MetaData()

    Table(
        ABSENT_TABLE_NAME,
        metadata,
        Column("Id", String(32), primary_key=True),
        *columns,
    )

    return metadata


def _legacy_engine() -> Engine:
    """一个"老库": `Widgets` 在, 已有数据, 但缺 `ADDED_COLUMN_NAME` 这一列."""

    engine = create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE TABLE {TABLE_NAME} ("
                "Id VARCHAR(32) NOT NULL PRIMARY KEY, "
                "Name VARCHAR(64) NOT NULL)"
            )
        )
        connection.execute(
            text(f"INSERT INTO {TABLE_NAME} (Id, Name) VALUES (:id, :name)"),
            {"id": EXISTING_ROW[0], "name": EXISTING_ROW[1]},
        )

    return engine


def _create_absent_table(connection: Connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE {ABSENT_TABLE_NAME} (Id VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
    )


def _column_names(connection: Connection, table_name: str) -> list[str]:
    return [
        column_info[1]
        for column_info in connection.execute(text(f"PRAGMA table_info({table_name})"))
    ]


def test_a_missing_column_is_added_and_existing_rows_get_the_default() -> None:
    """既有行必须拿到默认值: 补成 NULL 的话, 模型侧的非空声明与实际数据就对不上了."""

    engine = _legacy_engine()

    with engine.begin() as connection:
        added_columns = add_missing_columns(connection, _declared_metadata())

    assert added_columns == [f"{TABLE_NAME}.{ADDED_COLUMN_NAME}"]

    with engine.begin() as connection:
        assert _column_names(connection, TABLE_NAME) == [
            "Id",
            "Name",
            ADDED_COLUMN_NAME,
        ]
        assert list(
            connection.execute(
                text(f"SELECT Id, Name, {ADDED_COLUMN_NAME} FROM {TABLE_NAME}")
            )
        ) == [(*EXISTING_ROW, DEFAULT_VALUE)]


def test_the_added_column_carries_the_declared_constraints() -> None:
    """DDL 从模型渲染而非手写: 补出来的列必须与新建库时建出来的那一列同款."""

    engine = _legacy_engine()

    with engine.begin() as connection:
        add_missing_columns(connection, _declared_metadata())

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    f"INSERT INTO {TABLE_NAME} (Id, Name, {ADDED_COLUMN_NAME})"
                    " VALUES ('b', 'second', NULL)"
                )
            )


def test_running_it_twice_adds_nothing() -> None:
    """启动期每次都会跑它, 幂等是前提而不是优化."""

    engine = _legacy_engine()

    with engine.begin() as connection:
        add_missing_columns(connection, _declared_metadata())

    with engine.begin() as connection:
        assert add_missing_columns(connection, _declared_metadata()) == []


def test_a_freshly_created_database_has_nothing_to_backfill() -> None:
    """新库走的是 create_all 这条路, 补列那一步应当无事可做."""

    engine = create_engine("sqlite://")
    metadata = _declared_metadata()

    with engine.begin() as connection:
        metadata.create_all(connection)

        assert add_missing_columns(connection, metadata) == []


def test_a_table_absent_from_the_database_is_skipped() -> None:
    """建表是 `create_all` 的职责; 这里也建就成了两个写入者."""

    metadata = _metadata_for_absent_table(
        Column(ADDED_COLUMN_NAME, String(64), nullable=False, default=DEFAULT_VALUE)
    )

    engine = _legacy_engine()

    with engine.begin() as connection:
        assert add_missing_columns(connection, metadata) == []

    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT name FROM sqlite_master WHERE name = :name"),
            {"name": ABSENT_TABLE_NAME},
        ).first() is None


def test_a_not_null_column_without_a_renderable_default_is_left_alone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """可调用的默认值渲染不进 DDL, 故该列补不上——而**补不上时必须说出来**并点名.

    `default=utc_now` 是真实存在的情形 (`Runs.SubmittedAt` 就是), 静默跳过的话症状会推迟到
    第一次写入: 一句 "table X has no column named Y" 的英文报错, 而那时离"忘了重建库"这个
    原因已经很远了.
    """

    metadata = _metadata_for_absent_table(
        Column(UNRENDERABLE_COLUMN_NAME, DateTime, nullable=False, default=utc_now)
    )

    engine = _legacy_engine()

    with engine.begin() as connection:
        _create_absent_table(connection)

    with caplog.at_level(logging.WARNING), engine.begin() as connection:
        assert add_missing_columns(connection, metadata) == []

    assert ABSENT_TABLE_NAME in caplog.text
    assert UNRENDERABLE_COLUMN_NAME in caplog.text

    with engine.begin() as connection:
        assert _column_names(connection, ABSENT_TABLE_NAME) == ["Id"]


def test_a_nullable_column_is_added_without_needing_a_default() -> None:
    """SQLite 只对 NOT NULL 的新列要求默认值; 可空列照补不误."""

    metadata = _metadata_for_absent_table(
        Column(ADDED_COLUMN_NAME, String(64), nullable=True)
    )

    engine = _legacy_engine()

    with engine.begin() as connection:
        _create_absent_table(connection)

    with engine.begin() as connection:
        assert add_missing_columns(connection, metadata) == [
            f"{ABSENT_TABLE_NAME}.{ADDED_COLUMN_NAME}"
        ]
