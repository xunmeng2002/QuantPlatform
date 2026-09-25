"""运行提交、查询与取消.

运行只有提交人本人可见, 授权不延伸到他人的运行记录: 被授权人能提交回测, 不等于能看或能取消
别人的运行. 取数一律经 visibility 的收口.

**提交的权限判定就是可见性判定**: 用户拍板"授权即可跑, 不区分 read/run", 而能跑的集合与可见
集合逐字相同, 故走 `load_visible_strategy`, 不新增越权分支也不新增查询收口.
"""

from __future__ import annotations

import asyncio
import logging
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
    JobArtifactListResponse,
    JobArtifactResponse,
    PageResponse,
    RunDetailResponse,
    RunSubmitRequest,
    RunSummaryResponse,
)
from ..catalog.visibility import build_owned_run_query, load_owned_run
from ..clock import utc_now
from ..config import PlatformSettings
from ..dependencies import SchedulerDependency, SessionDependency, SettingsDependency
from ..errors import ConflictError, ResourceNotFoundError
from ..scheduler.registry import JobHandle
from ..scheduler.runner import CANCEL_MESSAGE
from ..services.run_submission import submit_run


logger = logging.getLogger(__name__)

router = APIRouter()

CANCEL_CONFIRMATION_SECONDS = 2
CANCEL_ATTEMPT_LIMIT = 3

RUN_ALREADY_FINISHED_MESSAGE = "运行已结束, 无法取消"

JOB_DIRECTORY_MISSING_MESSAGE = "该运行的作业目录不存在"
ARTIFACT_NOT_FOUND_MESSAGE = "产物不存在"

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


def _resolve_job_directory(settings: PlatformSettings, run: RunModel) -> Path:
    """由运行行还原它的作业目录, 并确认它确实落在运行根之内.

    目录名就是 `RunId` (`scheduler/workspace.py` 的 `runs_root / job_files.run_id`), 而
    `WorkspacePath` 列记的正是这个值 (`services/run_submission` 落行时写入), 故**从库里重建
    即可**, 不必去翻调度器的内存 (那个 `job_directory` 只是运行期属性, 重启后就没了).

    两道校验都不能省: `WorkspacePath` 的列缺省是空串, 空串拼出来的路径**就是运行根本身**,
    于是 `../../<别人的 RunId>/result.json` 这类请求会一路通过"在运行根之内"的判断, 变成
    跨租户读产物. 空值与非严格子路径一律当作"这份运行没有目录".
    """

    runs_root = settings.runs_root.resolve()

    if not run.workspace_path:
        raise ResourceNotFoundError(JOB_DIRECTORY_MISSING_MESSAGE)

    try:
        job_directory = (runs_root / run.workspace_path).resolve()
    except (OSError, ValueError) as error:
        logger.warning("作业目录名不合法 run_id=%s: %s", run.id, error)
        raise ResourceNotFoundError(JOB_DIRECTORY_MISSING_MESSAGE) from error

    if job_directory == runs_root or not job_directory.is_relative_to(runs_root):
        logger.warning("作业目录越出运行根 run_id=%s", run.id)
        raise ResourceNotFoundError(JOB_DIRECTORY_MISSING_MESSAGE)

    if not job_directory.is_dir():
        raise ResourceNotFoundError(JOB_DIRECTORY_MISSING_MESSAGE)

    return job_directory


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
