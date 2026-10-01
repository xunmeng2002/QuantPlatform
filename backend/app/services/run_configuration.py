"""运行配置的取值校验与渲染: 运行级字段与策略参数.

提交路径与模板路径**共用这一份判据**. 模板存下的是"将来要提交的那一套取值", 两边若各写一份
校验, 能存下的取值就与能提交的取值不是同一个集合——那种漂移的症状是"存得下、提交时 400",
且只在特定取值上出现. 故除"取哪个版本"与文件系统相关的那几道闸 (它们只对提交成立) 之外,
提交侧的判据全部收在这里.

本模块也是**两套词汇表之间的唯一那道桥**: API 与库用 snake_case (`bar_period`), 策略配置里用
引擎侧的 PascalCase (`BarPreces`). 对应关系集中在 `RUN_FIELD_KEY_NAMES` 一张表上, 渲染与解码
都按它走——于是"写进去的键"与"读回来的键"不可能不是同一个.

模块是**纯函数**: 不碰库、不碰文件系统. 于是"哪些取值能过"可以不起服务就逐条测.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

from ..catalog.enums import MarketDataType
from ..catalog.schemas import RunConfigurationRequest
from ..config import SUBSCRIPTION_BAR_PERIODS
from ..errors import InvalidRequestError
from ..scheduler.engine_config import (
    SUBMITTABLE_MATCH_MODES,
    resolve_market_data_type,
)
from ..strategy_configuration import (
    BAR_PERIOD_KEY_NAME,
    EXCHANGE_ID_KEY_NAME,
    INSTRUMENT_ID_KEY_NAME,
    PLATFORM_KEY_NAMES,
)


logger = logging.getLogger(__name__)

# 运行级字段名 (API 与库的 snake_case). 它们同时也是 `RunConfigurationRequest` 的字段名与
# `RunTemplateModel` 的具名列, 故只在这里声明一次.
EXCHANGE_ID_FIELD_NAME = "exchange_id"
INSTRUMENT_ID_FIELD_NAME = "instrument_id"
BAR_PERIOD_FIELD_NAME = "bar_period"

RUN_LEVEL_FIELD_NAMES = (
    EXCHANGE_ID_FIELD_NAME,
    INSTRUMENT_ID_FIELD_NAME,
    BAR_PERIOD_FIELD_NAME,
)

#: 运行级字段名 → 策略配置里的键名. 两套词汇表的全部对应关系就在这一张表上.
RUN_FIELD_KEY_NAMES: dict[str, str] = {
    EXCHANGE_ID_FIELD_NAME: EXCHANGE_ID_KEY_NAME,
    INSTRUMENT_ID_FIELD_NAME: INSTRUMENT_ID_KEY_NAME,
    BAR_PERIOD_FIELD_NAME: BAR_PERIOD_KEY_NAME,
}

# 引擎 `BackTest.json` 的键名. 它读的就是这些名字, 平台改一个字母就静默读不到那项取值.
# `BarPreces` 不在这里: 引擎那份是平台常量, 平台只写不读; 策略那份由 `strategy_configuration`
# 侧的同名常量管.
MATCH_MODE_KEY = "MatchMode"
START_TRADING_DAY_KEY = "StartTradingDay"
END_TRADING_DAY_KEY = "EndTradingDay"
INITIAL_CAPITAL_KEY = "InitialCapital"


TRADING_DAY_LENGTH = 8
MAXIMUM_RUN_FIELD_VALUE_LENGTH = 64

MAXIMUM_REPORTED_PARAMETER_KEYS = 10

MATCH_MODE_NOT_SUBMITTABLE_MESSAGE = "当前版本只支持 Bar 行情模式"
RUN_FIELD_REQUIRED_MESSAGE = "{field} 不能为空"
BAR_PERIOD_INVALID_MESSAGE = "bar_period 只能是 " + " / ".join(SUBSCRIPTION_BAR_PERIODS)
TRADING_DAY_ORDER_MESSAGE = "start_trading_day 不得晚于 end_trading_day"

RUN_FIELD_TOO_LONG_MESSAGE = "{field} 不得超过 {length} 个字符"
RUN_FIELD_INVALID_CHARACTERS_MESSAGE = "{field} 不得含控制字符"
TRADING_DAY_INVALID_MESSAGE = "{field} 须为 8 位数字"
INITIAL_CAPITAL_INVALID_MESSAGE = "initial_capital 须为大于 0 的有限数值"
UNKNOWN_PARAMETER_MESSAGE = "params 含该版本配置里没有的键: {keys}"
PLATFORM_PARAMETER_MESSAGE = "params 不得含平台按运行级字段写入的键: {keys}"
TRUNCATED_KEY_LIST_SUFFIX = " 等"


def normalize_run_field_value(field_name: str, raw_value: str | None) -> str | None:
    """运行级字段取值规范化: 去首尾空白, 空串即视为未提供, 并挡住过长与控制字符."""

    if raw_value is None:
        return None

    normalized_value = raw_value.strip()

    if not normalized_value:
        return None

    if len(normalized_value) > MAXIMUM_RUN_FIELD_VALUE_LENGTH:
        raise InvalidRequestError(
            RUN_FIELD_TOO_LONG_MESSAGE.format(
                field=field_name, length=MAXIMUM_RUN_FIELD_VALUE_LENGTH
            )
        )

    if any(ord(character) < 32 for character in normalized_value):
        raise InvalidRequestError(
            RUN_FIELD_INVALID_CHARACTERS_MESSAGE.format(field=field_name)
        )

    return normalized_value


def validate_trading_day(field_name: str, raw_value: str) -> str:
    """交易日须是 8 位数字, 且 `start <= end` 由调用方另判.

    只认 ASCII 数字: `str.isdigit()` 对阿拉伯-印度数字等也为真, 那种取值会一路写进引擎配置,
    而引擎拿到的是它读不懂的一串字符.
    """

    normalized_value = raw_value.strip()

    if len(normalized_value) != TRADING_DAY_LENGTH or not (
        normalized_value.isascii() and normalized_value.isdigit()
    ):
        raise InvalidRequestError(TRADING_DAY_INVALID_MESSAGE.format(field=field_name))

    return normalized_value


def validate_initial_capital(raw_value: float) -> float:
    """初始资金须为大于 0 的有限值.

    `NaN` / `Infinity` 挡在这里: JSON 里写不出它们, 但 pydantic 的 `float` 收得下, 而它们一旦
    进配置就会被引擎读成一个不可比的数——那轮结果再怎么看都正常, 只是永远算不对.
    """

    if not math.isfinite(raw_value) or raw_value <= 0:
        raise InvalidRequestError(INITIAL_CAPITAL_INVALID_MESSAGE)

    return float(raw_value)


def validate_trading_day_range(
    start_trading_day: str, end_trading_day: str
) -> tuple[str, str]:
    """校验两个交易日各自合法且 `start <= end`, 返回规范化后的取值.

    两件事合成一个入口: 分成两步的话, 调用方可以只做前一步, 而"起止倒置"那种提交会照常入队
    ——它跑出来的不是错误, 而是一轮空结果.
    """

    normalized_start = validate_trading_day("start_trading_day", start_trading_day)
    normalized_end = validate_trading_day("end_trading_day", end_trading_day)

    if normalized_start > normalized_end:
        raise InvalidRequestError(TRADING_DAY_ORDER_MESSAGE)

    return normalized_start, normalized_end


def validate_match_mode(requested_match_mode: MarketDataType) -> MarketDataType:
    """平台当前能提交哪些行情模式.

    只有 `Bar`. 从前这里还有第二道闸 (该策略声明支持哪些模式), 那是 manifest 的一部分; 随着
    manifest 整个摘掉, 平台没验证过的那三档 Tick 撮合方式**根本没有入口**——`SUBMITTABLE_MATCH_MODES`
    就是那唯一一道, 因为没有任何版本能声明出比它更宽的可提交集合.
    """

    if requested_match_mode not in SUBMITTABLE_MATCH_MODES:
        raise InvalidRequestError(MATCH_MODE_NOT_SUBMITTABLE_MESSAGE)

    return requested_match_mode


def resolve_run_field_values(
    submitted_values: RunConfigurationRequest,
) -> dict[str, str]:
    """三个运行级字段的**唯一一份**取值; 空值一律收成空串.

    三项现在**恒被收下**: 平台按固定键名把它们写进策略配置, 模板里有没有这几个键都一样.
    `exchange_id` / `instrument_id` 留空是正常的 (那一轮不指名合约, 行情准备会跳过), 而
    `bar_period` 必填且必须是订阅周期清单里的一项——它同时决定策略收到多粗的 bar, 填错的
    表现是从"策略读到一个不认识的周期"到"引擎装载期直接拒", 都不该等跑完才发现.
    """

    exchange_id = normalize_run_field_value(
        EXCHANGE_ID_FIELD_NAME, submitted_values.exchange_id
    )
    instrument_id = normalize_run_field_value(
        INSTRUMENT_ID_FIELD_NAME, submitted_values.instrument_id
    )
    bar_period = normalize_run_field_value(
        BAR_PERIOD_FIELD_NAME, submitted_values.bar_period
    )

    if bar_period is None:
        raise InvalidRequestError(
            RUN_FIELD_REQUIRED_MESSAGE.format(field=BAR_PERIOD_FIELD_NAME)
        )

    if bar_period not in SUBSCRIPTION_BAR_PERIODS:
        raise InvalidRequestError(BAR_PERIOD_INVALID_MESSAGE)

    return {
        EXCHANGE_ID_FIELD_NAME: exchange_id or "",
        INSTRUMENT_ID_FIELD_NAME: instrument_id or "",
        BAR_PERIOD_FIELD_NAME: bar_period,
    }


def validate_submitted_parameters(
    template: Mapping[str, object], submitted_parameters: Mapping[str, object]
) -> dict[str, object]:
    """提交的参数: 每一个键都必须是模板里已有的、且不是平台那三个.

    键集由**上传的那份文件**固定, 提交只有"改值"这一种权利. 于是"多传了一个键"不再是"多存一个
    参数", 而是一个明确的错——旧 manifest 靠声明表挡住了这一类, 现在挡住它的是模板本身.
    """

    # 撞平台键先判: 那种提交同时也会撞上"模板里没这个键" (模板通常不写那三个), 先说后者的话,
    # 用户只会看到"键不存在", 于是转头去改配置文件——而正确的动作是什么都不做.
    collided_keys = sorted(set(submitted_parameters) & set(PLATFORM_KEY_NAMES))

    if collided_keys:
        raise InvalidRequestError(
            PLATFORM_PARAMETER_MESSAGE.format(keys=_abbreviate_keys(collided_keys))
        )

    unknown_keys = sorted(set(submitted_parameters) - set(template))

    if unknown_keys:
        raise InvalidRequestError(
            UNKNOWN_PARAMETER_MESSAGE.format(keys=_abbreviate_keys(unknown_keys))
        )

    return dict(submitted_parameters)


def build_strategy_configuration(
    template: Mapping[str, object],
    run_field_values: Mapping[str, str],
    submitted_parameters: Mapping[str, object],
) -> dict[str, object]:
    """渲染策略配置: 模板 + 用户改过的参数 + 平台覆写的三个运行级键.

    三条性质都由这三行保证, 每条都值得一条用例:

    1. **从模板整份拷贝出发**, 不是从零拼起. 于是模板里平台控不了的键 (`null`、对象、数组)
       原样留下——旧 manifest 靠"声明期禁止参数键与运行级键撞名"来保证策略不丢键, 这里改成
       了结构性的: 没被显式改过的键, 其值就是文件里那个.
    2. **三个平台键是赋值而不是新增**, 故模板里本来就有的会被覆写、没有的会被追加, 两种情况
       是同一句话. 用户选的是那三个控件, 模板里写什么都不作数.
    3. **键序确定**: 模板的键保持原序 (覆写不改变位置), 平台键按 `RUN_FIELD_KEY_NAMES` 的次序
       追加. 于是落库的 `ParamsJson` 在测试里可比对, 在界面上也不会有莫名其妙的抖动.
    """

    configuration = dict(template)
    configuration.update(validate_submitted_parameters(template, submitted_parameters))

    for field_name, key_name in RUN_FIELD_KEY_NAMES.items():
        configuration[key_name] = run_field_values[field_name]

    return configuration


def _abbreviate_keys(parameter_keys: list[str]) -> str:
    """把参数名列表压成一句有界的文案.

    截断不是洁癖: 这份列表进响应体, 而列表元素来自请求——不截断的话, 报错文案的长度由调用方
    决定, 一次错误请求就能让平台回一份和请求体一样大的响应.
    """

    reported_keys = parameter_keys[:MAXIMUM_REPORTED_PARAMETER_KEYS]
    suffix = (
        TRUNCATED_KEY_LIST_SUFFIX
        if len(parameter_keys) > MAXIMUM_REPORTED_PARAMETER_KEYS
        else ""
    )

    return ", ".join(reported_keys) + suffix


@dataclass(frozen=True)
class DecodedRunFields:
    """一轮运行那两份配置文本里还原出来的取值.

    单个取值读不动时**逐项**回默认 (空串 / `None`), 不整体作废: 一个字段是坏数据, 没有理由让
    其余十来个字段跟着一起失效.
    """

    match_mode: MarketDataType | None
    bar_period: str
    exchange_id: str
    instrument_id: str
    start_trading_day: str
    end_trading_day: str
    initial_capital: float | None
    parameter_values: dict[str, object]


def decode_run_fields(
    engine_configuration: Mapping[str, object],
    strategy_configuration: Mapping[str, object],
) -> DecodedRunFields:
    """把渲染好的两份配置文本读回取值. 它是 `build_strategy_configuration` 的逆.

    **两个来源不能互换**: 撮合模式与两个交易日在引擎那份里, 合约与订阅周期在策略那份里.
    尤其 `bar_period` 只能从**策略配置**取——引擎那份的 `BarPreces` 是平台常量 (落盘精度),
    从那里读会让每一次预填都报 `5m`, 无论用户当初选的是什么.

    "参数 = 策略配置减去平台那三个键"因此是**常量差集**, 不再需要去查那个版本的 manifest:
    写路径与读路径引用的是同一个 `PLATFORM_KEY_NAMES`, 两处不可能不一致.
    """

    return DecodedRunFields(
        match_mode=resolve_market_data_type(engine_configuration.get(MATCH_MODE_KEY)),
        bar_period=_read_text(strategy_configuration, BAR_PERIOD_KEY_NAME),
        exchange_id=_read_text(strategy_configuration, EXCHANGE_ID_KEY_NAME),
        instrument_id=_read_text(strategy_configuration, INSTRUMENT_ID_KEY_NAME),
        start_trading_day=_read_text(engine_configuration, START_TRADING_DAY_KEY),
        end_trading_day=_read_text(engine_configuration, END_TRADING_DAY_KEY),
        initial_capital=_read_finite_number(engine_configuration, INITIAL_CAPITAL_KEY),
        parameter_values={
            key_name: value
            for key_name, value in strategy_configuration.items()
            if key_name not in PLATFORM_KEY_NAMES
        },
    )


def _read_text(configuration: Mapping[str, object], key_name: str) -> str:
    value = configuration.get(key_name)

    return value if isinstance(value, str) else ""


def _read_finite_number(
    configuration: Mapping[str, object], key_name: str
) -> float | None:
    """取一个有限数值; 类型不符或非有限 (JSON 允许 `Infinity` 字面量) 时回 `None`."""

    value = configuration.get(key_name)

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if not math.isfinite(value):
        return None

    return float(value)
