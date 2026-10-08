"""引擎侧硬条件的探测.

扩展模块的 ABI 标签、运行时 DLL、以及调度侧要读入的三个文件, 都属于"不满足时提交回测只会
得到一个难以归因的启动失败"那一类. health 端点据此在设置页提前暴露, 调度器用同一组判据做
**非阻断**诊断.

本模块刻意不依赖 FastAPI: 路由与调度器都要用它, 而调度器导入路由模块是反向依赖.

除"能不能跑"之外, 这里还回答"跑的是哪一版" —— 见 `read_engine_version`.
"""

from __future__ import annotations

import hashlib
import sys
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path


PYTHON_BINDING_FILENAME_PREFIX = "QuantTrading."

# 扩展模块允许叫什么后缀, 由**解释器**说了算, 不在这里写死: Windows 是 `.cp314-win_amd64.pyd` /
# `.pyd`, Linux 是 `.cpython-314-x86_64-linux-gnu.so` / `.abi3.so` / `.so`. 这份表就是 import
# 机制自己用的那一份, 故跨平台不必分档; 次序"最具体在前", 按序取首个命中即得 ABI 最贴的那个
# (目录里同时躺着通用的与带 ABI 标签的两份时, 选带标签的).
PYTHON_BINDING_FILENAME_SUFFIXES: tuple[str, ...] = tuple(EXTENSION_SUFFIXES)

ENGINE_RUNTIME_FILENAMES = ("BackTest.dll", "Core.dll", "Network.dll")

# 平台侧为引擎包约定的版本文件, 不是引擎自己认的文件名 (与 `Sessions.json` 那类不同):
# 它由 QuantTrading 的发布流程产出, 一行文本即版本号. 缺失时退回内容摘要, 见下.
ENGINE_VERSION_FILENAME = "engine-version.txt"

# 摘要有前缀而版本文件没有: 两种取值的**来源**在库里因此一眼可辨. 少了这个前缀, 一份
# `2026.09.26` 与一串十六进制会看起来像同类取值, 而它们**不可比较**, 分不出这一点的人会拿
# 它们去排序或去判"哪个更新".
VERSION_DIGEST_PREFIX = "sha256:"
VERSION_DIGEST_LENGTH = 12

# 与 `RunModel.EngineVersion` 的列宽一致; 超长的版本号截断而非拒收——它是一个标识符,
# 保住前缀远好过整个丢掉.
MAXIMUM_ENGINE_VERSION_LENGTH = 64


def interpreter_tag() -> str:
    """当前解释器的 ABI 标签, 如 cp314."""

    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def find_python_binding(engine_root: Path) -> Path | None:
    """找与当前解释器 ABI 匹配的扩展模块.

    判据与 import 机制**同源**: 文件名必须是"模块名 + 本平台允许的一个扩展后缀", 而那份后缀表由
    解释器给出 (`EXTENSION_SUFFIXES`). 于是 Windows 命中 `QuantTrading.cp314-win_amd64.pyd`、
    Linux 命中 `QuantTrading.cpython-314-x86_64-linux-gnu.so`; 而为别的 ABI 编出来的
    `QuantTrading.cp39-win_amd64.pyd` **不命中** —— 它不等于"模块名 + 允许的后缀"中的任何一个,
    这正是 import 机制也拒绝它的理由. 不靠 import 试错: 试错失败时拿不到原因.
    """

    if not engine_root.is_dir():
        return None

    module_name = PYTHON_BINDING_FILENAME_PREFIX.removesuffix(".")

    for suffix in PYTHON_BINDING_FILENAME_SUFFIXES:
        candidate = engine_root / f"{module_name}{suffix}"

        if candidate.is_file():
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


def read_engine_version(engine_root: Path) -> str:
    """标识"这一轮跑的是哪个引擎构建".

    两层口径, **文件优先**:

    1. `<engine_root>/engine-version.txt` 的内容 —— 人可读, 精确, 但要 QuantTrading 的发布
       流程产出它. 引擎的依赖闭包只有它自己知道, 故完整口径只能由它给出.
    2. 扩展模块 + 三个运行时 DLL 的**内容摘要** —— 不依赖任何人配合, 对"这四个文件变了"敏感.
       它盖不住引擎包里别的文件, 这只是兜底而不是等价替代.
    3. 两者都取不到时回空串: "不知道"是一个诚实的取值, 与 `Hostname` / `DbPath` 的空串同形.
       此时引擎类策略本来也起不来 (退出码 1), 不必另造一个哨兵值.

    结果**不缓存**: 每次调用重新算 (约 1 MB 的读盘). 缓存的代价在这里不是性能而是正确性 ——
    同一个后端进程内, 引擎根可能被换掉 (改 `.env` 后不必重启即可被下一次调用看见), 而测试会
    就地改写共享的临时引擎根, 缓存会给出过期结果.
    """

    declared_version = _read_declared_engine_version(engine_root)

    if declared_version:
        return declared_version

    return _hash_engine_binaries(engine_root)


def _read_declared_engine_version(engine_root: Path) -> str:
    """读版本文件; 不在、读不出、内容全为空白时回空串.

    只取**首个非空行**: 版本文件是给人看也给人写的, 有人会在里面加注释或改动说明, 取首个非空
    行的容错比"整份文件必须是一行"高得多.
    """

    try:
        version_text = (engine_root / ENGINE_VERSION_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    for line in version_text.splitlines():
        stripped_line = line.strip()

        if stripped_line:
            return stripped_line[:MAXIMUM_ENGINE_VERSION_LENGTH]

    return ""


def _hash_engine_binaries(engine_root: Path) -> str:
    """引擎可执行文件的内容摘要; 一个文件都取不到时回空串.

    一个文件都读不到时**必须**回空串而不是"空输入的摘要": 后者是一个确定的十六进制串, 会被读
    成"这确实是一个构建", 而真相是这个引擎根下什么都没有.
    """

    digest = hashlib.sha256()
    hashed_file_count = 0

    for binary_path in _engine_binary_paths(engine_root):
        try:
            digest.update(binary_path.read_bytes())
        except OSError:
            continue

        hashed_file_count += 1

    if not hashed_file_count:
        return ""

    return f"{VERSION_DIGEST_PREFIX}{digest.hexdigest()[:VERSION_DIGEST_LENGTH]}"


def _engine_binary_paths(engine_root: Path) -> list[Path]:
    """参与摘要的文件, 次序固定: 扩展模块在前, 运行时 DLL 按 `ENGINE_RUNTIME_FILENAMES` 的次序.

    次序必须固定: 摘要对输入次序敏感, 次序一变同一份引擎会算出两个值.
    """

    binding_path = find_python_binding(engine_root)
    candidates = [
        *([binding_path] if binding_path is not None else []),
        *(engine_root / filename for filename in ENGINE_RUNTIME_FILENAMES),
    ]

    return [path for path in candidates if path.is_file()]
