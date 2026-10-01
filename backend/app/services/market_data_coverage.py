"""「本地已落地的行情够不够这一轮用」的判据.

纯逻辑, 不碰数据库、不起进程: 输入是两个交易日集合, 输出是一个结论. 事实的读取在
`quote_hub`, 编排在 `market_data_preparation`. 拆开是为了让这张判定表能被穷举测试——
判错的代价不是"安静", 而是把"本地明明没有"当成"有": 下载被跳过, 引擎到装载期才发现, 整轮白跑.

判据本身只有一条式子:

    缺失 = 日历里该区间内的交易日 − 该合约**在该频率下**真正有 bar 的交易日

`covered` 必须来自**带频率**的那张表 (`MinuteBars`), 不能拿组件库里那张按日记账的表代替: 后者
记的是"那天跑过一次下载", 而前者记的是"那天真有这个精度的 bar". 判据要的正是后者——账上记了
而 bar 不在, 判成已覆盖就会跳过下载, 引擎按落盘精度过滤后一根都读不到, 于是它在**装载期**就报
"行情不存在"并以失败收场 (见 `SimExchange` 的 `ErrorMarketDataNotExist`), 这一轮白跑.

频率这一维**由平台常量固定** (`config.MARKET_DATA_PRECISION`), 不再是用户选的订阅周期: 那个
周期只决定引擎在运行时把 5m 聚合成多粗的 bar, 与"落盘数据在不在"无关.

判据之外还有一条**刷新后的收口**, 它同样是必需的而不是加固: 上游对停牌日、未上市日根本不返回
数据, 那样的缺口**永远补不上**, 若以"补齐"为放行条件, 一个含停牌日的区间会每次提交都触发一次
整所重下, 永不收敛. 故放行的界是「该频率在区间内**零行 vs 非零行**」, 不引入任何可调阈值.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RefreshDecision(Enum):
    """刷新之后这一轮该怎么办."""

    #: 区间内该频率的数据已齐全.
    PROCEED = "proceed"
    #: 仍有缺口, 但该频率在区间内确有数据——那些缺口是上游空档 (停牌 / 未上市), 补不上也不该堵.
    PROCEED_WITH_GAPS = "proceed_with_gaps"
    #: 区间内该频率一行都没有. 再刷也刷不出来, 这一轮注定空跑, 以失败收场好过安静地出零结果.
    FAIL_INSUFFICIENT = "fail_insufficient"


@dataclass(frozen=True)
class CoverageVerdict:
    """一次判定的事实与结论.

    三个集合都是 `YYYY-MM-DD` 且已排序去重. `expected_days` 为空有两种由来——区间里本就没有
    交易日, 或区间落在组件交易日历之外 (它的日历是上游同步来的, 可能比今天旧). 两种都不能当作
    "已经够了", 故下面两个 property 都显式看它.
    """

    expected_days: tuple[str, ...]
    covered_days: tuple[str, ...]
    missing_days: tuple[str, ...]

    @property
    def sufficient(self) -> bool:
        """这一轮**不需要下载**. 日历算不出应有交易日时**恒为假**: 无法校验不等于够了."""

        return bool(self.expected_days) and not self.missing_days

    @property
    def has_any_coverage(self) -> bool:
        """区间内该频率是否**至少有一天**的数据."""

        return bool(self.covered_days)


def judge_coverage(
    expected_days: list[str], covered_days: list[str]
) -> CoverageVerdict:
    """按交易日集合算出判定. 入参可含重复, 这里统一去重排序."""

    expected = tuple(sorted(set(expected_days)))
    covered = tuple(sorted(set(covered_days)))
    covered_set = set(covered)

    return CoverageVerdict(
        expected_days=expected,
        covered_days=covered,
        missing_days=tuple(day for day in expected if day not in covered_set),
    )


def decide_after_refresh(verdict: CoverageVerdict) -> RefreshDecision:
    """刷新之后按判定表收口.

    **第一问是"有没有数据"而不是"缺不缺"**: 反过来的话, 「日历算不出应有交易日、且一行数据
    也没有」会落到"缺失为空 → 齐全", 把一个必然空跑的区间放行——这正是 `sufficient` 不把空
    期望当齐的原因, 这里也必须先问同一件事.
    """

    if not verdict.has_any_coverage:
        return RefreshDecision.FAIL_INSUFFICIENT

    if not verdict.missing_days:
        return RefreshDecision.PROCEED

    return RefreshDecision.PROCEED_WITH_GAPS


def request_exceeds_calendar(
    end_day: str, calendar_last_trading_day: str | None
) -> bool:
    """请求区间的末端是否超过了组件交易日历的末日.

    超了说明上游日历没同步到最近, 那段区间的"应有交易日"会算少. 这**不改变**判定 (判定按
    "零行 vs 非零行"收口), 只是要记一条 warning: 现象是"数据看起来齐了", 而实际上最近这段
    根本没有可比对的日历.
    """

    if calendar_last_trading_day is None:
        return True

    return end_day > calendar_last_trading_day
