"""引擎配置的渲染.

本模块是**纯函数**: 只把运行级取值映射成配置文本, 不碰文件系统、不碰库、不起进程. 「写路径
必须相对」这条硬约束因此有了一个不必起进程就能测的落点 (见 `job-workspace.md` §2).

**绝对写路径在这里没有形参**: `DbHost` 与 `DumpPath` 是模块常量, 调用点想传也传不进来. 这条
隔离的暴露条件是并发 (两个作业写同一个库且不报错), 而测试默认并发为 1——靠测试盯不住它, 把它
变成"写不出来"才是可靠防线.

两个只读输入 (`MdDataPath` / `DbInitHost`) 反过来必须显式传入并保留绝对形态: 引擎相对**job
目录**解析它们, 写成相对值会立刻指到 job 目录里去.

渲染结果照抄引擎自带模板 `bin/Release/BackTest.json` 的键与次序, 含两个纯注释键
(`MatchModeType` / `DBTypeType`) 与两个空串 (`DbUser` / `DbPassword`): 成本为零, 收益是不必
去验证"引擎容忍它们缺席"——而这件事从未被验证过.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from ..catalog.enums import MarketDataType


ENGINE_CONFIG_FILENAME = "BackTest.json"
SESSION_FILENAME = "Sessions.json"
DUMP_DIRECTORY_NAME = "Dump"

RELATIVE_DATABASE_HOST = "./BackTest.db"
RELATIVE_DUMP_PATH = f"./{DUMP_DIRECTORY_NAME}"
RELATIVE_SESSION_FILE = f"./{SESSION_FILENAME}"

# 引擎侧的 DbType 是 int, 取值即 Spark `DbTypeType` 的枚举值 (对应关系见 DATABASE_TYPE_FIELD_HINT).
SQLITE_DATABASE_TYPE = 1
COMMISSION_GROUP_ID = 1

MATCH_MODE_FIELD_HINT = "OrderBook:0, LastPrice:1, OppositePrice:2, Bar:3"
DATABASE_TYPE_FIELD_HINT = "DuckDB:0, SqliteDB:1, MysqlDB:2, MariaDB:3"

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
    """

    return json.dumps(dict(configuration), indent=2) + "\n"


def render_engine_config(
    run_id: str,
    match_mode: MarketDataType,
    bar_period: str,
    start_trading_day: str,
    end_trading_day: str,
    initial_capital: float,
    market_data_path: Path,
    seed_database_path: Path,
) -> str:
    """渲染 `BackTest.json`.

    `RunId` 既是目录名也是引擎自己派生结果库名 (`BackTest_<RunId>.db`) 的依据, 故它就是平台
    生成的运行主键; `DbHost` 因此写 `./BackTest.db`, 由引擎拼后缀——平台若自己把 id 拼进去,
    会得到 `BackTest_<RunId>_<RunId>.db`, 不报错, 只是与 `result.json.DbPath` 对不上.
    """

    configuration = {
        "RunId": run_id,
        "MatchModeType": MATCH_MODE_FIELD_HINT,
        "MatchMode": resolve_match_mode(match_mode),
        "BarPreces": bar_period,
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
        "DbInitHost": str(seed_database_path),
        "InitialCapital": float(initial_capital),
        "CommissionGroupId": COMMISSION_GROUP_ID,
    }

    return serialize_configuration(configuration)
