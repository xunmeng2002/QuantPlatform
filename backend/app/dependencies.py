"""FastAPI 侧的基础依赖: 配置、数据库门面与会话.

应用级对象在装配时挂到 app.state, 依赖函数只做取出, 不在请求期构造.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, TypeAlias

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .catalog.database import PlatformDatabase
from .config import PlatformSettings


def get_settings(request: Request) -> PlatformSettings:
    """取装配时挂上的配置."""

    return request.app.state.settings


def get_database(request: Request) -> PlatformDatabase:
    """取装配时挂上的数据库门面."""

    return request.app.state.database


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """每请求一个会话.

    会话只管存活与异常回滚, 不代为提交: 写操作须在调用点显式 commit, 使"哪里写了库"
    在代码里一眼可见.
    """

    database: PlatformDatabase = request.app.state.database

    async with database.session_scope() as session:
        yield session


SettingsDependency: TypeAlias = Annotated[PlatformSettings, Depends(get_settings)]

DatabaseDependency: TypeAlias = Annotated[PlatformDatabase, Depends(get_database)]

SessionDependency: TypeAlias = Annotated[AsyncSession, Depends(get_session)]
