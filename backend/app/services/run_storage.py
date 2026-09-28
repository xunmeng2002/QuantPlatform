"""作业目录的定位与整目录移除: 读产物、读结果库、删运行三条路径共用的一份守卫.

**守卫只能有一份**. 三条路径都要"由运行行还原出它的作业目录", 而失败的含义各不相同: 读路径
失败只是 404 (少看一个文件), 删除路径失败若方向反了就**删错东西**——不可逆. 故判定收在这里,
且失败方向恒为"拒绝" (fail-closed).

**删除原语也不另立一份**. 运行删除端点与保留清理调的是同一个 `remove_run_directory`, 于是
"什么算删干净了"只有 `ROW_DELETABLE_OUTCOMES` 一个判据——两处各判一次的话, 迟早出现"端点认
删干净了、清理不认"这种只在某一条路径上泄漏磁盘的偏差.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..catalog.models import RunModel
from ..config import PlatformSettings


logger = logging.getLogger(__name__)


class RunDirectoryRemoval(StrEnum):
    """一次整目录移除的结局.

    `REMOVED` / `ALREADY_ABSENT` / `NO_DIRECTORY_RECORDED` 都表示"此刻盘上没有这一轮的目录",
    `REFUSED` / `FAILED` 表示"可能还在". 这个二分就是 `ROW_DELETABLE_OUTCOMES`.
    """

    REMOVED = "removed"
    ALREADY_ABSENT = "already_absent"
    NO_DIRECTORY_RECORDED = "no_directory_recorded"
    REFUSED = "refused"
    FAILED = "failed"


ROW_DELETABLE_OUTCOMES = frozenset(
    {
        RunDirectoryRemoval.REMOVED,
        RunDirectoryRemoval.ALREADY_ABSENT,
        RunDirectoryRemoval.NO_DIRECTORY_RECORDED,
    }
)


@dataclass(frozen=True, slots=True)
class RunDirectoryResolution:
    """作业目录的解析结果: `directory` 非空即可安全操作它.

    为空时 `absence` 说明为何没有目录, 且取值必然是 `NO_DIRECTORY_RECORDED` (这一行本就没记
    目录, 从未起过进程) 或 `REFUSED` (记了但不可信). 两者对读路径都是 404, 对删除路径却是
    "可删行"与"保留行"之别, 故不能合并成一个 `None`.
    """

    directory: Path | None
    absence: RunDirectoryRemoval | None


def resolve_run_directory(
    settings: PlatformSettings, run: RunModel
) -> RunDirectoryResolution:
    """由运行行还原它的作业目录, 并确认它确实落在运行根之内.

    目录名就是 `RunId` (`scheduler/workspace.py` 的 `runs_root / job_files.run_id`), 而
    `WorkspacePath` 列记的正是这个值 (`services/run_submission` 落行时写入), 故**从库里重建
    即可**, 不必去翻调度器的内存 (那个 `job_directory` 只是运行期属性, 重启后就没了).

    四道校验都不能省. `WorkspacePath` 的列缺省是空串, 空串拼出来的路径**就是运行根本身**,
    于是 `../../<别人的 RunId>/result.json` 这类请求会一路通过"在运行根之内"的判断, 变成跨
    租户读产物. 而"不得等于运行根"挡不住一个记错的合法目录名——比如把 A 轮的目录名写到 B 轮
    的行上, 读 B 读到 A 的产物、删 B 删掉 A 的目录. 故最后一条把契约"`RunId` 同时用作目录名"
    变成可执行判据: 名字对不上就是不可信, 读不到也删不得.

    **先 `resolve()` 再判是否在根内**: 符号链接要先解开才谈得上"在不在根内", 顺序反了等于没判.
    """

    runs_root = settings.runs_root.resolve()

    if not run.workspace_path:
        return RunDirectoryResolution(None, RunDirectoryRemoval.NO_DIRECTORY_RECORDED)

    try:
        job_directory = (runs_root / run.workspace_path).resolve()
    except (OSError, ValueError) as error:
        logger.warning("作业目录名不合法 run_id=%s: %s", run.id, error)
        return RunDirectoryResolution(None, RunDirectoryRemoval.REFUSED)

    if job_directory == runs_root or not job_directory.is_relative_to(runs_root):
        logger.warning("作业目录越出运行根 run_id=%s", run.id)
        return RunDirectoryResolution(None, RunDirectoryRemoval.REFUSED)

    if job_directory.name != run.id:
        logger.warning("作业目录名与运行号不符 run_id=%s", run.id)
        return RunDirectoryResolution(None, RunDirectoryRemoval.REFUSED)

    return RunDirectoryResolution(job_directory, None)


def remove_run_directory(
    settings: PlatformSettings, run: RunModel
) -> RunDirectoryRemoval:
    """整目录移除该轮的作业目录; 只有 `ROW_DELETABLE_OUTCOMES` 里的结局才允许接着删行.

    阻塞调用, 调用方负责 `asyncio.to_thread`. **不做 `exists()` 预检**: 先问后删在两次系统调用
    之间给出一个窗口, 窗口里目录刚被别处删掉就会把本来成功的删除报成失败. 这里直接删并接住
    `FileNotFoundError`, 语义一样而窗口没有.

    不加 `onerror` 回调去改属性: 那会把"删不掉"悄悄变成"改掉权限再删", 而在自己的运行根上改
    ACL 比删不掉更糟. 也不重试——Windows 上正在被占用 (比如孤儿进程还开着结果库) 的目录删不掉,
    如实返回 `FAILED`: 行是那个目录**唯一**的句柄, 保留行就还能下次再来, 而报成功等于把磁盘
    泄漏变成不可观测的.
    """

    resolution = resolve_run_directory(settings, run)

    if resolution.absence is not None:
        return resolution.absence

    try:
        shutil.rmtree(resolution.directory)
    except FileNotFoundError:
        return RunDirectoryRemoval.ALREADY_ABSENT
    except OSError as error:
        logger.warning(
            "作业目录删除失败 run_id=%s 类型=%s errno=%s",
            run.id,
            type(error).__name__,
            error.errno,
        )
        return RunDirectoryRemoval.FAILED

    return RunDirectoryRemoval.REMOVED
