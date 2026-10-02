"""「本地已落地的行情够不够这一轮用」的判据.

纯逻辑, 不碰数据库、不起进程: 输入是交易日集合与一个"有没有 bar"的布尔, 输出是一个结论. 事实
的读取在 `quote_hub`, 编排在 `market_data_preparation`. 拆开是为了让这张判定表能被穷举测试——
判错的代价不是"安静", 而是把"本地明明没有"当成"有": 下载被跳过, 引擎到装载期才发现, 整轮白跑.

判据本身只有一条式子:

    缺失 = 日历里该区间内的交易日 − 该合约**在该频率下已经问过上游**的交易日

`asked` 来自组件库里的 `QueriedBarSpans`——那是"我们向上游问过哪一段"的账, 记的是**我们做过
的事**, 不是"哪几天真有 bar". 两者必须分开: 上游对停牌日、未上市日根本不返回数据, 只按 bar
记账的那个缺口**永远补不上**, 判成"缺"就变成每次提交都重下一遍, 永不收敛. 账是闭区间的集合,
"某天落在某段里"即算问过, 不再逐日比对.

反过来, **账上记了而 bar 不在是允许的**——那正好就是停牌日的样子. 判成已覆盖、跳过下载, 引擎
读到的是不连续的那几天, 这是对的. 它只在「区间内一根 bar 都没有」时才该失败, 故那条界限由
`has_bars` 单独回答, 不混进缺失集合: 没有它, "这只合约在区间内根本没数据"这类结构性错误会从
平台的一句明确报错, 退化成引擎装载期的 `ErrorMarketDataNotExist` (见 `SimExchange`).

账的口径必须是「这一只合约、这一档频率、这一次上游调用**问成功了**」: 组件在调用失败时抛异常
并整轮非零退出, 走到写账那一步就说明这一段的答案是上游给的, 而不是我们被中断后猜的. 口径一旦
放宽成"这批命令跑完了", 账就会记上没真正问到的日子, 而那个洞再也不会被补.

频率这一维**由平台常量固定** (`config.MARKET_DATA_PRECISION`), 不再是用户选的订阅周期: 那个
周期只决定引擎在运行时把 5m 聚合成多粗的 bar, 与"落盘数据在不在"无关.

判据之外还有一条**刷新后的收口**, 它同样是必需的而不是加固: 账只说明"问过了", 说明不了"问出来
的东西引擎读得到". 若把"账覆盖了整个区间"直接当成可放行, 一个上游压根没有的合约会被静默放行,
引擎到装载期才报错, 而那一轮已经白跑了. 故放行前先问「该频率在区间内有没有**至少一行** bar」,
不引入任何可调阈值.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RefreshDecision(Enum):
    """刷新之后这一轮该怎么办."""

    #: 区间内该频率的数据已齐全.
    PROCEED = "proceed"
    #: 账没覆盖住整段 (区间落在账之外), 但该频率在区间内确有 bar——缺的那段是上游空档或
    #: 未上市段, 补不上也不该堵.
    PROCEED_WITH_GAPS = "proceed_with_gaps"
    #: 区间内该频率一行 bar 都没有. 再刷也刷不出来, 这一轮注定空跑, 以失败收场好过安静地出零结果.
    FAIL_INSUFFICIENT = "fail_insufficient"


@dataclass(frozen=True)
class CoverageVerdict:
    """一次判定的事实与结论.

    两个日期集合都是 `YYYY-MM-DD` 且已排序去重. `expected_days` 为空有两种由来——区间里本就没有
    交易日, 或区间落在组件交易日历之外 (它的日历是上游同步来的, 可能比今天旧). 两种都不能当作
    "已经够了", 故 `sufficient` 显式看它.
    """

    expected_days: tuple[str, ...]
    asked_days: tuple[str, ...]
    missing_days: tuple[str, ...]
    has_bars: bool

    @property
    def sufficient(self) -> bool:
        """这一轮**不需要下载**. 两种情形恒为假:

          - 日历算不出应有交易日——无法校验不等于够了;
          - 区间内一根 bar 都没有——**账盖住了整段也一样**. 退市或整段停牌的合约正是这样: 账上
            记着"问过了", 上游也确实答了"没有".

        少了 `has_bars` 这一问, 同一批事实会在下载前判"够"、下载后判"不够", 于是这一轮被放行到
        引擎, 最终以引擎的 `ErrorMarketDataNotExist` 收场, 而 `decide_after_refresh` 那句本该在
        平台这层讲清楚的话就没机会说了.
        """

        return bool(self.expected_days) and not self.missing_days and self.has_bars


def judge_coverage(
    expected_days: list[str], asked_days: list[str], has_bars: bool
) -> CoverageVerdict:
    """按交易日集合算出判定. 入参可含重复, 这里统一去重排序."""

    expected = tuple(sorted(set(expected_days)))
    asked = tuple(sorted(set(asked_days)))
    asked_set = set(asked)

    return CoverageVerdict(
        expected_days=expected,
        asked_days=asked,
        missing_days=tuple(day for day in expected if day not in asked_set),
        has_bars=has_bars,
    )


def decide_after_refresh(verdict: CoverageVerdict) -> RefreshDecision:
    """刷新之后按判定表收口.

    **第一问是"有没有 bar"而不是"缺不缺"**: 账记的是"问过了", 说明不了"问出来的东西引擎读得到"
    ——反过来的话, 「日历算不出应有交易日、且一行数据也没有」会落到"缺失为空 → 齐全", 把一个必
    然空跑的区间放行. 这正是 `sufficient` 不把空期望当齐的原因, 这里也必须先问同一件事.
    """

    if not verdict.has_bars:
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
