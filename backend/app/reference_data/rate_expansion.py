"""费率规则的展开: 把 (合约 / 品种 / 交易所) 三档 × (双向 / 只买 / 只卖) 的规则解析成
**具体合约 × 具体方向**的行.

**为什么要有这一层**: 引擎查费率是拿 `(手续费组, 交易所, 合约, 方向)` 四列做**一次精确哈希
查找** (`CommissionCalculator::Apply`), `BaseCommission` 表里没有「品种」这一档的位置,
`Trade` 也不带 `ProductId` —— 通配语义**不可能只靠数据表达**. 于是平台在写种子库时把它展开掉:
引擎读到的永远是一份只有具体合约的小库, 查找次数不变、引擎零改动.

**作用域由「合约格」的取值承载**, 不另存一列 —— 存了就会有"作用域写着品种、合约格写着
`600519`"那种自相矛盾的行, 而两处真相迟早会分叉:

| 作用域 | `ExchangeId` | `InstrumentId` |
| :--- | :--- | :--- |
| 合约级 | `SSE` | `600519` |
| 品种级 | `SSE` | `600` (且 `(SSE, 600)` 已登记在品种表) |
| 交易所级 | `SSE` | *空串* |

解析次序是**从具体到宽泛**: 精确合约 → 品种 → 空串, 更具体的一档遮住更泛的一档 (给某只股票
单独录一条就压掉它所属品种的规则). 品种档的判据是「合约码的前缀」**且**「该前缀已登记在品种表」
两个条件同时成立: 品种码不靠猜长度 —— A 股是三位数字、期货是 1-4 位字母, 而引擎拿去查
`Product` 表的正是这一格, 登记与否是平台唯一知道的事实.

**方向那一格也有一档通配**, 同样由取值承载 —— 就是这张表的 `Direction` 列:

| 规则行的 `Direction` | 含义 |
| :--- | :--- |
| `-1` | 双向: 买卖共用这一套费率 (展开时各出一行) |
| `0` | 只买 |
| `1` | 只卖 |

两侧同时放宽时谁赢, 两条次序: **作用域先于方向** —— 更具体的一档遮住更宽的一档 (与上文同一条
承诺), 故合约级的「双向」压过品种级的「卖」; **同一格内, 精确方向遮住双向** —— 某合约上同时有
「双向」与「卖」两行时, 卖那一笔取「卖」那一行.

于是"**方向不回退**"这条承诺收窄成它本来该有的意思: 卖出的成交绝不会因为只录了**买**那一行就按
买的费率算 (买是另一档, 不是这一档的通配); 但它会按一条**明写了双向**的行算 —— 那是这条规则自己
说了"两边一样". `BaseCommission` 按方向分行, 印花税的买卖差异正是这么表达的: 缺哪一侧就是缺哪
一侧, 由调用方决定拦还是记.

**展开是整行照搬加两处键列改写**, 不做字段级合并: 命中哪一档就把那一行的十条费率列原样复制,
把**合约格**换成具体合约、**方向格**换成具体方向 (双向那一行到此摊成两行). 方向格非换不可 ——
`-1` 原样写进种子库就是一行**谁也查不到**的费率, 而症状是手续费静默按 0 算, 不是一条报错.
"合约级只覆盖佣金、印花税从交易所级继承"那种部分覆盖则会让一条规则最终的费率无法从它本身读出来.

本模块是**纯函数**: 不碰库、不碰文件系统, 进出的都是契约列序的元组 (见 `seed_contract`)。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass

from ..catalog.enums import CommissionDirection, RateDirection
from .seed_contract import BASE_COMMISSION_TABLE, PRODUCT_TABLE


SeedRow = tuple[object, ...]

"""交易所级的合约格是**空串**而不是某个占位符: 空串是这个表里的合法查找键, 引擎拿成交合约去
查时永远不会给出空串, 故它天然只可能被这里展开时用到."""

EXCHANGE_LEVEL_INSTRUMENT_ID = ""

"""双向那一档的取值. 与合约格的空串同理: 引擎拿成交方向去查时永远只给出 0 与 1, 故 `-1` 天然
只可能被这里展开时用到."""

BOTH_DIRECTIONS = int(RateDirection.BOTH)

"""每格都要**绑成具体方向**的那两档. 次序固定, 好让同一份输入的输出可逐字节比对.

**不要换成遍历 `RateDirection`**: 它的成员序是 `[BOTH, BUY, SELL]`, `-1` 在前 —— 拿它当遍历源
会把 `-1` 直接写进种子库 (引擎那一格永远查不到, 费用静默按 0 算), 还会连产物排序一起改掉."""

RESOLUTION_DIRECTIONS = (CommissionDirection.BUY, CommissionDirection.SELL)

DIRECTION_LABELS: dict[int, str] = {
    BOTH_DIRECTIONS: "双向",
    CommissionDirection.BUY: "买",
    CommissionDirection.SELL: "卖",
}


def _column_index(column_names: tuple[str, ...], column_name: str) -> int:
    """列名在契约里的下标. 契约里没有这一列时立刻抛 —— 那意味着契约改了而本模块没跟上."""

    try:
        return column_names.index(column_name)
    except ValueError:
        raise LookupError(f"{column_name} 不在契约的列序里") from None


_COMMISSION_COLUMN_NAMES = BASE_COMMISSION_TABLE.column_names()
_PRODUCT_COLUMN_NAMES = PRODUCT_TABLE.column_names()

_COMMISSION_GROUP_ID_INDEX = _column_index(_COMMISSION_COLUMN_NAMES, "CommissionGroupId")
_EXCHANGE_ID_INDEX = _column_index(_COMMISSION_COLUMN_NAMES, "ExchangeId")
_INSTRUMENT_ID_INDEX = _column_index(_COMMISSION_COLUMN_NAMES, "InstrumentId")
_DIRECTION_INDEX = _column_index(_COMMISSION_COLUMN_NAMES, "Direction")

_PRODUCT_EXCHANGE_ID_INDEX = _column_index(_PRODUCT_COLUMN_NAMES, "ExchangeId")
_PRODUCT_ID_INDEX = _column_index(_PRODUCT_COLUMN_NAMES, "ProductId")


@dataclass(frozen=True)
class RunContract:
    """该轮要展开费率的一对 (交易所, 合约).

    合约格为空是**正常**的: 那一轮不指名合约, 无从展开, 于是这一轮的费率表整个留空 (引擎若真
    有成交, `CommissionMissingCount` 会把它记下来). 不拿空串去查行 —— 引擎只在有具体合约时才
    查, 展开出一行空合约的费率谁也不会用到.
    """

    exchange_id: str
    instrument_id: str


@dataclass(frozen=True, order=True)
class MissingRate:
    """某一格 (合约 × 方向) 三档都没命中.

    可排序 (`order=True`): 报错文案与运行失败原因都要按固定次序写出来, 同一份输入才给得出同一
    句话. 字段次序即排序次序, 故声明次序是 `(交易所, 合约, 方向)`.
    """

    exchange_id: str
    instrument_id: str
    direction: int


@dataclass(frozen=True)
class RateExpansion:
    """一次展开的结果: 合约级的行, 以及仍然没解析出来的格."""

    rows: tuple[SeedRow, ...]
    missing_rates: tuple[MissingRate, ...]


def _lookup_key(row: SeedRow) -> tuple[str, str, int]:
    """一行费率的查找键 (交易所, 合约格, 方向) —— 组号不在这里, 一次展开只针对一个组."""

    return (
        str(row[_EXCHANGE_ID_INDEX]),
        str(row[_INSTRUMENT_ID_INDEX]),
        int(row[_DIRECTION_INDEX]),
    )


def _index_rows_by_lookup_key(
    base_commission_rows: Sequence[SeedRow], commission_group_id: int
) -> dict[tuple[str, str, int], SeedRow]:
    """把费率行收成查找表, 只收指定组的行.

    混进别组的行会让同一个键落到两行上, 而后者静默胜出 —— 那是算错钱, 不是报错. 故这里逐行核对
    组号, 不符即抛.
    """

    rows_by_key: dict[tuple[str, str, int], SeedRow] = {}

    for row in base_commission_rows:
        row_group_id = int(row[_COMMISSION_GROUP_ID_INDEX])

        if row_group_id != commission_group_id:
            raise ValueError(
                f"费率行属于 {row_group_id} 号组, 本表收的是 {commission_group_id} 号组"
            )

        rows_by_key[_lookup_key(row)] = row

    return rows_by_key


def _find_row_for_direction(
    rows_by_key: dict[tuple[str, str, int], SeedRow],
    exchange_id: str,
    instrument_id: str,
    direction: int,
) -> SeedRow | None:
    """某一格上**管这个方向**的那一行: 先找精确方向, 再找双向.

    次序不能反. 同一格上既写了「双向」又写了「卖」, 意思是"大体两边一样, 卖单独说" —— 故精确的
    那一行必须胜出; 反过来就成了界面上写着卖那一条、引擎按双向算.
    """

    exact_row = rows_by_key.get((exchange_id, instrument_id, direction))

    if exact_row is not None:
        return exact_row

    return rows_by_key.get((exchange_id, instrument_id, BOTH_DIRECTIONS))


def _find_product_level_row(
    rows_by_key: dict[tuple[str, str, int], SeedRow],
    product_codes: Set[tuple[str, str]],
    exchange_id: str,
    instrument_id: str,
    direction: int,
) -> SeedRow | None:
    """品种档: 合约码的**前缀**里登记在品种表、且这一档管得上这个方向的那一款, 取最长的前缀.

    从长到短试, 第一个命中的即最具体的那一档. 中间某档已登记但恰好缺这一方向时不驻足, 继续往
    短的试 —— 链上找的是**这一档管得上这个方向**的最具体的那一款 (有精确方向的行, 或有双向的
    行), 一条行都没有不是"到此为止", 而是"这一档没话说".
    """

    for prefix_length in range(len(instrument_id) - 1, 0, -1):
        candidate_product_id = instrument_id[:prefix_length]

        if (exchange_id, candidate_product_id) not in product_codes:
            continue

        row = _find_row_for_direction(
            rows_by_key, exchange_id, candidate_product_id, direction
        )

        if row is not None:
            return row

    return None


def _resolve_from_index(
    rows_by_key: dict[tuple[str, str, int], SeedRow],
    product_codes: Set[tuple[str, str]],
    exchange_id: str,
    instrument_id: str,
    direction: int,
) -> SeedRow | None:
    """三档依次解析 (每档内先精确方向、再双向), 命中即返回整行; 三档都没有时回 `None`."""

    cell_row = _find_row_for_direction(rows_by_key, exchange_id, instrument_id, direction)

    if cell_row is not None:
        return cell_row

    product_level_row = _find_product_level_row(
        rows_by_key, product_codes, exchange_id, instrument_id, direction
    )

    if product_level_row is not None:
        return product_level_row

    return _find_row_for_direction(
        rows_by_key, exchange_id, EXCHANGE_LEVEL_INSTRUMENT_ID, direction
    )


def resolve_effective_rate(
    base_commission_rows: Sequence[SeedRow],
    commission_group_id: int,
    product_codes: Set[tuple[str, str]],
    exchange_id: str,
    instrument_id: str,
    direction: int,
) -> SeedRow | None:
    """解析一格 (合约 × 方向) 最终生效的那一行; 三档都没命中时回 `None`.

    **返回的是命中的那条规则, 不是给引擎直接用的行**: 命中的可能是双向那一行, 于是方向格仍是
    `-1`、合约格仍可能是品种码或空串 —— 那正是"这条规则管着我问的这一格"这个事实。把它绑成具体
    合约与具体方向是 `expand_rates_for_run` 的事 (它才有"这一轮用哪些合约"的上下文)。逐个查的
    用法 (报错文案、人工核对) 走它; 一整轮的展开走 `expand_rates_for_run`, 那边只建一次查找表.
    """

    return _resolve_from_index(
        _index_rows_by_lookup_key(base_commission_rows, commission_group_id),
        product_codes,
        exchange_id,
        instrument_id,
        direction,
    )


def expand_rates_for_run(
    base_commission_rows: Sequence[SeedRow],
    commission_group_id: int,
    product_codes: Set[tuple[str, str]],
    contracts: Sequence[RunContract],
) -> RateExpansion:
    """把三档规则展开成一组具体合约的行, 并报出仍然没命中的格.

    输出按 `(交易所, 合约格, 方向)` 排序, 与导出种子库的定序一致 —— 同一份规则每次生成的文件因此
    逐字节相同, 两次导出可以直接 diff.
    """

    rows_by_key = _index_rows_by_lookup_key(base_commission_rows, commission_group_id)

    expanded_rows = []
    missing_rates = []

    for contract in _deduplicated_contracts(contracts):
        for direction in RESOLUTION_DIRECTIONS:
            resolved_row = _resolve_from_index(
                rows_by_key,
                product_codes,
                contract.exchange_id,
                contract.instrument_id,
                int(direction),
            )

            if resolved_row is None:
                missing_rates.append(
                    MissingRate(
                        exchange_id=contract.exchange_id,
                        instrument_id=contract.instrument_id,
                        direction=int(direction),
                    )
                )

                continue

            expanded_rows.append(
                _bind_row_to_contract(
                    resolved_row, contract.instrument_id, int(direction)
                )
            )

    return RateExpansion(
        rows=tuple(sorted(expanded_rows, key=_lookup_key)),
        missing_rates=tuple(sorted(missing_rates)),
    )


def _deduplicated_contracts(contracts: Sequence[RunContract]) -> tuple[RunContract, ...]:
    """去重, 并丢掉不指名合约的那些.

    同名两份展开出的行会撞主键, 而撞主键在库上是一条 `IntegrityError` —— 让这种输入在那里报错
    不如在这里收敛掉. 合约格为空的那些整个跳过, 理由见 `RunContract`.
    """

    seen_contract_keys: set[tuple[str, str]] = set()

    unique_contracts = []

    for contract in contracts:
        if not contract.instrument_id:
            continue

        contract_key = (contract.exchange_id, contract.instrument_id)

        if contract_key in seen_contract_keys:
            continue

        seen_contract_keys.add(contract_key)

        unique_contracts.append(contract)

    return tuple(unique_contracts)


def _bind_row_to_contract(row: SeedRow, instrument_id: str, direction: int) -> SeedRow:
    """整行照搬, 把两处**键列**改成这一格的具体取值: 合约格换成具体合约、方向格换成具体方向.

    方向格非改不可 —— 双向那一行 (`-1`) 原样写进种子库就是一行**谁也查不到**的费率, 症状是手续费
    静默按 0 算; 而两处都不改则会让同一个合约的买、卖两行撞主键 (那是另一条路: 库上当场报错).

    两格都已经是对的时**原样返回同一个对象** (`is` 相等): 调用方与测试因此仍能断言"这一行没被
    改写过", 而"改写过"这件事也照着它读出费率列之外的两列差异.
    """

    if row[_INSTRUMENT_ID_INDEX] == instrument_id and row[_DIRECTION_INDEX] == direction:
        return row

    bound_row = list(row)
    bound_row[_INSTRUMENT_ID_INDEX] = instrument_id
    bound_row[_DIRECTION_INDEX] = direction

    return tuple(bound_row)


def registered_product_codes(product_rows: Iterable[SeedRow]) -> frozenset[tuple[str, str]]:
    """品种表里已登记的那些 (交易所, 品种代码). 展开时判断"这一档是不是品种级"就靠它."""

    return frozenset(
        (str(row[_PRODUCT_EXCHANGE_ID_INDEX]), str(row[_PRODUCT_ID_INDEX]))
        for row in product_rows
    )


def describe_direction(direction: int) -> str:
    """方向 → 中文. 与前端 `describeCommissionDirection` 逐字一致."""

    return DIRECTION_LABELS.get(direction, str(direction))


def describe_missing_rates(missing_rates: Sequence[MissingRate]) -> str:
    """把没命中的格写成人读的一串, 形如 `SSE / 600519 买、SSE / 600519 卖`."""

    return "、".join(
        f"{missing_rate.exchange_id} / {missing_rate.instrument_id} "
        f"{describe_direction(missing_rate.direction)}"
        for missing_rate in missing_rates
    )
