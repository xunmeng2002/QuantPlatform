"""引擎自检.

后端的可用性取决于引擎侧三个硬条件: 与当前解释器匹配的扩展模块存在 (它是 cp311-win_amd64,
Python 小版本不匹配即不可用)、引擎运行时 DLL 齐备、运行根可写. 这些条件不满足时提交回测
只会得到一个难以归因的启动失败, 故在设置页提前暴露.

本端点要求认证 (计划原文把它列为免认证): 单机部署没有负载均衡这类匿名消费者, 而响应要报出
绝对路径与缺失的 DLL 名, 对匿名调用者开放等于泄漏内部布局.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..auth.dependencies import CurrentUserDependency
from ..dependencies import SettingsDependency


router = APIRouter()

PYTHON_BINDING_FILENAME_PREFIX = "QuantTrading."
PYTHON_BINDING_FILENAME_SUFFIX = ".pyd"
ENGINE_RUNTIME_FILENAMES = ("BackTest.dll", "Core.dll", "Network.dll")
WRITE_PROBE_FILENAME_PREFIX = ".write-probe-"


class EngineHealthResponse(BaseModel):
    """引擎自检结果."""

    ready: bool
    interpreter_tag: str
    python_version: str
    engine_root: str
    engine_root_exists: bool
    python_binding_filename: str | None
    missing_runtime_filenames: list[str]
    runs_root: str
    runs_root_writable: bool


def _find_python_binding(engine_root: Path) -> Path | None:
    """找与当前解释器 ABI 匹配的扩展模块.

    扩展模块名形如 `QuantTrading.cp311-win_amd64.pyd`, 其中的 cp311 即 ABI 标签, 故按解释器
    版本派生出标签再比对文件名, 不靠 import 试错——试错失败时拿不到原因.
    """

    if not engine_root.is_dir():
        return None

    interpreter_tag = _interpreter_tag()

    for candidate in sorted(engine_root.glob(f"{PYTHON_BINDING_FILENAME_PREFIX}*")):
        if candidate.name.endswith(PYTHON_BINDING_FILENAME_SUFFIX) and interpreter_tag in candidate.name:
            return candidate

    return None


def _interpreter_tag() -> str:
    """当前解释器的 ABI 标签, 如 cp311."""

    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _probe_directory_writable(directory: Path) -> bool:
    """实际落一个探针文件判定可写性.

    不看权限位: Windows 上权限位不可靠 (只读属性与 ACL 是两回事), 只有真写一次才算数.
    目录不存在时顺带创建——运行根本就该存在.
    """

    try:
        directory.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            prefix=WRITE_PROBE_FILENAME_PREFIX, dir=directory, delete=True
        ):
            return True
    except OSError:
        return False


@router.get("", response_model=EngineHealthResponse)
async def read_health_handler(
    settings: SettingsDependency,
    current_user: CurrentUserDependency,
) -> EngineHealthResponse:
    """引擎自检: 扩展模块、运行时 DLL 与运行根可写性."""

    python_binding = _find_python_binding(settings.engine_root)

    missing_runtime_filenames = [
        filename
        for filename in ENGINE_RUNTIME_FILENAMES
        if not (settings.engine_root / filename).is_file()
    ]

    runs_root_writable = _probe_directory_writable(settings.runs_root)

    return EngineHealthResponse(
        ready=python_binding is not None and not missing_runtime_filenames and runs_root_writable,
        interpreter_tag=_interpreter_tag(),
        python_version=".".join(str(part) for part in sys.version_info[:3]),
        engine_root=str(settings.engine_root),
        engine_root_exists=settings.engine_root.is_dir(),
        python_binding_filename=python_binding.name if python_binding else None,
        missing_runtime_filenames=missing_runtime_filenames,
        runs_root=str(settings.runs_root),
        runs_root_writable=runs_root_writable,
    )
