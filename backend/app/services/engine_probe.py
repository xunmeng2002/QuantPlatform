"""引擎侧硬条件的探测.

扩展模块的 ABI 标签、运行时 DLL、以及调度侧要读入的三个文件, 都属于"不满足时提交回测只会
得到一个难以归因的启动失败"那一类. health 端点据此在设置页提前暴露, 调度器用同一组判据做
**非阻断**诊断.

本模块刻意不依赖 FastAPI: 路由与调度器都要用它, 而调度器导入路由模块是反向依赖.
"""

from __future__ import annotations

import sys
from pathlib import Path


PYTHON_BINDING_FILENAME_PREFIX = "QuantTrading."
PYTHON_BINDING_FILENAME_SUFFIX = ".pyd"
ENGINE_RUNTIME_FILENAMES = ("BackTest.dll", "Core.dll", "Network.dll")


def interpreter_tag() -> str:
    """当前解释器的 ABI 标签, 如 cp314."""

    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def find_python_binding(engine_root: Path) -> Path | None:
    """找与当前解释器 ABI 匹配的扩展模块.

    扩展模块名形如 `QuantTrading.cp314-win_amd64.pyd`, 其中的 cp314 即 ABI 标签, 故按解释器
    版本派生出标签再比对文件名, 不靠 import 试错——试错失败时拿不到原因.
    """

    if not engine_root.is_dir():
        return None

    current_tag = interpreter_tag()

    for candidate in sorted(engine_root.glob(f"{PYTHON_BINDING_FILENAME_PREFIX}*")):
        if (
            candidate.name.endswith(PYTHON_BINDING_FILENAME_SUFFIX)
            and current_tag in candidate.name
        ):
            return candidate

    return None


def missing_runtime_filenames(engine_root: Path) -> list[str]:
    """引擎运行时缺哪些 DLL, 名字原样列出."""

    return [
        filename
        for filename in ENGINE_RUNTIME_FILENAMES
        if not (engine_root / filename).is_file()
    ]


def is_python_binding_available(engine_root: Path) -> bool:
    """引擎根下是否存在与当前解释器匹配的扩展模块.

    供调度器做非阻断诊断: 不匹配时作业仍会被拉起 (测试的引擎根是空目录), 只是把这条事实写进
    该作业的 stderr 与服务端日志, 免得"退出码 1 + 引擎日志里什么都没有"无人可归因.
    """

    return find_python_binding(engine_root) is not None
