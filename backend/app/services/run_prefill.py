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

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.enums import MarketDataType
from ..catalog.models import RunModel, StrategyVersionModel, UserModel
from ..catalog.visibility import build_owned_run_query
from ..scheduler.engine_config import parse_configuration_object
from .run_configuration import decode_run_fields, read_run_field_key_names


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LastSubmittedParameters:
    """上一次提交的解码结果."""

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

    engine_configuration = parse_configuration_object(run.id, run.backtest_config_json)
    strategy_configuration = parse_configuration_object(run.id, run.params_json)

    if engine_configuration is None or strategy_configuration is None:
        return None

    version = await session.get(StrategyVersionModel, run.strategy_version_id)
    decoded_fields = decode_run_fields(
        engine_configuration,
        strategy_configuration,
        read_run_field_key_names(None if version is None else version.manifest_json),
    )

    return LastSubmittedParameters(
        run_id=run.id,
        submitted_at=run.submitted_at,
        match_mode=decoded_fields.match_mode,
        bar_period=decoded_fields.bar_period,
        exchange_id=decoded_fields.exchange_id,
        instrument_id=decoded_fields.instrument_id,
        start_trading_day=decoded_fields.start_trading_day,
        end_trading_day=decoded_fields.end_trading_day,
        initial_capital=decoded_fields.initial_capital,
        params=decoded_fields.parameter_values,
    )
