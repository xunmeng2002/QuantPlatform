"""运行提交: 校验 → 渲染两份配置 → 落一行 `queued`.

**权限判定就是可见性判定**, 一行不多: 用户拍板的是"授权即可跑, 不区分 read/run", 而用户能跑
的集合 (归属 ∪ public ∪ 共享且被授权) 与 `build_visible_strategy_query` 的可见集合**逐字相同**
——因为 private 下授权表本就不生效. 故这里调用 `load_visible_strategy`, 不新增越权分支也不新增
查询收口; 跨租户访问照旧一律 404, 不泄漏 id 是否存在.

**平台渲染策略配置, 不读策略自带的配置文件**: 上传只带源码与 manifest, 版本目录里没有配置文件
可作底稿. 于是策略读到的每一个键都必须由 manifest 声明——漏声明的键不会"保持原样", 它会整个
消失, 而策略多半是 `config["X"]` 直接取用, 以未捕获异常 (退出码 1) 收场.

**提交只写库, 不碰文件系统**: 作业目录由调度侧构造, 因为一次提交要落盘四五个文件, 把这段 IO
塞进请求路径既拖长响应, 又把"磁盘满"变成"提交失败"——而用户拿着一个 201 却找不到工作目录,
比拿一个 500 更难归因.
"""

from __future__ import annotations

import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.enums import MarketDataType, RunStatus
from ..catalog.models import RunModel, StrategyVersionModel, UserModel
from ..catalog.schemas import RunSubmitRequest
from ..catalog.visibility import load_visible_strategy
from ..config import PlatformSettings
from ..errors import InvalidRequestError
from ..ids import generate_identifier
from ..manifest import (
    BAR_PERIOD_FIELD_NAME,
    RUN_LEVEL_FIELD_NAMES,
    StrategyManifest,
    validate_parameter_value,
)
from ..scheduler.engine_config import (
    SUBMITTABLE_MATCH_MODES,
    render_engine_config,
    serialize_configuration,
)


logger = logging.getLogger(__name__)

TRADING_DAY_LENGTH = 8
MAXIMUM_RUN_FIELD_VALUE_LENGTH = 64

# 参数名与参数个数的上限, 都只为**一处**服务: 报错文案里有参数名, 而参数名来自请求, 不设限的话
# 一个 1 MB 的请求体能让响应体也涨到同一个量级——平台把一个错误请求放大成一次带宽消耗.
MAXIMUM_PARAMETER_KEY_LENGTH = 64
MAXIMUM_SUBMITTED_PARAMETERS = 200
MAXIMUM_REPORTED_PARAMETER_KEYS = 10

MATCH_MODE_NOT_SUBMITTABLE_MESSAGE = "当前版本只支持 Bar 行情模式"
MATCH_MODE_UNSUPPORTED_MESSAGE = "该策略不支持所选的行情模式"
BAR_PERIOD_REQUIRED_MESSAGE = "bar_period 不能为空"
RUN_FIELD_REQUIRED_MESSAGE = "{field} 不能为空"
RUN_FIELD_NOT_MAPPED_MESSAGE = "该策略未映射 {field}, 无法传递该字段"
RUN_FIELD_TOO_LONG_MESSAGE = "{field} 不得超过 {length} 个字符"
RUN_FIELD_INVALID_CHARACTERS_MESSAGE = "{field} 不得含控制字符"
VERSION_NOT_FOUND_MESSAGE = "指定的策略版本不存在"
NO_VERSION_MESSAGE = "该策略还没有可运行的版本"
MANIFEST_UNREADABLE_MESSAGE = "该策略版本的 manifest 无法解析, 请重新上传该版本"
TRADING_DAY_INVALID_MESSAGE = "{field} 须为 8 位数字"
TRADING_DAY_ORDER_MESSAGE = "start_trading_day 不得晚于 end_trading_day"
INITIAL_CAPITAL_INVALID_MESSAGE = "initial_capital 须为大于 0 的有限数值"
UNKNOWN_PARAMETER_MESSAGE = "params 含未声明的参数: {keys}"
MISSING_PARAMETER_MESSAGE = "params 缺少必填参数: {keys}"
TOO_MANY_PARAMETERS_MESSAGE = f"params 的参数个数不得超过 {MAXIMUM_SUBMITTED_PARAMETERS}"
PARAMETER_KEY_TOO_LONG_MESSAGE = f"params 的参数名不得超过 {MAXIMUM_PARAMETER_KEY_LENGTH} 个字符"
TRUNCATED_KEY_LIST_SUFFIX = " 等"
PARAMETER_INVALID_MESSAGE = "参数 {key} {reason}"
MARKET_DATA_MISSING_MESSAGE = "行情数据根目录不存在, 请先配置"
SESSION_FILE_MISSING_MESSAGE = "会话表文件不存在, 请先配置"


async def submit_run(
    session: AsyncSession,
    settings: PlatformSettings,
    current_user: UserModel,
    request_body: RunSubmitRequest,
) -> RunModel:
    """校验并落一行 `queued`; 调用方负责在这之后提交 `scheduler.wake()`.

    唤醒**必须**排在本次提交落库之后. 反过来的话调度器可能扫不到尚未提交的行, 一次提交就要等
    下一次唤醒——没有看门狗时就是**永不处理**; 有了看门狗也只是晚 5 秒, 但那 5 秒是白等的.
    """

    strategy = await load_visible_strategy(
        session, current_user, request_body.strategy_id
    )

    _ensure_engine_inputs_available(settings)

    version = await _resolve_version(
        session, strategy.id, request_body.strategy_version_id
    )
    manifest = _parse_version_manifest(version)

    match_mode = _validate_match_mode(manifest, request_body.match_mode)

    run_field_values = _resolve_run_field_values(manifest, request_body)

    start_trading_day = _validate_trading_day(
        "start_trading_day", request_body.start_trading_day
    )
    end_trading_day = _validate_trading_day(
        "end_trading_day", request_body.end_trading_day
    )

    if start_trading_day > end_trading_day:
        raise InvalidRequestError(TRADING_DAY_ORDER_MESSAGE)

    run_id = generate_identifier()

    engine_configuration_text = render_engine_config(
        run_id=run_id,
        match_mode=match_mode,
        bar_period=run_field_values[BAR_PERIOD_FIELD_NAME],
        start_trading_day=start_trading_day,
        end_trading_day=end_trading_day,
        initial_capital=_validate_initial_capital(request_body.initial_capital),
        market_data_path=settings.market_data_root,
        seed_database_path=settings.seed_database_path,
    )

    strategy_configuration_text = serialize_configuration(
        _build_strategy_configuration(manifest, request_body, run_field_values)
    )

    run = RunModel(
        id=run_id,
        user_id=current_user.id,
        strategy_id=strategy.id,
        strategy_version_id=version.id,
        status=RunStatus.QUEUED.value,
        params_json=strategy_configuration_text,
        backtest_config_json=engine_configuration_text,
        # 工作目录名就是主键, 二者是同一件事: 引擎按 RunId 派生结果库名, 平台按它找结果文件,
        # 库里再存一份相对路径只是让"这一轮落在哪"不必靠约定去推.
        workspace_path=run_id,
    )
    session.add(run)

    await session.commit()

    return run


def _ensure_engine_inputs_available(settings: PlatformSettings) -> None:
    """引擎的两个只读输入必须先在位.

    拦在提交侧而不是留给调度侧: 这两项缺失是**确定性**失败, 收下它只会让用户等一轮再收到一个
    与他的输入无关的 `failed`. 文案点名缺的是哪一项但不带路径——路径是服务端的目录布局.
    """

    if not settings.market_data_root.is_dir():
        raise InvalidRequestError(MARKET_DATA_MISSING_MESSAGE)

    if not settings.session_file_path.is_file():
        raise InvalidRequestError(SESSION_FILE_MISSING_MESSAGE)


async def _resolve_version(
    session: AsyncSession, strategy_id: str, requested_version_id: str | None
) -> StrategyVersionModel:
    """取要跑的版本: 指定了就用指定的 (必须属于该策略), 没指定就用最新的."""

    if requested_version_id is not None:
        version = await session.scalar(
            select(StrategyVersionModel)
            .where(StrategyVersionModel.id == requested_version_id)
            .where(StrategyVersionModel.strategy_id == strategy_id)
        )
    else:
        version = await session.scalar(
            select(StrategyVersionModel)
            .where(StrategyVersionModel.strategy_id == strategy_id)
            .order_by(StrategyVersionModel.version_no.desc())
            .limit(1)
        )

    if version is None:
        raise InvalidRequestError(
            VERSION_NOT_FOUND_MESSAGE if requested_version_id else NO_VERSION_MESSAGE
        )

    return version


def _parse_version_manifest(version: StrategyVersionModel) -> StrategyManifest:
    """取回该版本落库时的 manifest 快照.

    上传时已校验过, 解不动说明库里的快照被谁改坏了; 译成 400 而不是放它变成 500: 对调用方来说
    可行的动作一样 (换一个版本或重新上传), 而 500 会把它归因成"平台坏了, 稍后重试".
    """

    try:
        return StrategyManifest.model_validate_json(version.manifest_json)
    except ValueError as error:
        logger.error("策略版本的 manifest 快照无法解析: %s", version.id)
        raise InvalidRequestError(MANIFEST_UNREADABLE_MESSAGE) from error


def _validate_match_mode(
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


def _resolve_run_field_values(
    manifest: StrategyManifest, request_body: RunSubmitRequest
) -> dict[str, str]:
    """按 manifest 的映射决定运行级字段收不收, 并给出**唯一一份**取值.

    `bar_period` 是个特例: 它有映射时写两份 (BackTest.json 与策略配置), 没映射时只写引擎那份,
    但**两种情形下都必填**——引擎一定要它. 而"有映射则必填"另有一层意义: 映射了却不给值, 策略
    读到的就是缺键, 而它会以未捕获异常收场.

    `exchange_id` / `instrument_id` 则只在有映射时收: 引擎根本不认识它们 (只有策略 `subscribe_tick`
    用), 没映射而提交就是无处落地——收下它等于给用户一个"点了没反应".
    """

    mapped_field_names = set(manifest.named_run_field_keys())
    run_field_values: dict[str, str] = {}

    for field_name in RUN_LEVEL_FIELD_NAMES:
        supplied_value = _normalize_run_field_value(
            field_name, getattr(request_body, field_name)
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


def _normalize_run_field_value(field_name: str, raw_value: str | None) -> str | None:
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


def _build_strategy_configuration(
    manifest: StrategyManifest,
    request_body: RunSubmitRequest,
    run_field_values: dict[str, str],
) -> dict[str, object]:
    """渲染策略配置: 运行级字段 (有映射的那些) + 全部参数.

    结果**恰好**等于 `{映射声明的键} ∪ {manifest 声明的参数 key}`——没有第三类键. 这条性质是
    "平台不硬编码策略侧键名"的可测形式: 把 manifest 的映射键名换一组, 渲染结果要跟着变.
    """

    submitted_values = dict(request_body.params)

    if len(submitted_values) > MAXIMUM_SUBMITTED_PARAMETERS:
        raise InvalidRequestError(TOO_MANY_PARAMETERS_MESSAGE)

    if any(
        len(key) > MAXIMUM_PARAMETER_KEY_LENGTH for key in submitted_values
    ):
        raise InvalidRequestError(PARAMETER_KEY_TOO_LONG_MESSAGE)

    declared_parameters = {parameter.key: parameter for parameter in manifest.params}

    unknown_keys = sorted(set(submitted_values) - set(declared_parameters))

    if unknown_keys:
        raise InvalidRequestError(
            UNKNOWN_PARAMETER_MESSAGE.format(keys=_abbreviate_keys(unknown_keys))
        )

    missing_keys = sorted(
        key
        for key, parameter in declared_parameters.items()
        if key not in submitted_values and parameter.default is None
    )

    if missing_keys:
        raise InvalidRequestError(
            MISSING_PARAMETER_MESSAGE.format(keys=_abbreviate_keys(missing_keys))
        )

    configuration: dict[str, object] = {}

    for key, parameter in declared_parameters.items():
        parameter_value = submitted_values.get(key, parameter.default)

        try:
            validate_parameter_value(parameter, parameter_value)
        except ValueError as error:
            raise InvalidRequestError(
                PARAMETER_INVALID_MESSAGE.format(key=key, reason=error)
            ) from error

        configuration[key] = parameter_value

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


def _validate_trading_day(field_name: str, raw_value: str) -> str:
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


def _validate_initial_capital(raw_value: float) -> float:
    """初始资金须为大于 0 的有限值.

    `NaN` / `Infinity` 挡在这里: JSON 里写不出它们, 但 pydantic 的 `float` 收得下, 而它们一旦
    进配置就会被引擎读成一个不可比的数——那轮结果再怎么看都正常, 只是永远算不对.
    """

    if not math.isfinite(raw_value) or raw_value <= 0:
        raise InvalidRequestError(INITIAL_CAPITAL_INVALID_MESSAGE)

    return float(raw_value)
