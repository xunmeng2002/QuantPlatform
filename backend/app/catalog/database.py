"""catalog 数据库的引擎与会话.

异步引擎 + AsyncSession. 建表走 create_all, **未上迁移工具**; 但新增列会在启动期自动补上
(见 `migrations.py`): `create_all` 对已存在的表一列都不改, 不补的话, 加一列模型就等于要求
重建库——而库里有真实运行记录与已落盘的产物时, 那不是一个可以随手付掉的代价. 其余的表结构
变更 (改类型 / 删列 / 加约束) 仍需重建库.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .migrations import add_missing_columns
from .models import Base


logger = logging.getLogger(__name__)


SQLITE_DIALECT = "sqlite"
SQLITE_MEMORY_DATABASE = ":memory:"
SQLITE_BUSY_TIMEOUT_MILLISECONDS = 5000


class PlatformDatabase:
    """catalog 数据库门面: 持有引擎, 提供会话作用域与建表."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine: AsyncEngine = create_async_engine(database_url, echo=False)
        self._session_factory = async_sessionmaker(
            bind=self._engine, expire_on_commit=False, class_=AsyncSession
        )

        self._configure_sqlite_connections()

    @property
    def engine(self) -> AsyncEngine:
        """底层异步引擎, 供测试与运维检查连接串."""

        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """会话工厂, 供不经 FastAPI 依赖的场景 (如启动期播种) 自开会话."""

        return self._session_factory

    def _configure_sqlite_connections(self) -> None:
        """逐连接打开外键校验并设置忙等超时.

        SQLite 默认不校验外键, 不显式打开则外键声明形同注释; 默认忙等为 0, 并发写入会直接
        抛 SQLITE_BUSY 而非等待.
        """

        if self._engine.dialect.name != SQLITE_DIALECT:
            return

        def _apply_pragmas(
            dbapi_connection: Any, connection_record: Any
        ) -> None:
            cursor = dbapi_connection.cursor()

            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
            finally:
                cursor.close()

        event.listen(self._engine.sync_engine, "connect", _apply_pragmas)

    def _create_database_directory(self) -> None:
        """SQLite 不会自建目录, 库文件所在目录缺失会在首次连接时报错."""

        database_file = make_url(self._database_url).database

        if not database_file or database_file == SQLITE_MEMORY_DATABASE:
            return

        Path(database_file).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """建表, 并给已存在的表补上模型新增的列.

        两步的次序不能反: 补列要在建表**之后**, 否则一张全新库里什么表都没有, 补列那一步
        会被"表不存在"全部跳过, 然后 `create_all` 把表连同新列一起建出来——结果一样, 但
        "先建后补"才让"新库"与"老库升级"走的是同一条路径.
        """

        self._create_database_directory()

        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            added_columns = await connection.run_sync(
                add_missing_columns, Base.metadata
            )

        if added_columns:
            logger.info(
                "已给既有表补上 %d 个新增列: %s",
                len(added_columns),
                ", ".join(added_columns),
            )

    async def close(self) -> None:
        """释放连接池."""

        await self._engine.dispose()

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        """会话作用域: 只管会话存活与异常回滚, 不代为提交.

        提交必须由写操作在调用点显式发起. 不在此处隐式提交, 是因为本作用域既作 FastAPI
        依赖也作启动期脚本用: 依赖的清理时机在响应生成之后, 在那里提交若失败已无法转成
        正常的错误响应. 显式提交还使"哪里写了库"在代码里一眼可见.
        """

        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
