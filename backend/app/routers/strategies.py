"""策略的上传、查询与删除.

上传形态: `.py` 必需, manifest 以 multipart 的一个**文本字段**传入. 前端可以填表单、也可以
读入一份 `manifest.json` 再填进同一个字段——两条路落到同一字段, 故服务端只有一条路径.

写操作一律经 visibility 的归属收口 (`load_owned_strategy`): 传新版本与删除都只对归属人开放,
非归属人一律 404, 不区分"无权"与"不存在".
"""

from __future__ import annotations

import ast
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from sqlalchemy import select
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
    MessageResponse,
    PageResponse,
    StrategyDetailResponse,
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
from ..manifest import StrategyManifest, parse_strategy_manifest
from ..services.strategy_store import store_strategy_version


router = APIRouter()

MAXIMUM_SOURCE_BYTES = 1024 * 1024
UPLOAD_READ_CHUNK_BYTES = 64 * 1024

STRATEGY_NAME_TAKEN_MESSAGE = "同名策略已存在"
STRATEGY_DELETED_MESSAGE = "策略已删除"
BLANK_STRATEGY_NAME_MESSAGE = "策略名不能为空白"
EMPTY_SOURCE_MESSAGE = "策略源码不能为空"
SOURCE_TOO_LARGE_MESSAGE = f"策略源码不得超过 {MAXIMUM_SOURCE_BYTES} 字节"


async def _read_uploaded_source(source_file: UploadFile) -> bytes:
    """按上限分块读上传的源码, 超限即拒.

    分块而非一次读完: 一次读完等于把"客户端说多大就占多少内存"交给调用方.
    """

    chunks: list[bytes] = []
    total_bytes = 0

    while chunk := await source_file.read(UPLOAD_READ_CHUNK_BYTES):
        total_bytes += len(chunk)

        if total_bytes > MAXIMUM_SOURCE_BYTES:
            raise InvalidRequestError(SOURCE_TOO_LARGE_MESSAGE)

        chunks.append(chunk)

    if not chunks:
        raise InvalidRequestError(EMPTY_SOURCE_MESSAGE)

    return b"".join(chunks)


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


async def _read_validated_upload(
    source_file: UploadFile, manifest_text: str
) -> tuple[bytes, StrategyManifest]:
    """读上传的源码, 连 manifest 一起验完再返回.

    建策略与传新版本共用这一条路径: 两处各抄一遍, 迟早有一处漏掉其中一步——漏掉源码可解析性
    检查, 代价是收下一个只会以"宿主退出码 1"现身的策略.
    """

    parsed_manifest = parse_strategy_manifest(manifest_text)
    source_bytes = await _read_uploaded_source(source_file)

    _validate_source_is_parsable(source_bytes, parsed_manifest.entry_filename)

    return source_bytes, parsed_manifest


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
    manifest: Annotated[str, Form()],
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

    source_bytes, parsed_manifest = await _read_validated_upload(source_file, manifest)

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
        session, settings, strategy, current_user, source_bytes, parsed_manifest
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
    manifest: Annotated[str, Form()],
) -> StrategyVersionModel:
    """给既有策略上传一个新版本.

    判重是"与**既有任一**版本同内容"而非"与最新版本同内容": 改了参数又改回去, 就该命中那个
    老版本而不是再落一份同内容的新版本——版本号要能表达"内容变过几次", 不是"上传过几次".
    """

    strategy = await load_owned_strategy(session, current_user, strategy_id)

    source_bytes, parsed_manifest = await _read_validated_upload(source_file, manifest)

    return await store_strategy_version(
        session, settings, strategy, current_user, source_bytes, parsed_manifest
    )


@router.get("/{strategy_id}", response_model=StrategyDetailResponse)
async def read_strategy_handler(
    strategy_id: str,
    session: SessionDependency,
    current_user: CurrentUserDependency,
) -> StrategyDetailResponse:
    """策略详情、版本列表与授权列表."""

    strategy = await load_visible_strategy(session, current_user, strategy_id)

    return await _build_strategy_detail(session, strategy, current_user)


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
