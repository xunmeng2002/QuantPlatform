"""引擎自检.

后端的可用性取决于引擎侧一组硬条件: 与当前解释器匹配的扩展模块存在 (名字里的 cpXXX 即 ABI
标签, Python 小版本不匹配即不可用)、引擎运行时 DLL 齐备、运行根可写, 以及调度侧要读入的三个
文件分别在位. 这些条件不满足时提交回测只会得到一个难以归因的启动失败, 故在设置页提前暴露.

本端点要求管理员 (计划原文把它列为免认证, P1 实施时先收为需认证, 现再收为仅管理员): 响应要
报出引擎根与运行根的绝对路径、缺失的 DLL 名与解释器版本, 即本机内部布局的清单. 单机部署没有
负载均衡这类匿名消费者, 而普通用户拿到这份清单只有泄漏面——他能做的动作里没有一项需要它.

三个引擎侧输入的缺失**不并入 `ready`**: `market_data_root` 与 `session_file_path` 缺失会让
每个作业都构造不出工作目录, `seed_database_path` 缺失只是让费用三项退化成 0 (引擎自己
`exists` 之后 Warning 并继续, 见 `SimExchange.cpp`). 分成三个独立的旗标, 调用方才能分辨
"跑不了"与"跑得了但费用不全".
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..auth.dependencies import AdminUserDependency
from ..dependencies import SettingsDependency
from ..services.engine_probe import (
    find_python_binding,
    interpreter_tag,
    missing_runtime_filenames,
    read_engine_version,
)


router = APIRouter()

WRITE_PROBE_FILENAME_PREFIX = ".write-probe-"


class EngineHealthResponse(BaseModel):
    """引擎自检结果."""

    ready: bool
    interpreter_tag: str
    python_version: str
    engine_root: str
    engine_root_exists: bool
    # "跑的是哪一版", 与上面几项"能不能跑"不是一回事: 引擎根坏了它是空串, 引擎根好好的它也
    # 可能只是个内容摘要. 平台不收引擎包, 故这里报的是**探测到的**标识, 不是校验过的版本号.
    engine_version: str
    python_binding_filename: str | None
    missing_runtime_filenames: list[str]
    runs_root: str
    runs_root_writable: bool
    market_data_root: str
    market_data_root_exists: bool
    session_file_path: str
    session_file_exists: bool
    seed_database_path: str
    seed_database_exists: bool


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
    admin_user: AdminUserDependency,
) -> EngineHealthResponse:
    """引擎自检: 扩展模块、运行时 DLL、运行根可写性与三个引擎侧输入."""

    python_binding = find_python_binding(settings.engine_root)

    absent_runtime_filenames = missing_runtime_filenames(settings.engine_root)

    runs_root_writable = _probe_directory_writable(settings.runs_root)

    return EngineHealthResponse(
        ready=(
            python_binding is not None
            and not absent_runtime_filenames
            and runs_root_writable
        ),
        interpreter_tag=interpreter_tag(),
        python_version=".".join(str(part) for part in sys.version_info[:3]),
        engine_root=str(settings.engine_root),
        engine_root_exists=settings.engine_root.is_dir(),
        engine_version=read_engine_version(settings.engine_root),
        python_binding_filename=python_binding.name if python_binding else None,
        missing_runtime_filenames=absent_runtime_filenames,
        runs_root=str(settings.runs_root),
        runs_root_writable=runs_root_writable,
        market_data_root=str(settings.market_data_root),
        market_data_root_exists=settings.market_data_root.is_dir(),
        session_file_path=str(settings.session_file_path),
        session_file_exists=settings.session_file_path.is_file(),
        seed_database_path=str(settings.seed_database_path),
        seed_database_exists=settings.seed_database_path.is_file(),
    )
