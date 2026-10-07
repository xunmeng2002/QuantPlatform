"""`BackTest.json` 的渲染: 写路径必须相对, 读路径必须绝对.

这两条约束的暴露条件不对称. 读路径写错会**当场**失败 (`MdDataPath` 相对时引擎在 job 目录里找
不到行情, 一轮跑完 `BarMarketDataCount == 0`), 用例与真引擎验收都盯得住. 写路径写错则只在并发
下显形: 两个作业各写自己的作业目录、却指向同一个 `BackTest.db`, **谁都不报错**, 只是两份结果互相
覆盖——而测试默认并发为 1, 天然的用例覆盖不到它.

故这里对写路径的断言不止于"渲染结果是相对值", 还包含"调用点根本没有传入绝对写路径的机会":
`DbHost` / `DumpPath` 在函数里是常量, 形参表里没有对应入口. 少了这一条, 日后有人为了"让库文件
可配置"加一个形参, 上面那条渲染断言会跟着形参一起被改绿.

`BarPreces` 走的是同一条思路, 也是这个模块的第三条不变式: 它是**落盘精度** (平台常量), 而用户选
的那个周期是**策略的订阅目标**, 由引擎在读到 bar 之后自己聚合. 两者混为一个的症状不在渲染结果里
("渲染对了"与"渲染成另一个族"都只是一个字符串), 而在引擎装载期——它按 `Preces = '<用户选的>'`
去过滤 parquet, 磁盘上没有那一族, 于是整轮以 `ErrorMarketDataNotExist` 收场.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from app.catalog.enums import MarketDataType
from app.config import MARKET_DATA_PRECISION
from app.scheduler.engine_config import (
    DATABASE_TYPE_FIELD_HINT,
    MATCH_MODE_FIELD_HINT,
    RELATIVE_DATABASE_HOST,
    RELATIVE_DUMP_PATH,
    RELATIVE_SESSION_FILE,
    SQLITE_DATABASE_TYPE,
    SUBMITTABLE_MATCH_MODES,
    render_engine_config,
    resolve_match_mode,
    serialize_configuration,
)


RUN_ID = "6f1a0c2e4b8d49a8b3c5e7f901234567"
START_TRADING_DAY = "20241001"
END_TRADING_DAY = "20241231"
INITIAL_CAPITAL = 1000000.0
BAR_MATCH_MODE_VALUE = 3

# **刻意不取 1**: 组号从前是渲染器里的模块常量, 于是"配置里的那一格对不对"这件事, 断言写成
# `== 1` 与 `== 那个常量` 都对得上, 但两种写法都证明不了参数真的被写进去了 (改坏它、把 1 写死,
# 用例照样绿). 取一个只可能来自形参的值, 这条断言才有内容.
RENDERED_COMMISSION_GROUP_ID = 7

ENGINE_CONFIGURATION_KEYS = (
    "RunId",
    "MatchModeType",
    "MatchMode",
    "BarPreces",
    "MdDataPath",
    "DumpPath",
    "SessionFile",
    "StartTradingDay",
    "EndTradingDay",
    "DBTypeType",
    "DbType",
    "DbUser",
    "DbPassword",
    "DbHost",
    "DbInitHost",
    "InitialCapital",
    "CommissionGroupId",
)

RENDERER_PARAMETER_NAMES = (
    "run_id",
    "match_mode",
    "start_trading_day",
    "end_trading_day",
    "initial_capital",
    "commission_group_id",
    "market_data_path",
    "seed_database_path",
)

WINDOWS_DRIVE_SEPARATOR = ":"
PATH_SEPARATORS = ("/", "\\")


@pytest.fixture
def rendered_configuration(tmp_path: Path) -> dict[str, object]:
    """一份渲染结果, 读路径取临时目录里的绝对路径."""

    return json.loads(
        render_engine_config(
            run_id=RUN_ID,
            match_mode=MarketDataType.BAR,
            start_trading_day=START_TRADING_DAY,
            end_trading_day=END_TRADING_DAY,
            initial_capital=INITIAL_CAPITAL,
            commission_group_id=RENDERED_COMMISSION_GROUP_ID,
            market_data_path=tmp_path / "market-data",
            seed_database_path=tmp_path / "engine" / "BackTestInit.db",
        )
    )


def assert_is_a_relative_path(written_path: str) -> None:
    """写路径不许是绝对路径, 也不许带卷标或前导分隔符.

    逐项判而不是只判 `is_absolute()`: `"C:BackTest.db"` (盘符相对) 与 `"\\BackTest.db"` (根相对)
    在某些拼接方式下都会被引擎解析到作业目录之外, 而 `is_absolute()` 对前者为假.
    """

    assert written_path.startswith("./"), written_path
    assert not Path(written_path).is_absolute(), written_path
    assert WINDOWS_DRIVE_SEPARATOR not in written_path, written_path
    assert not written_path.startswith(PATH_SEPARATORS), written_path


def test_the_write_paths_are_relative(
    rendered_configuration: dict[str, object],
) -> None:
    """库、转储、会话表三处写路径 (或相对引用) 都落在作业目录之内."""

    assert rendered_configuration["DbHost"] == RELATIVE_DATABASE_HOST
    assert rendered_configuration["DumpPath"] == RELATIVE_DUMP_PATH
    assert rendered_configuration["SessionFile"] == RELATIVE_SESSION_FILE

    for written_key in ("DbHost", "DumpPath", "SessionFile"):
        assert_is_a_relative_path(str(rendered_configuration[written_key]))


def test_the_read_paths_are_absolute_and_verbatim(tmp_path: Path) -> None:
    """两个只读输入原样保留绝对形态.

    引擎相对 **job 目录** 解析 `MdDataPath` / `DbInitHost`, 写成相对值会立刻指到作业目录里去
    (那里只有作业自己那几个文件), 而引擎对"行情目录不存在"是安静降级. 故这两个值既不许被改写,
    也不许被规范化成别的形态.
    """

    market_data_path = tmp_path / "market-data"
    seed_database_path = tmp_path / "engine" / "BackTestInit.db"

    rendered_configuration = json.loads(
        render_engine_config(
            run_id=RUN_ID,
            match_mode=MarketDataType.BAR,
            start_trading_day=START_TRADING_DAY,
            end_trading_day=END_TRADING_DAY,
            initial_capital=INITIAL_CAPITAL,
            commission_group_id=RENDERED_COMMISSION_GROUP_ID,
            market_data_path=market_data_path,
            seed_database_path=seed_database_path,
        )
    )

    assert rendered_configuration["MdDataPath"] == str(market_data_path)
    assert rendered_configuration["DbInitHost"] == str(seed_database_path)
    assert Path(str(rendered_configuration["MdDataPath"])).is_absolute()
    assert Path(str(rendered_configuration["DbInitHost"])).is_absolute()


def test_the_renderer_takes_no_write_path_parameter() -> None:
    """形参表里没有写路径的入口.

    这条断言就是"写路径改不成绝对"的**实现本身**: 没有形参, 调用点就传不进来. 若日后有人加了
    一个 `database_host` 形参, 它会在这里转红, 而不是在生产环境里以"偶尔两份结果互相覆盖"的
    面目出现.
    """

    assert tuple(inspect.signature(render_engine_config).parameters) == (
        RENDERER_PARAMETER_NAMES
    )


def test_the_configuration_carries_exactly_the_engine_template_keys(
    rendered_configuration: dict[str, object],
) -> None:
    """键与次序照抄引擎自带模板, 含两个纯注释键与两个空串.

    `MatchModeType` / `DBTypeType` 是引擎模板里的取值提示注释, `DbUser` / `DbPassword` 对 SQLite
    为空串: 照抄的成本为零, 收益是不必去验证"引擎容忍它们缺席"——而这件事从未被验证过.
    """

    assert tuple(rendered_configuration) == ENGINE_CONFIGURATION_KEYS
    assert rendered_configuration["MatchModeType"] == MATCH_MODE_FIELD_HINT
    assert rendered_configuration["DBTypeType"] == DATABASE_TYPE_FIELD_HINT
    assert rendered_configuration["DbUser"] == ""
    assert rendered_configuration["DbPassword"] == ""
    assert rendered_configuration["DbType"] == SQLITE_DATABASE_TYPE


def test_the_run_identifier_lands_in_the_configuration_without_being_prefixed(
    rendered_configuration: dict[str, object],
) -> None:
    """`RunId` 就是平台生成的主键, 且**不**被提前拼进库文件名.

    引擎自己按 `RunId` 派生结果库名 (`BackTest_<RunId>.db`). 平台若好心先拼一份, 会得到
    `BackTest_<RunId>_<RunId>.db`——不报错, 只是与 `result.json.DbPath` 对不上, 而那个对不上
    会在真引擎验收时才被发现.
    """

    assert rendered_configuration["RunId"] == RUN_ID
    assert RUN_ID not in RELATIVE_DATABASE_HOST


def test_the_run_level_values_land_where_the_engine_reads_them(
    rendered_configuration: dict[str, object],
) -> None:
    """运行级取值逐项落到引擎读它们的键上.

    `BarPreces` 不在这里: 它是**平台常量**而不是运行级取值, 单独由下一条钉着.

    `CommissionGroupId` 与 `InitialCapital` 同一档: 它随轮冻结, 引擎按它去查费率. 写死的代价是
    管理端建的别的组永远不被用到, 而症状是一轮安静地按 0 计费的运行——故这里断言的是那个**只可能
    来自形参**的组号.
    """

    assert rendered_configuration["MatchMode"] == BAR_MATCH_MODE_VALUE
    assert rendered_configuration["StartTradingDay"] == START_TRADING_DAY
    assert rendered_configuration["EndTradingDay"] == END_TRADING_DAY
    assert rendered_configuration["InitialCapital"] == INITIAL_CAPITAL
    assert rendered_configuration["CommissionGroupId"] == RENDERED_COMMISSION_GROUP_ID


def test_the_dataset_bar_period_is_a_constant_the_caller_cannot_reach(
    rendered_configuration: dict[str, object],
) -> None:
    """引擎那份 `BarPreces` 恒为落盘精度, 且**形参表里没有任何入口**能改它.

    它决定引擎按 `Preces = '<值>'` 去读哪一族 parquet. 用户在提交页选的那个周期是**策略的订阅目标**
    (`TestStrategyGrid.json.BarPreces`), 由引擎在**读之后**聚合出来——把这两者混为一个时, 选 15m 会让
    引擎去读磁盘上不存在的 `15m` 那一族, 一行都取不到, 于是它在装载期以 `ErrorMarketDataNotExist`
    拒掉整轮, 白跑一次.

    **没有形参**才是这条不变式的实现本身, 与写路径那条同理: 留一个入口的话, 这里比一个常量照样是
    绿的——绿的却已经在读别的数据族.
    """

    assert rendered_configuration["BarPreces"] == MARKET_DATA_PRECISION
    assert "bar_period" not in inspect.signature(render_engine_config).parameters


def test_only_the_bar_match_mode_can_be_rendered() -> None:
    """Bar 映射到 3; Tick 没有收录值, 渲染它是内部错误.

    Tick 侧引擎有三档 (`OrderBook:0` / `LastPrice:1` / `OppositePrice:2`), 撮合价规则由那个 int
    决定, 而平台没有推得出那三档的信息. 猜一个的代价是静默跑出 0 成交——看起来完全正常.
    """

    assert resolve_match_mode(MarketDataType.BAR) == BAR_MATCH_MODE_VALUE
    assert SUBMITTABLE_MATCH_MODES == frozenset({MarketDataType.BAR})

    with pytest.raises(ValueError):
        resolve_match_mode(MarketDataType.TICK)


def test_a_non_ascii_value_survives_a_gbk_reader() -> None:
    """非 ASCII 取值落成 `\\uXXXX` 转义, 于是这份文件对读取方的编码假设免疫.

    中文 Windows 上 `open()` 默认按 GBK 解码, 一份 UTF-8 的中文配置会让策略在启动期直接抛异常
    ——而抛异常的是策略, 失败原因指向策略作者, 与平台无关.
    """

    serialized_configuration = serialize_configuration({"BarPreces": "中文周期"})

    assert serialized_configuration.isascii()
    assert "中文周期" not in serialized_configuration
    assert json.loads(serialized_configuration) == {"BarPreces": "中文周期"}
