"""上一次提交的参数: 从该用户在该策略下最新那一轮运行里读回来.

**平台不另存一份"记忆"**: 提交时 `Runs` 表已经落了两份渲染好的配置文本 (`ParamsJson` 策略配置、
`BacktestConfigJson` 引擎配置, 见 `run_submission`), 于是"上次用的参数"就是它们的派生. 这省掉
一张表, 也就消除了"记忆"与真实提交不一致的可能——它本来就只有一份.

「最近一次」= 最近一次**提交**, 与那一轮成没成功无关 (用户拍板). 失败后往往只需改一两个参数
重跑, 只认成功的那次反而会在最需要它的时候失效.

**归属过滤必须经 `visibility`**: 共享与公开策略下, 别人的运行不得成为你的预填值. 故取数走
`build_owned_run_query`, 不自己拼 `where`.

**坏数据只降级、不报错**: 一轮配置文本读不动就当作"没有记忆", 记一条 warning 了事——提交页是
入口, 那里因为历史数据而报错, 用户既看不懂也无从修复.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.enums import MarketDataType
from ..catalog.models import RunModel, StrategyVersionModel, UserModel
from ..catalog.visibility import build_owned_run_query
from ..manifest import (
    EXCHANGE_ID_FIELD_NAME,
    INSTRUMENT_ID_FIELD_NAME,
    StrategyManifest,
)
from ..scheduler.engine_config import resolve_market_data_type


logger = logging.getLogger(__name__)

# 引擎 `BackTest.json` 的键名. `BarPreces` 是引擎侧既有拼写, 非笔误, 不可擅改——它读的就是
# 这个名字, 平台改一个字母就静默读不到周期.
MATCH_MODE_KEY = "MatchMode"
BAR_PERIOD_KEY = "BarPreces"
START_TRADING_DAY_KEY = "StartTradingDay"
END_TRADING_DAY_KEY = "EndTradingDay"
INITIAL_CAPITAL_KEY = "InitialCapital"


@dataclass(frozen=True)
class LastSubmittedParameters:
    """上一次提交的解码结果.

    单个取值读不动时**逐项**回默认 (空串 / `None`), 不整体作废: 一个字段是坏数据, 没有理由
    让其余十来个字段的预填一起失效.
    """

    run_id: str
    submitted_at: datetime
    match_mode: MarketDataType | None = None
    bar_period: str = ""
    exchange_id: str = ""
    instrument_id: str = ""
    start_trading_day: str = ""
    end_trading_day: str = ""
    initial_capital: float | None = None
    params: dict[str, object] = field(default_factory=dict)


async def read_last_submitted_parameters(
    session: AsyncSession, current_user: UserModel, strategy_id: str
) -> LastSubmittedParameters | None:
    """该用户在该策略下最近一次提交的参数; 没有历史运行 (或配置文本读不动) 时回 `None`.

    回 `None` 是**正常结果**而不是错误: 首次使用某个策略走的就是这条路, 提交页据此用 manifest
    默认值填表.
    """

    run = (
        (
            await session.execute(
                build_owned_run_query(current_user)
                .where(RunModel.strategy_id == strategy_id)
                # 双键排序: `SubmittedAt` 在 Windows 上只有毫秒精度, 同值的两轮靠主键兜平局,
                # 否则"最新"是不确定的 (调度侧认领队列时用了同一个兜法).
                .order_by(RunModel.submitted_at.desc(), RunModel.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    if run is None:
        return None

    engine_configuration = _parse_configuration_text(run.id, run.backtest_config_json)
    strategy_configuration = _parse_configuration_text(run.id, run.params_json)

    if engine_configuration is None or strategy_configuration is None:
        return None

    version = await session.get(StrategyVersionModel, run.strategy_version_id)
    run_field_key_names = _read_run_field_key_names(version)
    run_field_values, parameter_values = _split_configuration(
        strategy_configuration, run_field_key_names
    )

    return LastSubmittedParameters(
        run_id=run.id,
        submitted_at=run.submitted_at,
        match_mode=resolve_market_data_type(engine_configuration.get(MATCH_MODE_KEY)),
        bar_period=_read_text(engine_configuration, BAR_PERIOD_KEY),
        exchange_id=run_field_values.get(EXCHANGE_ID_FIELD_NAME, ""),
        instrument_id=run_field_values.get(INSTRUMENT_ID_FIELD_NAME, ""),
        start_trading_day=_read_text(engine_configuration, START_TRADING_DAY_KEY),
        end_trading_day=_read_text(engine_configuration, END_TRADING_DAY_KEY),
        initial_capital=_read_finite_number(engine_configuration, INITIAL_CAPITAL_KEY),
        params=parameter_values,
    )


def _parse_configuration_text(
    run_id: str, configuration_text: str
) -> dict[str, object] | None:
    """解析一份落库的配置文本; 读不动只记日志并回 `None`."""

    try:
        parsed_configuration = json.loads(configuration_text)
    except json.JSONDecodeError as error:
        logger.warning("运行 %s 的配置文本不是合法 JSON: %s", run_id, error)
        return None

    if not isinstance(parsed_configuration, dict):
        logger.warning("运行 %s 的配置文本不是 JSON 对象", run_id)
        return None

    return parsed_configuration


def _read_run_field_key_names(version: StrategyVersionModel | None) -> dict[str, str]:
    """该运行自己那个版本的运行级键名映射 (字段名 → 配置键名).

    用**运行自己那个版本**的 manifest, 而不是最新版本: 键名是渲染当时定的, 事后改版不影响
    历史那份配置里的键叫什么.

    manifest 读不动时回空映射, 于是策略配置里的每个键都会被当成参数带回去一一前端按它自己那份
    manifest 的控件逐项判断, 多出来的键会被忽略, 故这里不必把整轮预填作废.
    """

    if version is None:
        return {}

    try:
        manifest = StrategyManifest.model_validate_json(version.manifest_json)
    except ValueError as error:
        logger.warning("版本 %s 的 manifest 无法解析, 预填按无映射处理: %s", version.id, error)
        return {}

    return manifest.named_run_field_keys()


def _split_configuration(
    strategy_configuration: Mapping[str, object],
    run_field_key_names: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, object]]:
    """把渲染出来的策略配置拆回"运行级字段"与"策略参数"两部分.

    manifest 保证参数键不会与运行级键撞名 (见 `manifest._check_declared_keys_do_not_collide`),
    故按键名做差集是精确的: 差集之外的都是参数, 连同类型原样带回去.
    """

    declared_key_names = set(run_field_key_names.values())
    run_field_values: dict[str, str] = {}
    parameter_values: dict[str, object] = {}

    for key_name, value in strategy_configuration.items():
        if key_name in declared_key_names:
            continue

        parameter_values[key_name] = value

    for field_name, key_name in run_field_key_names.items():
        run_field_values[field_name] = _read_text(strategy_configuration, key_name)

    return run_field_values, parameter_values


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
