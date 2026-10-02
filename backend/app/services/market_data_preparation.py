"""一轮回测开始前, 把这一轮要用的行情准备好.

在调度器起引擎**之前**跑: 先判本地已落地的行情够不够 (**够就一次子进程都不起**), 不够才请
QuoteHub 组件下载落地, 落地后**复判**再决定放行还是让这一轮失败.

四处设计要点:

  - **判据在 `market_data_coverage`, 事实在 `quote_hub`**, 本模块只做编排与失败分类.
  - **失败一律以固定中文文案收场**, 不带路径也不带组件原始输出 (同 `UnrunnableJob` 的纪律);
    组件输出只进日志.
  - **命令行传的是全集, 不是本次选中的那一个**: 组件只把传进去的合约写进年度文件, 少传一个
    就把那个合约从文件里**静默挤掉**. 全集取自组件库里"下过的全部代码", 故一次都不会漏
    (见 `quote_hub.read_ingested_codes`).
  - **下载区间对齐到年份边界**, 而不是只取缺失日的极值 (见 `_resolve_download_window`): 组件的
    年度文件本就按年整份导出, 只有取过整年, 第一次落地的那份才不含半年的空洞.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import (
    MARKET_DATA_PRECISION_FREQUENCY,
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
YEAR_LENGTH = 4

YEAR_START_MONTH_DAY = "-01-01"
YEAR_END_MONTH_DAY = "-12-31"


MARKET_DATA_COMPONENT_MISSING_MESSAGE = "行情组件不在位 (目录、入口脚本或数据库缺失)"
MARKET_DATA_COMPONENT_START_FAILED_MESSAGE = "行情组件无法启动"
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
    """一轮回测对行情的诉求. 起止都是平台内部的 8 位 `YYYYMMDD`.

    **没有周期**: 覆盖判据问的只是"这段区间问过上游没有", 而落盘精度是平台常量
    (`MARKET_DATA_PRECISION`). 用户选的订阅周期不改变这个答案——它在引擎装载期由那批 bar 聚合
    得到, 与组件库里的 `Frequency` 毫无关系.
    """

    exchange_id: str
    instrument_id: str
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
        # 这一轮**没有指名合约**: 提交时 `exchange_id` / `instrument_id` 留空, 或只填了其中一个.
        # 没有合约就无从表达"要准备哪一只的行情", 也就没有什么可准备的——引擎照旧去行情根里取
        # 它自己要的东西. 这不是失败, 更不该拦: 把它判成错, 等于让所有不指名合约的策略从"能跑"
        # 变成"跑不了", 而它们本来就跑得挺好.
        logger.info(
            "运行所引用的策略未映射合约 (exchange_id=%r, instrument_id=%r), 跳过行情准备",
            request.exchange_id,
            request.instrument_id,
        )
        return MarketDataPrepared.READY

    _assert_component_in_place(quote_hub_root)

    frequency = MARKET_DATA_PRECISION_FREQUENCY

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

    if _judge(facts).sufficient:
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


def _judge(facts: quote_hub.CoverageFacts) -> CoverageVerdict:
    """把读到的事实折成判定. 三处都走这一步, 折法只此一份."""

    return judge_coverage(facts.expected_days, facts.asked_days, facts.has_bars)


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
        facts = await _read_coverage_facts(
            settings.quote_hub_root, contract_code, frequency, start_day, end_day
        )
        verdict = _judge(facts)

        if verdict.sufficient:
            return MarketDataPrepared.READY

        window = _resolve_download_window(
            verdict, start_day, end_day, facts.calendar_last_day
        )

        if window is None:
            # 没有可问的区间了 (账上已经答过, 或整段落在组件日历之外). 不起子进程空跑一轮, 直接按
            # 同一张判定表收口: 区间内确有 bar 就放行, 一根都没有才给"区间内没有行情"那句话.
            return _finish_after_refresh(
                verdict, contract_code, frequency, start_day, end_day
            )

        universe = await _read_refresh_universe(settings.quote_hub_root, contract_code)
        download_start, download_end = window
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

        refreshed_facts = await _read_coverage_facts(
            settings.quote_hub_root, contract_code, frequency, start_day, end_day
        )

        return _finish_after_refresh(
            _judge(refreshed_facts), contract_code, frequency, start_day, end_day
        )


def _finish_after_refresh(
    verdict: CoverageVerdict,
    contract_code: str,
    frequency: str,
    start_day: str,
    end_day: str,
) -> MarketDataPrepared:
    """按收口那张判定表定这一轮的出口.

    **退出码 0 不等于有数据** (组件在库中无数据时只记一条 warning 仍以 0 退出), 故放行与否只能由
    复判说了算. `PROCEED_WITH_GAPS` 不是失败: 账没盖住整段而区间内确有 bar, 缺的那段是上游空档
    或未上市段, 补不上也不该把这一轮堵死, 只记一条 warning.
    """

    decision = decide_after_refresh(verdict)

    if decision is RefreshDecision.FAIL_INSUFFICIENT:
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
    verdict: CoverageVerdict,
    start_day: str,
    end_day: str,
    calendar_last_day: str | None,
) -> tuple[str, str] | None:
    """下载区间: 把**没问过的那一段**对齐到年份边界, 末端掐在组件日历末日; 无处可问时回 `None`.

    取的是缺失段的极值而不是用户请求的全区间 (组件对传进去的每个代码都是整段重取, 没有"已有则
    跳过"), 但两端要向外对齐到年界。对齐的理由是产出的年度 Parquet: 组件本就按年整份导出, 只有
    请求跨过了整年, 第一次落地的那份才不含半年的空洞, 后续同年的请求也才会直接判"够"而不必再
    取一次——否则半份的年度文件要等到下一次扩展区间才被补齐.

    末端必须掐有两条理由: 当前年份还没走完, 对齐到 12-31 就是把**未来**记进账里, 那些日子真到了
    反而不会再取; 而账一旦记上就没人会去纠正它。

    回 `None` 表示"没有可问的区间", 两种由来 —— 都在调用点按同一张判定表收口, 故这里不必分辨谁
    是谁, 只要别让注定白跑的一轮起子进程.
    """

    if not verdict.missing_days:
        if verdict.expected_days:
            # 账已经盖住整段, 而区间内一根 bar 都没有. 再问上游是同一个答案: 组件对传进去的区间
            # 整段重取, 而这段正是账上记着"问过、上游没给"的那段. 问不出新东西来, 故无所可问.
            return None

        # 日历算不出应有交易日 (区间落在组件日历之外) —— 没有"缺哪几天"可依据, 只能整段试一次.
        # **不能因为"日历里没有"就断定"上游没有"**: bar 是直接向上游取的, 与组件那张日历无关
        # (`TradeDates` 由组件自己的交易日同步维护, 不在平台会跑的那几条命令上), 日历落后于 bar
        # 是现实可达的状态.
        clamped_end = _clamp_day_to_calendar(end_day, calendar_last_day)

        return None if clamped_end < start_day else (start_day, clamped_end)

    window_start = _align_day_to_year_start(verdict.missing_days[0])
    window_end = _clamp_day_to_calendar(
        _align_day_to_year_end(verdict.missing_days[-1]), calendar_last_day
    )

    # 上面这一支不会倒过来: `missing_days` 的每一天都来自组件日历, 故 `window_start` 不晚于
    # `missing_days[0]`, 而它又不晚于日历末日——掐过之后 `window_start <= window_end` 恒成立.
    return window_start, window_end


def _align_day_to_year_start(day: str) -> str:
    """`2024-06-15` → `2024-01-01`."""

    return f"{day[:YEAR_LENGTH]}{YEAR_START_MONTH_DAY}"


def _align_day_to_year_end(day: str) -> str:
    """`2024-06-15` → `2024-12-31`."""

    return f"{day[:YEAR_LENGTH]}{YEAR_END_MONTH_DAY}"


def _clamp_day_to_calendar(day: str, calendar_last_day: str | None) -> str:
    """不越过组件日历末日; 日历里一天交易日都没有时 (`None`) 无从可比, 原样返回."""

    if calendar_last_day is None:
        return day

    return min(day, calendar_last_day)


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
