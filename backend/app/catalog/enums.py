"""平台枚举取值.

各枚举是该字段合法取值的唯一来源: 数据库列以其 value 落盘, pydantic 直接以枚举类型校验.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class UserType(StrEnum):
    """用户类型. admin 独占用户管理权限."""

    ADMIN = "admin"
    USER = "user"


class UserStatus(StrEnum):
    """账号状态. disabled 的账号登录与持令牌访问均被拒."""

    ACTIVE = "active"
    DISABLED = "disabled"


class StrategyVisibility(StrEnum):
    """策略可见范围.

    private 仅归属人可见且授权表不生效; shared 在归属人之外对授权表内的用户可见;
    public 对全体登录用户可见.

    授权写入口 `PUT /api/strategies/{id}/grants` 按授权集合在 private 与 shared 之间
    切换, **public 除外** (对它一概不动, 免得把没被逐一点名的用户挡在门外). 经该入口
    写授权, 不会留下"授权写进去了却谁也不可见"的状态——那正是 private 会造成的.
    注意"经该入口"这个限定: private 配一条授权行仍是可能的, 只是别处写入的,
    此时那行不生效 (见 `tests/test_strategy_visibility.py`).
    """

    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"


class GrantPermission(StrEnum):
    """授权粒度. read 对应"可看策略本体", run 对应"可在其上提交回测".

    **这一区分在 2026-09-25 已拍板不判定, 不是待做项**: 提交回测的权限判定与可见性
    判定**逐字重合** (见 `runs.py` 的取件——它用的就是 `load_visible_strategy`),
    被授权人无论持哪一档都能跑. 故「授权即可跑」, 没有 `read` / `run` 之别, 也没有
    新的越权分支; `public` 一并**隐含可跑**.

    因此给可见性查询补一条 `permission_type == 'read'` **不是"让注释成真"**, 而是
    一次权限收窄: 它会把只有 run 权限的人挡在策略本体之外——而他们接下来要提交回测,
    连本体都读不到. 真要区分, 改的是可见性收口那一处, 且须先拍板.

    两档都不延伸到他人的运行记录: 运行只有提交人本人可见 (见 visibility 模块),
    授权再高也不会让被授权人看到归属人或第三方的运行.
    """

    READ = "read"
    RUN = "run"


class MarketDataType(StrEnum):
    """行情模式. 取值即引擎字面量, 大小写不可改.

    引擎提交侧称 MatchMode, 结果侧称 MarketDataType, 两侧同一套词汇, 故共用一个枚举.
    """

    BAR = "Bar"
    TICK = "Tick"


class RunStatus(StrEnum):
    """回测作业状态. 终态为 succeeded / failed / interrupted / timeout."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    TIMEOUT = "timeout"


class ProductClass(IntEnum):
    """品种类型. 取值逐值对齐引擎 `ProductClassType` (`Spark/Types.h`), 以 `.value` 落盘.

    与上面几个 StrEnum 不同, 这个枚举的取值是**数字**: 引擎的 `Product` 表用 `Int32` 存它,
    且该列是整数, 故落盘时取 `.value` 而不是成员本身——直接写成员会让 SQLAlchemy 把枚举成员
    当参数绑, 而 `IntEnum` 是 `int` 子类, 二者的差别在写库那一刻才显形.

    这份取值表是平台侧**唯一**的副本, 来源是引擎头文件; 引擎侧改了它, 这里必须跟着改, 否则
    管理端录入的品种类型在回测里会落成另一种品种. 仓内 `QuantTrading/makeseeddb.py` 里的
    `PRODUCT_CLASS_STOCK = 6` 是同一件事的旁证.
    """

    FUTURE = 0
    FUTURE_OPTION = 1
    COMBINATION = 2
    SPOT = 3
    EFP = 4
    INDEX = 5
    STOCK = 6
    STOCK_OPTION = 7
    ETF = 8


class CommissionDirection(IntEnum):
    """买卖方向. 取值对齐引擎 `DirectionType` 的 Buy / Sell 两档.

    引擎的费率表按方向分行 (同一合约的买与卖是两条记录), 故这一列是 `BaseCommission` 主键的
    一部分: 引擎拿成交方向去查行, 再按开平标志决定用行里的开仓列还是平仓列.
    `result.json` 的 `MissingRateKeys` 报的正是这套 0/1, 形如 `1|SSE|600519|0`.
    """

    BUY = 0
    SELL = 1


class RateDirection(IntEnum):
    """一条费率规则**管哪个方向**: 引擎那两档, 加上平台自己的通配档「双向」.

    `BOTH` **只在平台这一侧存在**. 引擎的费率表按方向分行, `Direction` 是它查找键的一部分
    (`CommissionCalculator::Apply` 对四列做一次精确匹配), 多一个取值就等于那一格**永远查不到**
    —— 那一笔成交的三列费用静默按 0 算, 回测照常"成功". 故写种子库时它必须先被摊成 `BUY` 与
    `SELL` 两行 (`reference_data.rate_expansion`), 与合约 / 品种 / 交易所三级作用域是同一套路子.

    两个引擎取值**取自 `CommissionDirection`** 而不是在这里重写一遍: 它们是与引擎对齐的那部分,
    两处各写一份迟早在漂移里分叉.

    注意 `list(RateDirection)` 是 `[BOTH, BUY, SELL]` —— 遍历它去写种子库会把 `BOTH` 一起写进去.
    要遍历"引擎的两档"就用 `rate_expansion.RESOLUTION_DIRECTIONS`.
    """

    BOTH = -1
    BUY = int(CommissionDirection.BUY)
    SELL = int(CommissionDirection.SELL)


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.INTERRUPTED.value,
        RunStatus.TIMEOUT.value,
    }
)
