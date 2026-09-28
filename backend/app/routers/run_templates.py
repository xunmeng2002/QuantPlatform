"""配置模板: 命名保存一套运行取值, 供提交页重复套用.

**作用域是策略域, 可见性只按归属人**: 模板行同时带 `OwnerUserId` 与 `StrategyId`, 取件一律经
`catalog/visibility`. 授权与公开只影响"能不能给这个策略存模板", 不影响"能看见谁的模板"——与运行
记录同一条口径 (被授权人能跑, 不等于能看别人的运行).

**没有 apply 端点**. "套用"要与当前 manifest 派生出的控件、当前表单已填的值一起决定, 是纯前端
动作; 放在服务端就得把提交页那两段派生逻辑 (`createInitialParameterInputs` /
`buildPrefilledRunFields`) 抄一份, 而抄出来的那份迟早与界面上真正跑的那份不一致.

**校验与提交共用一份判据** (`services/run_configuration`): 两边各写一份的话, 能存下的取值就与
能提交的取值不是同一个集合, 症状是"存得下、提交时 400", 且只在特定取值上出现.

**删除是硬删** (模型侧的理由见 `catalog/models.RunTemplateModel`): 没有任何外键指向模板, 删它
不影响历史运行——那一轮的取值在提交时就烤进了 `Runs.ParamsJson`.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import CurrentUserDependency
from ..catalog.models import RunTemplateModel, UserModel
from ..catalog.schemas import (
    MessageResponse,
    RunTemplateCreateRequest,
    RunTemplateListResponse,
    RunTemplateRenameRequest,
    RunTemplateResponse,
)
from ..catalog.visibility import (
    build_owned_run_template_query,
    load_owned_run_template,
    load_visible_strategy,
)
from ..dependencies import SessionDependency
from ..errors import ConflictError, InvalidRequestError
from ..ids import generate_identifier
from ..manifest import (
    BAR_PERIOD_FIELD_NAME,
    EXCHANGE_ID_FIELD_NAME,
    INSTRUMENT_ID_FIELD_NAME,
)
from ..scheduler.engine_config import parse_configuration_object, serialize_configuration
from ..services.run_configuration import (
    build_parameter_configuration,
    resolve_run_field_values,
    validate_initial_capital,
    validate_match_mode,
    validate_trading_day_range,
)
from ..services.run_submission import parse_version_manifest, resolve_strategy_version


router = APIRouter()

MAXIMUM_TEMPLATES_PER_STRATEGY = 50

TEMPLATE_NAME_TAKEN_MESSAGE = "同名模板已存在"
BLANK_TEMPLATE_NAME_MESSAGE = "模板名不能为空白"
TEMPLATE_DELETED_MESSAGE = "模板已删除"
TOO_MANY_TEMPLATES_MESSAGE = (
    f"单个策略下保存的模板不得超过 {MAXIMUM_TEMPLATES_PER_STRATEGY} 个"
)


def _resolve_template_name(raw_name: str) -> str:
    """模板名先去空白, 全是空白即拒.

    不去空白的话, `网格` 与 `网格 ` 会各占一个模板, 而界面上看是同一个 (同 `create_strategy_handler`
    的理由). 唯一约束在这里帮不上忙——它逐字比较, 管的是"完全相同", 空白的差别归这里管.
    """

    template_name = raw_name.strip()

    if not template_name:
        raise InvalidRequestError(BLANK_TEMPLATE_NAME_MESSAGE)

    return template_name


def _build_template_response(template: RunTemplateModel) -> RunTemplateResponse:
    """把模板行译成响应, 其中 `ParamsJson` 解回字典.

    解不动只降级成空参数集并记一条 warning (由 `parse_configuration_object` 落日志): 列表里
    一行坏数据不该让整页报错. 模板的参数在这张表里没有第二份真相, 无从修补, 但**模板本身还在**
    ——名字与运行级字段仍可读可删, 用户至少能看出是哪一行坏了并把它删掉.
    """

    return RunTemplateResponse(
        id=template.id,
        strategy_id=template.strategy_id,
        name=template.name,
        match_mode=template.match_mode,
        bar_period=template.bar_period,
        exchange_id=template.exchange_id,
        instrument_id=template.instrument_id,
        start_trading_day=template.start_trading_day,
        end_trading_day=template.end_trading_day,
        initial_capital=template.initial_capital,
        params=parse_configuration_object(template.id, template.params_json) or {},
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


async def _ensure_template_quota(
    session: AsyncSession, current_user: UserModel, strategy_id: str
) -> None:
    """护栏: 单个策略下本人的模板数不超上限, 超了即 400.

    这是**护栏而不是不变量**: 计数与插入之间没有锁, 并发两次创建可能各自读到 49 而双双落库.
    多存一个模板的代价是几十字节, 为它加锁不划算; 上限防的是失控的客户端把这张表撑成主表,
    那个量级下差一两个没有任何意义.
    """

    template_count = (
        await session.scalar(
            select(func.count()).select_from(
                build_owned_run_template_query(current_user, strategy_id).subquery()
            )
        )
        or 0
    )

    if template_count >= MAXIMUM_TEMPLATES_PER_STRATEGY:
        raise InvalidRequestError(TOO_MANY_TEMPLATES_MESSAGE)


@router.get("", response_model=RunTemplateListResponse)
async def list_run_templates_handler(
    strategy_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> RunTemplateListResponse:
    """本人为该策略保存的模板, 最近创建的在前.

    走 `load_visible_strategy` 而不是 `load_owned_strategy`: 别人共享给你的策略也要能存模板,
    而**取哪一行模板**另有归属过滤 (在 `catalog/visibility`), 故共享或公开策略下列出来的仍是
    自己的那几份, 不会把归属人的模板带出来.

    双键排序: `CreatedAt` 在 Windows 上只有毫秒精度, 同值的两个模板靠主键兜平局, 否则"最新的
    在前"是不确定的 (同 `read_last_submitted_parameters` 的兜法).
    """

    strategy = await load_visible_strategy(session, current_user, strategy_id)

    template_rows = (
        (
            await session.execute(
                build_owned_run_template_query(current_user, strategy.id).order_by(
                    RunTemplateModel.created_at.desc(), RunTemplateModel.id.desc()
                )
            )
        )
        .scalars()
        .all()
    )

    return RunTemplateListResponse(
        strategy_id=strategy.id,
        templates=[_build_template_response(row) for row in template_rows],
    )


@router.post("", response_model=RunTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_run_template_handler(
    strategy_id: str,
    request_body: RunTemplateCreateRequest,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> RunTemplateResponse:
    """保存一套取值.

    参数的判据取自该策略**最新版本**的 manifest, 而模板**不绑版本**: 模板是策略级的, 换版本
    之后仍应可用. 代价是"某个参数在新版本里被删掉了"这件事要等到套用或提交时才暴露——那是
    版本迭代的固有代价, 绑版本换来的"存模板时就报错"会让每一次正常迭代作废全部旧模板.

    落库前一次校验都不省的顺序与提交侧逐条相同 (模式 → 运行级字段 → 交易日 → 资金 → 参数):
    同一个取值在两处被拒的理由该是同一句文案, 顺序不同就会给出不同的那一句.
    """

    strategy = await load_visible_strategy(session, current_user, strategy_id)

    template_name = _resolve_template_name(request_body.name)

    version = await resolve_strategy_version(session, strategy.id, None)
    manifest = parse_version_manifest(version)

    match_mode = validate_match_mode(manifest, request_body.match_mode)
    run_field_values = resolve_run_field_values(manifest, request_body)
    start_trading_day, end_trading_day = validate_trading_day_range(
        request_body.start_trading_day, request_body.end_trading_day
    )
    initial_capital = validate_initial_capital(request_body.initial_capital)
    submitted_parameters = build_parameter_configuration(
        manifest, dict(request_body.params)
    )

    await _ensure_template_quota(session, current_user, strategy.id)

    template = RunTemplateModel(
        id=generate_identifier(),
        owner_user_id=current_user.id,
        strategy_id=strategy.id,
        name=template_name,
        match_mode=match_mode.value,
        bar_period=run_field_values[BAR_PERIOD_FIELD_NAME],
        # 缺席即 `None`: 没映射的字段根本没被收下, 存空串会让"没这道取值"与"填了个空值"混成
        # 同一个样子, 而套用时前者要清空控件、后者要填一个空值.
        exchange_id=run_field_values.get(EXCHANGE_ID_FIELD_NAME),
        instrument_id=run_field_values.get(INSTRUMENT_ID_FIELD_NAME),
        start_trading_day=start_trading_day,
        end_trading_day=end_trading_day,
        initial_capital=initial_capital,
        # 只存参数, 不重复存运行级字段: 它们在这张表上各有具名列, 再写进这里就有了两份真相.
        params_json=serialize_configuration(submitted_parameters),
    )
    session.add(template)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(TEMPLATE_NAME_TAKEN_MESSAGE) from error

    return _build_template_response(template)


@router.patch("/{template_id}", response_model=RunTemplateResponse)
async def rename_run_template_handler(
    strategy_id: str,
    template_id: str,
    request_body: RunTemplateRenameRequest,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> RunTemplateResponse:
    """给模板改名. 只改名——改取值等于存一个新模板.

    归属判定只经 `load_owned_run_template` (它同时按 `OwnerUserId` 与 `StrategyId` 过滤), **不
    再要求策略此刻可见**: 模板是我的, 那它就该一直改得动、删得掉. 若策略被别人取消了共享或软
    删除, 要求可见性会让我的模板连同"删掉它"的接口一起消失, 而它们在库里还在——那是个只有
    数据库管理员能收拾的局面.

    重名以唯一约束为准 (捕 `IntegrityError`), 不做"先查后插": 后者要两次往返, 而且在两次之间
    并发的同名创建照样会一起落库.
    """

    template = await load_owned_run_template(
        session, current_user, strategy_id, template_id
    )

    template.name = _resolve_template_name(request_body.name)

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(TEMPLATE_NAME_TAKEN_MESSAGE) from error

    return _build_template_response(template)


@router.delete("/{template_id}", response_model=MessageResponse)
async def delete_run_template_handler(
    strategy_id: str,
    template_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> MessageResponse:
    """硬删模板; 已删的再删即 404 (同 `delete_strategy_handler` 的重复删除语义).

    不软删的理由在模型侧 (没有外键指向它). 这里只补一条操作面的后果: 删掉的名字立刻可以再用,
    故"模板满了"这件事总有出路——`MAXIMUM_TEMPLATES_PER_STRATEGY` 这道护栏因此不会把用户关在
    外面.
    """

    template = await load_owned_run_template(
        session, current_user, strategy_id, template_id
    )

    await session.delete(template)
    await session.commit()

    return MessageResponse(message=TEMPLATE_DELETED_MESSAGE)
