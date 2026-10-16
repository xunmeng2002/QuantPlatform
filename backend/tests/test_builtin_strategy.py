"""内置策略的播种: 源码取引擎包, 参数模板随平台包, 播过一次就不再碰.

三件事各一条: 引擎包里没有那份源码时**不播种也不报错** (老版引擎包照样能起平台), 播出来的那条
对全体可见且归属管理员 (归属决定谁能改它), 以及重跑不改动它 —— 幂等靠常量 id, 不靠"表是不是
空的", 故管理员删掉它之后平台也不会把它变回来.
"""

import logging

import pytest
from sqlalchemy import select

from app.builtin_strategy import (
    BUILTIN_STRATEGY_CONFIGURATION_FILENAME,
    BUILTIN_STRATEGY_ENTRY_FILENAME,
    BUILTIN_STRATEGY_ID,
    BUILTIN_STRATEGY_NAME,
    ensure_builtin_strategy,
)
from app.catalog.database import PlatformDatabase
from app.catalog.enums import StrategyVisibility, UserType
from app.catalog.models import StrategyModel, StrategyVersionModel, UserModel
from app.clock import utc_now
from app.config import PlatformSettings


ENGINE_STRATEGY_SOURCE_TEXT = "raise SystemExit('用例里的内置策略源码只是个占位')\n"


def put_strategy_source_in_engine_package(settings: PlatformSettings) -> None:
    """把源码摆进引擎根, 让它看起来像一份带了内置策略的引擎包."""

    (settings.engine_root / BUILTIN_STRATEGY_ENTRY_FILENAME).write_text(
        ENGINE_STRATEGY_SOURCE_TEXT, encoding="utf-8"
    )


async def read_seeded_strategy(database: PlatformDatabase) -> StrategyModel | None:
    async with database.session_scope() as session:
        return await session.get(StrategyModel, BUILTIN_STRATEGY_ID)


async def read_seeded_versions(database: PlatformDatabase) -> list[StrategyVersionModel]:
    async with database.session_scope() as session:
        versions = await session.execute(
            select(StrategyVersionModel).where(
                StrategyVersionModel.strategy_id == BUILTIN_STRATEGY_ID
            )
        )

        return list(versions.scalars().all())


async def test_an_engine_package_without_the_script_seeds_nothing(
    database: PlatformDatabase, platform_settings: PlatformSettings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.builtin_strategy"):
        await ensure_builtin_strategy(database, platform_settings)

    assert await read_seeded_strategy(database) is None
    assert BUILTIN_STRATEGY_ENTRY_FILENAME in caplog.text


async def test_the_builtin_strategy_is_public_and_owned_by_an_admin(database: PlatformDatabase, platform_settings: PlatformSettings) -> None:
    put_strategy_source_in_engine_package(platform_settings)

    await ensure_builtin_strategy(database, platform_settings)

    strategy = await read_seeded_strategy(database)

    assert strategy is not None
    assert strategy.name == BUILTIN_STRATEGY_NAME
    assert strategy.visibility_type == StrategyVisibility.PUBLIC.value

    versions = await read_seeded_versions(database)

    assert len(versions) == 1

    version = versions[0]
    assert version.version_no == 1
    assert version.entry_filename == BUILTIN_STRATEGY_ENTRY_FILENAME
    assert version.config_filename == BUILTIN_STRATEGY_CONFIGURATION_FILENAME

    version_directory = platform_settings.user_library_root / version.storage_path

    assert (version_directory / BUILTIN_STRATEGY_ENTRY_FILENAME).read_text(
        encoding="utf-8"
    ) == ENGINE_STRATEGY_SOURCE_TEXT
    assert (version_directory / BUILTIN_STRATEGY_CONFIGURATION_FILENAME).is_file()

    async with database.session_scope() as session:
        owner = await session.get(UserModel, strategy.owner_user_id)

    assert owner is not None
    assert owner.user_type == UserType.ADMIN.value


async def test_a_second_seed_leaves_the_strategy_alone(database: PlatformDatabase, platform_settings: PlatformSettings) -> None:
    put_strategy_source_in_engine_package(platform_settings)

    await ensure_builtin_strategy(database, platform_settings)

    async with database.session_scope() as session:
        seeded_strategy = await session.get(StrategyModel, BUILTIN_STRATEGY_ID)

        assert seeded_strategy is not None

        seeded_strategy.deleted_at = utc_now()
        await session.commit()

    await ensure_builtin_strategy(database, platform_settings)

    strategy = await read_seeded_strategy(database)

    assert strategy is not None
    assert strategy.deleted_at is not None
    assert len(await read_seeded_versions(database)) == 1
