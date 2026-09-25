"""应用装配.

配置解析、建表与播种全放在 lifespan 内: TestClient 与 uvicorn 因此走同一条装配路径, 不会
出现"测试里能跑、线上少挂一步初始化"这类偏差.

启动方式: 在 backend 目录下 `python -m app.main`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import uvicorn

from .bootstrap import ensure_initial_admin
from .catalog.database import PlatformDatabase
from .config import PlatformSettings
from .errors import (
    ConflictError,
    InvalidRequestError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from .routers import auth, health, runs, strategies, users


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

UNEXPECTED_ERROR_DETAIL = "服务器内部错误"

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
    """建表、播种, 并在退出时释放连接池."""

    settings: PlatformSettings = application.state.settings
    database: PlatformDatabase = application.state.database

    await database.initialize()
    await ensure_initial_admin(database, settings)

    try:
        yield
    finally:
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
    """构造应用. 未传配置时按环境变量解析."""

    resolved_settings = settings if settings is not None else PlatformSettings.from_environment()

    application = FastAPI(
        title=APPLICATION_TITLE,
        description=APPLICATION_DESCRIPTION,
        version=APPLICATION_VERSION,
        lifespan=_application_lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = PlatformDatabase(resolved_settings.database_url)

    _register_exception_handlers(application)
    _register_routers(application)

    return application


def main() -> None:
    """以 uvicorn 启动后端."""

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    settings = PlatformSettings.from_environment()

    uvicorn.run(create_application(settings), host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":
    main()
