"""应用装配.

配置解析、建表与播种全放在 lifespan 内: TestClient 与 uvicorn 因此走同一条装配路径, 不会
出现"测试里能跑、线上少挂一步初始化"这类偏差.

启动方式: 在 backend 目录下 `python -m app.main`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import uvicorn

from .bootstrap import ensure_initial_admin
from .catalog.database import PlatformDatabase
from .config import PlatformSettings, resolve_platform_settings
from .errors import (
    ConflictError,
    InvalidRequestError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from .manifest import MAXIMUM_MANIFEST_BYTES
from .routers import auth, health, runs, strategies, users
from .routers.strategies import MAXIMUM_SOURCE_BYTES
from .scheduler.recovery import recover_interrupted_runs
from .scheduler.scheduler import RunScheduler


APPLICATION_TITLE = "QuantPlatform"
APPLICATION_DESCRIPTION = "量化回测平台: 包裹 QuantTrading 引擎, 运行用户上传的 Python 策略"
APPLICATION_VERSION = "0.1.0"

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

AUTH_PREFIX = "/api/auth"
HEALTH_PREFIX = "/api/health"
USERS_PREFIX = "/api/users"
STRATEGIES_PREFIX = "/api/strategies"
RUNS_PREFIX = "/api/runs"

MULTIPART_FRAMING_ALLOWANCE_BYTES = 64 * 1024

MAXIMUM_REQUEST_BODY_BYTES = (
    MAXIMUM_SOURCE_BYTES + MAXIMUM_MANIFEST_BYTES + MULTIPART_FRAMING_ALLOWANCE_BYTES
)

UNEXPECTED_ERROR_DETAIL = "服务器内部错误"
REQUEST_TOO_LARGE_DETAIL = "请求体过大"
INVALID_CONTENT_LENGTH_DETAIL = "Content-Length 不合法"

SENSITIVE_FIELD_NAMES = frozenset({"password", "password_hash"})
REDACTED_FIELD_VALUE = "***"


def _redact_validation_error(validation_error: dict[str, Any]) -> dict[str, Any]:
    """抹掉报错中回显的敏感字段值, 其余原样保留.

    默认的校验失败回执会把出错的输入整个抄回响应体, 而这类响应常被接入层与代理日志落盘,
    等于口令进日志.
    """

    location = validation_error.get("loc", ())

    if not any(str(part) in SENSITIVE_FIELD_NAMES for part in location):
        return validation_error

    redacted = dict(validation_error)

    if "input" in redacted:
        redacted["input"] = REDACTED_FIELD_VALUE

    return redacted

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _application_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """建表、播种、结清上一轮残留, 再起调度器; 退出时反序收场.

    次序不能换: 恢复必须在调度器**启动之前**跑完. 反过来的话, 恢复那条 UPDATE 会把调度器刚认领
    的作业一起标成中断, 而那个作业的进程已经起来了——库里说"中断", 进程还在写盘.
    """

    settings: PlatformSettings = application.state.settings
    database: PlatformDatabase = application.state.database
    scheduler: RunScheduler = application.state.scheduler

    await database.initialize()
    await ensure_initial_admin(database, settings)
    await recover_interrupted_runs(database)
    await scheduler.start()

    try:
        yield
    finally:
        await scheduler.stop()
        await database.close()


def _register_exception_handlers(application: FastAPI) -> None:
    """把领域异常翻译成 HTTP 状态码.

    未预期的异常统一回一句固定文案: 堆栈、路径与库结构只进服务端日志, 不外泄给调用方.
    """

    @application.exception_handler(ResourceNotFoundError)
    async def _handle_resource_not_found(
        request: Request, error: ResourceNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @application.exception_handler(PermissionDeniedError)
    async def _handle_permission_denied(
        request: Request, error: PermissionDeniedError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(error)})

    @application.exception_handler(ConflictError)
    async def _handle_conflict(request: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(InvalidRequestError)
    async def _handle_invalid_request(
        request: Request, error: InvalidRequestError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @application.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """把校验失败回执里的敏感字段值抹掉后再回."""

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": [
                    _redact_validation_error(item) for item in error.errors()
                ]
            },
        )

    @application.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("未处理的异常: %s %s", request.method, request.url.path)

        return JSONResponse(status_code=500, content={"detail": UNEXPECTED_ERROR_DETAIL})


def _register_request_size_guard(application: FastAPI) -> None:
    """在解析请求体之前拦下超限的请求.

    处理函数里的那种"按上限分块读"只保护内存, 不保护磁盘: 轮到它执行时, Starlette 早已把整个
    multipart 体读完, 超过约 1 MB 的部分已经落进磁盘临时文件. 于是"上传限 1 MB"这句话对磁盘
    并不成立——任何登录用户都能用一次请求把盘写满, 且他不需要真的准备一份大文件, 慢速发送即可.

    这道闸按 `Content-Length` 在路由之前回 413, 体一个字节都不读.

    只认 `Content-Length`: 分块编码 (Transfer-Encoding: chunked) 不携带这个头, 绕得过这道闸.
    堵住它得在读取过程中计字节数, 那又要把每个请求体过一遍手, 代价与收益不成比例——留到 P8
    由反向代理按字节数兜底. 见 PROGRESS.md ❓.

    解析这个头**不能拿 `str.isdigit()` 当守卫**: 成帧层为了判长度会按 OWS 裁掉首尾空白, 但 ASGI
    scope 里放的是**未裁剪的原文**, 于是 `"1179649 "` 这种取值 `isdigit()` 为假, 闸整条跳过,
    请求被完整解析、超限部分落进磁盘临时文件后才由内层那道管内存的闸拦下——而这正是它想防的
    事. 一个尾随空格就够了. `int()` 自己会吃掉 OWS, 故直接交给它; 解析不出来就拒, 不留 500:
    这个头是客户端给的, 它给了一个应用层读不懂的值, 没有理由放行. 拒绝必须发生在**这道中间件
    内部**——注册的异常处理器在用户中间件栈的内侧, 从这里抛出去的异常会绕开它, 客户端拿到的是
    Starlette 的纯文本 500, 中文固定文案与 `logger.exception` 都不生效.

    上限的余量取 64 KB, 按两个真实上限推出来, 不另设魔数: 源码与 manifest 各有一道自己的闸,
    这一道只是把二者之前的那段 (multipart 分隔符、各部件头、名字与说明) 也圈进来.
    """

    @application.middleware("http")
    async def _reject_oversized_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared_length = request.headers.get("content-length")

        if declared_length is not None:
            try:
                declared_bytes = int(declared_length)
            except ValueError:
                logger.warning("Content-Length 无法解析, 已拒绝该请求")

                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": INVALID_CONTENT_LENGTH_DETAIL},
                )

            if declared_bytes > MAXIMUM_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": REQUEST_TOO_LARGE_DETAIL},
                )

        return await call_next(request)


def _register_routers(application: FastAPI) -> None:
    """挂载各路由模块."""

    application.include_router(auth.router, prefix=AUTH_PREFIX, tags=["auth"])
    application.include_router(health.router, prefix=HEALTH_PREFIX, tags=["health"])
    application.include_router(users.router, prefix=USERS_PREFIX, tags=["users"])
    application.include_router(
        strategies.router, prefix=STRATEGIES_PREFIX, tags=["strategies"]
    )
    application.include_router(runs.router, prefix=RUNS_PREFIX, tags=["runs"])


def create_application(settings: PlatformSettings | None = None) -> FastAPI:
    """构造应用. 未传配置时读环境文件再按环境变量解析."""

    resolved_settings = settings if settings is not None else resolve_platform_settings()

    application = FastAPI(
        title=APPLICATION_TITLE,
        description=APPLICATION_DESCRIPTION,
        version=APPLICATION_VERSION,
        lifespan=_application_lifespan,
    )
    database = PlatformDatabase(resolved_settings.database_url)

    application.state.settings = resolved_settings
    application.state.database = database
    # 调度器在装配期就建好 (而不是在 lifespan 里): 取消端点要经依赖取它, 而依赖函数只做取出.
    # 未起循环时注册表为空、信号量满格, 端点照样能答——它只是取不到句柄而已.
    application.state.scheduler = RunScheduler(resolved_settings, database)

    _register_exception_handlers(application)
    _register_request_size_guard(application)
    _register_routers(application)

    return application


def main() -> None:
    """以 uvicorn 启动后端."""

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # 配置在 basicConfig 之后解析: 环境文件读了哪几项要能被日志看见, 否则打错键名时
    # 唯一的现象是"口令没生效", 而看不到任何提示.
    settings = resolve_platform_settings()

    uvicorn.run(create_application(settings), host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":
    main()
