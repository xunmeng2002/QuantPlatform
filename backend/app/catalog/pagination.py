"""列表端点的分页收口.

三个列表接口共用同一套取数骨架, 故集中在此. 分页边界与"总数与当页各取一次"这两件事若各写
一遍, 改一处漏两处就会出现前后端页长不一致, 而这类偏差在单页数据下看不出来.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import PageResponse


RecordType = TypeVar("RecordType")

DEFAULT_PAGE_SIZE = 20
MAXIMUM_PAGE_SIZE = 100


async def fetch_page(
    session: AsyncSession,
    base_query: Select[tuple[object]],
    response_model: type[RecordType],
    offset: int,
    limit: int,
    order_by: ColumnElement[object] | None = None,
) -> PageResponse[RecordType]:
    """取一页记录, 连同符合筛选条件的总数.

    总数由 base_query 的子查询得出, 故筛选条件对总数与当页始终一致; 不传 order_by 时按
    查询自身既有的排序取.
    """

    total = (
        await session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    )

    page_query = base_query if order_by is None else base_query.order_by(order_by)
    rows = (await session.execute(page_query.offset(offset).limit(limit))).scalars().all()

    return PageResponse[RecordType](
        total=total,
        offset=offset,
        limit=limit,
        records=[response_model.model_validate(row) for row in rows],
    )
