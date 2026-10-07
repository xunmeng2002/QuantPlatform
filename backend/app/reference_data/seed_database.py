"""把 catalog 里的基本数据写成**这一轮**引擎要读的那个 SQLite 文件.

**它是派生物, 而且是按轮派生的**: catalog 是数据的家, 每一轮运行在自己的作业目录里得到一份
只含该轮合约的种子库 (`runs/<RunId>/BackTestInit.db`). 引擎只在启动时读它
(`SimExchange.cpp` 的 `LoadBasicDataFromInitDb`: 开库 → 抽干 → 关库), 从不写它.

**为什么不是全局一份**: 管理端存的是按合约 / 品种 / 交易所三级设置的规则, 而引擎的表里没有
「品种」这一档的位置 —— 通配必须由平台展开掉 (见 `rate_expansion`). 按轮展开的另一个好处是
种子库小得可以忽略: 只写该轮用到的那些合约, 不必把全市场铺进去.

三张表里 `Product` 与 `CommissionGroup` 整体照抄, `BaseCommission` 只写**展开之后**的行 ——
写入口对这一点有断言 (见 `_ensure_base_commissions_are_expanded`), 因为未展开的行在引擎那里
不是"多几行没用的数据", 而是"这一笔成交查不到费率, 费用静默按 0 算".

**列序是引擎面的硬契约**: 引擎 `SELECT *` 出来按列**下标**绑定, 列序一错就是静默错位 (把
`ExchangeId` 读成 `ProductId`), 引擎不报错, 只算错钱. 故这里逐列手写 DDL, 绝不走
`Base.metadata.create_all` —— 那会把 `Users` / `Runs` 等平台表一并写进种子库, 且平台的
`Id` 主键落在首列. 列序契约本身在 `seed_contract.py`, 那里也写着为什么它不能由 ORM 生成.

用**同步 sqlite3 + `asyncio.to_thread`**, 与本仓读外部库的既定做法一致
(`services/result_database.py`), 不为此引入 aiosqlite.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.models import (
    BaseCommissionModel,
    CommissionGroupModel,
    ProductModel,
)
from ..config import SEED_DATABASE_FILENAME
from ..ids import generate_identifier
from .rate_expansion import (
    RESOLUTION_DIRECTIONS,
    RateExpansion,
    RunContract,
    SeedRow,
    expand_rates_for_run,
    registered_product_codes,
)
from .seed_contract import (
    BASE_COMMISSION_TABLE,
    COMMISSION_GROUP_TABLE,
    PRODUCT_TABLE,
    SeedTable,
)


logger = logging.getLogger(__name__)


"""契约里的表名 ↔ 承载它的 ORM 模型.

配对按表名 (而不是按位置) 建立, 迭代的次序由契约给出 —— 契约是引擎面的事实, 模型只是它在
平台里的落脚处, 让平台侧的定义去决定导出次序就本末倒置了. 三对一一对应, 缺一个会在首次
读写时立刻报错 (见 `engine_attribute_names`).
"""

SEED_TABLE_MODELS: dict[str, Any] = {
    "Product": ProductModel,
    "CommissionGroup": CommissionGroupModel,
    "BaseCommission": BaseCommissionModel,
}


@dataclass(frozen=True)
class SeedRows:
    """一份种子库的全部内容: 三张表的行, 列序按契约.

    三张表装在同一个对象里而不拆成三个实参, 是因为它们要一起走完"读 → 展开 → 写": 展开只换
    `BaseCommission` 那一份 (`dataclasses.replace`), 另两份原样带过去.
    """

    products: tuple[SeedRow, ...]
    commission_groups: tuple[SeedRow, ...]
    base_commissions: tuple[SeedRow, ...]

    def product_codes(self) -> frozenset[tuple[str, str]]:
        """已登记品种码集合 —— 展开时判断某档是不是品种级的依据."""

        return registered_product_codes(self.products)


def build_create_table_statement(table: SeedTable) -> str:
    """按契约拼出一条 `CREATE TABLE`.

    表名与列名都来自契约 (编译期常量), 不是外部输入, 故直接拼进 SQL; 双引号照旧加上,
    `Order` 那类保留字在别的表上会咬人.
    """

    column_definitions = ", ".join(
        f"{column.name} {column.sqlite_type}" for column in table.columns
    )
    primary_key_columns = ", ".join(table.primary_key_columns)

    return (
        f'CREATE TABLE "{table.table_name}"'
        f"({column_definitions}, PRIMARY KEY({primary_key_columns}))"
    )


def _attribute_names_by_column_name(model: Any) -> dict[str, str]:
    """模型的 `数据库列名 → ORM 属性名` 对照.

    **不能拿 `Column.key` 当属性名**: `mapped_column("ExchangeId", String(8))` 显式给了列名,
    SQLAlchemy 便不再把列名改写成属性名, 于是 `column.key` 就是 `"ExchangeId"`, 而类上那个属性
    叫 `exchange_id`. 拿 `key` 去 `getattr(model, ...)` 会得到一句 "type object 'ProductModel'
    has no attribute 'ExchangeId'" —— 归因不到列名与属性名的这层错位上.

    走 mapper 的列属性: 它的 `key` 是**声明出来的属性名**, 它的 `columns[0].name` 是数据库列名.
    """

    return {
        column_property.columns[0].name: column_property.key
        for column_property in inspect(model).column_attrs
    }


def engine_attribute_names(table: SeedTable, model: Any) -> tuple[str, ...]:
    """把契约的列名翻译成该模型上的属性名, 保持契约的次序.

    按**列名**配对而不是按下标: ORM 还带着平台专有的 `Id` / `CreatedAt` / `UpdatedAt`, 按下标
    对就是拿两套不同的形状硬凑. 契约里有一列在模型上找不到时立刻抛错 —— 那意味着有人改了契约
    而没改模型, 而**错位的后果是静默算错钱**, 不是一条报错.

    这是本包唯一一处把"引擎列名"与"ORM 属性名"对上的地方, 导出 (`read_seed_rows`) 与
    播种 (`initial_rows`) 共用它: 两边各写一份的话, 一次列名订正只会改到其中一处, 而那一处
    会绿 (它自己也一致), 症状出在另一条路径上.
    """

    attribute_names_by_column_name = _attribute_names_by_column_name(model)

    attribute_names = []

    for seed_column in table.columns:
        attribute_name = attribute_names_by_column_name.get(seed_column.name)

        if attribute_name is None:
            raise LookupError(
                f"{model.__name__} 缺少契约要求的列 {table.table_name}.{seed_column.name}"
            )

        attribute_names.append(attribute_name)

    return tuple(attribute_names)


def _primary_key_attribute_names(table: SeedTable, model: Any) -> tuple[str, ...]:
    """契约主键列的属性名, 用于给导出定行序."""

    attribute_names_by_column_name = _attribute_names_by_column_name(model)

    return tuple(
        attribute_names_by_column_name[column_name]
        for column_name in table.primary_key_columns
        if column_name in attribute_names_by_column_name
    )


async def _read_table_rows(
    session: AsyncSession, table: SeedTable, model: Any, where_clause: Any = None
) -> tuple[SeedRow, ...]:
    """读出这张表的行, 列序按契约.

    行序按契约主键排: 同一份数据每次导出的字节因此相同, 文件之间可以直接比对 —— 一个会随
    查询计划变化的行序会让"重新生成"看不出差异, 也会让两次导出无法 diff.
    """

    attribute_names = engine_attribute_names(table, model)

    statement = select(*(getattr(model, name) for name in attribute_names))

    if where_clause is not None:
        statement = statement.where(where_clause)

    ordering_attributes = _primary_key_attribute_names(table, model)

    if ordering_attributes:
        statement = statement.order_by(
            *(getattr(model, name) for name in ordering_attributes)
        )

    result = await session.execute(statement)

    return tuple(tuple(row) for row in result.all())


async def read_seed_rows(
    session: AsyncSession, commission_group_id: int
) -> SeedRows:
    """读出这份种子库该有的行: 品种与手续费组整体, 费率只取**一个组**的.

    组号是这里的过滤条件 (而不是展开时的事): 引擎每轮只看一个组 (那一轮的 `CommissionGroupId`),
    把别组的行也读进来只会让展开时的查找表多出同键的干扰行. 过滤写进 SQL, 一处生效.

    单独一个函数而不在调用点各写一遍: 提交期校验与调度期生成必须读到**逐字相同**的那份输入,
    否则"提交时校验通过、跑起来却缺费率"这种事会在两者之间偷偷长出来.
    """

    return SeedRows(
        products=await _read_table_rows(
            session, PRODUCT_TABLE, SEED_TABLE_MODELS["Product"]
        ),
        commission_groups=await _read_table_rows(
            session, COMMISSION_GROUP_TABLE, SEED_TABLE_MODELS["CommissionGroup"]
        ),
        base_commissions=await _read_table_rows(
            session,
            BASE_COMMISSION_TABLE,
            SEED_TABLE_MODELS["BaseCommission"],
            where_clause=(
                SEED_TABLE_MODELS["BaseCommission"].commission_group_id
                == commission_group_id
            ),
        ),
    )


"""引擎认的方向就这两档 —— 展开器每格遍历的那两档, 不另立一份."""

ENGINE_RATE_DIRECTIONS = frozenset(int(direction) for direction in RESOLUTION_DIRECTIONS)

_COMMISSION_COLUMN_NAMES = BASE_COMMISSION_TABLE.column_names()

_INSTRUMENT_ID_INDEX = _COMMISSION_COLUMN_NAMES.index("InstrumentId")

_DIRECTION_INDEX = _COMMISSION_COLUMN_NAMES.index("Direction")


def _ensure_base_commissions_are_expanded(
    base_commission_rows: Sequence[SeedRow],
) -> None:
    """费率行必须是**展开过**的: 方向只许 0 / 1, 合约格不许为空.

    挡的是"某个调用方把 catalog 的行直接灌进来"这条路 —— 那类行带着平台专有的取值 (`-1` 的双向、
    品种码或空串的合约格), 而引擎按四列精确查找**一条都命不中**: 三列费用静默按 0 算, 回测照常
    "成功", `result.json` 里也只多一个不显眼的计数. 库里多几行查不到的数据本身不报错, 故只能在这
    里拦. 目前唯一的调用方 (`generate_run_seed_database`) 走的是展开器, 这道断言是为后来者留的.

    报第几行、哪一列取值不对, 不报路径 —— 路径是服务端的目录布局.
    """

    engine_directions = " / ".join(str(direction) for direction in sorted(ENGINE_RATE_DIRECTIONS))

    for row_number, row in enumerate(base_commission_rows, start=1):
        direction = int(row[_DIRECTION_INDEX])

        if direction not in ENGINE_RATE_DIRECTIONS:
            raise ValueError(
                f"费率第 {row_number} 行的 Direction 是 {direction}, "
                f"而引擎只认 {engine_directions}"
            )

        if not str(row[_INSTRUMENT_ID_INDEX]):
            raise ValueError(
                f"费率第 {row_number} 行的 InstrumentId 是空串, 这一行没有落到具体合约上"
            )


def _write_seed_database_file(
    database_path: Path,
    table_rows: tuple[tuple[SeedTable, tuple[SeedRow, ...]], ...],
) -> None:
    """在一个新建的 SQLite 文件里建三张表并灌行. 同步实现, 由调用方丢进线程."""

    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            for table, rows in table_rows:
                connection.execute(build_create_table_statement(table))

                if not rows:
                    continue

                placeholders = ", ".join("?" for _ in table.columns)

                connection.executemany(
                    f'INSERT INTO "{table.table_name}" VALUES ({placeholders})', rows
                )


async def write_seed_database(destination: Path, rows: SeedRows) -> None:
    """把这份行写成一个新的 SQLite 文件, 父目录不在时先建出来.

    目标已存在会以 `sqlite3.OperationalError` (表已存在) 收场, 那是调用方拿错了路径 —— 每次
    生成都该落在一个新文件上, 作业目录那一份就写在该轮刚建好的暂存目录里.

    行先过一遍 `_ensure_base_commissions_are_expanded`: 不合格时**连目录都不建**, 免得留下一份
    半成品让人以为"生成过了".
    """

    _ensure_base_commissions_are_expanded(rows.base_commissions)

    destination.parent.mkdir(parents=True, exist_ok=True)

    table_rows = (
        (PRODUCT_TABLE, rows.products),
        (COMMISSION_GROUP_TABLE, rows.commission_groups),
        (BASE_COMMISSION_TABLE, rows.base_commissions),
    )

    await asyncio.to_thread(_write_seed_database_file, destination, table_rows)


def expand_seed_rows(
    seed_rows: SeedRows,
    commission_group_id: int,
    contracts: Sequence[RunContract],
) -> RateExpansion:
    """把一份读好的种子数据里的费率三级展开掉. 纯函数: 不碰库, 也不碰文件.

    提交期的校验与调度期的生成都经它 —— 那一串实参 (哪三份行、按哪个组、展开到哪些合约) 只有
    这一份写法, 于是"提交时校验通过、跑起来却缺费率"没有地方可以长出来.
    """

    return expand_rates_for_run(
        seed_rows.base_commissions,
        commission_group_id,
        seed_rows.product_codes(),
        contracts,
    )


async def generate_run_seed_database(
    session: AsyncSession,
    destination: Path,
    commission_group_id: int,
    contracts: Sequence[RunContract],
) -> RateExpansion:
    """给这一轮生成种子库: 读 catalog → 展开三级规则 → 写到 `destination`.

    **缺费率不在这里拦**: 提交期与调度期的处置不同 (前者回 400 让用户补, 后者让这一轮失败),
    由调用方按返回的 `missing_rates` 决定. 文件照写 —— 调用方要么把它交付给这一轮, 要么删掉.
    """

    seed_rows = await read_seed_rows(session, commission_group_id)

    expansion = expand_seed_rows(seed_rows, commission_group_id, contracts)

    await write_seed_database(
        destination, replace(seed_rows, base_commissions=expansion.rows)
    )

    return expansion


SEED_DATABASE_STAGING_PREFIX = ".seed-"


def build_seed_database_staging_path(runs_root: Path) -> Path:
    """给这一轮的种子库取一个还没被占用的暂存路径.

    落在**运行根之内**: 作业目录构造那一步是把它**搬**进去 (`os.replace`), 而跨卷改名在
    Windows 上必然失败 —— 同根之内, 这一步不可能跨卷. 名字以 `.` 开头且带固定前缀: 运行根里
    排在最前面的那几个点开头的条目就是这类残留, 一眼能认出来.
    """

    return runs_root / (
        f"{SEED_DATABASE_STAGING_PREFIX}{generate_identifier()}-{SEED_DATABASE_FILENAME}"
    )


def discard_staged_seed_database(staging_path: Path) -> None:
    """删掉一个没能交付出去的暂存种子库.

    删不掉只记日志, 不抛: 它此刻只是垃圾, 而调用方正要处理的是真正的失败原因 —— 让删文件这一步
    的异常盖住那个原因, 是把归因弄丢.
    """

    try:
        staging_path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("暂存种子库文件未能删除: %s", error)


"""三张表, 次序取自 `BackTestInitDbTableList.h`; `SEED_TABLES` 与 `SEED_TABLE_MODELS` 必须覆盖
同一批表名, 由 `tests/test_seed_contract.py` 断言 —— 少一张就是静默漏掉一整张表."""
