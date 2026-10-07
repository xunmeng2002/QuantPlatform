"""种子库生成器: 按轮生成的内容、行过滤、以及暂存文件的下场.

这一层的关系是"catalog 是家, 产物是按轮的派生物". 故断言分三组: 生成出来的东西确实等于库里
(该轮的合约 × 展开后的费率); 组号过滤真的生效 (别组的行不能混进来); 以及暂存文件不会留下残渣.

**不再有"发布"这一步**: 文件写在调用方指定的位置 (作业目录的暂存区), 交付与丢弃由调用方负责,
故这里不再有发布失败、字节比对、启动期补生成那一类用例 —— 它们验的那条路已经不存在了.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.database import PlatformDatabase
from app.catalog.enums import CommissionDirection, ProductClass, RateDirection
from app.catalog.models import (
    BaseCommissionModel,
    CommissionGroupModel,
    ProductModel,
)
from app.config import SEED_DATABASE_FILENAME, PlatformSettings
from app.ids import generate_identifier
from app.reference_data.rate_expansion import RunContract
from app.reference_data.seed_contract import BASE_COMMISSION_TABLE
from app.reference_data.seed_database import (
    SEED_DATABASE_STAGING_PREFIX,
    build_seed_database_staging_path,
    discard_staged_seed_database,
    generate_run_seed_database,
    read_seed_rows,
    write_seed_database,
)
from .helpers import DEFAULT_COMMISSION_GROUP_ID, read_seed_database_rows


RUN_CONTRACT = RunContract(exchange_id="SSE", instrument_id="600519")

EXCHANGE_LEVEL_INSTRUMENT_ID = ""

REGISTERED_STOCK_PRODUCT_ID = "600"

"""平台侧的第三档方向 (双向). 库里收它, 引擎不认它 —— 故它**只能**在展开那一步被摊掉, 产物里
必须一行都不剩.
"""

BOTH_DIRECTIONS = int(RateDirection.BOTH)

"""费率表里两列的下标, **按列名从契约取**而不是写死数字.

契约里插一列时, 写死的那个下标会静默指到另一列上 —— 断言照旧跑, 只是验的东西换了. 只有这两列
按名字取: 下面几处断言看的就是它们, 其余列仍按整行比对.
"""

RATE_INSTRUMENT_ID_INDEX = BASE_COMMISSION_TABLE.column_names().index("InstrumentId")
RATE_DIRECTION_INDEX = BASE_COMMISSION_TABLE.column_names().index("Direction")

"""本用例生成到哪一个目录下. 取一个固定的轮次名而不是真去建一轮: 这里验的是生成器写完之
后的文件内容, 而路径的形态 (运行根 / 轮次 / 文件名) 由 `test_run_scheduler.py` 在真跑一轮时验.
"""

GENERATED_RUN_DIRECTORY_NAME = "generated-run"


def _build_product(
    exchange_id: str = "SSE",
    product_id: str = "600519",
    product_name: str = "贵州茅台",
) -> ProductModel:
    """造一行品种, 只换自然键.

    其余九列取值固定: 它们不是各用例要验的东西, 十一列逐处重复只会让"这个用例动的是哪一列"
    看不出来.
    """

    return ProductModel(
        id=generate_identifier(),
        exchange_id=exchange_id,
        product_id=product_id,
        product_name=product_name,
        product_class=ProductClass.STOCK.value,
        volume_multiple=1,
        price_tick=0.01,
        max_market_order_volume=100,
        min_market_order_volume=1,
        max_limit_order_volume=200,
        min_limit_order_volume=1,
        session_name="",
    )


def _build_commission_group(commission_group_id: int = 1) -> CommissionGroupModel:
    return CommissionGroupModel(
        id=generate_identifier(),
        commission_group_id=commission_group_id,
        commission_group_name="默认组",
    )


def _build_base_commission(
    instrument_id: str = "600519",
    direction: int = CommissionDirection.SELL,
    commission_group_id: int = 1,
) -> BaseCommissionModel:
    """造一行费率明细.

    方向收的是**裸 `int`** 而不是枚举: 库里这一列允许存平台侧的第三档 `双向` (`-1`), 它不在
    `CommissionDirection` 里 —— 收枚举就等于在造数据的入口上把那个取值先挡掉一半.

    那十列刻意取了十个**互不相同**的值: 其中任意两列弄反了, 期望值与实际值就会在某两个位置上
    互换 —— 而那正是引擎会遇到、且不会报错的错位形态 (它按列下标读).
    """

    return BaseCommissionModel(
        id=generate_identifier(),
        commission_group_id=commission_group_id,
        exchange_id="SSE",
        instrument_id=instrument_id,
        direction=int(direction),
        open_by_money=0.0003,
        close_by_money=0.0004,
        open_by_volume=0.0,
        close_by_volume=0.0,
        open_stamp_tax_by_money=0.0005,
        close_stamp_tax_by_money=0.0006,
        open_transfer_fee_by_money=0.00001,
        close_transfer_fee_by_money=0.00002,
        min_commission=5.0,
        max_commission=100.0,
    )


async def _clear_all_three_tables(session: AsyncSession) -> None:
    """清空三张表. 次序按外键的依赖反着来 (先明细后组).

    清空是为了让"文件里的内容"有一个**确定的**期望值: 三张表的初始化 CSV 也会在启动期播种
    (Product 两行), 不清的话期望值里就混着那份 CSV 的内容, 改一次 CSV 会连带弄红这里.
    """

    await session.execute(delete(BaseCommissionModel))
    await session.execute(delete(CommissionGroupModel))
    await session.execute(delete(ProductModel))


async def _add_one_of_each(session: AsyncSession) -> None:
    """放一行品种 (已登记的品种码 `600`)、一个手续费组、交易所级的两条费率; 前两张表之间冲一次盘.

    费率明细对组有外键, 而本仓不用 `relationship()`, 于是 SQLAlchemy 排不出这两张表的插入次
    序 (它按映射名排, `BaseCommissions` 恰好排在 `CommissionGroups` 前面). 冲这一下等于明说
    "组先落库". 生产侧同一条次序由 `initial_rows.ensure_initial_reference_data` 里的逐表 flush
    保证.
    """

    session.add(_build_product(product_id=REGISTERED_STOCK_PRODUCT_ID))
    session.add(_build_commission_group())

    await session.flush()

    for direction in CommissionDirection:
        session.add(
            _build_base_commission(
                instrument_id=EXCHANGE_LEVEL_INSTRUMENT_ID, direction=direction
            )
        )


def _generated_path(platform_settings: PlatformSettings) -> Path:
    return (
        platform_settings.runs_root
        / GENERATED_RUN_DIRECTORY_NAME
        / SEED_DATABASE_FILENAME
    )


async def _generate(
    session: AsyncSession,
    platform_settings: PlatformSettings,
    contracts: tuple[RunContract, ...] = (RUN_CONTRACT,),
) -> Path:
    destination = _generated_path(platform_settings)

    await generate_run_seed_database(
        session, destination, DEFAULT_COMMISSION_GROUP_ID, contracts
    )

    return destination


async def test_the_generated_file_carries_the_expanded_contract_rows(
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """交易所级的规则被展开成该轮合约的两行 (买、卖各一行), 且逐列等于库里那一行.

    展开掉的是**合约格**: 库里那一行的 `InstrumentId` 是空串 (交易所级), 文件里必须变成具体合约
    —— 引擎拿成交合约去查, 空串那一格它永远查不到.
    """

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        await _add_one_of_each(session)

        generated_path = await _generate(session, platform_settings)

    assert read_seed_database_rows(generated_path, "Product") == [
        ("SSE", REGISTERED_STOCK_PRODUCT_ID, "贵州茅台", 6, 1, 0.01, 100, 1, 200, 1, "")
    ]

    assert read_seed_database_rows(generated_path, "CommissionGroup") == [(1, "默认组")]

    assert read_seed_database_rows(generated_path, "BaseCommission") == [
        (
            1,
            "SSE",
            "600519",
            CommissionDirection.BUY.value,
            0.0003,
            0.0004,
            0.0,
            0.0,
            0.0005,
            0.0006,
            0.00001,
            0.00002,
            5.0,
            100.0,
        ),
        (
            1,
            "SSE",
            "600519",
            CommissionDirection.SELL.value,
            0.0003,
            0.0004,
            0.0,
            0.0,
            0.0005,
            0.0006,
            0.00001,
            0.00002,
            5.0,
            100.0,
        ),
    ]


async def test_a_both_directions_rule_reaches_the_file_as_two_engine_rows(
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """库里只录**一条双向**的费率, 文件里是买、卖两行, 且方向格只有 0 与 1.

    这一条是"平台侧的第三档不会漏进引擎面"的第二张网: 第一张在写库那一步 (`seed_database` 里
    按契约列序写文件前的那次断言), 它管得住任何调用方; 这里换成**从真生成的库往回读**, 跨过
    "有没有人绕过 `generate_run_seed_database` 直接调写函数"这一问.

    `-1` 一旦原样写进文件, 引擎拿成交去查那四列**一条都命不中**: 费用静默按 0 算, 而回测照常
    报"成功"—— 这是本次唯一会悄悄算错钱的路径, 故两个方向都点名断言, 不只数行数.
    """

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        session.add(_build_product(product_id=REGISTERED_STOCK_PRODUCT_ID))
        session.add(_build_commission_group())

        await session.flush()

        session.add(
            _build_base_commission(
                instrument_id=EXCHANGE_LEVEL_INSTRUMENT_ID,
                direction=BOTH_DIRECTIONS,
            )
        )

        generated_path = await _generate(session, platform_settings)

    generated_rows = read_seed_database_rows(generated_path, "BaseCommission")

    # 只看方向与合约那两列: 本用例管的是"第三档有没有漏出去、有没有落到具体合约上", 其余十二列的
    # 取值由上面那条用例逐列钉住.
    assert [row[RATE_DIRECTION_INDEX] for row in generated_rows] == [
        CommissionDirection.BUY.value,
        CommissionDirection.SELL.value,
    ]

    assert [row[RATE_INSTRUMENT_ID_INDEX] for row in generated_rows] == [
        "600519",
        "600519",
    ]


async def test_an_unexpanded_rate_row_is_refused_before_any_file_is_written(
    database: PlatformDatabase,
    tmp_path: Path,
) -> None:
    """把**没展开**的行直接交给写函数 → 当场抛, 且连那个目录都不建.

    这道断言管的是"绕过展开器的那条路": `write_seed_database` 是公开函数, 谁都可以把 catalog 里
    的行直接喂给它, 而带着 `-1` (双向) 或空合约格的行进了种子库, 引擎那四列精确查找**一条都命不
    中** —— 费用静默按 0 算, 回测照常"成功". 库里多几行查不到的数据**本身不报错**, 故只能在这里拦.

    先建目录再校验的实现会留下一份半成品, 故这里断言那个文件压根不存在.
    """

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        await _add_one_of_each(session)

        seed_rows = await read_seed_rows(session, DEFAULT_COMMISSION_GROUP_ID)

    unexpanded_rows = replace(
        seed_rows,
        base_commissions=tuple(
            (
                *row[:RATE_DIRECTION_INDEX],
                BOTH_DIRECTIONS,
                *row[RATE_DIRECTION_INDEX + 1 :],
            )
            for row in seed_rows.base_commissions
        ),
    )

    destination = tmp_path / GENERATED_RUN_DIRECTORY_NAME / SEED_DATABASE_FILENAME

    with pytest.raises(ValueError, match="Direction"):
        await write_seed_database(destination, unexpanded_rows)

    assert not destination.parent.exists()


async def test_a_run_without_a_contract_gets_an_empty_rate_table(
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """该轮没指名合约时, 费率表整个留空, 另两张表照抄.

    空合约无从展开 (见 `RunContract`): 引擎只在有具体合约时才查费率, 展开出一行空合约的费率谁也
    不会用到, 而它会以"这一轮配置错了"的面目出现在人工核对里.
    """

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        await _add_one_of_each(session)

        generated_path = await _generate(session, platform_settings, contracts=())

    assert read_seed_database_rows(generated_path, "BaseCommission") == []

    assert read_seed_database_rows(generated_path, "CommissionGroup") == [(1, "默认组")]


async def test_an_empty_table_is_written_as_an_empty_table(
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """库里的表是空的, 生成的文件里那张表也要在, 只是没有行.

    "没有行"与"没有表"对引擎不是一回事: 表缺失会让开库那一步失败, 整轮降级成无基本数据.
    """

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        generated_path = await _generate(session, platform_settings)

    assert read_seed_database_rows(generated_path, "Product") == []
    assert read_seed_database_rows(generated_path, "CommissionGroup") == []
    assert read_seed_database_rows(generated_path, "BaseCommission") == []


async def test_rows_of_another_commission_group_never_reach_the_file(
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """别组的费率行不进这个文件.

    读取那一步就在 SQL 里按组号过滤 (`read_seed_rows`): 引擎每轮只看一个组, 多读进来的行会让
    展开时的查找表多出同键的干扰行 —— 那不是报错, 是静默算错钱. 品种表不按组过滤 (它不分组),
    故这里断言它照旧整表带过去.
    """

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        await _add_one_of_each(session)

        session.add(_build_commission_group(commission_group_id=2))

        await session.flush()

        session.add(
            _build_base_commission(
                instrument_id=EXCHANGE_LEVEL_INSTRUMENT_ID,
                direction=CommissionDirection.BUY,
                commission_group_id=2,
            )
        )

        generated_path = await _generate(session, platform_settings)

    assert read_seed_database_rows(generated_path, "CommissionGroup") == [
        (1, "默认组"),
        (2, "默认组"),
    ]

    assert [
        row[0]
        for row in read_seed_database_rows(generated_path, "BaseCommission")
    ] == [1, 1]


async def test_missing_rates_are_reported_but_the_file_is_still_written(
    database: PlatformDatabase,
    platform_settings: PlatformSettings,
) -> None:
    """三档都没命中时, 缺失清单如实报出; 文件照写 (只是那两张表里没有这一轮的行).

    拦不拦由调用方决定 (提交期回 400, 调度期让这一轮失败), 故生成器本身不抛 —— 而它也不能悄悄
    跳过写文件: 交付与丢弃都发生在调用方那侧, 文件不存在会让"丢弃"变成一次无谓的报错.
    """

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        expansion = await generate_run_seed_database(
            session,
            _generated_path(platform_settings),
            DEFAULT_COMMISSION_GROUP_ID,
            (RUN_CONTRACT,),
        )

    assert _generated_path(platform_settings).exists()

    assert [
        (missing.exchange_id, missing.instrument_id, missing.direction)
        for missing in expansion.missing_rates
    ] == [
        ("SSE", "600519", CommissionDirection.BUY.value),
        ("SSE", "600519", CommissionDirection.SELL.value),
    ]

    assert read_seed_database_rows(_generated_path(platform_settings), "BaseCommission") == []


async def test_read_seed_rows_keeps_only_the_named_group(
    database: PlatformDatabase,
) -> None:
    """`read_seed_rows` 只取指定组的费率行 —— 提交期校验与调度期生成读的是同一份这一份."""

    async with database.session_scope() as session:
        await _clear_all_three_tables(session)

        await _add_one_of_each(session)

        session.add(_build_commission_group(commission_group_id=2))

        await session.flush()

        session.add(
            _build_base_commission(
                instrument_id=EXCHANGE_LEVEL_INSTRUMENT_ID,
                direction=CommissionDirection.BUY,
                commission_group_id=2,
            )
        )

        await session.flush()

        seed_rows = await read_seed_rows(session, DEFAULT_COMMISSION_GROUP_ID)

    assert {row[0] for row in seed_rows.base_commissions} == {DEFAULT_COMMISSION_GROUP_ID}
    assert len(seed_rows.base_commissions) == 2


def test_the_staging_path_sits_under_the_runs_root_with_a_recognisable_name(
    platform_settings: PlatformSettings,
) -> None:
    """暂存路径落在运行根**之内**.

    作业目录构造那一步是把它搬进作业目录 (`os.replace`), 而跨卷改名在 Windows 上必然失败 ——
    同根之内, 这一步不可能跨卷.
    """

    staging_path = build_seed_database_staging_path(platform_settings.runs_root)

    assert staging_path.parent == platform_settings.runs_root
    assert staging_path.name.startswith(SEED_DATABASE_STAGING_PREFIX)
    assert staging_path.name.endswith(SEED_DATABASE_FILENAME)


def test_discarding_a_staging_file_is_a_no_op_when_it_is_gone(
    tmp_path: Path,
) -> None:
    """文件已经不在时"丢弃"什么都不做, 也不抛.

    成功那一路就是这样收场的: 文件早被搬进作业目录, 随后那次清理是幂等的空操作 —— 它抛出来的话,
    一轮跑成功的作业会在收尾处变成失败.
    """

    staging_path = tmp_path / f"{SEED_DATABASE_STAGING_PREFIX}gone-{SEED_DATABASE_FILENAME}"

    staging_path.write_bytes(b"staged")

    discard_staged_seed_database(staging_path)

    assert not staging_path.exists()

    discard_staged_seed_database(staging_path)

    assert not staging_path.exists()
