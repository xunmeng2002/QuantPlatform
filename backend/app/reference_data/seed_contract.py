"""引擎种子库的列序契约 —— 平台侧唯一一份.

它是三处的共同依据: 生成器 (`seed_database.py`) 按它写 DDL 与插行, 初始化 CSV 的解析
(`initial_rows.py`) 按它校验表头并转换取值, 产物级测试 (`tests/test_seed_contract.py`) 按它
断言生成出来的文件.

**来源是引擎仓, 不是本仓的 ORM 模型**:

  - `QuantTrading/src/Mdb/MdbStructs.cpp` —— 字段描述数组给出列名与列序;
  - `QuantTrading/src/Mdb/MdbStructs.h` —— 结构体声明给出字段类型;
  - `QuantTrading/src/BackTest/BackTestInitDbTableList.h` —— 三张表的清单与次序;
  - `Spark/include/Spark/Types.h` —— 类型别名, 如 `ExchangeIdType = char[8]`、
    `GroupNameType = char[64]`.

**为什么不拿 ORM 生成**: 引擎按列**下标**绑定 —— 它把 `SELECT *` 出来的每一行按位置喂进
字段数组, 列序一错就是静默错位 (把 `ExchangeId` 读成 `ProductId`), 引擎不报错, 只算错钱.
而 ORM 的表是按 catalog 的用法设计的 (平台自造的 `Id` 主键、两个审计时间戳), 与引擎那张表
不是同一个东西; 拿 `Base.metadata` 去建库还会把 `Users` / `Runs` 等平台表一并写进种子库,
且 `Id` 落在首列 —— 正是上面那种错位. 故这里逐列手写.

列类型沿用 `QuantTrading/makeseeddb.py` 一直在写的那一套字面量 (`char(n)` / `int` / `bigint` /
`double`), 不换成 SQLite 的标准类型名: 那个文件生成的库引擎已实际读过, 形状是验证过的;
SQLite 的列类型只决定亲和性, 两边等价.

**已知维护缺口**: 引擎侧的 `MdbStructs.cpp` 由模板生成, 会漂移. 它改了而这里没跟上, 产物级
测试照样全绿 (测试比对的是本模块与生成物, 两边同源). 故手工验收里留了一步与引擎仓逐列对照,
见 `docs/acceptance-checklist.md`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedColumn:
    """引擎表里的一列: 列名与写入 DDL 的类型字面量."""

    name: str
    sqlite_type: str


@dataclass(frozen=True)
class SeedTable:
    """一张引擎基本数据表的完整形状: 列序、主键与表名.

    列序即引擎结构体的字段序, 也是列下标绑定的依据 —— 元组本身有序, 顺序就是契约的一部分.
    """

    table_name: str
    columns: tuple[SeedColumn, ...]
    primary_key_columns: tuple[str, ...]

    def column_names(self) -> tuple[str, ...]:
        """列名按序. 初始化 CSV 的表头必须逐字等于它."""

        return tuple(column.name for column in self.columns)


PRODUCT_TABLE = SeedTable(
    table_name="Product",
    columns=(
        SeedColumn("ExchangeId", "char(8)"),
        SeedColumn("ProductId", "char(32)"),
        SeedColumn("ProductName", "char(32)"),
        SeedColumn("ProductClass", "int"),
        SeedColumn("VolumeMultiple", "int"),
        SeedColumn("PriceTick", "double"),
        SeedColumn("MaxMarketOrderVolume", "bigint"),
        SeedColumn("MinMarketOrderVolume", "bigint"),
        SeedColumn("MaxLimitOrderVolume", "bigint"),
        SeedColumn("MinLimitOrderVolume", "bigint"),
        SeedColumn("SessionName", "char(32)"),
    ),
    primary_key_columns=("ExchangeId", "ProductId"),
)


COMMISSION_GROUP_TABLE = SeedTable(
    table_name="CommissionGroup",
    columns=(
        SeedColumn("CommissionGroupId", "int"),
        SeedColumn("CommissionGroupName", "char(64)"),
    ),
    primary_key_columns=("CommissionGroupId",),
)


BASE_COMMISSION_TABLE = SeedTable(
    table_name="BaseCommission",
    columns=(
        SeedColumn("CommissionGroupId", "int"),
        SeedColumn("ExchangeId", "char(8)"),
        SeedColumn("InstrumentId", "char(32)"),
        SeedColumn("Direction", "int"),
        SeedColumn("OpenByMoney", "double"),
        SeedColumn("CloseByMoney", "double"),
        SeedColumn("OpenByVolume", "double"),
        SeedColumn("CloseByVolume", "double"),
        SeedColumn("OpenStampTaxByMoney", "double"),
        SeedColumn("CloseStampTaxByMoney", "double"),
        SeedColumn("OpenTransferFeeByMoney", "double"),
        SeedColumn("CloseTransferFeeByMoney", "double"),
        SeedColumn("MinCommission", "double"),
        SeedColumn("MaxCommission", "double"),
    ),
    primary_key_columns=(
        "CommissionGroupId",
        "ExchangeId",
        "InstrumentId",
        "Direction",
    ),
)


"""三张表, 次序取自 `BackTestInitDbTableList.h`."""

SEED_TABLES = (PRODUCT_TABLE, COMMISSION_GROUP_TABLE, BASE_COMMISSION_TABLE)
