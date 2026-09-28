"""运行提交、查询与取消.

运行只有提交人本人可见, 授权不延伸到他人的运行记录: 被授权人能提交回测, 不等于能看或能取消
别人的运行. 取数一律经 visibility 的收口.

**提交的权限判定就是可见性判定**: 用户拍板"授权即可跑, 不区分 read/run", 而能跑的集合与可见
集合逐字相同, 故走 `load_visible_strategy`, 不新增越权分支也不新增查询收口.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import CurrentUserDependency
from ..catalog.enums import TERMINAL_RUN_STATUSES, RunStatus
from ..catalog.models import RunModel
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import (
    EquityPointResponse,
    JobArtifactListResponse,
    JobArtifactResponse,
    MessageResponse,
    PageResponse,
    ResultTableResponse,
    RunComparisonEntryResponse,
    RunComparisonResponse,
    RunDetailResponse,
    RunEquityResponse,
    RunSubmitRequest,
    RunSummaryResponse,
)
from ..catalog.visibility import (
    RUN_NOT_FOUND_MESSAGE,
    build_owned_run_query,
    load_owned_run,
)
from ..clock import utc_now
from ..config import PlatformSettings
from ..dependencies import SchedulerDependency, SessionDependency, SettingsDependency
from ..errors import ConflictError, InvalidRequestError, ResourceNotFoundError
from ..scheduler.registry import JobHandle
from ..scheduler.runner import CANCEL_MESSAGE
from ..services.result_database import (
    CapitalPointRecord,
    read_capital_series,
    read_result_table_page,
)
from ..services.run_storage import (
    ROW_DELETABLE_OUTCOMES,
    remove_run_directory,
    resolve_run_directory,
)
from ..services.run_submission import submit_run


logger = logging.getLogger(__name__)

router = APIRouter()

CANCEL_CONFIRMATION_SECONDS = 2
CANCEL_ATTEMPT_LIMIT = 3

RUN_ALREADY_FINISHED_MESSAGE = "运行已结束, 无法取消"
RUN_DELETED_MESSAGE = "运行已删除"
RUN_DIRECTORY_NOT_REMOVED_MESSAGE = "该运行的作业目录无法删除, 运行记录已保留"

JOB_DIRECTORY_MISSING_MESSAGE = "该运行的作业目录不存在"
ARTIFACT_NOT_FOUND_MESSAGE = "产物不存在"

RESULT_NOT_READY_MESSAGE = "运行尚未结束, 结果库还在被引擎写入"
RESULT_DATABASE_MISSING_MESSAGE = "该运行没有结果库"

# 一次对比的上限: 三条独立的理由各自都够 —— ECharts 默认调色板 9 色 (再多就靠撞色区分),
# `ids` 走在 query string 上, 以及每一轮都要开一次结果库 (开销随轮数线性增长).
MAXIMUM_COMPARISON_RUNS = 6

COMPARISON_IDS_REQUIRED_MESSAGE = "至少给一个运行号"
COMPARISON_TOO_MANY_RUNS_MESSAGE = f"一次最多对比 {MAXIMUM_COMPARISON_RUNS} 轮"
COMPARISON_EQUITY_FILE_MISSING_MESSAGE = "该运行的结果库文件已不在"
COMPARISON_EQUITY_UNREADABLE_MESSAGE = "该运行的结果库读不出来"

ARTIFACT_MEDIA_TYPE = "application/octet-stream"

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


@router.get("/compare", response_model=RunComparisonResponse)
async def compare_runs_handler(
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
    ids: str = Query(..., description="逗号分隔的运行号, 最多 6 个"),
) -> RunComparisonResponse:
    """多轮并列: 每轮一份列表列 + 它自己的权益曲线. 响应列序 = 请求序.

    **本路由必须声明在 `@router.get("/{run_id}")` 之前**: `/compare` 与 `/{run_id}` 的形状
    完全一样, 声明晚了就被当成 `run_id="compare"` 吞掉, 且不会报任何错——只会得到一句
    "运行不存在".

    归属与不存在**一起**判, 粒度是整请求: 一次 `IN` 取件, 有任何一个 id 取不到就整请求 404,
    文案与单轮详情逐字相同. 不降级成"少一列"——那正是多租户要求消除的可分辨信号: 越权与不存在
    一旦在响应形状上留下差别, 拿一组 id 去试就能问出"这个运行号存不存在".

    曲线逐轮降级 (未结束 / 没有结果库 / 库文件不在 / 库读不出来), 只把**那一列**的曲线置空并
    附一句原因, 指标与其余列照常出——一条曲线画不出来不该让整页变白. 曲线上限之外的部分不在
    这里做: 断线、缺日、对齐都交给前端 (`domain/equity.ts`), 后端多回一列就等于把图表形状钉进
    接口 (同单轮权益端点的理由).
    """

    comparison_run_ids = _parse_comparison_run_ids(ids)

    owned_query = build_owned_run_query(current_user).where(
        RunModel.id.in_(comparison_run_ids)
    )
    runs_by_id = {
        run.id: run for run in (await session.scalars(owned_query)).all()
    }

    if any(run_id not in runs_by_id for run_id in comparison_run_ids):
        raise ResourceNotFoundError(RUN_NOT_FOUND_MESSAGE)

    ordered_runs = [runs_by_id[run_id] for run_id in comparison_run_ids]

    equity_results = await asyncio.gather(
        *(_read_comparison_equity(settings, run) for run in ordered_runs)
    )

    return RunComparisonResponse(
        runs=[
            RunComparisonEntryResponse(
                summary=RunSummaryResponse.model_validate(run),
                equity_points=equity_points,
                equity_unavailable_reason=unavailable_reason,
            )
            for run, (equity_points, unavailable_reason) in zip(
                ordered_runs, equity_results
            )
        ]
    )


def _parse_comparison_run_ids(raw_ids: str) -> list[str]:
    """把 query 里的一串运行号切成有序、去重的列表.

    两处必须这么做. **保序**: 响应列的次序就是用户勾选的次序, 而 `IN (...)` 的返回序是未定义的,
    不收口的话界面上的列序会在驱动之间随机跳. **去重**: 重复的 id 会让同一轮出现两列, 而它在
    库里只有一行——取件那一步的字典会把它合并, 于是响应体与请求对不上, 报的是"少了一列".

    上限按去重**之后**判: 传 8 个 id 而其中 3 个重复, 实际要画的是 5 列, 没有理由拒.
    """

    comparison_run_ids = list(
        dict.fromkeys(part.strip() for part in raw_ids.split(",") if part.strip())
    )

    if not comparison_run_ids:
        raise InvalidRequestError(COMPARISON_IDS_REQUIRED_MESSAGE)

    if len(comparison_run_ids) > MAXIMUM_COMPARISON_RUNS:
        raise InvalidRequestError(COMPARISON_TOO_MANY_RUNS_MESSAGE)

    return comparison_run_ids


async def _read_comparison_equity(
    settings: PlatformSettings, run: RunModel
) -> tuple[list[EquityPointResponse], str | None]:
    """读一轮的权益曲线; 读不出来就回一句原因, **绝不抛**.

    与单轮权益端点的差别只有这一点: 那边读不出来即 404/409, 这边是"整页里少一条线", 逐轮降级
    必须由这里接住, 否则一条读不出来的曲线会让整个对比请求 500——而那与"少一条线"的代价差着
    一个数量级.

    结果库文件一律经 `_resolve_result_database_path` 解析 (作业目录守卫与"必须是文件"两条都在
    里面): 这里换一条不经守卫的路径去读, 等于新开一个穿越读口子. 空 `DbPath` 单列一档文案——
    它与"文件被删了"在界面上的下一步动作不同.
    """

    if run.status not in TERMINAL_RUN_STATUSES:
        return [], RESULT_NOT_READY_MESSAGE

    if not run.db_path.removeprefix("./"):
        return [], RESULT_DATABASE_MISSING_MESSAGE

    try:
        database_file = _resolve_result_database_path(settings, run)
    except ResourceNotFoundError:
        return [], COMPARISON_EQUITY_FILE_MISSING_MESSAGE

    try:
        capital_points = await asyncio.to_thread(read_capital_series, database_file)
    except (OSError, sqlite3.Error) as error:
        # 库文件在、但读不出来 (半份文件、被独占、表结构不对): 原因只进日志且**不带路径**.
        logger.warning("对比读权益曲线失败 run_id=%s: %s", run.id, error)
        return [], COMPARISON_EQUITY_UNREADABLE_MESSAGE

    return _to_equity_point_responses(capital_points), None


def _to_equity_point_responses(
    capital_points: Sequence[CapitalPointRecord],
) -> list[EquityPointResponse]:
    """把结果库的权益行翻成对外视图.

    单轮权益端点与对比端点共用. 各写一遍的话, 将来多出一列只会加在其中一边, 而那种偏差在界面上
    表现为"对比页的曲线与详情页不一样"——两处都不报错, 只有并排看才发现.
    """

    return [
        EquityPointResponse(
            trading_day=point.trading_day,
            balance=point.balance,
            available=point.available,
        )
        for point in capital_points
    ]


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


@router.delete("/{run_id}", response_model=MessageResponse)
async def delete_run_handler(
    run_id: str,
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
) -> MessageResponse:
    """删除一个已结束的运行: **先删作业目录, 后删行**. 非本人提交即 404.

    顺序不能反. 行是那个目录**唯一**的句柄 (目录名就是 `RunId`, 而知道它的只有这一列), 行先
    没了就再没有任何东西能找到那个目录, 磁盘泄漏变成永久且不可观测的. 反过来, 目录删干净了而
    行还在, 只是一行指向空目录的记录, 下一次删除会幂等地把它收掉.

    权限只走 `load_owned_run`: 运行只有提交人本人可见, **管理员也没有旁路**——与列表、详情、
    取消同一条口径.

    状态闸复用 `_require_finished_run` (未结束即 409, 文案与读结果库那句相同): 运行中的轮其
    目录正被引擎占着, 删它要么失败、要么留下一个半死的作业. 想让排队轮消失请走取消端点.

    幂等: 目录已经不在 (或这一行本就没记目录) 而行还在时, 照常删行并回 200; 行也没了 (重复
    调用) 即 404, 同 `delete_strategy_handler` 的先例.

    目录删不掉时返回 **409 且保留行**, 文案不带路径: 行在, 下次还能再试; 而报成功等于把磁盘
    泄漏变成看不见的. `shutil.rmtree` 是阻塞调用, 故走 `asyncio.to_thread`.
    """

    run = await load_owned_run(session, current_user, run_id)
    _require_finished_run(run)

    removal = await asyncio.to_thread(remove_run_directory, settings, run)

    if removal not in ROW_DELETABLE_OUTCOMES:
        logger.warning("运行 %s 的作业目录未能移除: %s", run.id, removal.value)
        raise ConflictError(RUN_DIRECTORY_NOT_REMOVED_MESSAGE)

    await session.delete(run)
    await session.commit()

    return MessageResponse(message=RUN_DELETED_MESSAGE)


@router.get("/{run_id}", response_model=RunDetailResponse)
async def read_run_handler(
    run_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> RunModel:
    """运行详情. 非本人提交即 404."""

    return await load_owned_run(session, current_user, run_id)


@router.get("/{run_id}/files", response_model=JobArtifactListResponse)
async def list_run_artifacts_handler(
    run_id: str,
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
) -> JobArtifactListResponse:
    """列出一次运行的作业目录里的全部文件. 非本人提交即 404.

    运行中的轮也能列: 这里读的是文件系统, 不碰库里的状态, 故没有"还没收尾"的限制.
    目录不存在时 404 而不是 500——目录的构造在调度侧, 一轮可能在构造之前就失败了
    (`services/run_submission` 已先落行), 那种轮本来就没有目录.
    """

    run = await load_owned_run(session, current_user, run_id)
    job_directory = _resolve_job_directory(settings, run)

    artifacts: list[JobArtifactResponse] = []

    try:
        for artifact_path in sorted(job_directory.rglob("*")):
            if artifact_path.is_file():
                artifacts.append(
                    JobArtifactResponse(
                        relative_path=artifact_path.relative_to(job_directory).as_posix(),
                        size_bytes=artifact_path.stat().st_size,
                    )
                )
    except OSError as error:
        # 权限、文件被独占、遍历中途目录消失: 一律当作"这份目录读不出来". 具体原因只进日志,
        # 响应里不带路径与系统错误原文.
        logger.warning("作业目录读取失败 run_id=%s: %s", run_id, error)
        raise ResourceNotFoundError(JOB_DIRECTORY_MISSING_MESSAGE) from error

    return JobArtifactListResponse(run_id=run.id, artifacts=artifacts)


@router.get("/{run_id}/files/{file_path:path}", response_class=FileResponse)
async def download_run_artifact_handler(
    run_id: str,
    file_path: str,
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
) -> FileResponse:
    """按相对路径下载作业目录里的一个文件. 非本人提交即 404.

    路径参数写作 `{file_path:path}`: FastAPI 的普通路径参数**不匹配 `/`**,
    嵌套产物 (`Dump/<RunId>/t_trade.csv`) 那样写会取不到.

    **越界一律当"不存在"**, 与越权同一处理: 判据是把结果 `resolve()` 后必须仍在作业目录内,
    不能只查字符串里的 `..`——符号链接与 Windows 的大小写不敏感都能绕过字符串判断.

    **一律按附件下发**: `Content-Disposition: attachment` + 通用二进制类型, 绝不内联渲染.
    策略是用户上传的任意 Python, 它可以往自己的作业目录里写一个 `.html`, 同源内联渲染
    等于让它在平台域上执行脚本.

    已知行为: 运行中的轮下载 `.db` 可能拿到半份文件 (引擎还在写), 这是取字节的必然结果,
    不在这里拦——拦了就得先判状态, 而那会把"取文件"和调度器状态机绑在一起.
    """

    run = await load_owned_run(session, current_user, run_id)
    job_directory = _resolve_job_directory(settings, run)

    artifact_path = _resolve_artifact_path(job_directory, file_path)

    return FileResponse(
        artifact_path,
        media_type=ARTIFACT_MEDIA_TYPE,
        filename=artifact_path.name,
    )


@router.get("/{run_id}/equity", response_model=RunEquityResponse)
async def read_run_equity_handler(
    run_id: str,
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
) -> RunEquityResponse:
    """逐日权益序列 (读结果库的 `Capital` 表). 非本人提交即 404, 运行未结束即 409.

    只有轮结束之后才读: 引擎写库用的 `SqliteWrapper` 没设 `busy_timeout`, 边写边读会拿到
    SQLITE_BUSY 或半份数据, 而"结束"的信号就是进程退出 (状态进终态).

    **回撤不在这里算**: 它是从这条序列派生出来的量, 由前端算 (见 `domain/equity.ts`),
    后端多回一列就等于把图表的形状钉进接口.
    """

    run = await load_owned_run(session, current_user, run_id)
    _require_finished_run(run)
    database_file = _resolve_result_database_path(settings, run)

    capital_points = await asyncio.to_thread(read_capital_series, database_file)

    return RunEquityResponse(
        run_id=run.id,
        points=_to_equity_point_responses(capital_points),
    )


@router.get("/{run_id}/tables/{table_name}", response_model=ResultTableResponse)
async def read_run_result_table_handler(
    run_id: str,
    table_name: str,
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> ResultTableResponse:
    """引擎结果表的一页. 非本人提交即 404, 未收录的表名即 404, 运行未结束即 409.

    表名白名单与它的 SQL 拼接同处一层 (`services/result_database`), 这里不重复判定:
    界面上给的页签本来就出自同一份清单, 只有手敲 URL 才会走到 404 那一条.
    """

    run = await load_owned_run(session, current_user, run_id)
    _require_finished_run(run)
    database_file = _resolve_result_database_path(settings, run)

    result_page = await asyncio.to_thread(
        read_result_table_page, database_file, table_name, offset, limit
    )

    return ResultTableResponse(
        table=result_page.table_name,
        columns=result_page.column_names,
        total=result_page.total,
        offset=offset,
        limit=limit,
        records=result_page.records,
    )


def _require_finished_run(run: RunModel) -> None:
    """结果库的读操作只在轮结束之后放行, 否则 409.

    这不是风格问题而是硬约束 (见 `read_run_equity_handler` 的说明). 取消/超时/失败的轮也
    放行: 它们的库是完好的 (进程已退出), 只是多半没有 `result.json`, 于是在下一步 404.
    """

    if run.status not in TERMINAL_RUN_STATUSES:
        raise ConflictError(RESULT_NOT_READY_MESSAGE)


def _resolve_result_database_path(settings: PlatformSettings, run: RunModel) -> Path:
    """由运行行还原结果库文件, 并确认它确实落在作业目录之内.

    `DbPath` 列的值来自引擎写的 `result.json` (`./BackTest_<RunId>.db`), 而策略是任意
    Python 且与引擎同进程 —— 这个值**同样可以被伪造**, 故与产物路径同等对待: 解析后必须
    仍在作业目录内、且真的是个文件. 文件名**不自己拼**: `scheduler/engine_config` 已写明
    平台拼一次会得到 `BackTest_<RunId>_<RunId>.db`.

    未结束的轮与失败的轮其 `DbPath` 是空串 (列缺省即空串, `result.json` 没写成功), 而空串
    拼出来的路径**就是作业目录本身**, 故直接当作"没有结果库", 不去解析.
    """

    job_directory = _resolve_job_directory(settings, run)
    relative_database_path = run.db_path.removeprefix("./")

    if not relative_database_path:
        raise ResourceNotFoundError(RESULT_DATABASE_MISSING_MESSAGE)

    try:
        database_file = (job_directory / relative_database_path).resolve()
    except (OSError, ValueError) as error:
        logger.warning("结果库路径不合法 run_id=%s: %s", run.id, error)
        raise ResourceNotFoundError(RESULT_DATABASE_MISSING_MESSAGE) from error

    if not database_file.is_relative_to(job_directory) or not database_file.is_file():
        logger.warning("结果库越出作业目录 run_id=%s", run.id)
        raise ResourceNotFoundError(RESULT_DATABASE_MISSING_MESSAGE)

    return database_file


def _resolve_job_directory(settings: PlatformSettings, run: RunModel) -> Path:
    """由运行行还原它的作业目录, 且要求它此刻真的存在.

    路径判定本身在 `services/run_storage`, 与删除路径**共用同一份**; 这里只补读路径独有的那条
    前置——目录必须真在. 删除路径不能复用它正是因为这个前置: 删一个已经不在的目录是幂等成功,
    读一个不存在的目录才是 404.
    """

    resolution = resolve_run_directory(settings, run)

    if resolution.directory is None or not resolution.directory.is_dir():
        raise ResourceNotFoundError(JOB_DIRECTORY_MISSING_MESSAGE)

    return resolution.directory


def _resolve_artifact_path(job_directory: Path, file_path: str) -> Path:
    """把相对路径解析成作业目录内的真实文件, 越界或不是文件即当"不存在".

    绝对路径 (`C:/Windows/win.ini`) 由 pathlib 的规则直接顶掉左侧的作业目录, 故它走的是
    与 `..` 同一条判断, 不需要另立分支.
    """

    try:
        artifact_path = (job_directory / file_path).resolve()
    except (OSError, ValueError) as error:
        logger.warning("产物路径不合法 %s: %s", file_path, error)
        raise ResourceNotFoundError(ARTIFACT_NOT_FOUND_MESSAGE) from error

    if not artifact_path.is_relative_to(job_directory) or not artifact_path.is_file():
        raise ResourceNotFoundError(ARTIFACT_NOT_FOUND_MESSAGE)

    return artifact_path
