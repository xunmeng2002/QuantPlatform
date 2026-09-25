"""启动期播种.

首次运行建一个管理员账号. 口令必须由 QUANT_INITIAL_ADMIN_PASSWORD 显式给出: 源码里不留
默认口令, 也不把随机生成的口令打进日志——平台此刻还没有改口令的入口, 一旦打进日志就收不回.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from .auth.passwords import hash_password
from .catalog.database import PlatformDatabase
from .catalog.enums import UserStatus, UserType
from .catalog.models import UserModel
from .config import PlatformSettings
from .ids import generate_identifier


MISSING_INITIAL_PASSWORD_MESSAGE = (
    "库中没有管理员账号, 需设置环境变量 QUANT_INITIAL_ADMIN_PASSWORD 后重新启动以创建初始管理员"
)

logger = logging.getLogger(__name__)


async def ensure_initial_admin(
    database: PlatformDatabase, settings: PlatformSettings
) -> None:
    """确保库中至少有一个管理员账号; 已存在则原样返回."""

    async with database.session_scope() as session:
        existing_admin_count = await session.scalar(
            select(func.count())
            .select_from(UserModel)
            .where(UserModel.user_type == UserType.ADMIN.value)
        )

        if existing_admin_count:
            return

        if not settings.initial_admin_password:
            raise RuntimeError(MISSING_INITIAL_PASSWORD_MESSAGE)

        session.add(
            UserModel(
                id=generate_identifier(),
                username=settings.initial_admin_username,
                password_hash=hash_password(settings.initial_admin_password),
                display_name=settings.initial_admin_username,
                user_type=UserType.ADMIN.value,
                status=UserStatus.ACTIVE.value,
            )
        )

        await session.commit()

    logger.info("已创建初始管理员账号 %s", settings.initial_admin_username)
