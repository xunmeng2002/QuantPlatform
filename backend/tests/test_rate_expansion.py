"""三级费率规则的展开: 命中次序、方向不回退、前缀判据与产物形状.

本模块只测那个**纯函数**: 不建库、不起应用、也不写文件. 三级规则的全部语义都在这里, 而它错了
的症状是"费用静默按 0 算"或"算成了另一档的数"——两者都不会让回测失败, 故只能靠断言钉住.

行的取值刻意各不相同 (`rule_tag`): 命中哪一档, 从产物的那一列就能读出来, 不必在断言里重排
十四列. 列序一律经 `BASE_COMMISSION_TABLE` 取, 不手写下标 —— 契约改了列序时, 这里要么跟着走,
要么当场红.
"""

from __future__ import annotations

import pytest

from app.catalog.enums import CommissionDirection, RateDirection
from app.reference_data.rate_expansion import (
    EXCHANGE_LEVEL_INSTRUMENT_ID,
    MissingRate,
    RunContract,
    describe_missing_rates,
    expand_rates_for_run,
    registered_product_codes,
    resolve_effective_rate,
)
from app.reference_data.seed_contract import BASE_COMMISSION_TABLE, PRODUCT_TABLE

from .helpers import DEFAULT_COMMISSION_GROUP_ID


STOCK_CONTRACT = RunContract(exchange_id="SSE", instrument_id="600519")

STOCK_PRODUCT_CODE = "600"

FUTURES_CONTRACT = RunContract(exchange_id="SHFE", instrument_id="rb2401")

FUTURES_PRODUCT_CODE = "rb"

SECOND_GROUP_ID = 2

"""双向那一档的取值: 只在平台侧存在, 展开时必须被摊成 0 与 1."""

BOTH_DIRECTIONS = int(RateDirection.BOTH)

BASE_COMMISSION_COLUMN_NAMES = BASE_COMMISSION_TABLE.column_names()

PRODUCT_COLUMN_NAMES = PRODUCT_TABLE.column_names()


def _rate_row(
    exchange_id: str,
    instrument_id: str,
    direction: int,
    rule_tag: float = 1.0,
    commission_group_id: int = DEFAULT_COMMISSION_GROUP_ID,
) -> tuple[object, ...]:
    """按契约列序造一行费率, 数值列全部取 `rule_tag`.

    十条费率列里只有六条有量纲差异; 这里让它们统一带上 `rule_tag`, 于是"这一行是哪一条规则"是
    一个可以逐行读出来的事实.

    `direction` 收裸 `int` 而不是 `CommissionDirection`: 双向那一档 (`-1`) 不在引擎那个枚举里
    —— 它只在这一侧存在, 正是本模块要展开掉的东西之一.
    """

    column_values = dict.fromkeys(BASE_COMMISSION_COLUMN_NAMES, rule_tag)

    column_values.update(
        CommissionGroupId=commission_group_id,
        ExchangeId=exchange_id,
        InstrumentId=instrument_id,
        Direction=int(direction),
    )

    return tuple(column_values[column_name] for column_name in BASE_COMMISSION_COLUMN_NAMES)


def _product_row(exchange_id: str, product_id: str) -> tuple[object, ...]:
    """按契约列序造一行品种. 只有前两列 (交易所与品种代码) 参与展开时的判定."""

    column_values = dict.fromkeys(PRODUCT_COLUMN_NAMES, "")

    column_values.update(ExchangeId=exchange_id, ProductId=product_id)

    return tuple(column_values[column_name] for column_name in PRODUCT_COLUMN_NAMES)


def _as_record(row: tuple[object, ...]) -> dict[str, object]:
    """按契约列名把一行读成字典 —— 断言里就不必数第几列."""

    return dict(zip(BASE_COMMISSION_COLUMN_NAMES, row))


def _contract_rows(expansion) -> list[tuple[str, int, float, int]]:
    """展开结果里每行的 (合约格, 方向, 规则标记, 组号), 保持产物的次序."""

    return [
        (
            str(_as_record(row)["InstrumentId"]),
            int(_as_record(row)["Direction"]),
            float(_as_record(row)["OpenByMoney"]),
            int(_as_record(row)["CommissionGroupId"]),
        )
        for row in expansion.rows
    ]


def test_the_exact_contract_row_shadows_the_product_and_the_exchange_rows() -> None:
    """三档都在时, 合约级那一行胜出.

    这是"给某只股票单独录一条就压掉它所属品种的规则"这条承诺的唯一实现处.
    """

    contract_row = _rate_row("SSE", "600519", CommissionDirection.BUY, rule_tag=1.0)
    product_row = _rate_row("SSE", STOCK_PRODUCT_CODE, CommissionDirection.BUY, rule_tag=2.0)
    exchange_row = _rate_row(
        "SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, CommissionDirection.BUY, rule_tag=3.0
    )

    resolved_row = resolve_effective_rate(
        [exchange_row, product_row, contract_row],
        DEFAULT_COMMISSION_GROUP_ID,
        registered_product_codes([_product_row("SSE", STOCK_PRODUCT_CODE)]),
        "SSE",
        "600519",
        int(CommissionDirection.BUY),
    )

    assert resolved_row is contract_row


def test_a_registered_short_prefix_is_the_product_level() -> None:
    """合约级没录, 但品种档录了: 命中的是品种那一行."""

    product_row = _rate_row("SSE", STOCK_PRODUCT_CODE, CommissionDirection.BUY, rule_tag=2.0)
    exchange_row = _rate_row(
        "SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, CommissionDirection.BUY, rule_tag=3.0
    )

    resolved_row = resolve_effective_rate(
        [exchange_row, product_row],
        DEFAULT_COMMISSION_GROUP_ID,
        registered_product_codes([_product_row("SSE", STOCK_PRODUCT_CODE)]),
        "SSE",
        "600519",
        int(CommissionDirection.BUY),
    )

    assert resolved_row is product_row


def test_a_futures_contract_matches_its_letter_product_code() -> None:
    """期货 `rb2401` 与 A 股 `600519` 用同一套前缀规则: 品种码不靠猜长度, 靠品种表里的登记."""

    product_row = _rate_row("SHFE", FUTURES_PRODUCT_CODE, CommissionDirection.SELL, rule_tag=2.0)

    resolved_row = resolve_effective_rate(
        [product_row],
        DEFAULT_COMMISSION_GROUP_ID,
        registered_product_codes([_product_row("SHFE", FUTURES_PRODUCT_CODE)]),
        FUTURES_CONTRACT.exchange_id,
        FUTURES_CONTRACT.instrument_id,
        int(CommissionDirection.SELL),
    )

    assert resolved_row is product_row


def test_a_prefix_that_is_not_registered_is_not_a_product_level_match() -> None:
    """前缀没登记在品种表里时这一档不算命中, 直接落到交易所级那一行.

    判据是"登记与否"而不是"长度像不像品种码": 平台唯一知道的事实是品种表里有什么.
    """

    unregistered_prefix_row = _rate_row("SSE", "6005", CommissionDirection.BUY, rule_tag=2.0)
    exchange_row = _rate_row(
        "SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, CommissionDirection.BUY, rule_tag=3.0
    )

    resolved_row = resolve_effective_rate(
        [unregistered_prefix_row, exchange_row],
        DEFAULT_COMMISSION_GROUP_ID,
        registered_product_codes([_product_row("SSE", STOCK_PRODUCT_CODE)]),
        "SSE",
        "600519",
        int(CommissionDirection.BUY),
    )

    assert resolved_row is exchange_row


def test_a_registered_prefix_without_a_row_for_this_direction_keeps_looking_shorter() -> None:
    """已登记但恰好没录这一方向 (也没录双向) 时, 继续往短的前缀试, 不驻足.

    链上找的是"这一档管得上这个方向"的最具体那一款 —— 有精确方向的行, 或有双向的行. 驻足等于把
    这一条链当成"要么命中要么断掉", 于是"录了 `600` 的规则"会因为有人顺手登记过 `6005` 而整条
    失效.
    """

    longer_but_empty_prefix = "6005"
    shorter_product_row = _rate_row(
        "SSE", STOCK_PRODUCT_CODE, CommissionDirection.BUY, rule_tag=2.0
    )

    resolved_row = resolve_effective_rate(
        [shorter_product_row],
        DEFAULT_COMMISSION_GROUP_ID,
        registered_product_codes(
            [
                _product_row("SSE", longer_but_empty_prefix),
                _product_row("SSE", STOCK_PRODUCT_CODE),
            ]
        ),
        "SSE",
        "600519",
        int(CommissionDirection.BUY),
    )

    assert resolved_row is shorter_product_row


def test_a_direction_never_falls_back_to_the_other_direction() -> None:
    """卖出的成交绝不会因为只录了**买**那一行就按买的费率算 —— 买是另一档, 不是这一档的通配.

    库里的合约级只录了买, 品种级录了卖 —— 卖的必须命中的是**品种那一行**, 而不是合约的买行.
    印花税的买卖差异正是靠这条口径表达的.

    (会按卖算的只有一种: 那一行**明写了双向**. 见 `test_a_both_directions_row_covers_both_sides`.)
    """

    contract_buy_row = _rate_row("SSE", "600519", CommissionDirection.BUY, rule_tag=1.0)
    product_sell_row = _rate_row("SSE", STOCK_PRODUCT_CODE, CommissionDirection.SELL, rule_tag=2.0)

    base_commission_rows = [contract_buy_row, product_sell_row]
    product_codes = registered_product_codes([_product_row("SSE", STOCK_PRODUCT_CODE)])

    assert (
        resolve_effective_rate(
            base_commission_rows,
            DEFAULT_COMMISSION_GROUP_ID,
            product_codes,
            "SSE",
            "600519",
            int(CommissionDirection.SELL),
        )
        is product_sell_row
    )


def test_a_both_directions_row_covers_both_sides_and_is_bound_to_each_of_them() -> None:
    """只录一条双向, 买卖两格都命中, 且产物里方向格被摊成了 0 与 1.

    **产物里绝不能出现 -1**: 引擎那四列精确查找只认 0 与 1, 种子库里留一个 -1 等于这一行谁也
    查不到 —— 费用静默按 0 算, 而回测照常"成功". 故这里同时钉住两件事: 两格都被覆盖, 且方向格
    就是引擎认的那两个值.
    """

    both_row = _rate_row("SSE", "600519", BOTH_DIRECTIONS, rule_tag=7.0)

    expansion = expand_rates_for_run(
        [both_row], DEFAULT_COMMISSION_GROUP_ID, frozenset(), (STOCK_CONTRACT,)
    )

    assert _contract_rows(expansion) == [
        ("600519", int(CommissionDirection.BUY), 7.0, DEFAULT_COMMISSION_GROUP_ID),
        ("600519", int(CommissionDirection.SELL), 7.0, DEFAULT_COMMISSION_GROUP_ID),
    ]

    assert expansion.missing_rates == ()


def test_an_exact_direction_row_shadows_the_both_directions_row_in_the_same_cell() -> None:
    """同一格上既写了双向又写了卖时, 卖取「卖」那一行.

    反过来就成了"界面上写着卖那一条, 引擎按双向算". 买那一格只有双向可用, 正好把"逐格解析"
    这件事一并钉住.
    """

    both_row = _rate_row("SSE", "600519", BOTH_DIRECTIONS, rule_tag=7.0)
    sell_row = _rate_row("SSE", "600519", CommissionDirection.SELL, rule_tag=8.0)

    expansion = expand_rates_for_run(
        [both_row, sell_row], DEFAULT_COMMISSION_GROUP_ID, frozenset(), (STOCK_CONTRACT,)
    )

    assert _contract_rows(expansion) == [
        ("600519", int(CommissionDirection.BUY), 7.0, DEFAULT_COMMISSION_GROUP_ID),
        ("600519", int(CommissionDirection.SELL), 8.0, DEFAULT_COMMISSION_GROUP_ID),
    ]


def test_a_contract_level_both_directions_row_shadows_a_product_level_exact_row() -> None:
    """作用域先于方向: 合约级的「双向」压过品种级的「卖」.

    两处同时在放宽, 定序只能有一个赢家 —— 沿用"更具体的一档遮住更宽的一档"这条既有承诺, 于是
    给某只股票单独录一条双向, 就是给这一只单独定死.
    """

    product_sell_row = _rate_row(
        "SSE", STOCK_PRODUCT_CODE, CommissionDirection.SELL, rule_tag=2.0
    )
    contract_both_row = _rate_row("SSE", "600519", BOTH_DIRECTIONS, rule_tag=1.0)

    expansion = expand_rates_for_run(
        [product_sell_row, contract_both_row],
        DEFAULT_COMMISSION_GROUP_ID,
        registered_product_codes([_product_row("SSE", STOCK_PRODUCT_CODE)]),
        (STOCK_CONTRACT,),
    )

    assert _contract_rows(expansion) == [
        ("600519", int(CommissionDirection.BUY), 1.0, DEFAULT_COMMISSION_GROUP_ID),
        ("600519", int(CommissionDirection.SELL), 1.0, DEFAULT_COMMISSION_GROUP_ID),
    ]


def test_a_both_directions_row_at_a_longer_prefix_stops_the_walk() -> None:
    """更长的前缀上录了双向, 这一档就算"管得上这个方向", 搜索到此为止, 不再往短的试.

    与"缺这一方向就继续往短的试"是同一条判据的两面: 驻不驻足取决于这一档有没有话说.
    """

    longer_prefix_both_row = _rate_row("SSE", "6005", BOTH_DIRECTIONS, rule_tag=5.0)
    shorter_prefix_sell_row = _rate_row(
        "SSE", STOCK_PRODUCT_CODE, CommissionDirection.SELL, rule_tag=2.0
    )

    assert (
        resolve_effective_rate(
            [longer_prefix_both_row, shorter_prefix_sell_row],
            DEFAULT_COMMISSION_GROUP_ID,
            registered_product_codes(
                [_product_row("SSE", "6005"), _product_row("SSE", STOCK_PRODUCT_CODE)]
            ),
            "SSE",
            "600519",
            int(CommissionDirection.SELL),
        )
        is longer_prefix_both_row
    )


def test_resolve_effective_rate_hands_back_the_rule_row_it_matched() -> None:
    """逐个查的那个入口交回的是**命中的那条规则**, 它可能仍是方向格写着 `-1` 的双向行.

    它不是给引擎直接用的行 —— 只有 `expand_rates_for_run` 的产物才绑过具体合约与具体方向. 报错
    文案与人工核对要的正是"这条规则管着我问的这一格"这个事实, 故把这个契约钉住, 免得后来者以为
    拿到的一定是引擎侧那种行.
    """

    both_row = _rate_row("SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, BOTH_DIRECTIONS, rule_tag=7.0)

    assert (
        resolve_effective_rate(
            [both_row],
            DEFAULT_COMMISSION_GROUP_ID,
            frozenset(),
            "SSE",
            "600519",
            int(CommissionDirection.SELL),
        )
        is both_row
    )


def test_a_direction_missing_everywhere_is_reported_alone() -> None:
    """只有买录了时, 报出来的缺失清单里只有卖这一格 —— 不是"这个合约缺费率". """

    expansion = expand_rates_for_run(
        [_rate_row("SSE", "600519", CommissionDirection.BUY, rule_tag=1.0)],
        DEFAULT_COMMISSION_GROUP_ID,
        frozenset(),
        (STOCK_CONTRACT,),
    )

    assert expansion.missing_rates == (
        MissingRate(
            exchange_id="SSE",
            instrument_id="600519",
            direction=int(CommissionDirection.SELL),
        ),
    )


def test_three_missing_levels_report_both_directions_in_a_stable_order() -> None:
    """三档都没录时, 两格都报出来, 且次序固定 (交易所, 合约, 方向) —— 报错文案因此逐字可预期."""

    expansion = expand_rates_for_run(
        [], DEFAULT_COMMISSION_GROUP_ID, frozenset(), (STOCK_CONTRACT,)
    )

    assert expansion.rows == ()

    assert expansion.missing_rates == (
        MissingRate(
            exchange_id="SSE",
            instrument_id="600519",
            direction=int(CommissionDirection.BUY),
        ),
        MissingRate(
            exchange_id="SSE",
            instrument_id="600519",
            direction=int(CommissionDirection.SELL),
        ),
    )


def test_expansion_replaces_the_two_key_cells_and_keeps_the_rest_of_the_row() -> None:
    """整行照搬, 两处**键列**换成具体取值: 合约格换合约, 组号与十条费率列一个都不动.

    不做字段级合并是刻意的: "合约级只覆盖佣金、印花税从交易所级继承"那种部分覆盖会让一条规则
    最终的费率无法从它本身读出来.
    """

    exchange_buy_row = _rate_row(
        "SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, CommissionDirection.BUY, rule_tag=3.0
    )
    exchange_sell_row = _rate_row(
        "SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, CommissionDirection.SELL, rule_tag=4.0
    )

    expansion = expand_rates_for_run(
        [exchange_sell_row, exchange_buy_row], DEFAULT_COMMISSION_GROUP_ID, frozenset(), (STOCK_CONTRACT,)
    )

    assert _contract_rows(expansion) == [
        (STOCK_CONTRACT.instrument_id, int(CommissionDirection.BUY), 3.0, DEFAULT_COMMISSION_GROUP_ID),
        (STOCK_CONTRACT.instrument_id, int(CommissionDirection.SELL), 4.0, DEFAULT_COMMISSION_GROUP_ID),
    ]

    assert expansion.missing_rates == ()


def test_expansion_skips_contracts_without_an_instrument_and_deduplicates_the_rest() -> None:
    """不指名合约的那些整个跳过, 重复的合约只展开一次.

    名字重复的两份展开出的行会撞主键, 而撞主键在库上是一条 `IntegrityError`; 空合约则不可能被
    引擎查到 (它拿成交合约去查). 两者都在这里收敛掉, 下游不必各判一次.
    """

    expansion = expand_rates_for_run(
        [
            _rate_row("SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, CommissionDirection.BUY, rule_tag=3.0),
            _rate_row("SSE", EXCHANGE_LEVEL_INSTRUMENT_ID, CommissionDirection.SELL, rule_tag=3.0),
        ],
        DEFAULT_COMMISSION_GROUP_ID,
        frozenset(),
        (
            STOCK_CONTRACT,
            RunContract(exchange_id="SSE", instrument_id=EXCHANGE_LEVEL_INSTRUMENT_ID),
            STOCK_CONTRACT,
            RunContract(exchange_id="SSE", instrument_id="600036"),
        ),
    )

    assert [
        (instrument_id, direction)
        for instrument_id, direction, _, _ in _contract_rows(expansion)
    ] == [
        ("600036", int(CommissionDirection.BUY)),
        ("600036", int(CommissionDirection.SELL)),
        ("600519", int(CommissionDirection.BUY)),
        ("600519", int(CommissionDirection.SELL)),
    ]


def test_a_row_that_belongs_to_another_group_is_rejected() -> None:
    """混进别组的行立刻抛, 不静默胜出.

    展开只针对一个组 (引擎每轮只看一个组). 容忍别组的行会让同一个键落到两行上, 而后者静默胜出
    —— 那是算错钱, 不是报错.
    """

    with pytest.raises(ValueError):
        expand_rates_for_run(
            [
                _rate_row(
                    "SSE",
                    EXCHANGE_LEVEL_INSTRUMENT_ID,
                    CommissionDirection.BUY,
                    commission_group_id=SECOND_GROUP_ID,
                )
            ],
            DEFAULT_COMMISSION_GROUP_ID,
            frozenset(),
            (STOCK_CONTRACT,),
        )


def test_a_contract_only_row_does_not_leak_to_another_contract() -> None:
    """只录了 `600519` 的合约级行时, 另一只合约两格都缺.

    展开不是"把已有的行复制给所有合约": 只有真的命中某一档才复制.
    """

    expansion = expand_rates_for_run(
        [_rate_row("SSE", "600519", CommissionDirection.BUY, rule_tag=1.0)],
        DEFAULT_COMMISSION_GROUP_ID,
        frozenset(),
        (RunContract(exchange_id="SSE", instrument_id="600036"),),
    )

    assert expansion.rows == ()

    assert [missing.instrument_id for missing in expansion.missing_rates] == [
        "600036",
        "600036",
    ]


def test_registered_product_codes_reads_the_product_rows() -> None:
    """品种集来自品种表那几行 —— 展开时判断"这一档是不是品种级"就靠它."""

    assert registered_product_codes(
        [
            _product_row("SSE", STOCK_PRODUCT_CODE),
            _product_row("SHFE", FUTURES_PRODUCT_CODE),
        ]
    ) == frozenset(
        {("SSE", STOCK_PRODUCT_CODE), ("SHFE", FUTURES_PRODUCT_CODE)}
    )


def test_missing_rates_are_described_with_the_contract_and_the_direction() -> None:
    """报错文案形如 `SSE / 600519 买、SSE / 600519 卖` —— 用户据此知道去补哪一条."""

    assert (
        describe_missing_rates(
            [
                MissingRate("SSE", "600519", int(CommissionDirection.BUY)),
                MissingRate("SSE", "600519", int(CommissionDirection.SELL)),
            ]
        )
        == "SSE / 600519 买、SSE / 600519 卖"
    )
