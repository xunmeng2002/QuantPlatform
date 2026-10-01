"""运行配置的取值校验与渲染: 运行级字段与策略参数.

提交路径与模板路径**共用这一份判据**. 模板存下的是"将来要提交的那一套取值", 两边若各写一份
校验, 能存下的取值就与能提交的取值不是同一个集合——那种漂移的症状是"存得下、提交时 400",
且只在特定取值上出现. 故除"取哪个版本"与文件系统相关的那几道闸 (它们只对提交成立) 之外,
提交侧的判据全部收在这里.

模块是**纯函数**: 不碰库、不碰文件系统. 于是"哪些取值能过"可以不起服务就逐条测.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

from ..catalog.enums import MarketDataType
from ..catalog.schemas import RunConfigurationRequest
from ..errors import InvalidRequestError
from ..manifest import (
    BAR_PERIOD_FIELD_NAME,
    EXCHANGE_ID_FIELD_NAME,
    INSTRUMENT_ID_FIELD_NAME,
    RUN_LEVEL_FIELD_NAMES,
    StrategyManifest,
    validate_parameter_value,
)
from ..scheduler.engine_config import (
    SUBMITTABLE_MATCH_MODES,
    resolve_market_data_type,
)


logger = logging.getLogger(__name__)

# 引擎 `BackTest.json` 的键名. `BarPreces` 是引擎侧既有拼写, 非笔误, 不可擅改——它读的就是
# 这个名字, 平台改一个字母就静默读不到周期.
MATCH_MODE_KEY = "MatchMode"
BAR_PERIOD_KEY = "BarPreces"
START_TRADING_DAY_KEY = "StartTradingDay"
END_TRADING_DAY_KEY = "EndTradingDay"
INITIAL_CAPITAL_KEY = "InitialCapital"


TRADING_DAY_LENGTH = 8
MAXIMUM_RUN_FIELD_VALUE_LENGTH = 64

MAXIMUM_PARAMETER_KEY_LENGTH = 64
MAXIMUM_SUBMITTED_PARAMETERS = 200
MAXIMUM_REPORTED_PARAMETER_KEYS = 10

MATCH_MODE_NOT_SUBMITTABLE_MESSAGE = "当前版本只支持 Bar 行情模式"
MATCH_MODE_UNSUPPORTED_MESSAGE = "该策略不支持所选的行情模式"
RUN_FIELD_REQUIRED_MESSAGE = "{field} 不能为空"
RUN_FIELD_NOT_MAPPED_MESSAGE = "该策略未映射 {field}, 无法传递该字段"
TRADING_DAY_ORDER_MESSAGE = "start_trading_day 不得晚于 end_trading_day"

RUN_FIELD_TOO_LONG_MESSAGE = "{field} 不得超过 {length} 个字符"
RUN_FIELD_INVALID_CHARACTERS_MESSAGE = "{field} 不得含控制字符"
TRADING_DAY_INVALID_MESSAGE = "{field} 须为 8 位数字"
INITIAL_CAPITAL_INVALID_MESSAGE = "initial_capital 须为大于 0 的有限数值"
TOO_MANY_PARAMETERS_MESSAGE = f"params 的参数个数不得超过 {MAXIMUM_SUBMITTED_PARAMETERS}"
PARAMETER_KEY_TOO_LONG_MESSAGE = f"params 的参数名不得超过 {MAXIMUM_PARAMETER_KEY_LENGTH} 个字符"
UNKNOWN_PARAMETER_MESSAGE = "params 含未声明的参数: {keys}"
MISSING_PARAMETER_MESSAGE = "params 缺少必填参数: {keys}"
PARAMETER_INVALID_MESSAGE = "参数 {key} {reason}"
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


def validate_trading_day_range(start_trading_day: str, end_trading_day: str) -> tuple[str, str]:
    """校验两个交易日各自合法且 `start <= end`, 返回规范化后的取值.

    两件事合成一个入口: 分成两步的话, 调用方可以只做前一步, 而"起止倒置"那种提交会照常入队
    ——它跑出来的不是错误, 而是一轮空结果.
    """

    normalized_start = validate_trading_day("start_trading_day", start_trading_day)
    normalized_end = validate_trading_day("end_trading_day", end_trading_day)

    if normalized_start > normalized_end:
        raise InvalidRequestError(TRADING_DAY_ORDER_MESSAGE)

    return normalized_start, normalized_end


def validate_match_mode(
    manifest: StrategyManifest, requested_match_mode: MarketDataType
) -> MarketDataType:
    """两道闸: 平台当前能提交哪些模式, 以及该策略声明支持哪些.

    缺一不可. 只有第一道, 一个只写了 Bar 的策略会被允许提交 Tick; 只有第二道, 平台上没人验证
    过的那三档 Tick 撮合方式会照收——而它们跑出来的结果看起来完全正常 (静默 0 成交).
    """

    if requested_match_mode not in SUBMITTABLE_MATCH_MODES:
        raise InvalidRequestError(MATCH_MODE_NOT_SUBMITTABLE_MESSAGE)

    if requested_match_mode not in manifest.supported_match_modes:
        raise InvalidRequestError(MATCH_MODE_UNSUPPORTED_MESSAGE)

    return requested_match_mode


def resolve_run_field_values(
    manifest: StrategyManifest, submitted_values: RunConfigurationRequest
) -> dict[str, str]:
    """按 manifest 的映射决定运行级字段收不收, 并给出**唯一一份**取值.

    `bar_period` 是个特例: 它有映射时写两份 (BackTest.json 与策略配置), 没映射时只写引擎那份,
    但**两种情形下都必填**——引擎一定要它. 而"有映射则必填"另有一层意义: 映射了却不给值, 策略
    读到的就是缺键, 而它会以未捕获异常收场.

    `exchange_id` / `instrument_id` 则只在有映射时收: 引擎根本不认识它们 (只有策略 `subscribe_tick`
    用), 没映射而提交就是无处落地——收下它等于给用户一个"点了没反应".

    返回表里**只有收下的那些字段**, 故缺键与空值是同一件事 (都缺席): 调用方据此写库, 可空列
    拿到 `None` 而不是空串.
    """

    mapped_field_names = set(manifest.named_run_field_keys())
    run_field_values: dict[str, str] = {}

    for field_name in RUN_LEVEL_FIELD_NAMES:
        supplied_value = normalize_run_field_value(
            field_name, getattr(submitted_values, field_name)
        )
        is_required = field_name == BAR_PERIOD_FIELD_NAME or field_name in mapped_field_names

        if supplied_value is None:
            if is_required:
                raise InvalidRequestError(RUN_FIELD_REQUIRED_MESSAGE.format(field=field_name))
            continue

        if not is_required:
            raise InvalidRequestError(
                RUN_FIELD_NOT_MAPPED_MESSAGE.format(field=field_name)
            )

        run_field_values[field_name] = supplied_value

    return run_field_values


def build_parameter_configuration(
    manifest: StrategyManifest, submitted_parameters: dict[str, object]
) -> dict[str, object]:
    """按 manifest 声明的参数渲染策略配置的**参数部分**.

    结果恰好等于 `{manifest 声明的参数 key}`: 未声明的键拒收, 未给值的必填参数拒收, 给了值的
    按声明类型与范围校验. 运行级字段不在这里——它们由 manifest 的映射键另加.
    """

    if len(submitted_parameters) > MAXIMUM_SUBMITTED_PARAMETERS:
        raise InvalidRequestError(TOO_MANY_PARAMETERS_MESSAGE)

    if any(len(key) > MAXIMUM_PARAMETER_KEY_LENGTH for key in submitted_parameters):
        raise InvalidRequestError(PARAMETER_KEY_TOO_LONG_MESSAGE)

    declared_parameters = {parameter.key: parameter for parameter in manifest.params}

    unknown_keys = sorted(set(submitted_parameters) - set(declared_parameters))

    if unknown_keys:
        raise InvalidRequestError(
            UNKNOWN_PARAMETER_MESSAGE.format(keys=_abbreviate_keys(unknown_keys))
        )

    missing_keys = sorted(
        key
        for key, parameter in declared_parameters.items()
        if key not in submitted_parameters and parameter.default is None
    )

    if missing_keys:
        raise InvalidRequestError(
            MISSING_PARAMETER_MESSAGE.format(keys=_abbreviate_keys(missing_keys))
        )

    configuration: dict[str, object] = {}

    for key, parameter in declared_parameters.items():
        parameter_value = submitted_parameters.get(key, parameter.default)

        try:
            validate_parameter_value(parameter, parameter_value)
        except ValueError as error:
            raise InvalidRequestError(
                PARAMETER_INVALID_MESSAGE.format(key=key, reason=error)
            ) from error

        configuration[key] = parameter_value

    return configuration


def build_strategy_configuration(
    manifest: StrategyManifest,
    run_field_values: dict[str, str],
    submitted_parameters: dict[str, object],
) -> dict[str, object]:
    """渲染策略配置: 运行级字段 (有映射的那些) + 全部参数.

    结果**恰好**等于 `{映射声明的键} ∪ {manifest 声明的参数 key}`——没有第三类键. 这条性质是
    "平台不硬编码策略侧键名"的可测形式: 把 manifest 的映射键名换一组, 渲染结果要跟着变.
    """

    configuration = build_parameter_configuration(manifest, submitted_parameters)

    for field_name, key_name in manifest.named_run_field_keys().items():
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


def read_run_field_key_names(manifest_json: str | None) -> dict[str, str]:
    """该版本 manifest 里"运行级字段名 → 配置键名"的映射.

    用**那一轮自己那个版本**的 manifest, 而不是最新版本: 键名是渲染当时定的, 事后改版不影响
    历史那份配置里的键叫什么.

    manifest 读不动时回空映射: 于是策略配置里的每个键都会被当成参数, 而调用方按各自那份
    manifest 的控件逐项判断, 多出来的键被忽略——故不必把整轮解码作废.
    """

    if not manifest_json:
        return {}

    try:
        manifest = StrategyManifest.model_validate_json(manifest_json)
    except ValueError as error:
        logger.warning("策略版本的 manifest 无法解析, 按无映射处理: %s", error)
        return {}

    return manifest.named_run_field_keys()


def decode_run_fields(
    engine_configuration: Mapping[str, object],
    strategy_configuration: Mapping[str, object],
    run_field_key_names: Mapping[str, str],
) -> DecodedRunFields:
    """把渲染好的两份配置文本读回取值. 它是 `build_strategy_configuration` 的逆.

    两个来源**不能互换**: 运行级字段里 `bar_period` 只在有映射时才写进策略配置, 而引擎那份
    一定有它; `exchange_id` / `instrument_id` 则相反——引擎根本不认识它们. 故前三个从引擎
    配置取, 后两个从策略配置取.

    manifest 保证参数键不会与运行级键撞名 (见 `manifest._check_declared_keys_do_not_collide`),
    故按键名做差集是精确的: 差集之外的都是参数, 连同类型原样带回去.
    """

    declared_key_names = set(run_field_key_names.values())
    run_field_values = {
        field_name: _read_text(strategy_configuration, key_name)
        for field_name, key_name in run_field_key_names.items()
    }

    return DecodedRunFields(
        match_mode=resolve_market_data_type(engine_configuration.get(MATCH_MODE_KEY)),
        bar_period=_read_text(engine_configuration, BAR_PERIOD_KEY),
        exchange_id=run_field_values.get(EXCHANGE_ID_FIELD_NAME, ""),
        instrument_id=run_field_values.get(INSTRUMENT_ID_FIELD_NAME, ""),
        start_trading_day=_read_text(engine_configuration, START_TRADING_DAY_KEY),
        end_trading_day=_read_text(engine_configuration, END_TRADING_DAY_KEY),
        initial_capital=_read_finite_number(engine_configuration, INITIAL_CAPITAL_KEY),
        parameter_values={
            key_name: value
            for key_name, value in strategy_configuration.items()
            if key_name not in declared_key_names
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
