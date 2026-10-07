"""手续费组的共用判据.

组号是**唯一的身份**: 组名是给界面看的, 计费只认组号 (引擎按它去费率哈希表里查).

三处要问"这个组号在不在", 但它们的**错误语义不是同一个**:

- 管理端录费率时, 组不存在是"这条写请求的前提不成立" —— 409.
- 提交回测与保存配置模板时, 组不存在是"用户填的这个取值不成立" —— 400, 且文案相同 (两条路径
  收的是同一份字段, 同一个取值在两处该被同一句话拒绝).

故这里同时给出谓词与那条 400 包装: 前一条给 409 那处自己决定抛什么, 后一条给两条 400 路径共用
——两处各写一遍的话, 文案迟早分叉, 而用户看到的是"存模板时说的一回事、提交时说的另一回事".
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.models import CommissionGroupModel
from ..errors import InvalidRequestError


UNKNOWN_COMMISSION_GROUP_MESSAGE_TEMPLATE = (
    "{commission_group_id} 号手续费组不存在. 请到「基础数据」页先把这一组建出来, 或改用某个"
    "已登记的组号"
)


async def commission_group_exists(
    session: AsyncSession, commission_group_id: int
) -> bool:
    """这个组号是否已登记."""

    existing_group_id = await session.scalar(
        select(CommissionGroupModel.commission_group_id).where(
            CommissionGroupModel.commission_group_id == commission_group_id
        )
    )

    return existing_group_id is not None


async def ensure_commission_group_exists(
    session: AsyncSession, commission_group_id: int
) -> None:
    """组不存在即 400, 文案点名组号.

    拦在这里而不是留给引擎: 引擎对一个不存在的组**不报错**, 它照跑完、费用全按 0 计, 而配置里
    那个组号看起来完全正常 —— 用户拿到的是一个金额不对的回测, 报告里没有一处说明为什么.
    """

    if not await commission_group_exists(session, commission_group_id):
        raise InvalidRequestError(
            UNKNOWN_COMMISSION_GROUP_MESSAGE_TEMPLATE.format(
                commission_group_id=commission_group_id
            )
        )
