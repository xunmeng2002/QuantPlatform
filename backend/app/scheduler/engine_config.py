"""引擎配置的渲染.

本模块是**纯函数**: 只把运行级取值映射成配置文本, 不碰文件系统、不碰库、不起进程. 「写路径
必须相对」这条硬约束因此有了一个不必起进程就能测的落点 (见 `job-workspace.md` §2).

**绝对写路径在这里没有形参**: `DbHost` 与 `DumpPath` 是模块常量, 调用点想传也传不进来. 这条
隔离的暴露条件是并发 (两个作业写同一个库且不报错), 而测试默认并发为 1——靠测试盯不住它, 把它
变成"写不出来"才是可靠防线.

唯一的绝对只读输入是 `MdDataPath`: 历史行情是只读的, 但它**不在 job 目录里** (在部署配置给出的
行情根下), 故只能显式传入并原样保留. `DbInitHost` **不在此列**——种子库由平台按轮生成并**放进
job 目录** (见 `workspace.py`), 于是它是一条活在 job 目录里的路径, 与两条写路径同形, 也是常量
`./BackTestInit.db`. 写成提交时算出的绝对路径同样跑得动, 但那条路径由**提交侧**拼、文件由**调度
侧**放, 两处各算一次迟早会分叉, 症状是引擎读不到种子库而整轮照跑完: 费用三项恒为 0、合约乘数全部
退化, 而 `result.json` 仍报 `Success: true`.

渲染结果照抄引擎自带模板 `bin/Release/BackTest.json` 的键与次序, 含两个纯注释键
(`MatchModeType` / `DBTypeType`) 与两个空串 (`DbUser` / `DbPassword`): 成本为零, 收益是不必
去验证"引擎容忍它们缺席"——而这件事从未被验证过.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from ..catalog.enums import MarketDataType
from ..config import MARKET_DATA_PRECISION, SEED_DATABASE_FILENAME
from ..errors import InvalidRequestError


logger = logging.getLogger(__name__)


ENGINE_CONFIG_FILENAME = "BackTest.json"
SESSION_FILENAME = "Sessions.json"
DUMP_DIRECTORY_NAME = "Dump"

RELATIVE_DATABASE_HOST = "./BackTest.db"
RELATIVE_DUMP_PATH = f"./{DUMP_DIRECTORY_NAME}"
RELATIVE_SESSION_FILE = f"./{SESSION_FILENAME}"
# 种子库是**读**路径, 但它活在 job 目录里, 故与上面三条同形: 相对值恰好指对, 而绝对路径会把
# "文件放哪"这件事从调度侧复制一份到提交侧 (见模块 docstring).
RELATIVE_SEED_DATABASE_HOST = f"./{SEED_DATABASE_FILENAME}"

# 引擎侧的 DbType 是 int, 取值即 Spark `DbTypeType` 的枚举值 (对应关系见 DATABASE_TYPE_FIELD_HINT).
SQLITE_DATABASE_TYPE = 1

MATCH_MODE_FIELD_HINT = "OrderBook:0, LastPrice:1, OppositePrice:2, Bar:3"
DATABASE_TYPE_FIELD_HINT = "DuckDB:0, SqliteDB:1, MysqlDB:2, MariaDB:3"

CONFIGURATION_NOT_SERIALIZABLE_MESSAGE = "配置里含无法写进 JSON 的数值 (NaN 或 Infinity)"

# 行情模式到引擎 `MatchMode` 的取值. 只收录 Bar: Tick 侧引擎有三档
# (OrderBook:0 / LastPrice:1 / OppositePrice:2), 撮合价规则由这个 int 决定, 而平台没有推得出
# 那三档的信息, 本机也没有 tick 行情数据可供验证. 未收录即拒绝提交, 不猜——猜错只会静默跑出
# 0 成交, 而那种结果看起来完全正常.
MATCH_MODE_VALUES = {MarketDataType.BAR: 3}

# 可提交的行情模式即"引擎取值已知"的那些: 两者是同一件事, 故由同一张表派生.
SUBMITTABLE_MATCH_MODES = frozenset(MATCH_MODE_VALUES)

# 反查表由同一张表翻转而来: 读回 `MatchMode` 时的对应关系因此不可能与写出去的那份不一致.
MARKET_DATA_TYPES_BY_ENGINE_VALUE = {
    engine_value: match_mode for match_mode, engine_value in MATCH_MODE_VALUES.items()
}


def resolve_market_data_type(engine_value: object) -> MarketDataType | None:
    """`MatchMode` 那个 int 反查回行情模式; 未收录或不是整数时回 `None`.

    回 `None` 而不是猜一个: 平台只提交得了 Bar, 库里出现别的取值说明这份配置不是本平台写的
    (或来自日后放开了 Tick 的版本). 让界面留默认值, 比替用户认领一个撮合规则安全得多.
    """

    # bool 是 int 的子类, 而 JSON 的 `true` 落到这里只可能是坏数据, 不能当 1 去查表.
    if isinstance(engine_value, bool) or not isinstance(engine_value, int):
        return None

    return MARKET_DATA_TYPES_BY_ENGINE_VALUE.get(engine_value)


def resolve_match_mode(match_mode: MarketDataType) -> int:
    """行情模式对应的引擎取值.

    提交侧已按 `SUBMITTABLE_MATCH_MODES` 拦过一道, 走到这里仍未收录说明两条路径的前提不一致
    ——那是缺陷, 不是调用方的输入问题, 故抛内部错误而不译成 400.
    """

    try:
        return MATCH_MODE_VALUES[match_mode]
    except KeyError:
        raise ValueError(f"未收录的行情模式: {match_mode}") from None


def serialize_configuration(configuration: Mapping[str, object]) -> str:
    """把一份配置序列化成落盘文本.

    `ensure_ascii` 保持默认的开启状态: 取值可能含用户输入的非 ASCII 字符, 而落成纯 ASCII 的
    `\\uXXXX` 转义后, 这份文件对读取方的编码假设免疫——中文 Windows 上 `open()` 默认按 GBK
    解码, 一份 UTF-8 的中文配置会让策略在启动期直接抛异常.

    `allow_nan=False` 必须显式给出: 标准 JSON 里没有 `NaN` / `Infinity`, 但 `json.loads`
    默认收得下它们, 于是一份手工构造的请求体能带一个非有限浮点穿过 pydantic 到这里, 再被写成
    `NaN`——那是**读侧解析不了**的一份文件, 而写的时候毫无动静. 拒在这里, 换一句 400.
    """

    try:
        return json.dumps(dict(configuration), indent=2, allow_nan=False) + "\n"
    except ValueError as error:
        logger.warning("配置含无法写进 JSON 的数值, 已拒绝: %s", error)
        raise InvalidRequestError(CONFIGURATION_NOT_SERIALIZABLE_MESSAGE) from error


def parse_configuration_object(
    source_id: str, configuration_text: str
) -> dict[str, object] | None:
    """把一份落库的配置文本解回字典; 读不动只记日志并回 `None`.

    与 `serialize_configuration` 成对放在这里: 同一个形状的读写各写一份的话, 改了一侧而另一侧
    没跟上, 症状是"写进去的配置读不回来", 而那正是配置文本唯一的用途.

    **回 `None` 而不抛**: 调用方拿到的都是"本来能读到"的数据 (提交页预填、模板列表), 一行坏
    数据不该把整页变成 500. `source_id` 只进日志, 不进响应.
    """

    try:
        parsed_configuration = json.loads(configuration_text)
    except json.JSONDecodeError as error:
        logger.warning("配置文本不是合法 JSON source=%s: %s", source_id, error)
        return None

    if not isinstance(parsed_configuration, dict):
        logger.warning("配置文本不是 JSON 对象 source=%s", source_id)
        return None

    return parsed_configuration


def render_engine_config(
    run_id: str,
    match_mode: MarketDataType,
    start_trading_day: str,
    end_trading_day: str,
    initial_capital: float,
    commission_group_id: int,
    market_data_path: Path,
) -> str:
    """渲染 `BackTest.json`.

    **`BarPreces` 没有形参**: 它恒等于 `MARKET_DATA_PRECISION`, 即落盘数据的精度. 这是那条
    "数据源精度不是订阅周期"的边界本身 —— 从前它收一个 `bar_period`, 于是用户在提交页选的
    周期被同时写进这里和策略配置, 而磁盘上只有 5m, 结果是一轮注定读不到行情、详情报着通用
    失败文案的运行. 改成常量之后, **没有任何调用点能把它写错**; 而策略侧的订阅周期由引擎在
    运行时聚合得到 (见 `BarAggregator`).

    `RunId` 既是目录名也是引擎自己派生结果库名 (`BackTest_<RunId>.db`) 的依据, 故它就是平台
    生成的运行主键; `DbHost` 因此写 `./BackTest.db`, 由引擎拼后缀——平台若自己把 id 拼进去,
    会得到 `BackTest_<RunId>_<RunId>.db`, 不报错, 只是与 `result.json.DbPath` 对不上.

    `CommissionGroupId` 有一个形参而**不是模块常量**: 引擎按
    `(CommissionGroupId, ExchangeId, InstrumentId, Direction)` 四元组精确查一张费率哈希表, 查不到
    只累加 `CommissionMissingCount` 而不报错——一个写死的组号会让"管理端建的 2 号、3 号组"永远
    不被任何一轮用到, 且症状是一轮安静地按 0 计费的运行. 它随轮冻结在**这一份**配置里, 于是
    提交期的费率校验与调度期按轮生成种子库读的都是同一个值.

    `DbInitHost` 没有形参, 与 `DbHost` 同理: 它是一条活在 job 目录里的路径 (见模块 docstring),
    调用点传进来的任何取值都只会是"另一处算出来的同一个位置".
    """

    configuration = {
        "RunId": run_id,
        "MatchModeType": MATCH_MODE_FIELD_HINT,
        "MatchMode": resolve_match_mode(match_mode),
        "BarPreces": MARKET_DATA_PRECISION,
        "MdDataPath": str(market_data_path),
        "DumpPath": RELATIVE_DUMP_PATH,
        "SessionFile": RELATIVE_SESSION_FILE,
        "StartTradingDay": start_trading_day,
        "EndTradingDay": end_trading_day,
        "DBTypeType": DATABASE_TYPE_FIELD_HINT,
        "DbType": SQLITE_DATABASE_TYPE,
        "DbUser": "",
        "DbPassword": "",
        "DbHost": RELATIVE_DATABASE_HOST,
        "DbInitHost": RELATIVE_SEED_DATABASE_HOST,
        "InitialCapital": float(initial_capital),
        "CommissionGroupId": int(commission_group_id),
    }

    return serialize_configuration(configuration)
