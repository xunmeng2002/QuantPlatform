"""回测基础数据的管理端.

三张表 (`Product` / `CommissionGroup` / `BaseCommission`) 是引擎启动时从种子库读的"基本数据",
在平台里**没有归属概念** —— 它们对整个平台是同一份, 谁录的都是同一批. 故全部端点要求管理员,
与用户清单同理: 这三张表决定每一轮回测怎么算钱, 不是普通用户该随手改的东西.

维护面只有 CRUD, 写完即提交. **不再"保存即导出"**: 引擎读的那份种子库现在**按轮生成** (落在
作业目录里, 见 `reference_data.rate_expansion`), 因为费率可以按合约 / 品种 / 交易所三级设置,
而引擎的表里没有"品种"这一档 —— 通配只能由平台在写库时展开掉. 于是这里既不维护盘上某个全局
文件, 也没有"改完还要不要点一下同步"这种问题.

**品种级的那个代码必须在品种表里登记过**, 由 `_ensure_instrument_scope_is_known` 守门: 少了
这一问, 一个打错的短码会以"合约级"的身份躺在表里, 展开时匹配不上任何合约 —— 回测照跑完, 只是
那一笔费用静默按 0 算.

三个列表的排序都带全自然键: 自然键是唯一的, 故翻页稳定. 排序写在 base_query 里而不是走
`fetch_page` 的 `order_by` 参数, 因为那个参数只收一列, 而这里的自然键都是复合的.

**一处例外**: `GET /commission-group-options` 只要求登录. 它不是管理面, 而是给**提交页**用的
选项投影 —— 普通用户有权提交回测, 而要选一个组就得先看得见有哪些组; 把选组这件事也锁在管理员
后面, 等于让用户去问管理员"现在有哪几个组". 它只读、只给组号与组名, 且**不**因此放宽与本模块
任何一条写路由的权限.
"""

from __future__ import annotations

from typing import TypeVar

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import AdminUserDependency, CurrentUserDependency
from ..catalog.models import (
    BaseCommissionModel,
    CommissionGroupModel,
    ProductModel,
    RunTemplateModel,
)
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import (
    MAXIMUM_PRODUCT_CODE_LENGTH,
    BaseCommissionResponse,
    BaseCommissionWriteRequest,
    CommissionGroupOptionResponse,
    CommissionGroupResponse,
    CommissionGroupWriteRequest,
    MessageResponse,
    PageResponse,
    ProductResponse,
    ProductWriteRequest,
)
from ..dependencies import SessionDependency
from ..errors import ConflictError, InvalidRequestError, ResourceNotFoundError
from ..ids import generate_identifier
from ..services.commission_group import commission_group_exists


router = APIRouter()

PRODUCT_NOT_FOUND_MESSAGE = "品种不存在"
COMMISSION_GROUP_NOT_FOUND_MESSAGE = "手续费组不存在"
BASE_COMMISSION_NOT_FOUND_MESSAGE = "费率明细不存在"

PRODUCT_DUPLICATED_MESSAGE = "该交易所下的这个品种代码已存在"
COMMISSION_GROUP_DUPLICATED_MESSAGE = "该组号已存在"
BASE_COMMISSION_DUPLICATED_MESSAGE = "该组号下这个交易所、代码与方向已有一条费率"

UNREGISTERED_PRODUCT_CODE_MESSAGE_TEMPLATE = (
    "{exchange_id} 下没有登记过品种代码 {instrument_id}. 若想按品种设置费率, 请先在「品种」页把"
    "它建出来; 若这是合约代码, 请填完整 (合约代码长于 {maximum_length} 个字符)"
)

PRODUCT_DELETED_MESSAGE = "品种已删除"
COMMISSION_GROUP_DELETED_MESSAGE = "手续费组已删除"
BASE_COMMISSION_DELETED_MESSAGE = "费率明细已删除"

COMMISSION_GROUP_REFERENCED_MESSAGE_TEMPLATE = (
    "该手续费组仍被 {rate_row_count} 条费率明细引用, 先删掉那些明细"
)

COMMISSION_GROUP_RENUMBER_REFERENCED_MESSAGE_TEMPLATE = (
    "该手续费组下还有 {rate_row_count} 条费率明细, 改组号会让它们指向一个不存在的组. "
    "先把那些明细的组号改成别处, 或另建一个组."
)

COMMISSION_GROUP_ABSENT_MESSAGE = "该费率明细指向的手续费组不存在, 先把这个组建出来"

COMMISSION_GROUP_TEMPLATE_REFERENCED_MESSAGE_TEMPLATE = (
    "该手续费组仍被 {template_count} 个配置模板引用, 先改掉那些模板的组号或删掉它们"
)

# 并发下的兜底: 先数一遍与真正删之间可能有人插进来一条费率行, 那时 `IntegrityError` 已经拿不到
# 行数了. 说得出"仍被引用"就够用, 报不出条数不影响用户知道该做什么.
COMMISSION_GROUP_REFERENCED_MESSAGE = "该手续费组仍被费率明细引用, 先删掉那些明细"


RecordType = TypeVar("RecordType")


async def _load_record(
    session: AsyncSession,
    model: type[RecordType],
    record_id: str,
    not_found_message: str,
) -> RecordType:
    """按主键取一行, 取不到即视为不存在.

    这三张表没有归属过滤, 故比 `catalog.visibility` 里那几个取件函数简单一层 —— 但仍走同一个
    形状: 取件与报错挨着写, 调用点不必各自再判一次 None.
    """

    record = await session.get(model, record_id)

    if record is None:
        raise ResourceNotFoundError(not_found_message)

    return record


def _apply_product_write(
    product: ProductModel, request_body: ProductWriteRequest
) -> None:
    """把一次写入的取值整体盖到品种行上.

    建单与改单共用它: 两条路径各写一遍的话, 日后加一列时只会改到其中一处, 而那一处自己是绿的,
    缺口出在另一条路径上 —— 与"建单 / 改单共用一份 schema"是同一条理由.
    """

    product.exchange_id = request_body.exchange_id
    product.product_id = request_body.product_id
    product.product_name = request_body.product_name
    product.product_class = request_body.product_class.value
    product.volume_multiple = request_body.volume_multiple
    product.price_tick = request_body.price_tick
    product.max_market_order_volume = request_body.max_market_order_volume
    product.min_market_order_volume = request_body.min_market_order_volume
    product.max_limit_order_volume = request_body.max_limit_order_volume
    product.min_limit_order_volume = request_body.min_limit_order_volume
    product.session_name = request_body.session_name


def _apply_commission_group_write(
    commission_group: CommissionGroupModel,
    request_body: CommissionGroupWriteRequest,
) -> None:
    """把一次写入的取值整体盖到手续费组行上."""

    commission_group.commission_group_id = request_body.commission_group_id
    commission_group.commission_group_name = request_body.commission_group_name


def _apply_base_commission_write(
    base_commission: BaseCommissionModel,
    request_body: BaseCommissionWriteRequest,
) -> None:
    """把一次写入的取值整体盖到费率行上."""

    base_commission.commission_group_id = request_body.commission_group_id
    base_commission.exchange_id = request_body.exchange_id
    base_commission.instrument_id = request_body.instrument_id
    base_commission.direction = request_body.direction.value
    base_commission.open_by_money = request_body.open_by_money
    base_commission.close_by_money = request_body.close_by_money
    base_commission.open_by_volume = request_body.open_by_volume
    base_commission.close_by_volume = request_body.close_by_volume
    base_commission.open_stamp_tax_by_money = request_body.open_stamp_tax_by_money
    base_commission.close_stamp_tax_by_money = request_body.close_stamp_tax_by_money
    base_commission.open_transfer_fee_by_money = (
        request_body.open_transfer_fee_by_money
    )
    base_commission.close_transfer_fee_by_money = (
        request_body.close_transfer_fee_by_money
    )
    base_commission.min_commission = request_body.min_commission
    base_commission.max_commission = request_body.max_commission


async def _count_referencing_rate_rows(
    session: AsyncSession, commission_group_id: int
) -> int:
    """某个组号下挂着几条费率明细. 删组与改组号两处都要先数一遍, 故抽出来共用."""

    return (
        await session.scalar(
            select(func.count())
            .select_from(BaseCommissionModel)
            .where(BaseCommissionModel.commission_group_id == commission_group_id)
        )
    ) or 0


async def _count_referencing_templates(
    session: AsyncSession, commission_group_id: int
) -> int:
    """有多少个配置模板指着这个组号.

    模板**没有**外键指向组表 (组号在模板上只是一个整数列, 与 `Runs.BacktestConfigJson` 里的那个
    一样是当轮的取值), 故删组在数据库层面不会报错 —— 拦不拦纯粹是这里的策略: 留着这个组号、而
    组没了, 那些模板套用出来的提交会被"组不存在"拒掉, 用户看到的是一份存得下却永远提交不出去的
    模板. 数一遍, 把话在删的那一刻说清.
    """

    return (
        await session.scalar(
            select(func.count())
            .select_from(RunTemplateModel)
            .where(RunTemplateModel.commission_group_id == commission_group_id)
        )
    ) or 0


async def _ensure_commission_group_exists(
    session: AsyncSession, commission_group_id: int
) -> None:
    """费率行引用的那个组得先在.

    真正的保证是外键 (并发下先查这一次可能落空, 漏到提交那一步由 `IntegrityError` 兜底),
    先查这一次只为给出**对得上**的说法: 外键兜底那句话是"该组号下这个交易所、代码与方向已有一
    条费率", 而组根本不存在时会把人打发去翻重名, 实际该做的是先把组建出来.

    谓词与提交侧共用 (`services.commission_group`), 但错**不共用**: 这里是一条写请求的前提不
    成立 (409), 而那边是用户填的一个取值不成立 (400).
    """

    if not await commission_group_exists(session, commission_group_id):
        raise ConflictError(COMMISSION_GROUP_ABSENT_MESSAGE)


async def _ensure_instrument_scope_is_known(
    session: AsyncSession, request_body: BaseCommissionWriteRequest
) -> None:
    """费率行的作用域代码得说得通.

    合约格非空且**不长于** `MAXIMUM_PRODUCT_CODE_LENGTH` 时, 它只可能是两种意图: 一个已登记的
    品种码 (品种级), 或者一个打错 / 漏登记的短码. 前者放行, 后者拦下来并说清两条出路 —— 不拦
    的话它会以"合约级"的身份躺在表里: 展开时匹配不上任何合约, 回测照跑完, 费用静默按 0 算,
    而这正是本期要消掉的那种静默.

    长于那个阈值的取值一律当合约码放行 (合约清单不在平台手里, 无从核实); 空串是交易所级那一档,
    同样放行.
    """

    instrument_id = request_body.instrument_id

    if not instrument_id or len(instrument_id) > MAXIMUM_PRODUCT_CODE_LENGTH:
        return

    registered_product_id = await session.scalar(
        select(ProductModel.product_id).where(
            ProductModel.exchange_id == request_body.exchange_id,
            ProductModel.product_id == instrument_id,
        )
    )

    if registered_product_id is None:
        raise InvalidRequestError(
            UNREGISTERED_PRODUCT_CODE_MESSAGE_TEMPLATE.format(
                exchange_id=request_body.exchange_id,
                instrument_id=instrument_id,
                maximum_length=MAXIMUM_PRODUCT_CODE_LENGTH,
            )
        )


@router.get("/products", response_model=PageResponse[ProductResponse])
async def list_products_handler(
    session: SessionDependency,
    admin_user: AdminUserDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[ProductResponse]:
    """品种列表, 按 (交易所, 品种代码) 排序."""

    return await fetch_page(
        session,
        select(ProductModel).order_by(
            ProductModel.exchange_id.asc(), ProductModel.product_id.asc()
        ),
        ProductResponse,
        offset,
        limit,
    )


@router.post(
    "/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED
)
async def create_product_handler(
    request_body: ProductWriteRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> ProductModel:
    """新建一个品种."""

    product = ProductModel(id=generate_identifier())

    _apply_product_write(product, request_body)

    session.add(product)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(PRODUCT_DUPLICATED_MESSAGE) from error

    return product


@router.patch("/products/{record_id}", response_model=ProductResponse)
async def update_product_handler(
    record_id: str,
    request_body: ProductWriteRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> ProductModel:
    """改一个品种. 字段是整行替换语义: 请求里没给的列会回到 schema 的默认值."""

    product = await _load_record(
        session, ProductModel, record_id, PRODUCT_NOT_FOUND_MESSAGE
    )

    _apply_product_write(product, request_body)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(PRODUCT_DUPLICATED_MESSAGE) from error

    return product


@router.delete("/products/{record_id}", response_model=MessageResponse)
async def delete_product_handler(
    record_id: str,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> MessageResponse:
    """删一个品种. 删掉它不会影响既有费率行 (两者在引擎侧没有关联)."""

    product = await _load_record(
        session, ProductModel, record_id, PRODUCT_NOT_FOUND_MESSAGE
    )

    await session.delete(product)

    await session.commit()

    return MessageResponse(message=PRODUCT_DELETED_MESSAGE)


@router.get(
    "/commission-group-options", response_model=PageResponse[CommissionGroupOptionResponse]
)
async def list_commission_group_options_handler(
    session: SessionDependency,
    current_user: CurrentUserDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[CommissionGroupOptionResponse]:
    """提交页选组用的选项: 组号 + 组名, **只要求登录**.

    这是本模块唯一的非管理端点 (理由见模块 docstring): 普通用户要提交回测就得能选组, 而"有哪几个
    组"不是机密——真正要拦的是**改**它们, 那些路由照旧要求管理员.

    与上面那条管理列表**同库同序**, 只是投影更窄: 界面只需要"选哪一组", 而多给的每个字段都会被
    某个调用方在某天当成可以依赖的东西. 分页口径与管理列表一致, 前端用同一个收全工具取完.
    """

    return await fetch_page(
        session,
        select(CommissionGroupModel).order_by(
            CommissionGroupModel.commission_group_id.asc()
        ),
        CommissionGroupOptionResponse,
        offset,
        limit,
    )


@router.get("/commission-groups", response_model=PageResponse[CommissionGroupResponse])
async def list_commission_groups_handler(
    session: SessionDependency,
    admin_user: AdminUserDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[CommissionGroupResponse]:
    """手续费组列表, 按组号排序."""

    return await fetch_page(
        session,
        select(CommissionGroupModel).order_by(
            CommissionGroupModel.commission_group_id.asc()
        ),
        CommissionGroupResponse,
        offset,
        limit,
    )


@router.post(
    "/commission-groups",
    response_model=CommissionGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_commission_group_handler(
    request_body: CommissionGroupWriteRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> CommissionGroupModel:
    """新建一个手续费组."""

    commission_group = CommissionGroupModel(id=generate_identifier())

    _apply_commission_group_write(commission_group, request_body)

    session.add(commission_group)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(COMMISSION_GROUP_DUPLICATED_MESSAGE) from error

    return commission_group


@router.patch(
    "/commission-groups/{record_id}", response_model=CommissionGroupResponse
)
async def update_commission_group_handler(
    record_id: str,
    request_body: CommissionGroupWriteRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> CommissionGroupModel:
    """改一个手续费组.

    费率行存的是**组号**而不是组的主键, 而外键是 `BaseCommissions.CommissionGroupId →
    CommissionGroups.CommissionGroupId` 且未声明 `ON UPDATE`, 故改一个**仍被引用**的组的组号
    会被 SQLite 直接拒掉 (`FOREIGN KEY constraint failed`). 那句话落到 `IntegrityError` 上会被
    译成"该组号已存在"—— 而真相与重名无关, 故这里先数一遍, 给出对得上的说法.
    """

    commission_group = await _load_record(
        session, CommissionGroupModel, record_id, COMMISSION_GROUP_NOT_FOUND_MESSAGE
    )

    if request_body.commission_group_id != commission_group.commission_group_id:
        referencing_row_count = await _count_referencing_rate_rows(
            session, commission_group.commission_group_id
        )

        if referencing_row_count:
            raise ConflictError(
                COMMISSION_GROUP_RENUMBER_REFERENCED_MESSAGE_TEMPLATE.format(
                    rate_row_count=referencing_row_count
                )
            )

    _apply_commission_group_write(commission_group, request_body)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(COMMISSION_GROUP_DUPLICATED_MESSAGE) from error

    return commission_group


@router.delete("/commission-groups/{record_id}", response_model=MessageResponse)
async def delete_commission_group_handler(
    record_id: str,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> MessageResponse:
    """删一个手续费组. 仍被引用时拒绝, **不级联删任何东西**.

    两道闸, 按"引用得有多硬"排序:

    1. **费率明细** (有外键). 级联看起来更省事, 但删掉的是别处录进来的费率数据, 而用户点的是
       "删这个组". 先数一遍只为给出干净的报错 (说得出被几条引用); 真正的保证来自外键, 并发下漏到
       提交那一步时由 `IntegrityError` 兜底译成同一句话.
    2. **配置模板** (无外键, 纯策略). 见 `_count_referencing_templates`: 放它过去不会报错, 只会
       留下一批永远提交不出去的模板.

    已在队列里的轮不受这两道闸保护——它们在提交时就把组号冻结进自己的引擎配置了. 那种轮会在起跑
    前以 `UnrunnableJob` 失败并点名缺哪个合约的费率 (见 `scheduler.runner`), 不会静默按 0 计费.
    """

    commission_group = await _load_record(
        session, CommissionGroupModel, record_id, COMMISSION_GROUP_NOT_FOUND_MESSAGE
    )

    referencing_row_count = await _count_referencing_rate_rows(
        session, commission_group.commission_group_id
    )

    if referencing_row_count:
        raise ConflictError(
            COMMISSION_GROUP_REFERENCED_MESSAGE_TEMPLATE.format(
                rate_row_count=referencing_row_count
            )
        )

    referencing_template_count = await _count_referencing_templates(
        session, commission_group.commission_group_id
    )

    if referencing_template_count:
        raise ConflictError(
            COMMISSION_GROUP_TEMPLATE_REFERENCED_MESSAGE_TEMPLATE.format(
                template_count=referencing_template_count
            )
        )

    await session.delete(commission_group)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(COMMISSION_GROUP_REFERENCED_MESSAGE) from error

    return MessageResponse(message=COMMISSION_GROUP_DELETED_MESSAGE)


@router.get("/base-commissions", response_model=PageResponse[BaseCommissionResponse])
async def list_base_commissions_handler(
    session: SessionDependency,
    admin_user: AdminUserDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAXIMUM_PAGE_SIZE),
) -> PageResponse[BaseCommissionResponse]:
    """费率明细列表, 按 (组号, 交易所, 合约, 方向) 排序.

    键序与引擎那四列一致, 只是这一页收的**还含通配的规则** (空 / 品种码的合约格, `-1` 的双向),
    而 `direction` 升序把双向 (`-1`) 排在同一格的前面 —— 展开之后它们才变成引擎读得到的行.
    """

    return await fetch_page(
        session,
        select(BaseCommissionModel).order_by(
            BaseCommissionModel.commission_group_id.asc(),
            BaseCommissionModel.exchange_id.asc(),
            BaseCommissionModel.instrument_id.asc(),
            BaseCommissionModel.direction.asc(),
        ),
        BaseCommissionResponse,
        offset,
        limit,
    )


@router.post(
    "/base-commissions",
    response_model=BaseCommissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_base_commission_handler(
    request_body: BaseCommissionWriteRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> BaseCommissionModel:
    """新建一条费率明细.

    组号指向一个不存在的组时回 409 而不是 400: 请求本身是合法的, 冲突在于它引用的东西不在
    —— 与"重名"是同一类, 只是那句话由 `_ensure_commission_group_exists` 给出, 而不是让外键
    兜底的那一句 `BASE_COMMISSION_DUPLICATED_MESSAGE` 顶上去.

    两道先查各自解释一种"引用的东西不在": 作用域代码说的可能是品种级 (那就要品种已登记), 组号
    说的是这条费率挂在哪个组下.
    """

    await _ensure_instrument_scope_is_known(session, request_body)

    await _ensure_commission_group_exists(session, request_body.commission_group_id)

    base_commission = BaseCommissionModel(id=generate_identifier())

    _apply_base_commission_write(base_commission, request_body)

    session.add(base_commission)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(BASE_COMMISSION_DUPLICATED_MESSAGE) from error

    return base_commission


@router.patch(
    "/base-commissions/{record_id}", response_model=BaseCommissionResponse
)
async def update_base_commission_handler(
    record_id: str,
    request_body: BaseCommissionWriteRequest,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> BaseCommissionModel:
    """改一条费率明细. 整行替换语义, 故改单同样要验一次它将要指向的那个组与作用域代码."""

    await _ensure_instrument_scope_is_known(session, request_body)

    await _ensure_commission_group_exists(session, request_body.commission_group_id)

    base_commission = await _load_record(
        session, BaseCommissionModel, record_id, BASE_COMMISSION_NOT_FOUND_MESSAGE
    )

    _apply_base_commission_write(base_commission, request_body)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(BASE_COMMISSION_DUPLICATED_MESSAGE) from error

    return base_commission


@router.delete("/base-commissions/{record_id}", response_model=MessageResponse)
async def delete_base_commission_handler(
    record_id: str,
    session: SessionDependency,
    admin_user: AdminUserDependency,
) -> MessageResponse:
    """删一条费率明细."""

    base_commission = await _load_record(
        session, BaseCommissionModel, record_id, BASE_COMMISSION_NOT_FOUND_MESSAGE
    )

    await session.delete(base_commission)

    await session.commit()

    return MessageResponse(message=BASE_COMMISSION_DELETED_MESSAGE)
