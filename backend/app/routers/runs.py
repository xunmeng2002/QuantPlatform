"""运行提交、查询与取消.

运行只有提交人本人可见, 授权不延伸到他人的运行记录: 被授权人能提交回测, 不等于能看或能取消
别人的运行. 取数一律经 visibility 的收口.

**提交的权限判定就是可见性判定**: 用户拍板"授权即可跑, 不区分 read/run", 而能跑的集合与可见
集合逐字相同, 故走 `load_visible_strategy`, 不新增越权分支也不新增查询收口.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import CurrentUserDependency
from ..catalog.enums import TERMINAL_RUN_STATUSES, RunStatus
from ..catalog.models import RunModel
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import (
    PageResponse,
    RunDetailResponse,
    RunSubmitRequest,
    RunSummaryResponse,
)
from ..catalog.visibility import build_owned_run_query, load_owned_run
from ..clock import utc_now
from ..dependencies import SchedulerDependency, SessionDependency, SettingsDependency
from ..errors import ConflictError
from ..scheduler.registry import JobHandle
from ..scheduler.runner import CANCEL_MESSAGE
from ..services.run_submission import submit_run


logger = logging.getLogger(__name__)

router = APIRouter()

CANCEL_CONFIRMATION_SECONDS = 2
CANCEL_ATTEMPT_LIMIT = 3

RUN_ALREADY_FINISHED_MESSAGE = "运行已结束, 无法取消"

RUN_SORT_COLUMNS = {
    "balance": RunModel.balance,
    "duration_ms": RunModel.duration_ms,
    "finished_at": RunModel.finished_at,
    "order_count": RunModel.order_count,
    "submitted_at": RunModel.submitted_at,
    "total_commission": RunModel.total_commission,
    "trade_count": RunModel.trade_count,
}
DEFAULT_SORT_COLUMN = "submitted_at"


@router.get("", response_model=PageResponse[RunSummaryResponse])
async def list_runs_handler(
    session: SessionDependency,
    current_user: CurrentUserDependency,
    status_filter: RunStatus | None = Query(None, alias="status"),
    strategy_id: str | None = Query(None),
    sort_by: str = Query(DEFAULT_SORT_COLUMN),
    descending: bool = Query(True),
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[RunSummaryResponse]:
    """本人运行列表, 可按状态与策略筛选、按指标排序.

    sort_by 经白名单映射到列对象后再排序, 绝不把请求里的字符串拼进 SQL; 未收录的取值
    静默回落到默认列, 不报错也不泄漏可用列名.
    """

    owned_query = build_owned_run_query(current_user)

    if status_filter is not None:
        owned_query = owned_query.where(RunModel.status == status_filter.value)

    if strategy_id is not None:
        owned_query = owned_query.where(RunModel.strategy_id == strategy_id)

    sort_column = RUN_SORT_COLUMNS.get(sort_by, RUN_SORT_COLUMNS[DEFAULT_SORT_COLUMN])
    ordering = sort_column.desc() if descending else sort_column.asc()

    return await fetch_page(
        session, owned_query, RunSummaryResponse, offset, limit, order_by=ordering
    )


@router.post(
    "", response_model=RunDetailResponse, status_code=status.HTTP_201_CREATED
)
async def submit_run_handler(
    request_body: RunSubmitRequest,
    session: SessionDependency,
    settings: SettingsDependency,
    scheduler: SchedulerDependency,
    current_user: CurrentUserDependency,
) -> RunModel:
    """提交一次回测: 落一行 `queued`, 然后唤醒调度器.

    提交成功即 201, **即使作业目录还没构造**: 目录构造失败发生在调度侧, 那一轮会被标成
    `failed`. 反过来 (把落盘放进请求路径) 要么把长 IO 塞进响应时间, 要么让调用方拿着一个不存在
    的工作目录——两种都比一个诚实返回的 201 差.
    """

    run = await submit_run(session, settings, current_user, request_body)

    scheduler.wake()

    return run


@router.post("/{run_id}/cancel", response_model=RunDetailResponse)
async def cancel_run_handler(
    run_id: str,
    session: SessionDependency,
    scheduler: SchedulerDependency,
    current_user: CurrentUserDependency,
) -> RunModel:
    """取消一个尚未结束的运行. 只归提交人, 非本人提交即 404.

    **不能写成"读一次 → 按读到的状态分支走到底"**: 状态在分支之间会变 (认领、收尾都在抢它),
    于是要么这一轮按 `running` 处理、实际行已被收尾写成终态, 要么反过来。故写成有界重试——每轮
    重新回库读一次, 任一步发现自己抢输了就重来.

    终态回 **409** 而不是 400/404: 行存在且对调用方可见 (404 不成立), 请求本身合法 (400 不
    成立), 冲突的是**资源当前状态**.
    """

    refreshed_run: RunModel | None = None

    for _ in range(CANCEL_ATTEMPT_LIMIT):
        # 每次重试都必须**真的回库**: 同一个会话里 `expire_on_commit=False`, 直接再查一次会命中
        # identity map 拿回同一个陈旧对象, 于是"重读"读的还是上一轮的值, 重试形同虚设.
        # 用 `populate_existing` 而不是 `session.expire_all()`: 后者会把认证时取到的用户对象也
        # 作废, 而下一次读它的属性会触发同步的惰性加载 (异步会话里是 `MissingGreenlet`)。
        refreshed_run = await load_owned_run(
            session, current_user, run_id, populate_existing=True
        )

        if refreshed_run.status in TERMINAL_RUN_STATUSES:
            raise ConflictError(RUN_ALREADY_FINISHED_MESSAGE)

        handle = await scheduler.registry.get(run_id)

        if handle is not None:
            await scheduler.registry.request_cancel(run_id)
            await _await_finalization(handle)
            await session.refresh(refreshed_run)
            return refreshed_run

        # 无句柄: 要么是还没被认领的 `queued`, 要么落在"已认领但尚未登记句柄"那一个 await 宽的
        # 窗口里 (`running` 却无句柄). 两种情况都就地写成中断——后一种的下场是那个作业任务读到
        # 行已不是 `running`, 起进程之前就退出, 不会留下跑了一半的作业.
        if await _interrupt_unstarted_run(session, run_id):
            await session.refresh(refreshed_run)
            return refreshed_run

    logger.warning("取消在 %d 次尝试内未落定, 已返回当前状态", CANCEL_ATTEMPT_LIMIT)

    await session.refresh(refreshed_run)
    return refreshed_run


async def _await_finalization(handle: JobHandle) -> None:
    """有界地等终态落库, 让响应不必让前端靠轮询猜.

    正常路径毫秒级完成——取消已送达, runner 收掉进程、写完终态, 返回时行就是 `interrupted`.
    异常路径最多等两秒就返回当前状态, 不会把 HTTP 处理器挂住.

    `shield` 不能省: 少了它, 超时会把大家共享的那个等待取消掉, 别的等待者会拿到
    `CancelledError`.
    """

    try:
        async with asyncio.timeout(CANCEL_CONFIRMATION_SECONDS):
            await asyncio.shield(handle.finished.wait())
    except TimeoutError:
        logger.warning("取消已送达, 但终态未在 %d 秒内落定", CANCEL_CONFIRMATION_SECONDS)


async def _interrupt_unstarted_run(session: AsyncSession, run_id: str) -> bool:
    """把还没起进程的运行写成中断; 抢输了返回 False.

    条件更新: 只有行**仍然**是 `queued` 或 `running` 才改写. 无条件写会复活一个刚被 runner 收尾
    写成终态的运行——它已经跑完了, 而这一笔把它按回 `interrupted`, 且不会报任何错.
    """

    interrupted = await session.execute(
        update(RunModel)
        .where(RunModel.id == run_id)
        .where(
            RunModel.status.in_(
                (RunStatus.QUEUED.value, RunStatus.RUNNING.value)
            )
        )
        .values(
            status=RunStatus.INTERRUPTED.value,
            finished_at=utc_now(),
            error_msg=CANCEL_MESSAGE,
        )
    )

    await session.commit()

    return bool(interrupted.rowcount)


@router.get("/{run_id}", response_model=RunDetailResponse)
async def read_run_handler(
    run_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> RunModel:
    """运行详情. 非本人提交即 404."""

    return await load_owned_run(session, current_user, run_id)
