"""策略的上传、查询、删除与授权共享.

上传形态是**两个文件字段**: `source` (`.py`) 与 `configuration` (`.json`, 策略启动时真正去读的
那一份). 两份都必须是文件而不是文本字段, 因为**文件名本身是载荷**: 策略在作业目录里按自己硬编
码的名字 `open()` 那份配置, 平台得知道该把它落成什么名字, 而一个纯文本字段没有名字可给.

写操作一律经 visibility 的归属收口 (`load_owned_strategy`): 传新版本、删除与改授权都只对
归属人开放, 非归属人一律 404, 不区分"无权"与"不存在".
"""

from __future__ import annotations

import ast
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import CurrentUserDependency
from ..catalog.enums import StrategyVisibility
from ..catalog.models import (
    StrategyGrantModel,
    StrategyModel,
    StrategyVersionModel,
    UserModel,
)
from ..catalog.pagination import DEFAULT_PAGE_SIZE, MAXIMUM_PAGE_SIZE, fetch_page
from ..catalog.schemas import (
    MAXIMUM_STRATEGY_DESCRIPTION_LENGTH,
    MAXIMUM_STRATEGY_NAME_LENGTH,
    LastSubmittedParametersResponse,
    MessageResponse,
    PageResponse,
    StrategyDetailResponse,
    StrategyGrantReplaceRequest,
    StrategyGrantResponse,
    StrategyResponse,
    StrategyVersionResponse,
)
from ..catalog.visibility import (
    build_visible_strategy_query,
    load_owned_strategy,
    load_visible_strategy,
)
from ..clock import utc_now
from ..dependencies import SessionDependency, SettingsDependency
from ..errors import ConflictError, InvalidRequestError
from ..ids import generate_identifier
from ..services.run_prefill import read_last_submitted_parameters
from ..services.strategy_store import (
    UploadedStrategyVersion,
    store_strategy_version,
)
from ..strategy_configuration import (
    CONFIGURATION_FILENAME_SUFFIX,
    ENTRY_FILENAME_SUFFIX,
    MAXIMUM_CONFIGURATION_BYTES,
    parse_configuration_template,
    validate_bare_filename,
)


router = APIRouter()

MAXIMUM_SOURCE_BYTES = 1024 * 1024
UPLOAD_READ_CHUNK_BYTES = 64 * 1024
MAXIMUM_GRANTS_PER_STRATEGY = 1000

STRATEGY_NAME_TAKEN_MESSAGE = "同名策略已存在"
STRATEGY_DELETED_MESSAGE = "策略已删除"
BLANK_STRATEGY_NAME_MESSAGE = "策略名不能为空白"
EMPTY_SOURCE_MESSAGE = "策略源码不能为空"
SOURCE_TOO_LARGE_MESSAGE = f"策略源码不得超过 {MAXIMUM_SOURCE_BYTES} 字节"
EMPTY_CONFIGURATION_MESSAGE = "策略配置不能为空"
CONFIGURATION_TOO_LARGE_MESSAGE = (
    f"策略配置不得超过 {MAXIMUM_CONFIGURATION_BYTES} 字节"
)
MISSING_UPLOAD_FILENAME_MESSAGE = "上传的文件没有文件名"
CONFIGURATION_NOT_UTF8_MESSAGE = "策略配置须是 UTF-8 编码的文本"
ENTRY_FILENAME_LABEL = "入口文件名"
CONFIGURATION_FILENAME_LABEL = "配置文件名"
TOO_MANY_GRANTS_MESSAGE = f"单次授权的用户数不得超过 {MAXIMUM_GRANTS_PER_STRATEGY}"
REPEATED_GRANTEE_MESSAGE = "同一被授权人不得重复出现"
UNKNOWN_GRANTEE_MESSAGE = "被授权人不存在"
GRANT_TO_OWNER_MESSAGE = "不能授权给策略归属人"
GRANTS_CONFLICTED_MESSAGE = "授权正被其他请求修改, 请重试"


async def _read_uploaded_file(
    upload: UploadFile,
    maximum_bytes: int,
    empty_message: str,
    too_large_message: str,
) -> bytes:
    """按上限分块读一个上传部件, 超限即拒.

    分块而非一次读完: 一次读完等于把"客户端说多大就占多少内存"交给调用方.

    两份上传共用这一个函数而不是各写一遍: 两处各写一遍时, 缺的分块或漏的上限只会出现在其中
    一处, 而症状是"某一个入口能把自己撑爆"。
    """

    chunks: list[bytes] = []
    total_bytes = 0

    while chunk := await upload.read(UPLOAD_READ_CHUNK_BYTES):
        total_bytes += len(chunk)

        if total_bytes > maximum_bytes:
            raise InvalidRequestError(too_large_message)

        chunks.append(chunk)

    if not chunks:
        raise InvalidRequestError(empty_message)

    return b"".join(chunks)


def _validate_uploaded_filename(
    upload: UploadFile, required_suffix: str, field_label: str
) -> str:
    """取上传部件自带的文件名并校验它是个能当裸文件名的取值; 不合法即译成 400.

    缺失不是罕见情形: 客户端拿字符串当文件传时 `filename` 就是 `None`, 而名字是**载荷**的一部分
    (见模块 docstring), 没有它这次上传就不完整.

    译 `ValueError` 的这一层不能省: `validate_bare_filename` 是纯函数, 按契约抛 `ValueError`, 而
    裸 ValueError 从处理函数里抛出去会绕过 `InvalidRequestError` 那条处理器, 落到兜底的 500 ——
    用户拿到"服务器内部错误", 而问题出在他自己传的文件名上.
    """

    if not upload.filename:
        raise InvalidRequestError(MISSING_UPLOAD_FILENAME_MESSAGE)

    try:
        return validate_bare_filename(
            upload.filename, required_suffix, field_label
        )
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error


def _validate_source_is_parsable(source_bytes: bytes, entry_filename: str) -> None:
    """源码须能被解析.

    语法错误留到运行期只会以"宿主退出码 1"的面目出现, 归因要翻 stdout; 在上传时拦下,
    报错能直接指到行列. 交给 `ast` 而非 `eval`/`exec`: 只解析, 一行都不执行.
    """

    try:
        ast.parse(source_bytes, filename=entry_filename)
    except (SyntaxError, ValueError) as error:
        raise InvalidRequestError(
            f"策略源码无法解析: {entry_filename}:{error.lineno}:{error.offset}: {error.msg}"
        ) from error


def _decode_configuration(configuration_bytes: bytes) -> str:
    """配置须是 UTF-8 文本.

    非 UTF-8 的配置**两份读者都读不动**: 平台这边解不出文本, 而渲染后的那份配置虽然被
    `serialize_configuration` 转成了纯 ASCII, 上传的这份原文在作者本机是拿什么写的却无从得知
    ——早一点说清楚, 好过留一句"策略报告说读不到配置"。
    """

    try:
        return configuration_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidRequestError(CONFIGURATION_NOT_UTF8_MESSAGE) from error


async def _read_validated_version_upload(
    source_file: UploadFile, configuration_file: UploadFile
) -> UploadedStrategyVersion:
    """把两个上传部件读完并验完; 任一项不合法即整批拒.

    建策略与传新版本共用这一条路径: 两处各抄一遍, 迟早有一处漏掉其中一步——漏掉源码可解析性
    检查, 代价是收下一个只会以"宿主退出码 1"现身的策略.

    文件名一律经 `validate_bare_filename`: 它们会变成作业目录里的裸文件名与 `argv[0]`.
    """

    entry_filename = _validate_uploaded_filename(
        source_file,
        ENTRY_FILENAME_SUFFIX,
        ENTRY_FILENAME_LABEL,
    )
    configuration_filename = _validate_uploaded_filename(
        configuration_file,
        CONFIGURATION_FILENAME_SUFFIX,
        CONFIGURATION_FILENAME_LABEL,
    )

    source_bytes = await _read_uploaded_file(
        source_file, MAXIMUM_SOURCE_BYTES, EMPTY_SOURCE_MESSAGE, SOURCE_TOO_LARGE_MESSAGE
    )
    configuration_bytes = await _read_uploaded_file(
        configuration_file,
        MAXIMUM_CONFIGURATION_BYTES,
        EMPTY_CONFIGURATION_MESSAGE,
        CONFIGURATION_TOO_LARGE_MESSAGE,
    )

    _validate_source_is_parsable(source_bytes, entry_filename)

    configuration_text = _decode_configuration(configuration_bytes)

    try:
        parse_configuration_template(configuration_text)
    except ValueError as error:
        raise InvalidRequestError(f"策略配置不合法: {error}") from error

    return UploadedStrategyVersion(
        source_bytes=source_bytes,
        entry_filename=entry_filename,
        configuration_text=configuration_text,
        configuration_filename=configuration_filename,
    )


async def _build_strategy_detail(
    session: AsyncSession, strategy: StrategyModel, current_user: UserModel
) -> StrategyDetailResponse:
    """策略详情: 本体 + 版本列表 + 授权列表.

    授权列表只对归属人返回: 被授权人若能看到完整授权表, 就知道还有谁也被授权了.
    """

    version_rows = (
        (
            await session.execute(
                select(StrategyVersionModel)
                .where(StrategyVersionModel.strategy_id == strategy.id)
                .order_by(StrategyVersionModel.version_no.desc())
            )
        )
        .scalars()
        .all()
    )

    grant_responses: list[StrategyGrantResponse] = []

    if strategy.owner_user_id == current_user.id:
        grant_rows = (
            (
                await session.execute(
                    select(StrategyGrantModel)
                    .where(StrategyGrantModel.strategy_id == strategy.id)
                    .order_by(StrategyGrantModel.granted_at.desc())
                )
            )
            .scalars()
            .all()
        )
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


async def _ensure_each_grantee_exists(
    session: AsyncSession, grantee_user_ids: set[str]
) -> None:
    """确认这些被授权人都在册, 缺一个即整批拒.

    一条 `IN` 查完, 不逐个查: 逐个查的话往返次数由请求体里的列表长度决定, 等于把"发多少个
    查询"交给调用方.

    比对的是集合而非计数: 传进来的 id 可能重复 (另一处已拒), 计数相等说明不了一切.

    在册校验是一条 `IN`, 参数个数受 SQLite 的 `SQLITE_MAX_VARIABLE_NUMBER` 约束
    (3.32 起默认 32766, 本机 3.39.4 实测 32766 通过、32767 起报
    `too many SQL variables`). **上限正是挡在这道坎前面的东西**, 不只是业务尺寸:
    不设上限的话, 列表长度就只剩请求体上限这一个约束, 而那道闸 (约 1.2 MB) 挡不住
    ——被授权人 id 的 schema 下限是 1 个字符, 且名单不许重复, 于是一份 3 个字符的
    互异 id 凑满 32767 条的请求体只有约 85 万字节, 过得了体量闸. 这样的请求会走到
    这里, 在参数绑定时抛出 `OperationalError`, 而本端点只捕获 `IntegrityError`,
    于是客户端拿到的是 500 而不是干净的 400. 上限既是业务答案, 也是这道底线的闸.

    同一条也解释了为何按"32 位主键"估算体量是不够的: 那种读法算得约 2.14 万条,
    看着离 32766 还远, 但 schema 允许更短的 id, 估算的前提就不成立.

    被授权人不存在时明说, 不并入"无权"一类含糊文案: 这里泄漏的只是"某个 32 位随机主键
    在不在册", 而调用方要么已经知道这个 id (那它本来就是已知的人), 要么是在猜——猜中
    128 位随机串的概率不值得为它牺牲可用性.
    """

    found_user_ids = set(
        (
            await session.execute(
                select(UserModel.id).where(UserModel.id.in_(grantee_user_ids))
            )
        )
        .scalars()
        .all()
    )

    if found_user_ids != grantee_user_ids:
        raise InvalidRequestError(UNKNOWN_GRANTEE_MESSAGE)


def _apply_visibility_for_grants(strategy: StrategyModel, has_grants: bool) -> None:
    """按授权集合调整可见性.

    `private` 下授权表不生效, 于是"授权写成功"与"对方能看见"必须绑成一件事: 授权非空即转
    `shared`, 授权清空即转回 `private`. 不绑的话, 在 private 策略上授权会回 200 而无人获得
    访问权——调用方看到的成败与真实结果相反, 且整条接口清单里没有第二处能把可见性改成
    `shared`, 用户无从自救.

    `public` 一概不动: 那份授权本就多余 (全体登录用户已然可见), 而 `public` 转 `shared` 会
    把"没被逐一点名的用户"静默挡在外面——收窄既有访问不是这条接口该做的事, 真要做也该由
    调用方明说.
    """

    if strategy.visibility_type == StrategyVisibility.PUBLIC.value:
        return

    desired_visibility = (
        StrategyVisibility.SHARED.value if has_grants else StrategyVisibility.PRIVATE.value
    )

    if strategy.visibility_type != desired_visibility:
        strategy.visibility_type = desired_visibility


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


@router.post(
    "", response_model=StrategyDetailResponse, status_code=status.HTTP_201_CREATED
)
async def create_strategy_handler(
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
    source_file: Annotated[UploadFile, File(alias="source")],
    configuration_file: Annotated[UploadFile, File(alias="configuration")],
    name: Annotated[
        str, Form(min_length=1, max_length=MAXIMUM_STRATEGY_NAME_LENGTH)
    ],
    description: Annotated[
        str, Form(max_length=MAXIMUM_STRATEGY_DESCRIPTION_LENGTH)
    ] = "",
    visibility_type: Annotated[StrategyVisibility, Form()] = StrategyVisibility.PRIVATE,
) -> StrategyDetailResponse:
    """上传一个新策略: 建策略本体并落成首个版本.

    名字先 `strip` 再存: 不去空白的话, "网格" 与 "网格 " 会各占一个策略, 而界面上看是同一个.
    """

    strategy_name = name.strip()

    if not strategy_name:
        raise InvalidRequestError(BLANK_STRATEGY_NAME_MESSAGE)

    uploaded_version = await _read_validated_version_upload(
        source_file, configuration_file
    )

    strategy = StrategyModel(
        id=generate_identifier(),
        owner_user_id=current_user.id,
        name=strategy_name,
        description=description,
        visibility_type=visibility_type.value,
    )
    session.add(strategy)

    try:
        await session.flush()
    except IntegrityError as error:
        raise ConflictError(STRATEGY_NAME_TAKEN_MESSAGE) from error

    await store_strategy_version(
        session, settings, strategy, current_user, uploaded_version
    )

    return await _build_strategy_detail(session, strategy, current_user)


@router.post(
    "/{strategy_id}/versions",
    response_model=StrategyVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_strategy_version_handler(
    strategy_id: str,
    session: SessionDependency,
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
    source_file: Annotated[UploadFile, File(alias="source")],
    configuration_file: Annotated[UploadFile, File(alias="configuration")],
) -> StrategyVersionModel:
    """给既有策略上传一个新版本.

    判重是"与**既有任一**版本同内容"而非"与最新版本同内容": 改了参数又改回去, 就该命中那个
    老版本而不是再落一份同内容的新版本——版本号要能表达"内容变过几次", 不是"上传过几次".
    """

    strategy = await load_owned_strategy(session, current_user, strategy_id)

    uploaded_version = await _read_validated_version_upload(
        source_file, configuration_file
    )

    return await store_strategy_version(
        session, settings, strategy, current_user, uploaded_version
    )


@router.put("/{strategy_id}/grants", response_model=StrategyDetailResponse)
async def replace_strategy_grants_handler(
    strategy_id: str,
    request_body: StrategyGrantReplaceRequest,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> StrategyDetailResponse:
    """整体替换策略的授权集合, 只对归属人开放.

    `PUT` 是整体替换: 请求体就是替换后的全集, 空列表即撤销全部授权.

    未经校验的请求一条都不落库: 删旧插新与改可见性全在同一事务里, 任一项不合法即整批拒.
    否则"部分替换成功"会留下一个既非旧名单也非新名单的授权集合, 而调用方从响应上看不出
    自己拿到的是哪一份.

    `GrantedAt` 因此记的是"最近一次整体写入的时间", 不是"首次授权的时间"——未被改动的条目
    也一并删掉重建. 要保留首次授权时间得改成逐条增删改, 那是另一套语义.
    """

    strategy = await load_owned_strategy(session, current_user, strategy_id)

    grantee_user_ids = [grant.grantee_user_id for grant in request_body.grants]

    if len(grantee_user_ids) > MAXIMUM_GRANTS_PER_STRATEGY:
        raise InvalidRequestError(TOO_MANY_GRANTS_MESSAGE)

    if len(set(grantee_user_ids)) != len(grantee_user_ids):
        raise InvalidRequestError(REPEATED_GRANTEE_MESSAGE)

    if current_user.id in grantee_user_ids:
        raise InvalidRequestError(GRANT_TO_OWNER_MESSAGE)

    if grantee_user_ids:
        await _ensure_each_grantee_exists(session, set(grantee_user_ids))

    await session.execute(
        delete(StrategyGrantModel).where(StrategyGrantModel.strategy_id == strategy.id)
    )

    session.add_all(
        StrategyGrantModel(
            strategy_id=strategy.id,
            grantee_user_id=grant.grantee_user_id,
            permission_type=grant.permission_type.value,
            granted_by_user_id=current_user.id,
        )
        for grant in request_body.grants
    )

    _apply_visibility_for_grants(strategy, bool(grantee_user_ids))

    try:
        await session.commit()
    except IntegrityError as error:
        raise ConflictError(GRANTS_CONFLICTED_MESSAGE) from error

    return await _build_strategy_detail(session, strategy, current_user)


@router.get("/{strategy_id}", response_model=StrategyDetailResponse)
async def read_strategy_handler(
    strategy_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> StrategyDetailResponse:
    """策略详情、版本列表与授权列表."""

    strategy = await load_visible_strategy(session, current_user, strategy_id)

    return await _build_strategy_detail(session, strategy, current_user)


@router.get(
    "/{strategy_id}/last-submitted-parameters",
    response_model=LastSubmittedParametersResponse,
)
async def read_last_submitted_parameters_handler(
    strategy_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> LastSubmittedParametersResponse:
    """该用户在该策略下最近一次提交的参数, 供提交页预填.

    可见性闸与详情同款 (`load_visible_strategy`): 别人共享给你的策略也要能用这个功能. 但
    **取哪一轮运行**另有归属过滤 (服务层经 `visibility.build_owned_run_query`), 故共享或公开
    策略下不会把归属人的参数填到你表单里.

    没有历史运行时照常回 200 且字段全空, 不是 404——首次用某个策略走的就是这条路.

    参数**不在这里按范围过滤**: 前端为了渲染控件本来就要逐项判断取值能不能用, 后端再滤一道
    就是两处真相, 而且会静默吞掉键、让排障变难.
    """

    strategy = await load_visible_strategy(session, current_user, strategy_id)

    last_submitted_parameters = await read_last_submitted_parameters(
        session, current_user, strategy.id
    )

    if last_submitted_parameters is None:
        return LastSubmittedParametersResponse()

    return LastSubmittedParametersResponse(
        run_id=last_submitted_parameters.run_id,
        submitted_at=last_submitted_parameters.submitted_at,
        match_mode=last_submitted_parameters.match_mode,
        bar_period=last_submitted_parameters.bar_period,
        exchange_id=last_submitted_parameters.exchange_id,
        instrument_id=last_submitted_parameters.instrument_id,
        start_trading_day=last_submitted_parameters.start_trading_day,
        end_trading_day=last_submitted_parameters.end_trading_day,
        initial_capital=last_submitted_parameters.initial_capital,
        params=last_submitted_parameters.params,
    )


@router.delete("/{strategy_id}", response_model=MessageResponse)
async def delete_strategy_handler(
    strategy_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> MessageResponse:
    """软删除策略.

    只落 `DeletedAt`, 不动盘上的版本目录: 历史运行要复现就得靠那份原文, 清掉目录会让旧结果
    失去可追溯的依据. 已删的策略连归属人也取不到, 重复删除即 404.
    """

    strategy = await load_owned_strategy(session, current_user, strategy_id)

    strategy.deleted_at = utc_now()

    await session.commit()

    return MessageResponse(message=STRATEGY_DELETED_MESSAGE)
