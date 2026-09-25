"""策略查询.

P1 只开查询, 上传与授权属 P2. 本模块的取数一律经 visibility 的统一收口, 不在此自行拼
可见性条件.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..auth.dependencies import CurrentUserDependency
from ..catalog.models import StrategyGrantModel, StrategyModel, StrategyVersionModel
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import (
    PageResponse,
    StrategyDetailResponse,
    StrategyGrantResponse,
    StrategyResponse,
    StrategyVersionResponse,
)
from ..catalog.visibility import build_visible_strategy_query, load_visible_strategy
from ..dependencies import SessionDependency


router = APIRouter()


@router.get("", response_model=PageResponse[StrategyResponse])
async def list_strategies_handler(
    session: SessionDependency,
    current_user: CurrentUserDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[StrategyResponse]:
    """可见策略列表: 本人所有 + 被授权共享 + 公开."""

    return await fetch_page(
        session,
        build_visible_strategy_query(current_user),
        StrategyResponse,
        offset,
        limit,
        order_by=StrategyModel.created_at.desc(),
    )


@router.get("/{strategy_id}", response_model=StrategyDetailResponse)
async def read_strategy_handler(
    strategy_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> StrategyDetailResponse:
    """策略详情、版本列表与授权列表.

    授权列表只对归属人返回: 被授权人若能看到完整授权表, 就知道还有谁被授权了.
    """

    strategy = await load_visible_strategy(session, current_user, strategy_id)

    version_rows = (
        await session.execute(
            select(StrategyVersionModel)
            .where(StrategyVersionModel.strategy_id == strategy.id)
            .order_by(StrategyVersionModel.version_no.desc())
        )
    ).scalars().all()

    grant_responses: list[StrategyGrantResponse] = []

    if strategy.owner_user_id == current_user.id:
        grant_rows = (
            await session.execute(
                select(StrategyGrantModel)
                .where(StrategyGrantModel.strategy_id == strategy.id)
                .order_by(StrategyGrantModel.granted_at.desc())
            )
        ).scalars().all()
        grant_responses = [
            StrategyGrantResponse.model_validate(grant) for grant in grant_rows
        ]

    return StrategyDetailResponse(
        strategy=StrategyResponse.model_validate(strategy),
        versions=[
            StrategyVersionResponse.model_validate(version) for version in version_rows
        ],
        grants=grant_responses,
    )
