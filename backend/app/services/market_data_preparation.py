"""一轮回测开始前, 把这一轮要用的行情准备好.

在调度器起引擎**之前**跑: 先判本地已落地的行情够不够 (**够就一次子进程都不起**), 不够才请
QuoteHub 组件下载落地, 落地后**复判**再决定放行还是让这一轮失败.

三处设计要点:

  - **判据在 `market_data_coverage`, 事实在 `quote_hub`**, 本模块只做编排与失败分类.
  - **失败一律以固定中文文案收场**, 不带路径也不带组件原始输出 (同 `UnrunnableJob` 的纪律);
    组件输出只进日志.
  - **命令行传的是全集, 不是本次选中的那一个**: 组件只把传进去的合约写进年度文件, 少传一个
    就把那个合约从文件里**静默挤掉**. 全集取自组件库里"下过的全部代码", 故一次都不会漏
    (见 `quote_hub.read_ingested_codes`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import (
    MAXIMUM_MARKET_DATA_CODES,
    QUOTE_HUB_CLI_FILENAME,
    QUOTE_HUB_DATABASE_FILENAME,
    PlatformSettings,
)
from . import quote_hub
from .market_data_coverage import (
    CoverageVerdict,
    RefreshDecision,
    decide_after_refresh,
    judge_coverage,
    request_exceeds_calendar,
)


logger = logging.getLogger(__name__)

PLATFORM_DAY_LENGTH = 8


MARKET_DATA_COMPONENT_MISSING_MESSAGE = "行情组件不在位 (目录、入口脚本或数据库缺失)"
MARKET_DATA_COMPONENT_START_FAILED_MESSAGE = "行情组件无法启动"
MARKET_DATA_PERIOD_UNSUPPORTED_MESSAGE = (
    "该策略的 K 线周期不在行情组件支持的范围内 (仅 5 / 15 / 30 / 60 分钟)"
)
MARKET_DATA_REQUEST_INCOMPLETE_MESSAGE = "该轮运行记录的行情准备参数不完整, 无法确定要准备哪段区间"
MARKET_DATA_UNIVERSE_TOO_LARGE_MESSAGE = "需要一并刷新的合约过多, 超出单次命令的长度上限"
MARKET_DATA_LOGIN_FAILED_MESSAGE = "行情数据源登录失败, 请检查行情组件的凭据与网络"
MARKET_DATA_DOWNLOAD_FAILED_MESSAGE = "行情下载失败"
MARKET_DATA_PREPARE_TIMEOUT_MESSAGE = "行情准备超时"
MARKET_DATA_STILL_INSUFFICIENT_MESSAGE = "该合约在所请求区间内没有行情数据"

#: 刷新"够不够"这件事一次只准跑一个: 两个并发作业同时缺数据会各起一个组件进程, 写同一个库与
#: 同一批 parquet 文件. 单进程是本仓调度器的硬前提, 故进程内的锁就够.
REFRESH_LOCK = asyncio.Lock()


class MarketDataPrepared(Enum):
    """准备阶段的两个出口."""

    #: 可以起引擎了.
    READY = "ready"
    #: 用户在准备期间取消了. **不设错误文案**——终态由调度器的仲裁表按取消算出, 复用现成路径.
    CANCELLED = "cancelled"


class MarketDataUnavailableError(Exception):
    """行情备不齐, 且这一轮不该硬跑. 文案可直接进运行行的 `ErrorMsg`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class MarketDataRequest:
    """一轮回测对行情的诉求. 起止都是平台内部的 8 位 `YYYYMMDD`."""

    exchange_id: str
    instrument_id: str
    bar_period: str
    start_trading_day: str
    end_trading_day: str


async def ensure_market_data_available(
    settings: PlatformSettings,
    request: MarketDataRequest,
    cancel_signal: asyncio.Event,
) -> MarketDataPrepared:
    """确保这一轮要用的行情已在本地落地; 备不齐就抛 `MarketDataUnavailableError`."""

    quote_hub_root = settings.quote_hub_root

    contract_code = quote_hub.build_contract_code(
        request.exchange_id, request.instrument_id
    )

    if contract_code is None:
        # 这一轮**没有指名合约**: manifest 没映射 `exchange_id` / `instrument_id`, 或只映射了
        # 其中一个. 没有合约就无从表达"要准备哪一只的行情", 也就没有什么可准备的——引擎照旧去
        # 行情根里取它自己要的东西. 这不是失败, 更不该拦: 把它判成错, 等于让所有没映射这两个
        # 字段的策略从"能跑"变成"跑不了", 而它们本来就跑得挺好.
        logger.info(
            "运行所引用的策略未映射合约 (exchange_id=%r, instrument_id=%r), 跳过行情准备",
            request.exchange_id,
            request.instrument_id,
        )
        return MarketDataPrepared.READY

    _assert_component_in_place(quote_hub_root)

    frequency = quote_hub.bar_period_to_frequency(request.bar_period)

    if frequency is None:
        raise MarketDataUnavailableError(MARKET_DATA_PERIOD_UNSUPPORTED_MESSAGE)

    if not _is_a_platform_day(request.start_trading_day) or not _is_a_platform_day(
        request.end_trading_day
    ):
        # 那两个值由提交侧渲染进引擎配置, 正常路径上一定合法. 这里挡的是被改坏的运行行:
        # 不加这一问, 一个空串会一路走到切片格式化那里炸成 `调度器内部异常`, 把一个"行坏了"
        # 说成"平台有 bug".
        raise MarketDataUnavailableError(MARKET_DATA_REQUEST_INCOMPLETE_MESSAGE)

    start_day = quote_hub.format_component_day(request.start_trading_day)
    end_day = quote_hub.format_component_day(request.end_trading_day)

    facts = await _read_coverage_facts(
        quote_hub_root, contract_code, frequency, start_day, end_day
    )
    verdict = judge_coverage(facts.expected_days, facts.covered_days)

    if verdict.sufficient:
        return MarketDataPrepared.READY

    if request_exceeds_calendar(end_day, facts.calendar_last_day):
        logger.warning(
            "请求区间末端 %s 超出行情组件的交易日历末日 %s, 该区间的应有交易日无法比对",
            end_day,
            facts.calendar_last_day,
        )

    return await _refresh_and_rejudge(
        settings, contract_code, frequency, start_day, end_day, cancel_signal
    )


async def _read_coverage_facts(
    quote_hub_root: Path, contract_code: str, frequency: str, start_day: str, end_day: str
) -> quote_hub.CoverageFacts:
    """读判定要用的事实. 读库是阻塞 IO, 且一次开 40 MB 的库, 故整个丢进线程."""

    try:
        return await asyncio.to_thread(
            quote_hub.read_coverage_facts,
            quote_hub_root,
            contract_code,
            frequency,
            start_day,
            end_day,
        )
    except quote_hub.QuoteHubUnavailableError as error:
        # 上面那道存在性检查挡不住 schema 漂移, 这里兜住并换成给用户的固定文案.
        logger.error("读取行情组件数据库失败: %s", error)
        raise MarketDataUnavailableError(MARKET_DATA_COMPONENT_MISSING_MESSAGE) from error


async def _judge_again(
    quote_hub_root: Path, contract_code: str, frequency: str, start_day: str, end_day: str
) -> CoverageVerdict:
    """重新读一遍事实再判定."""

    facts = await _read_coverage_facts(
        quote_hub_root, contract_code, frequency, start_day, end_day
    )

    return judge_coverage(facts.expected_days, facts.covered_days)


async def _refresh_and_rejudge(
    settings: PlatformSettings,
    contract_code: str,
    frequency: str,
    start_day: str,
    end_day: str,
    cancel_signal: asyncio.Event,
) -> MarketDataPrepared:
    """下载 + 复判. 下载期间只准一个作业在跑."""

    async with REFRESH_LOCK:
        # 等在锁上的这段时间里, 前一个作业可能已经把数据下回来了. 复判一次能省掉一整轮整所重下.
        verdict = await _judge_again(
            settings.quote_hub_root, contract_code, frequency, start_day, end_day
        )

        if verdict.sufficient:
            return MarketDataPrepared.READY

        universe = await _read_refresh_universe(settings.quote_hub_root, contract_code)
        download_start, download_end = _resolve_download_window(
            verdict, start_day, end_day
        )
        command = quote_hub.build_download_command(
            frequency, download_start, download_end, universe, settings.market_data_root
        )

        outcome = await _run_download(
            command,
            settings.quote_hub_root,
            cancel_signal,
            settings.market_data_prepare_timeout_seconds,
        )

        if outcome.cancelled:
            return MarketDataPrepared.CANCELLED

        if outcome.timed_out:
            raise MarketDataUnavailableError(MARKET_DATA_PREPARE_TIMEOUT_MESSAGE)

        if outcome.exit_code != 0:
            logger.error("行情组件退出码 %s: %s", outcome.exit_code, outcome.stderr_tail)
            raise MarketDataUnavailableError(_classify_failure(outcome.stderr_tail))

        decision = decide_after_refresh(
            await _judge_again(
                settings.quote_hub_root, contract_code, frequency, start_day, end_day
            )
        )

        if decision is RefreshDecision.FAIL_INSUFFICIENT:
            # **退出码 0 不等于有数据**: 组件在库中无数据时只记一条 warning 仍以 0 退出.
            raise MarketDataUnavailableError(MARKET_DATA_STILL_INSUFFICIENT_MESSAGE)

        if decision is RefreshDecision.PROCEED_WITH_GAPS:
            logger.warning(
                "合约 %s 在 %s ~ %s 仍有交易日无 %s 分钟行情, 按上游空档放行",
                contract_code,
                start_day,
                end_day,
                frequency,
            )

        return MarketDataPrepared.READY


async def _run_download(
    command: list[str],
    quote_hub_root: Path,
    cancel_signal: asyncio.Event,
    timeout_seconds: int,
) -> quote_hub.DownloadOutcome:
    """起一次下载子进程. 起不来 (入口脚本存在但解释器跑不动一类) 也要有确切文案."""

    try:
        return await quote_hub.run_download_command(
            command, quote_hub_root, cancel_signal, timeout_seconds
        )
    except quote_hub.QuoteHubUnavailableError as error:
        logger.error("行情组件无法启动: %s", error)
        raise MarketDataUnavailableError(
            MARKET_DATA_COMPONENT_START_FAILED_MESSAGE
        ) from error


async def _read_refresh_universe(
    quote_hub_root: Path, contract_code: str
) -> list[str]:
    """本次要一并写进年度文件的全部合约: 库里下过的 ∪ 本次选中的.

    传全集而不是单合约, 是因为组件**只把传进去的代码写进文件**——传少了就把同所其他合约从文件里
    **静默挤掉** (它只记 warning, 不报错).
    """

    try:
        ingested_codes = await asyncio.to_thread(
            quote_hub.read_ingested_codes, quote_hub_root
        )
    except quote_hub.QuoteHubUnavailableError as error:
        logger.error("读取行情组件数据库失败: %s", error)
        raise MarketDataUnavailableError(MARKET_DATA_COMPONENT_MISSING_MESSAGE) from error

    return _merge_refresh_universe(ingested_codes, contract_code)


def _merge_refresh_universe(ingested_codes: list[str], contract_code: str) -> list[str]:
    """去重排序并卡长度上限.

    上限不是为了整齐: `--codes` 要挤进 Windows 那条约 32767 字符的命令行, 超了就是 CreateProcess
    的裸报错. 与其让它撞成系统错误, 不如在这里给一句能看懂的文案.
    """

    universe = sorted(set(ingested_codes) | {contract_code})

    if len(universe) > MAXIMUM_MARKET_DATA_CODES:
        raise MarketDataUnavailableError(MARKET_DATA_UNIVERSE_TOO_LARGE_MESSAGE)

    return universe


def _resolve_download_window(
    verdict: CoverageVerdict, start_day: str, end_day: str
) -> tuple[str, str]:
    """下载区间取**缺失日的最小~最大**, 不是用户请求的全区间.

    这是唯一的成本闸: 组件对传进去的每个代码都是整段重取, 没有"已有则跳过". 缺的若是区间两端的
    零散几天, 这一条能把重取量压到那几天, 而不是整段.
    """

    if not verdict.missing_days:
        # 日历算不出应有交易日 (区间落在组件日历之外) —— 没有"缺哪几天"可依据, 只能整段试一次.
        return start_day, end_day

    return verdict.missing_days[0], verdict.missing_days[-1]


def _is_a_platform_day(day: str) -> bool:
    """平台内部的交易日是 8 位 ASCII 数字 (`YYYYMMDD`).

    只认 ASCII: `str.isdigit()` 对阿拉伯-印度数字等也为真, 那种取值会在切片格式化处拼出一串
    组件读不懂的日期.
    """

    return len(day) == PLATFORM_DAY_LENGTH and day.isascii() and day.isdigit()


def _assert_component_in_place(quote_hub_root: Path) -> None:
    """三样缺一不可: 组件目录、入口脚本、数据库. 缺了就报明确文案, 而不是等子进程去撞."""

    if not (quote_hub_root / QUOTE_HUB_CLI_FILENAME).is_file():
        raise MarketDataUnavailableError(MARKET_DATA_COMPONENT_MISSING_MESSAGE)

    if not (quote_hub_root / QUOTE_HUB_DATABASE_FILENAME).is_file():
        raise MarketDataUnavailableError(MARKET_DATA_COMPONENT_MISSING_MESSAGE)


def _classify_failure(stderr_tail: str) -> str:
    """把非零退出分成两类. 两者对用户的指向完全不同: 一个是凭据/网络, 一个是其它."""

    if quote_hub.indicates_login_failure(stderr_tail):
        return MARKET_DATA_LOGIN_FAILED_MESSAGE

    return MARKET_DATA_DOWNLOAD_FAILED_MESSAGE
