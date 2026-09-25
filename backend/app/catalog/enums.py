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


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.INTERRUPTED.value,
        RunStatus.TIMEOUT.value,
    }
)
