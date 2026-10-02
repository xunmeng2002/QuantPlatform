"""「本地已落地的行情够不够这一轮用」的判定表.

这里每一条都对应一种**判错就白跑一轮**的情形——把"本地没有"判成"有", 于是跳过下载, 引擎到装载
期才发现没有行情并以失败收场. 故判定表要穷举而不是抽两三条. 上半部分测纯逻辑 (输入是两个交易
日集合与一个"有没有 bar"), 下半部分用一张**最小可用的桩库**测事实的读取: 真库有 40 MB, 而这里
要验的只是"取哪几张表、过滤哪几列".

判据的事实有**两个来源**, 它们答的不是同一个问题, 故下半部分对两者各钉一条:

  - `QueriedBarSpans` 记「问过上游哪一段」——覆盖判据用它。停牌日在 `MinuteBars` 里永远没有行,
    只按 bar 判就会每次提交都重下一遍.
  - `MinuteBars` 记「哪几天真有 bar」——只在"区间内一根都没有"时用它拦下必然空跑的一轮.

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


#: 账与 bar 都在, 且完全覆盖 —— 判"够".
INGESTED_CONTRACT = "sh.600519"

#: 区间内有**停牌日**: 账覆盖了整段, 但那一天没有 bar. 判"够"是对的, 否则每次提交都重下.
SUSPENSION_CONTRACT = "sh.600004"

#: 有 bar 但**账里没有** (账表是后加的, 老数据都是这个样子). 判"没问过", 于是再取一次.
UNRECORDED_SPAN_CONTRACT = "sh.601398"

#: 账只盖住了区间的前两天, 后两天在账之外. 判"缺", 补的就是那两天.
PARTIAL_LEDGER_CONTRACT = "sh.601988"

#: 一条 bar 也没有, 账里也没有. 判"不够".
OTHER_FREQUENCY_CONTRACT = "sz.000001"

FIVE_MINUTE_FREQUENCY = "5"
FIFTEEN_MINUTE_FREQUENCY = "15"

CALENDAR_START_DAY = "2024-01-01"
CALENDAR_LAST_TRADING_DAY = "2024-01-05"

#: 区间内的四个交易日. 中间夹着一个非交易日 `2024-01-03`, 它不得进入期望集合.
TRADING_DAYS = ("2024-01-01", "2024-01-02", "2024-01-04", "2024-01-05")
NON_TRADING_DAY = "2024-01-03"

#: `SUSPENSION_CONTRACT` 唯一没有 bar 的那个交易日 (上游对停牌日就是不返回数据).
SUSPENDED_TRADING_DAY = "2024-01-04"

FULL_MONTH_SPAN = ("2024-01-01", "2024-01-31")

#: `PARTIAL_LEDGER_CONTRACT` 的账: 只盖到第二个交易日, 后两个交易日留在账外.
PARTIAL_LEDGER_SPAN = ("2024-01-01", "2024-01-02")


def test_covers_every_expected_day() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS), has_bars=True)

    assert verdict.sufficient
    assert verdict.missing_days == ()


def test_missing_day_in_the_middle() -> None:
    verdict = judge_coverage(
        list(TRADING_DAYS), ["2024-01-01", "2024-01-02", "2024-01-05"], has_bars=True
    )

    assert not verdict.sufficient
    assert verdict.missing_days == ("2024-01-04",)


def test_missing_first_day() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS[1:]), has_bars=True)

    assert not verdict.sufficient
    assert verdict.missing_days == (TRADING_DAYS[0],)


def test_missing_last_day() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS[:-1]), has_bars=True)

    assert not verdict.sufficient
    assert verdict.missing_days == (TRADING_DAYS[-1],)


def test_half_year_of_coverage_is_not_enough_for_a_full_year() -> None:
    first_half = [f"2024-01-{day:02d}" for day in range(1, 10)]
    second_half = [f"2024-07-{day:02d}" for day in range(1, 10)]

    verdict = judge_coverage(first_half + second_half, first_half, has_bars=True)

    assert not verdict.sufficient
    assert verdict.missing_days == tuple(second_half)


def test_coverage_spanning_a_year_boundary() -> None:
    expected = ["2023-12-29", "2024-01-02", "2024-01-03"]
    covered = ["2023-12-29", "2024-01-02"]

    verdict = judge_coverage(expected, covered, has_bars=True)

    assert not verdict.sufficient
    assert verdict.missing_days == ("2024-01-03",)


def test_duplicate_days_are_collapsed_before_comparing() -> None:
    verdict = judge_coverage(
        list(TRADING_DAYS) + list(TRADING_DAYS) + ["2024-01-02"],
        list(TRADING_DAYS),
        has_bars=True,
    )

    assert verdict.sufficient
    assert verdict.expected_days == TRADING_DAYS


def test_no_expected_days_is_never_sufficient() -> None:
    """日历算不出应有交易日时**恒为假**: 无法校验不等于够了."""

    verdict = judge_coverage([], ["2024-01-01"], has_bars=True)

    assert not verdict.sufficient
    assert verdict.has_bars


def test_extra_asked_days_do_not_make_it_insufficient() -> None:
    verdict = judge_coverage(
        list(TRADING_DAYS), list(TRADING_DAYS) + ["2023-12-29", "2024-01-08"], has_bars=True
    )

    assert verdict.sufficient


def test_a_gap_the_ledger_does_not_cover_is_let_through() -> None:
    """账没盖住整段、但区间内确有 bar 时放行 —— 补不上的那一段不该把整轮堵死."""

    verdict = judge_coverage(list(TRADING_DAYS), ["2024-01-01", "2024-01-02"], has_bars=True)

    assert verdict.has_bars
    assert decide_after_refresh(verdict) is RefreshDecision.PROCEED_WITH_GAPS


def test_zero_rows_after_refresh_is_failed() -> None:
    """区间内该频率一行都没有: 再刷也刷不出来, 以失败收场好过安静地出零结果.

    这一条与账的状态**无关**: 账可以盖住整段 (问过了), 但上游什么都没给, 放行只会让引擎在装载
    期报 `ErrorMarketDataNotExist` —— 那时这一轮已经白跑了.
    """

    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS), has_bars=False)

    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_zero_rows_is_judged_the_same_before_and_after_a_download() -> None:
    """同一批事实, 下载**前**与下载**后**必须是同一个结论.

    账盖住了整段却一根 bar 都没有, 是退市或整段停牌的合约的样子. 若 `sufficient` 不问 `has_bars`,
    这一格会在下载前判"够"、下载后判"不够"——`ensure_market_data_available` 看的是前者, 于是这一
    轮被放行到引擎, 报错退化成装载期的 `ErrorMarketDataNotExist`, 平台自己那句话再没机会说.
    """

    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS), has_bars=False)

    assert not verdict.sufficient
    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_no_calendar_and_no_rows_after_refresh_is_failed() -> None:
    """**先问"有没有 bar"而不是"缺不缺"**: 反过来的话这一格会落到"缺失为空 → 齐全"."""

    verdict = judge_coverage([], [], has_bars=False)

    assert not verdict.missing_days
    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_full_coverage_after_refresh_proceeds() -> None:
    verdict = judge_coverage(list(TRADING_DAYS), list(TRADING_DAYS), has_bars=True)

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


REQUIRED_TABLES_SQL = """
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

QUERIED_BAR_SPANS_SQL = """
    CREATE TABLE QueriedBarSpans (
        Code TEXT, Frequency TEXT, StartDay TEXT, EndDay TEXT,
        PRIMARY KEY (Code, Frequency, StartDay)
    );
"""


def _build_stub_database(quote_hub_root: Path) -> None:
    """一张只够判据用的桩库: 四个交易日、四只合约, 三者的账与 bar 状态各不相同."""

    connection = sqlite3.connect(quote_hub_root / quote_hub.QUOTE_HUB_DATABASE_FILENAME)
    connection.executescript(REQUIRED_TABLES_SQL + QUERIED_BAR_SPANS_SQL)

    connection.executemany(
        "INSERT INTO Securities VALUES (?, ?, ?, ?, ?)",
        [
            (INGESTED_CONTRACT, "贵州茅台", "1", "sh", "1"),
            (SUSPENSION_CONTRACT, "白云机场", "1", "sh", "1"),
            (UNRECORDED_SPAN_CONTRACT, "工商银行", "1", "sh", "1"),
            (PARTIAL_LEDGER_CONTRACT, "中国银行", "1", "sh", "1"),
            (OTHER_FREQUENCY_CONTRACT, "平安银行", "1", "sz", "1"),
            ("sh.000300", "沪深300", "2", "sh", "1"),
            ("sh.510300", "沪深300ETF", "5", "sh", "1"),
        ],
    )

    calendar_days = [*TRADING_DAYS[:2], NON_TRADING_DAY, *TRADING_DAYS[2:]]
    connection.executemany(
        "INSERT INTO TradeDates VALUES (?, ?)",
        [(day, 0 if day == NON_TRADING_DAY else 1) for day in calendar_days],
    )

    bars_without_suspension = [
        day for day in TRADING_DAYS if day != SUSPENDED_TRADING_DAY
    ]
    connection.executemany(
        "INSERT INTO MinuteBars VALUES (?, ?, ?, ?)",
        [
            (code, FIVE_MINUTE_FREQUENCY, f"{day} 09:35:00", day)
            for code, days in (
                (INGESTED_CONTRACT, TRADING_DAYS),
                (SUSPENSION_CONTRACT, bars_without_suspension),
                (UNRECORDED_SPAN_CONTRACT, TRADING_DAYS),
                (PARTIAL_LEDGER_CONTRACT, TRADING_DAYS),
            )
            for day in days
        ],
    )

    # 账只记在**真正取过的那几只**上: `UNRECORDED_SPAN_CONTRACT` 有 bar 却没账, 那是账表加进来
    # 之前的库的样子, 判据据此再取一次 —— 有意的降级方向.
    connection.executemany(
        "INSERT INTO QueriedBarSpans VALUES (?, ?, ?, ?)",
        [
            *(
                (code, FIVE_MINUTE_FREQUENCY, *FULL_MONTH_SPAN)
                for code in (INGESTED_CONTRACT, SUSPENSION_CONTRACT)
            ),
            (PARTIAL_LEDGER_CONTRACT, FIVE_MINUTE_FREQUENCY, *PARTIAL_LEDGER_SPAN),
        ],
    )

    connection.commit()
    connection.close()


def _read_verdict(quote_hub_root: Path, code: str, frequency: str, start_day: str, end_day: str):
    facts = quote_hub.read_coverage_facts(
        quote_hub_root, code, frequency, start_day, end_day
    )

    return facts, judge_coverage(facts.expected_days, facts.asked_days, facts.has_bars)


def _bar_days(quote_hub_root: Path, code: str, frequency: str) -> set[str]:
    """桩库里那只合约真有 bar 的交易日. 只为把用例的前提也钉住, 不参与判据本身."""

    connection = sqlite3.connect(quote_hub_root / quote_hub.QUOTE_HUB_DATABASE_FILENAME)

    try:
        rows = connection.execute(
            "SELECT DISTINCT TradingDay FROM MinuteBars WHERE Code = ? AND Frequency = ?",
            (code, frequency),
        ).fetchall()
    finally:
        connection.close()

    return {row[0] for row in rows}


@pytest.fixture
def quote_hub_root(tmp_path: Path) -> Path:
    _build_stub_database(tmp_path)

    return tmp_path


def test_coverage_facts_are_read_for_one_frequency_only(quote_hub_root: Path) -> None:
    """事实按**具体频率**读: 换一档库里没有的, 那一档既没账也没 bar.

    这一条钉在函数一级, 记的是"`MinuteBars` 与 `QueriedBarSpans` 的 `Frequency` 列都是判据的
    一部分": 少了这一列, 判据会把"别的周期取过"读成"这个周期取过", 于是跳过下载, 引擎到装载期
    才发现没有行情.

    平台只问**落盘精度**那一档 (`MARKET_DATA_PRECISION_FREQUENCY`), 用户的订阅周期不进这条路
    ——它只决定引擎在运行时把 5m 聚合成多粗的 bar. 故这里构造的"15 分钟"不是平台会问的一种取值,
    而是用来证明频率确实参与了判据.
    """

    facts, verdict = _read_verdict(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIFTEEN_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    assert not verdict.sufficient
    assert not verdict.has_bars
    assert verdict.expected_days == TRADING_DAYS
    assert verdict.asked_days == ()
    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_five_minute_coverage_is_enough(quote_hub_root: Path) -> None:
    facts, verdict = _read_verdict(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    assert verdict.sufficient
    assert facts.calendar_last_day == CALENDAR_LAST_TRADING_DAY


def test_a_suspension_day_inside_the_ledger_is_not_a_gap(quote_hub_root: Path) -> None:
    """**这一条是整张账的目的**: 那一天上游没有 bar, 但它落在已问区间里, 故不算缺.

    只按 bar 判的话这里会回"不够", 于是每一次提交都要为这一天重下一整段——上游永远补不上它,
    于是永远不收敛.
    """

    facts, verdict = _read_verdict(
        quote_hub_root,
        SUSPENSION_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    # 前提由桩库保证, 也在这里钉住: 那一天**确实**没有 bar, 而它仍然算"问过了".
    assert SUSPENDED_TRADING_DAY not in _bar_days(
        quote_hub_root, SUSPENSION_CONTRACT, FIVE_MINUTE_FREQUENCY
    )

    assert facts.has_bars
    assert SUSPENDED_TRADING_DAY in facts.asked_days
    assert verdict.sufficient
    assert verdict.missing_days == ()


def test_bars_without_a_ledger_entry_are_asked_for_again(quote_hub_root: Path) -> None:
    """有 bar 却没账 = 账表加进来之前的库. 判"没问过", 于是再取一次.

    这是**有意**的降级方向: 多取只是慢, 少取才是错. 取过一次之后账就补上了, 不会反复.
    """

    facts, verdict = _read_verdict(
        quote_hub_root,
        UNRECORDED_SPAN_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    assert facts.has_bars
    assert verdict.asked_days == ()
    assert not verdict.sufficient


def test_contract_without_any_bars_is_not_enough(quote_hub_root: Path) -> None:
    facts, verdict = _read_verdict(
        quote_hub_root,
        OTHER_FREQUENCY_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    assert not verdict.sufficient
    assert decide_after_refresh(verdict) is RefreshDecision.FAIL_INSUFFICIENT


def test_a_ledger_span_that_starts_before_the_window_still_covers_it(
    quote_hub_root: Path,
) -> None:
    """账里的一段只要与区间相交就算数: 段是闭的, 两头都算, 不必整段落在区间里."""

    facts = quote_hub.read_coverage_facts(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        "2024-01-02",
    )

    assert facts.asked_days == ["2024-01-01", "2024-01-02"]


def test_days_outside_the_ledger_span_are_not_asked(quote_hub_root: Path) -> None:
    """段的边界要真的参与判断: 落在账之外的那几天不算问过, 缺的正是它们."""

    facts, verdict = _read_verdict(
        quote_hub_root,
        PARTIAL_LEDGER_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    assert facts.asked_days == ["2024-01-01", "2024-01-02"]
    assert verdict.missing_days == ("2024-01-04", "2024-01-05")
    assert not verdict.sufficient


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

    facts, verdict = _read_verdict(
        quote_hub_root,
        INGESTED_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        "2024-06-01",
        "2024-06-30",
    )

    assert not verdict.expected_days
    assert not verdict.sufficient
    assert request_exceeds_calendar("2024-06-30", facts.calendar_last_day)


def test_a_component_without_the_ledger_table_is_tolerated(tmp_path: Path) -> None:
    """账表不在 = 组件还停在 v2.3.0 之前, 那时判据退化成"一段都没问过、每次都取".

    必须**容忍**而不是报错: 平台先读事实、后跑组件, 而建表是组件跑起来才做的. 若这里报错, 升级
    之后第一次提交就直接失败, 得手工跑一次组件才能恢复.
    """

    connection = sqlite3.connect(tmp_path / quote_hub.QUOTE_HUB_DATABASE_FILENAME)
    connection.executescript(REQUIRED_TABLES_SQL)
    connection.commit()
    connection.close()

    facts = quote_hub.read_coverage_facts(
        tmp_path,
        INGESTED_CONTRACT,
        FIVE_MINUTE_FREQUENCY,
        CALENDAR_START_DAY,
        CALENDAR_LAST_TRADING_DAY,
    )

    assert facts.asked_days == []


def test_ingested_codes_are_the_whole_minute_bar_universe(quote_hub_root: Path) -> None:
    assert quote_hub.read_ingested_codes(quote_hub_root) == sorted(
        [
            INGESTED_CONTRACT,
            SUSPENSION_CONTRACT,
            UNRECORDED_SPAN_CONTRACT,
            PARTIAL_LEDGER_CONTRACT,
        ]
    )


def test_tradable_contracts_exclude_indices_and_funds(quote_hub_root: Path) -> None:
    contracts = quote_hub.read_tradable_contracts(quote_hub_root)

    assert [contract.code for contract in contracts] == sorted(
        [
            INGESTED_CONTRACT,
            SUSPENSION_CONTRACT,
            UNRECORDED_SPAN_CONTRACT,
            PARTIAL_LEDGER_CONTRACT,
            OTHER_FREQUENCY_CONTRACT,
        ]
    )

    by_code = {contract.code: contract for contract in contracts}

    assert by_code[INGESTED_CONTRACT].exchange_id == "SSE"
    assert by_code[INGESTED_CONTRACT].instrument_id == "600519"
    assert by_code[INGESTED_CONTRACT].display_name == "贵州茅台"


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


def test_a_drifted_ledger_table_reports_unavailable(tmp_path: Path) -> None:
    """账表**在**而列漂了是另一回事: 那不是"还没开始记账", 是 schema 变了, 必须当场报错.

    若把它当成"账是空的", 判据会永远判"没问过", 于是每次提交都整段重下——安静地坏掉.
    """

    connection = sqlite3.connect(tmp_path / quote_hub.QUOTE_HUB_DATABASE_FILENAME)
    connection.executescript(REQUIRED_TABLES_SQL)
    connection.executescript(
        "CREATE TABLE QueriedBarSpans (Code TEXT, Frequency TEXT, StartDay TEXT);"
    )
    connection.commit()
    connection.close()

    with pytest.raises(quote_hub.QuoteHubUnavailableError):
        quote_hub.read_coverage_facts(
            tmp_path,
            INGESTED_CONTRACT,
            FIVE_MINUTE_FREQUENCY,
            CALENDAR_START_DAY,
            CALENDAR_LAST_TRADING_DAY,
        )
