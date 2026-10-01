"""「本地已落地的行情够不够这一轮用」的判定表.

这里每一条都对应一种**判错就白跑一轮**的情形——把"本地没有"判成"有", 于是跳过下载, 引擎到装载
期才发现没有行情并以失败收场. 故判定表要穷举而不是抽两三条. 上半部分测纯逻辑 (输入是两个交易
日集合), 下半部分用一张**最小可用的桩库**测事实的读取: 真库有 40 MB, 而这里要验的只是"取哪张
表、过滤哪几列".

桩库的列名与 `quote_hub.EXPECTED_SCHEMA_COLUMNS` 是同源约定, 故它不是"随手画的一张表"——它
变了, 那套 schema 断言也该跟着变.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services import quote_hub
from app.services.market_data_coverage import (
    RefreshDecision,
    decide_after_refresh,
    judge_coverage,
    request_exceeds_calendar,
)


#: 桩库里的合约. 组件主键形态 (前缀.代码), 与 `EXCHANGE_PREFIX_TO_IDENTIFIER` 同一张表.
INGESTED_CONTRACT = "sh.600519"
OTHER_FREQUENCY_CONTRACT = "sz.000001"

FIVE_MINUTE_FREQUENCY = "5"
FIFTEEN_MINUTE_FREQUENCY = "15"

CALENDAR_START_DAY = "2024-01-01"
CALENDAR_LAST_TRADING_DAY = "2024-01-05"

#: 区间内的四个交易日. 中间夹着一个非交易日 `2024-01-03`, 它不得进入期望集合.
TRADING_DAYS = ("2024-01-01", "2024-01-02", "2024-01-04", "2024-01-05")
NON_TRADING_DAY = "2024-01-03"


def test_covers_every_expected_day() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS))

    assert verdict.sufficient
    assert verdict.missing_days == ()


def test_missing_day_in_the_middle() -> None:
    verdict = judge_coverage(
        list(TRADING_DAYS), ["2024-01-01", "2024-01-02", "2024-01-05"]
    )

    assert not verdict.sufficient
    assert verdict.missing_days == ("2024-01-04",)


def test_missing_first_day() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS[1:]))

    assert not verdict.sufficient
    assert verdict.missing_days == (TRADING_DAYS[0],)


def test_missing_last_day() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS[:-1]))

    assert not verdict.sufficient
    assert verdict.missing_days == (TRADING_DAYS[-1],)


def test_half_year_of_coverage_is_not_enough_for_a_full_year() -> None:
    first_half = [f"2024-01-{day:02d}" for day in range(1, 10)]
    second_half = [f"2024-07-{day:02d}" for day in range(1, 10)]

    verdict = judge_coverage(first_half + second_half, first_half)

    assert not verdict.sufficient
    assert verdict.missing_days == tuple(second_half)


def test_coverage_spanning_a_year_boundary() -> None:
    expected = ["2023-12-29", "2024-01-02", "2024-01-03"]
    covered = ["2023-12-29", "2024-01-02"]

    verdict = judge_coverage(expected, covered)

    assert not verdict.sufficient
    assert verdict.missing_days == ("2024-01-03",)


def test_duplicate_days_are_collapsed_before_comparing() -> None:
    verdict = judge_coverage(
        list(TRADING_DAYS) + list(TRADING_DAYS) + ["2024-01-02"],
        list(TRADING_DAYS),
    )

    assert verdict.sufficient
    assert verdict.expected_days == TRADING_DAYS


def test_no_expected_days_is_never_sufficient() -> None:
    """日历算不出应有交易日时**恒为假**: 无法校验不等于够了."""

    verdict = judge_coverage([], ["2024-01-01"])

    assert not verdict.sufficient
    assert verdict.has_any_coverage


def test_extra_covered_days_do_not_make_it_insufficient() -> None:
    verdict = judge_coverage(
        list(TRADING_DAYS), list(TRADING_DAYS) + ["2023-12-29", "2024-01-08"]
    )

    assert verdict.sufficient


def test_suspension_gap_after_refresh_is_allowed_through() -> None:
    """缺口补不上 (上游对停牌日就是不返回数据) 时放行——否则含停牌日的区间会每次提交都重下."""

    verdict = judge_coverage(list(TRADING_DAYS), ["2024-01-01", "2024-01-02"])

    assert verdict.has_any_coverage
    assert decide_after_refresh(verdict) is RefreshDecision.PROCEED_WITH_GAPS


def test_zero_rows_after_refresh_is_failed() -> None:
    """区间内该频率一行都没有: 再刷也刷不出来, 以失败收场好过安静地出零结果."""

    verdict = judge_coverage(list(TRADING_DAYS), [])

    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_no_calendar_and_no_rows_after_refresh_is_failed() -> None:
    """**先问"有没有数据"而不是"缺不缺"**: 反过来的话这一格会落到"缺失为空 → 齐全"."""

    verdict = judge_coverage([], [])

    assert not verdict.missing_days
    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_full_coverage_after_refresh_proceeds() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS))

    assert decide_after_refresh(verdict) is RefreshDecision.PROCEED


@pytest.mark.parametrize(
    ("end_day", "calendar_last_day", "expected"),
    [
        ("2024-01-05", "2024-01-05", False),
        ("2024-01-04", "2024-01-05", False),
        ("2024-01-08", "2024-01-05", True),
        ("2024-01-05", None, True),
    ],
)
def test_request_beyond_calendar(
    end_day: str, calendar_last_day: str | None, expected: bool
) -> None:
    assert request_exceeds_calendar(end_day, calendar_last_day) is expected


def test_splitting_and_rebuilding_a_contract_code() -> None:
    assert quote_hub.split_contract_code(INGESTED_CONTRACT) == ("SSE", "600519")
    assert quote_hub.build_contract_code("SSE", "600519") == INGESTED_CONTRACT
    assert quote_hub.split_contract_code("xx.600519") is None
    assert quote_hub.split_contract_code("600519") is None
    assert quote_hub.build_contract_code("NYSE", "600519") is None


def test_calendar_days_are_converted_in_both_directions() -> None:
    assert quote_hub.format_component_day("20241231") == "2024-12-31"
    assert quote_hub.format_platform_day("2024-12-31") == "20241231"


def _build_stub_database(quote_hub_root: Path) -> None:
    """一张只够判据用的桩库: 三张表、四个交易日、一只有 5m 数据的合约."""

    connection = sqlite3.connect(quote_hub_root / quote_hub.QUOTE_HUB_DATABASE_FILENAME)

    connection.executescript(
        """
        CREATE TABLE Securities (
            Code TEXT PRIMARY KEY, CodeName TEXT, StockType TEXT,
            Exchange TEXT, CurrentTradeStatus TEXT
        );
        CREATE TABLE TradeDates (CalendarDate TEXT, IsTradingDay INTEGER);
        CREATE TABLE MinuteBars (
            Code TEXT, Frequency TEXT, Time TEXT, TradingDay TEXT,
            PRIMARY KEY (Code, Frequency, Time)
        );
        """
    )

    connection.executemany(
        "INSERT INTO Securities VALUES (?, ?, ?, ?, ?)",
        [
            (INGESTED_CONTRACT, "贵州茅台", "1", "sh", "1"),
            (OTHER_FREQUENCY_CONTRACT, "平安银行", "1", "sz", "1"),
            ("sh.000300", "沪深300", "2", "sh", "1"),
            ("sh.510300", "沪深300ETF", "5", "sh", "1"),
        ],
    )

    calendar_days = [*TRADING_DAYS[:2], NON_TRADING_DAY, *TRADING_DAYS[2:]]
    connection.executemany(
        "INSERT INTO TradeDates VALUES (?, ?)",
        [
            (day, 0 if day == NON_TRADING_DAY else 1)
            for day in calendar_days
        ],
    )

    connection.executemany(
        "INSERT INTO MinuteBars VALUES (?, ?, ?, ?)",
        [
            (INGESTED_CONTRACT, FIVE_MINUTE_FREQUENCY, f"{day} 09:35:00", day)
            for day in TRADING_DAYS
        ],
    )

    connection.commit()
    connection.close()


@pytest.fixture
def quote_hub_root(tmp_path: Path) -> Path:
    _build_stub_database(tmp_path)

    return tmp_path


def test_coverage_facts_are_read_for_one_frequency_only(quote_hub_root: Path) -> None:
    """事实按**具体频率**读: 换一档库里没有的, 那一档就是零覆盖.

    这一条钉在函数一级, 记的是"`MinuteBars` 的 `Frequency` 列是判据的一部分": 少了这一列, 判据
    会把"别的周期有数据"读成"这个周期有数据", 于是跳过下载, 引擎到装载期才发现没有行情.

    平台只问**落盘精度**那一档 (`MARKET_DATA_PRECISION_FREQUENCY`), 用户的订阅周期不进这条路
    ——它只决定引擎在运行时把 5m 聚合成多粗的 bar. 故这里构造的"15 分钟"不是平台会问的一种取值,
    而是用来证明频率确实参与了判据.
    """

    facts = quote_hub.read_coverage_facts(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIFTEEN_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )
    verdict = judge_coverage(facts.expected_days, facts.covered_days)

    assert not verdict.sufficient
    assert not verdict.has_any_coverage
    assert verdict.expected_days == TRADING_DAYS
    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_five_minute_coverage_is_enough(quote_hub_root: Path) -> None:
    facts = quote_hub.read_coverage_facts(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )
    verdict = judge_coverage(facts.expected_days, facts.covered_days)

    assert verdict.sufficient
    assert facts.calendar_last_day == CALENDAR_LAST_TRADING_DAY


def test_contract_without_any_bars_is_not_enough(quote_hub_root: Path) -> None:
    facts = quote_hub.read_coverage_facts(
        quote_hub_root,
        OTHER_FREQUENCY_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )
    verdict = judge_coverage(facts.expected_days, facts.covered_days)

    assert not verdict.sufficient
    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_non_trading_days_are_not_expected(quote_hub_root: Path) -> None:
    facts = quote_hub.read_coverage_facts(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    assert NON_TRADING_DAY not in facts.expected_days


def test_range_beyond_the_calendar_has_no_expected_days(quote_hub_root: Path) -> None:
    """区间整个落在组件日历之外时, 期望集合为空 —— 判定不得据此说"够了"."""

    facts = quote_hub.read_coverage_facts(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        "2024-06-01",
        "2024-06-30",
    )
    verdict = judge_coverage(facts.expected_days, facts.covered_days)

    assert not verdict.expected_days
    assert not verdict.sufficient
    assert request_exceeds_calendar("2024-06-30", facts.calendar_last_day)


def test_ingested_codes_are_the_whole_minute_bar_universe(quote_hub_root: Path) -> None:
    assert quote_hub.read_ingested_codes(quote_hub_root) == [INGESTED_CONTRACT]


def test_tradable_contracts_exclude_indices_and_funds(quote_hub_root: Path) -> None:
    contracts = quote_hub.read_tradable_contracts(quote_hub_root)

    assert [contract.code for contract in contracts] == [
        INGESTED_CONTRACT,
        OTHER_FREQUENCY_CONTRACT,
    ]
    assert contracts[0].exchange_id == "SSE"
    assert contracts[0].instrument_id == "600519"
    assert contracts[0].display_name == "贵州茅台"


def test_missing_database_reports_unavailable(tmp_path: Path) -> None:
    with pytest.raises(quote_hub.QuoteHubUnavailableError):
        quote_hub.read_ingested_codes(tmp_path)


def test_drifted_schema_reports_unavailable_instead_of_no_data(tmp_path: Path) -> None:
    """schema 漂移必须**显式报错**: 当作"没数据"会让判据安静地判错, 那正是要防的事."""

    connection = sqlite3.connect(tmp_path / quote_hub.QUOTE_HUB_DATABASE_FILENAME)
    connection.executescript(
        """
        CREATE TABLE Securities (Code TEXT PRIMARY KEY, CodeName TEXT);
        CREATE TABLE TradeDates (CalendarDate TEXT, IsTradingDay INTEGER);
        CREATE TABLE MinuteBars (Code TEXT, Frequency TEXT, TradingDay TEXT);
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(quote_hub.QuoteHubUnavailableError):
        quote_hub.read_ingested_codes(tmp_path)
