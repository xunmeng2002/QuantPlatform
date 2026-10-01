"""行情准备这一步: 什么时候起组件、命令长什么样、失败了怎么说.

组件用 `tests/quote_hub_stub.py` 的替身, 它落在 `tmp_path` 里并以自己的目录为 cwd 被启动——
这与真组件唯一的接触面 (库 schema + CLI 参数 + cwd) 逐条对上, 故不需要往生产代码里开注入缝.

这里最要紧的一条是「够了就**一个子进程都不起**」: 判据判错的代价不是多下一点数据, 而是把
"本地明明有"变成"每次都整所重下".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import PlatformSettings
from app.services import quote_hub
from app.services.market_data_preparation import (
    MARKET_DATA_COMPONENT_MISSING_MESSAGE,
    MARKET_DATA_COMPONENT_START_FAILED_MESSAGE,
    MARKET_DATA_DOWNLOAD_FAILED_MESSAGE,
    MARKET_DATA_LOGIN_FAILED_MESSAGE,
    MARKET_DATA_PERIOD_UNSUPPORTED_MESSAGE,
    MARKET_DATA_PREPARE_TIMEOUT_MESSAGE,
    MARKET_DATA_REQUEST_INCOMPLETE_MESSAGE,
    MARKET_DATA_STILL_INSUFFICIENT_MESSAGE,
    MarketDataPrepared,
    MarketDataRequest,
    MarketDataUnavailableError,
    _run_download,
    ensure_market_data_available,
)

from .quote_hub_stub import (
    DEFAULT_CONTRACT_CODE,
    DEFAULT_FREQUENCY,
    STUB_EXCHANGE,
    STUB_STOCK_TYPE,
    build_covered_component,
    count_starts,
    enable_bar_ingestion,
    read_recorded_arguments,
    set_delay_seconds,
    set_exit_code,
    write_catalog_database,
    write_stub_component,
)


REQUESTED_EXCHANGE_ID = "SSE"
REQUESTED_INSTRUMENT_ID = "600519"
REQUESTED_BAR_PERIOD = "5m"

#: 区间落在桩库日历之内, 但那只新合约还没有任何 bar —— 于是判据回"不够", 走下载那条路.
WINDOW_START_DAY = "20240102"
WINDOW_END_DAY = "20240105"

OTHER_INGESTED_CONTRACT_CODE = "sz.000001"
NEW_CONTRACT_CODE = "sz.300750"

TEST_PREPARE_TIMEOUT_SECONDS = 30
TEST_SHORT_PREPARE_TIMEOUT_SECONDS = 1
STUB_DELAY_SECONDS = 5
CANCEL_REACTION_SECONDS = 0.4

LOGIN_FAILURE_STDERR = "BaoStock 登录失败"
QUOTE_HUB_CLI_FILENAME = quote_hub.QUOTE_HUB_CLI_FILENAME


def _build_settings(
    tmp_path: Path, quote_hub_root: Path, **overrides: object
) -> PlatformSettings:
    overrides.setdefault(
        "market_data_prepare_timeout_seconds", TEST_PREPARE_TIMEOUT_SECONDS
    )

    return PlatformSettings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        engine_root=tmp_path / "engine",
        runs_root=tmp_path / "runs",
        user_library_root=tmp_path / "users",
        jwt_secret_key="market-data-preparation-test-key",
        access_token_expire_minutes=60,
        max_concurrent_runs=1,
        run_timeout_seconds=60,
        initial_admin_username="admin",
        initial_admin_password=None,
        http_host="127.0.0.1",
        http_port=8000,
        market_data_root=tmp_path / "market-data",
        session_file_path=tmp_path / "engine" / "Sessions.json",
        seed_database_path=tmp_path / "engine" / "BackTestInit.db",
        quote_hub_root=quote_hub_root,
        **overrides,
    )


def _build_request(
    exchange_id: str = REQUESTED_EXCHANGE_ID,
    instrument_id: str = REQUESTED_INSTRUMENT_ID,
    bar_period: str = REQUESTED_BAR_PERIOD,
    start_trading_day: str = WINDOW_START_DAY,
    end_trading_day: str = WINDOW_END_DAY,
) -> MarketDataRequest:
    return MarketDataRequest(
        exchange_id=exchange_id,
        instrument_id=instrument_id,
        bar_period=bar_period,
        start_trading_day=start_trading_day,
        end_trading_day=end_trading_day,
    )


def _window_calendar_days() -> tuple[str, ...]:
    """请求区间内的四个交易日 (`2024-01-02` ~ `2024-01-05`)."""

    return ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05")


@pytest.fixture
def covered_component(tmp_path: Path) -> Path:
    return build_covered_component(tmp_path / "quote-hub")


@pytest.fixture
def download_component(tmp_path: Path) -> Path:
    """日历覆盖整个测试区间, 库里下过的是另一只 —— 于是**本次请求的那只一条 bar 都没有**.

    "下过的" 与 "请求的" 刻意错开: 全集那条断言要能区分"库里已有的"与"本次选中的"两个来源,
    重合的话两者就分不出来了.
    """

    component_root = tmp_path / "quote-hub"
    write_stub_component(component_root)
    write_catalog_database(
        component_root,
        covered_contracts={DEFAULT_FREQUENCY: (OTHER_INGESTED_CONTRACT_CODE,)},
        calendar_days=_window_calendar_days(),
        contracts=(
            (DEFAULT_CONTRACT_CODE, "贵州茅台", STUB_STOCK_TYPE, STUB_EXCHANGE),
            (OTHER_INGESTED_CONTRACT_CODE, "平安银行", STUB_STOCK_TYPE, "sz"),
            (NEW_CONTRACT_CODE, "宁德时代", STUB_STOCK_TYPE, "sz"),
        ),
    )

    return component_root


async def test_a_covered_request_starts_no_subprocess(
    tmp_path: Path, covered_component: Path
) -> None:
    """本地已落地就**一个子进程都不起** —— 这是整件事的成本闸."""

    settings = _build_settings(tmp_path, covered_component)
    request = _build_request(
        start_trading_day="20240102", end_trading_day="20240105"
    )

    prepared = await ensure_market_data_available(
        settings, request, asyncio.Event()
    )

    assert prepared is MarketDataPrepared.READY
    assert count_starts(covered_component) == 0


async def test_an_unmapped_contract_skips_preparation(
    tmp_path: Path, covered_component: Path
) -> None:
    """manifest 没映射合约的策略照旧能跑: 没有合约就没什么可准备的, 不该拦."""

    settings = _build_settings(tmp_path, covered_component)
    request = _build_request(exchange_id="", instrument_id="")

    prepared = await ensure_market_data_available(
        settings, request, asyncio.Event()
    )

    assert prepared is MarketDataPrepared.READY
    assert count_starts(covered_component) == 0


async def test_missing_coverage_downloads_the_whole_universe(
    tmp_path: Path, download_component: Path
) -> None:
    """不够时起一次组件, 且 `--codes` 必须是**全集** —— 少传一个就把那个合约从年度文件里挤掉."""

    enable_bar_ingestion(download_component)
    settings = _build_settings(tmp_path, download_component)

    prepared = await ensure_market_data_available(
        settings, _build_request(), asyncio.Event()
    )

    assert prepared is MarketDataPrepared.READY
    assert count_starts(download_component) == 1

    recorded_arguments = read_recorded_arguments(download_component)

    assert recorded_arguments is not None
    assert recorded_arguments[0] == "backfill"

    recorded_options = dict(
        zip(recorded_arguments[1::2], recorded_arguments[2::2])
    )

    assert recorded_options["--frequency"] == DEFAULT_FREQUENCY
    assert recorded_options["--output-root"] == str(settings.market_data_root)
    assert recorded_options["--start"] == "2024-01-02"
    assert recorded_options["--end"] == "2024-01-05"
    # 全集 = 库里下过的 ∪ 本次选中的. 漏掉任何一边, 那个合约都会被从年度文件里**静默挤掉**.
    assert recorded_options["--codes"].split(",") == sorted(
        [DEFAULT_CONTRACT_CODE, OTHER_INGESTED_CONTRACT_CODE]
    )


async def test_a_contract_never_downloaded_before_joins_the_universe(
    tmp_path: Path, download_component: Path
) -> None:
    """库里没下过的合约也要进 `--codes`, 否则下完它自己不在文件里."""

    enable_bar_ingestion(download_component)
    settings = _build_settings(tmp_path, download_component)

    await ensure_market_data_available(
        settings,
        _build_request(exchange_id="SZSE", instrument_id="300750"),
        asyncio.Event(),
    )

    recorded_arguments = read_recorded_arguments(download_component)
    assert recorded_arguments is not None

    recorded_options = dict(
        zip(recorded_arguments[1::2], recorded_arguments[2::2])
    )

    assert recorded_options["--codes"].split(",") == sorted(
        [OTHER_INGESTED_CONTRACT_CODE, NEW_CONTRACT_CODE]
    )


async def test_the_download_window_starts_at_the_first_missing_day(
    tmp_path: Path, download_component: Path
) -> None:
    """区间取**缺失日的最小~最大**而不是用户请求的全区间 —— 这是唯一的成本闸."""

    enable_bar_ingestion(download_component)
    settings = _build_settings(tmp_path, download_component)

    await ensure_market_data_available(
        settings, _build_request(), asyncio.Event()
    )

    recorded_arguments = read_recorded_arguments(download_component)
    assert recorded_arguments is not None

    recorded_options = dict(
        zip(recorded_arguments[1::2], recorded_arguments[2::2])
    )

    assert recorded_options["--start"] == "2024-01-02"
    assert recorded_options["--end"] == "2024-01-05"


async def test_a_login_failure_points_at_credentials(
    tmp_path: Path, download_component: Path
) -> None:
    """登录失败与其它非零退出**必须分开**: 一个指向凭据与网络, 一个不指向."""

    set_exit_code(download_component, 1, LOGIN_FAILURE_STDERR)
    settings = _build_settings(tmp_path, download_component)

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(settings, _build_request(), asyncio.Event())

    assert failure.value.message == MARKET_DATA_LOGIN_FAILED_MESSAGE


async def test_any_other_nonzero_exit_is_a_plain_download_failure(
    tmp_path: Path, download_component: Path
) -> None:
    set_exit_code(download_component, 1, "traceback: something else")
    settings = _build_settings(tmp_path, download_component)

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(settings, _build_request(), asyncio.Event())

    assert failure.value.message == MARKET_DATA_DOWNLOAD_FAILED_MESSAGE


async def test_exit_code_zero_without_bars_is_still_insufficient(
    tmp_path: Path, download_component: Path
) -> None:
    """退出码 0 **不等于**有数据: 真组件在库中无数据时只记一条 warning 仍以 0 退出.

    故放行与否只能由复判说了算 —— 只看退出码就会把一个必然空跑的区间放过去.
    """

    settings = _build_settings(tmp_path, download_component)

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(settings, _build_request(), asyncio.Event())

    assert failure.value.message == MARKET_DATA_STILL_INSUFFICIENT_MESSAGE


async def test_a_download_that_outruns_its_deadline_is_reported_as_such(
    tmp_path: Path, download_component: Path
) -> None:
    set_delay_seconds(download_component, STUB_DELAY_SECONDS)
    settings = _build_settings(
        tmp_path,
        download_component,
        market_data_prepare_timeout_seconds=TEST_SHORT_PREPARE_TIMEOUT_SECONDS,
    )

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(settings, _build_request(), asyncio.Event())

    assert failure.value.message == MARKET_DATA_PREPARE_TIMEOUT_MESSAGE


async def test_a_cancelled_preparation_is_not_reported_as_a_failure(
    tmp_path: Path, download_component: Path
) -> None:
    """取消**不设错误文案**: 终态由调度器的仲裁表按取消算成 `interrupted`, 复用现成路径."""

    set_delay_seconds(download_component, STUB_DELAY_SECONDS)
    enable_bar_ingestion(download_component)
    settings = _build_settings(tmp_path, download_component)
    cancel_signal = asyncio.Event()

    async def _request_cancellation() -> None:
        await asyncio.sleep(CANCEL_REACTION_SECONDS)
        cancel_signal.set()

    cancellation = asyncio.ensure_future(_request_cancellation())

    try:
        prepared = await ensure_market_data_available(
            settings, _build_request(), cancel_signal
        )
    finally:
        cancellation.cancel()

    assert prepared is MarketDataPrepared.CANCELLED


async def test_concurrent_preparations_start_the_component_only_once(
    tmp_path: Path, download_component: Path
) -> None:
    """单飞: 两个作业同时缺数据, 不能各起一个组件进程去写同一批落地文件.

    第一个进锁的负责下载; 第二个在锁上等到它出来, 复判已是"够了", 于是 **不再起进程**.
    """

    set_delay_seconds(download_component, CANCEL_REACTION_SECONDS)
    enable_bar_ingestion(download_component)
    settings = _build_settings(tmp_path, download_component)

    prepared_results = await asyncio.gather(
        ensure_market_data_available(settings, _build_request(), asyncio.Event()),
        ensure_market_data_available(settings, _build_request(), asyncio.Event()),
        ensure_market_data_available(settings, _build_request(), asyncio.Event()),
    )

    assert prepared_results == [MarketDataPrepared.READY] * 3
    assert count_starts(download_component) == 1


async def test_an_unsupported_bar_period_is_rejected_before_any_subprocess(
    tmp_path: Path, download_component: Path
) -> None:
    """周期与落地文件后缀必须逐字一致, 否则引擎过滤后得 0 根 bar —— **静默 0 成交**."""

    settings = _build_settings(tmp_path, download_component)

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(
            settings, _build_request(bar_period="1d"), asyncio.Event()
        )

    assert failure.value.message == MARKET_DATA_PERIOD_UNSUPPORTED_MESSAGE
    assert count_starts(download_component) == 0


async def test_incomplete_trading_days_are_rejected(tmp_path: Path, download_component: Path) -> None:
    """被改坏的运行行会给出确切文案, 而不是一路走到切片格式化那里炸成"调度器内部异常"."""

    settings = _build_settings(tmp_path, download_component)

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(
            settings, _build_request(start_trading_day=""), asyncio.Event()
        )

    assert failure.value.message == MARKET_DATA_REQUEST_INCOMPLETE_MESSAGE


async def test_a_missing_component_is_reported_without_starting_anything(
    tmp_path: Path,
) -> None:
    component_root = tmp_path / "quote-hub"
    write_stub_component(component_root)
    settings = _build_settings(tmp_path, component_root)

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(settings, _build_request(), asyncio.Event())

    assert failure.value.message == MARKET_DATA_COMPONENT_MISSING_MESSAGE


async def test_a_missing_entry_script_is_reported_the_same_way(tmp_path: Path) -> None:
    """同名**目录**不算"入口脚本在位" —— 起进程只会得到 WinError, 那句话说不到点子上."""

    component_root = tmp_path / "quote-hub"
    write_catalog_database(
        component_root,
        covered_contracts={},
        calendar_days=_window_calendar_days(),
        contracts=(),
    )
    (component_root / QUOTE_HUB_CLI_FILENAME).mkdir()

    settings = _build_settings(tmp_path, component_root)

    with pytest.raises(MarketDataUnavailableError) as failure:
        await ensure_market_data_available(settings, _build_request(), asyncio.Event())

    assert failure.value.message == MARKET_DATA_COMPONENT_MISSING_MESSAGE


async def test_an_unlaunchable_component_is_reported_as_a_start_failure(
    tmp_path: Path,
) -> None:
    """进程都没起来与"起来了但退出码非零"要分开.

    这一格走的是 `OSError` 那条路 (解释器本身起不来), 从外部很难造: 入口脚本存在性那道检查已
    经挡住了"文件不在", 而"文件在但跑不动"通常是解释器退非零、落到下载失败那一格. 故这里直接
    验命令那一段, 而不是绕路去伪造一个不可启动的组件目录.
    """

    settings = _build_settings(tmp_path, tmp_path / "quote-hub")

    with pytest.raises(MarketDataUnavailableError) as failure:
        await _run_download(
            [str(tmp_path / "no-such-interpreter")],
            settings.quote_hub_root,
            asyncio.Event(),
            TEST_PREPARE_TIMEOUT_SECONDS,
        )

    assert failure.value.message == MARKET_DATA_COMPONENT_START_FAILED_MESSAGE
