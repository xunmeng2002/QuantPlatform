"""运行查询.

运行只有提交人本人可见, 授权不延伸到他人的运行记录. 取数一律经 visibility 的收口.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..auth.dependencies import CurrentUserDependency
from ..catalog.enums import RunStatus
from ..catalog.models import RunModel
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import PageResponse, RunDetailResponse, RunSummaryResponse
from ..catalog.visibility import build_owned_run_query, load_owned_run
from ..dependencies import SessionDependency


router = APIRouter()

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


@router.get("/{run_id}", response_model=RunDetailResponse)
async def read_run_handler(
    run_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> RunModel:
    """运行详情. 非本人提交即 404."""

    return await load_owned_run(session, current_user, run_id)
