"""平台枚举取值.

各枚举是该字段合法取值的唯一来源: 数据库列以其 value 落盘, pydantic 直接以枚举类型校验.
"""

from __future__ import annotations

from enum import StrEnum


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
    """

    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"


class GrantPermission(StrEnum):
    """授权粒度. read 可看策略与历史运行, run 可在其上提交回测."""

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


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.INTERRUPTED.value,
        RunStatus.TIMEOUT.value,
    }
)
