"""领域异常.

业务与数据层只抛这些框架无关的异常, 由 main 统一翻译成 HTTP 状态码; 数据层因此不必依赖
FastAPI, 状态码映射也只有一处.
"""

from __future__ import annotations


class ResourceNotFoundError(LookupError):
    """资源对当前用户不存在, 或存在但无权知晓. 两者刻意不区分."""


class PermissionDeniedError(PermissionError):
    """已确认资源存在, 但当前用户无权执行该操作."""


class InvalidRequestError(ValueError):
    """请求内容不合法, 且原因可以明说给调用方."""


class ConflictError(ValueError):
    """请求与现有数据冲突, 如同名资源已存在."""
