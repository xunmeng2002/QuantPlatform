"""提交页要用的两份行情清单: 能选哪些合约, 以及"本地的够不够".

两个端点都是**只读的告知**: 提交页拿它们把自由文本换成下拉框, 并在提交前先说清"这一轮要不要
先下载". 真正下载发生在调度器里 (`services/market_data_preparation.py`) —— 提交侧不碰文件系统,
这条纪律不破.

**组件不在位不是错误, 是一种状态.** 两个端点因此都回 200 + `available=false` + 一句原因, 而不是
503: 提交页据此禁用下拉框并显示原因, 用户看到的是"现在不能选合约"这件确定的事, 而不是一个需要
他去猜的失败. 这与 `LastSubmittedParametersResponse` 用空对象表达"没有历史"是同一条口径.

合约全集**只读推导**, 不落平台自己的表: 组件库里下过哪些代码是它自己的事实, 平台另存一份只会
漂移 (见 `quote_hub.read_ingested_codes` 关于"少传一个就静默挤掉"的说明).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..auth.dependencies import CurrentUserDependency
from ..config import MARKET_DATA_PRECISION_FREQUENCY
from ..dependencies import SettingsDependency
from ..services import quote_hub
from ..services.market_data_coverage import judge_coverage
from ..services.market_data_preparation import MARKET_DATA_COMPONENT_MISSING_MESSAGE


logger = logging.getLogger(__name__)

router = APIRouter()

COMPONENT_UNAVAILABLE_REASON = MARKET_DATA_COMPONENT_MISSING_MESSAGE

# 单次返回的合约条数不设上限: 股票就那五千来只, 且下拉框本来就要一次拿全才能本地筛选.
class MarketDataContractResponse(BaseModel):
    """下拉框的一项. `code` 是组件的原生主键, 选中时由前端拆成下面两个字段."""

    code: str
    exchange_id: str
    instrument_id: str
    display_name: str


class MarketDataContractListResponse(BaseModel):
    """可选的合约.

    `available=false` 时 `contracts` 为空, 且 `reason` 是一句可以直接显示的中文; 提交页据此
    禁用下拉框而**不是退化成自由文本**——自由文本会让用户填出一个跑不起来、却看着正常的取值.

    受支持的 K 线周期**不在这里**: 它不来自组件, 是平台自己的常量, 双方各持一份有注释相互指认的
    镜像即可 (提交页那份在 `domain/market-data.ts`). 挂在这里只会多一个没人读的字段.
    """

    available: bool
    reason: str = ""
    contracts: list[MarketDataContractResponse] = []


class MarketDataCoverageResponse(BaseModel):
    """一轮提交的行情预检结果. 它只**告知**, 放行与否不归它管."""

    available: bool
    reason: str = ""
    sufficient: bool = False
    expected_day_count: int = 0
    missing_day_count: int = 0


@router.get("/contracts", response_model=MarketDataContractListResponse)
async def read_contracts_handler(
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
) -> MarketDataContractListResponse:
    """下拉框的全部选项: 现在能回测哪些合约."""

    try:
        contracts = await asyncio.to_thread(
            quote_hub.read_tradable_contracts, settings.quote_hub_root
        )
    except quote_hub.QuoteHubUnavailableError as error:
        logger.warning("行情组件不可用, 提交页无法列出合约: %s", error)

        return MarketDataContractListResponse(
            available=False, reason=COMPONENT_UNAVAILABLE_REASON
        )

    return MarketDataContractListResponse(
        available=True,
        contracts=[
            MarketDataContractResponse(
                code=contract.code,
                exchange_id=contract.exchange_id,
                instrument_id=contract.instrument_id,
                display_name=contract.display_name,
            )
            for contract in contracts
        ],
    )


@router.get("/coverage", response_model=MarketDataCoverageResponse)
async def read_coverage_handler(
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
    exchange_id: str = Query(..., max_length=64),
    instrument_id: str = Query(..., max_length=64),
    start_trading_day: str = Query(..., min_length=8, max_length=8),
    end_trading_day: str = Query(..., min_length=8, max_length=8),
) -> MarketDataCoverageResponse:
    """这一轮要不要先下载. 组件不在位或参数拼不出合约时回 `available=false`, 不阻断提交.

    **不问用户在提交页选的订阅周期**: 覆盖判据问的是"这段问过上游没有", 而落盘精度是平台常量
    (`MARKET_DATA_PRECISION_FREQUENCY`). 收下那个周期只会让判据看起来依赖它——而用户的周期与
    "数据在不在"毫无关系, 这份依赖一旦写进签名, 早晚有人照着它去改判据.

    这里只报"缺几天", 不做刷新后的收口: 那个收口 (`decide_after_refresh`) 要的是刷新**之后**的
    事实, 而本端点是不碰文件系统的只读预检. 两者判的也不是同一件事——预检说"要不要下载", 收口说
    "下完了能不能放行".
    """

    contract_code = quote_hub.build_contract_code(exchange_id, instrument_id)

    if contract_code is None:
        return MarketDataCoverageResponse(
            available=False, reason="所选合约无法用于行情检查"
        )

    try:
        facts = await asyncio.to_thread(
            quote_hub.read_coverage_facts,
            settings.quote_hub_root,
            contract_code,
            MARKET_DATA_PRECISION_FREQUENCY,
            quote_hub.format_component_day(start_trading_day),
            quote_hub.format_component_day(end_trading_day),
        )
    except quote_hub.QuoteHubUnavailableError as error:
        logger.warning("行情组件不可用, 提交页无法预检覆盖情况: %s", error)

        return MarketDataCoverageResponse(
            available=False, reason=COMPONENT_UNAVAILABLE_REASON
        )

    verdict = judge_coverage(facts.expected_days, facts.asked_days, facts.has_bars)

    return MarketDataCoverageResponse(
        available=True,
        sufficient=verdict.sufficient,
        expected_day_count=len(verdict.expected_days),
        missing_day_count=len(verdict.missing_days),
    )
