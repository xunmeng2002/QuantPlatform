"""内置策略: 随引擎包发布的策略源码, 首次启动时在库里立起一条对全体登录用户可见的策略.

与 `reference_data.initial_rows` 同性质 —— 都是"把一份随版本走的资产补进一张表" —— 但资产分住
两处, 因为这两半的性质不同:

  - **源码**住在引擎发布包里 (`<engine_root>/<策略源码文件名>`). 它是引擎的产物, 随引擎换版而
    换, 平台不另存副本, 免得同一份策略在两个仓里各漂一份.
  - **参数模板**住在平台包里 (`backend/builtin_strategies/`). 模板是平台与策略作者之间的契约
    (见 `strategy_configuration`), 而引擎侧那份同名配置里写着真实资金账号, 不能随包发出去.

**幂等以 id 为准**: id 是常量, 播过一次就不再碰. 管理员此后可以传新版本、改可见性、甚至删掉它,
平台都不会在下次启动时把它变回来 —— "有没有播过"靠 id 记着, 不靠"表是不是空的".

**归属人是管理员**: 写操作一律经 `load_owned_strategy` 收口, 故普通用户天然改不了也删不了它;
可见性设为 `public`, 让全体登录用户都能看见、能跑.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .catalog.database import PlatformDatabase
from .catalog.enums import StrategyVisibility, UserStatus, UserType
from .catalog.models import StrategyModel, UserModel
from .config import BUILTIN_STRATEGY_TEMPLATE_DIRECTORY, PlatformSettings
from .services.strategy_store import UploadedStrategyVersion, store_strategy_version
from .strategy_configuration import parse_configuration_template


logger = logging.getLogger(__name__)

# 常量 id 而不是随机主键: 它就是"这条策略是不是内置的"那唯一一处判据, 于是播种的幂等、以及
# 后续要按它取这条策略, 都只认这一处. 刻意不是 32 位十六进制, 让人一眼看出它不是普通策略.
BUILTIN_STRATEGY_ID = "builtin-grid-strategy"

BUILTIN_STRATEGY_NAME = "网格策略（内置）"

BUILTIN_STRATEGY_DESCRIPTION = "随引擎包发布的成对网格策略, 对全体登录用户可见"

# 引擎发布包里的策略源码文件名.
BUILTIN_STRATEGY_ENTRY_FILENAME = "grid_strategy.py"

# 平台这边的参数模板文件名. 与源码里 `open("TestStrategyGrid.json")` 的那个名字**必须一致**:
# 作业目录里落的就是它, 名字对不上即"跑起来了但读不到配置".
BUILTIN_STRATEGY_CONFIGURATION_FILENAME = "TestStrategyGrid.json"

BUILTIN_STRATEGY_TEMPLATE_FILENAME = "grid_strategy.json"


async def ensure_builtin_strategy(database: PlatformDatabase, settings: PlatformSettings) -> None:
    """把内置策略补进策略库; 已经播过就原样返回.

    引擎包里没有这份源码时**只记一条 warning 就返回**: 引擎包与平台是各自演进的, 老版引擎包
    少一个可选文件不该让平台起不来——与 `initial_rows` 的"空表才播"是同一条让步.
    """

    entry_path = settings.engine_root / BUILTIN_STRATEGY_ENTRY_FILENAME

    if not entry_path.is_file():
        logger.warning(
            "引擎目录下没有 %s, 本轮不播种内置策略: %s",
            BUILTIN_STRATEGY_ENTRY_FILENAME,
            settings.engine_root,
        )
        return

    configuration_text = (
        BUILTIN_STRATEGY_TEMPLATE_DIRECTORY / BUILTIN_STRATEGY_TEMPLATE_FILENAME
    ).read_text(encoding="utf-8")
    parse_configuration_template(configuration_text)

    async with database.session_scope() as session:
        if await session.get(StrategyModel, BUILTIN_STRATEGY_ID) is not None:
            return

        seeding_admin = await _find_seeding_admin(session)

        if seeding_admin is None:
            logger.warning("库中没有启用中的管理员账号, 本轮不播种内置策略")
            return

        strategy = StrategyModel(
            id=BUILTIN_STRATEGY_ID,
            owner_user_id=seeding_admin.id,
            name=BUILTIN_STRATEGY_NAME,
            description=BUILTIN_STRATEGY_DESCRIPTION,
            visibility_type=StrategyVisibility.PUBLIC.value,
        )
        session.add(strategy)
        await session.flush()

        await store_strategy_version(
            session,
            settings,
            strategy,
            seeding_admin,
            UploadedStrategyVersion(
                source_bytes=entry_path.read_bytes(),
                entry_filename=BUILTIN_STRATEGY_ENTRY_FILENAME,
                configuration_text=configuration_text,
                configuration_filename=BUILTIN_STRATEGY_CONFIGURATION_FILENAME,
            ),
        )

        logger.info(
            "已播种内置策略 %s (归属 %s, 可见性 %s)",
            strategy.name,
            seeding_admin.username,
            strategy.visibility_type,
        )


async def _find_seeding_admin(session: AsyncSession) -> UserModel | None:
    """播种的归属人: 最早建成的那个启用中的管理员.

    取"最早"而不是"任意一个": 换台机器重来时归属人始终落在同一个账号上, 不随查询顺序变.
    """

    return await session.scalar(
        select(UserModel)
        .where(UserModel.user_type == UserType.ADMIN.value)
        .where(UserModel.status == UserStatus.ACTIVE.value)
        .order_by(UserModel.created_at, UserModel.id)
        .limit(1)
    )
