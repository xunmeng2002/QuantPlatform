"""作业工作目录的构造.

布局见 `job-workspace.md` §1. 骨架照搬 `services/strategy_store.write_strategy_version` 的
**staging + 整目录改名** (失败时唯一的删除动作只落在自己刚建的临时目录上, 读侧永远看不到半个
作业), 但有两处不同:

1. 没有版本号争抢——`RunId` 是随机 UUID4, 不存在两个执行者瞄同一个目标目录, 故不需要
   "改名撞车 = 认号失败" 那套语义. 仍保留一条防御: 目标目录已存在即拒, 绝不覆盖.
2. 临时目录必须建在 **`runs_root` 之内**: 跨卷改名在 Windows 上会失败 (`MoveFile` 不能跨
   卷), 而开发机上临时目录与运行根常在同一卷, 这类错误在那里**测不出来**.

`result.json` 绝不在构造阶段创建: 它的缺席是"本轮没走完收尾"的唯一信号, 见 §4.3 的仲裁表.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import SEED_DATABASE_FILENAME, PlatformSettings
from ..ids import generate_identifier
from .engine_config import (
    DUMP_DIRECTORY_NAME,
    ENGINE_CONFIG_FILENAME,
    SESSION_FILENAME,
)


STAGING_DIRECTORY_PREFIX = ".staging-"

FORBIDDEN_DIRECTORY_NAME_CHARACTERS = ("/", "\\", ":")


@dataclass(frozen=True)
class JobFileSet:
    """构造一个作业目录所需的全部输入.

    两个配置文本由提交侧渲染好并落库 (`BacktestConfigJson` / `ParamsJson`), 调度侧只负责
    写出来: 于是"库里记的"与"盘上写的"永远是同一份, 事后复查不必重新渲染一次来对账.

    `seed_database_source_path` 是调度侧刚给**这一轮**生成的那份种子库 (见
    `reference_data.seed_database`): 它被**搬**进作业目录而不是复制, 因为里面的费率行是按这一轮
    的合约展开出来的, 留在别处只会被误当成"下一轮也能用".
    """

    run_id: str
    entry_filename: str
    entry_source_path: Path
    strategy_configuration_filename: str
    strategy_configuration_text: str
    engine_configuration_text: str
    seed_database_source_path: Path


def build_job_directory(settings: PlatformSettings, job_files: JobFileSet) -> Path:
    """构造 `runs/<RunId>/` 并返回其绝对路径.

    任一步失败都只抛异常, 由调度侧把该轮标成失败——不在这里改写库, 也不在这里重试.
    """

    _ensure_safe_directory_name(job_files.run_id)

    runs_root = settings.runs_root
    runs_root.mkdir(parents=True, exist_ok=True)

    target_directory = runs_root / job_files.run_id

    if target_directory.exists():
        raise FileExistsError(f"作业目录已存在, 拒绝覆盖: {job_files.run_id}")

    staging_directory = runs_root / f"{STAGING_DIRECTORY_PREFIX}{generate_identifier()}"

    # 跨卷改名在 Windows 上必然失败, 而那种失败发生在收尾那一步, 前面写的一切都得作废. 把它
    # 变成构造期的硬失败, 位置一换就是"要么不可能、要么立刻可见".
    if staging_directory.parent != runs_root:
        raise RuntimeError("临时目录必须与运行根同卷")

    try:
        staging_directory.mkdir()
        _write_job_files(staging_directory, settings, job_files)
        staging_directory.rename(target_directory)
    except OSError:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise

    return target_directory


def _write_job_files(
    staging_directory: Path, settings: PlatformSettings, job_files: JobFileSet
) -> None:
    """把五个文件与 `Dump/` 父目录写进临时目录."""

    (staging_directory / ENGINE_CONFIG_FILENAME).write_text(
        job_files.engine_configuration_text, encoding="utf-8"
    )

    # 会话表按字节复制: 引擎原样读它, 平台不对它的内容做任何解释.
    (staging_directory / SESSION_FILENAME).write_bytes(
        settings.session_file_path.read_bytes()
    )

    # 入口文件**原样**复制到 job 目录根部: 它同时是裸文件名、`argv[0]` 与策略自己硬编码的那个
    # 名字, 改一个字都会让引擎日志器拼不出日志路径并在启动期终止 (见 §3.1).
    shutil.copyfile(
        job_files.entry_source_path, staging_directory / job_files.entry_filename
    )

    (staging_directory / job_files.strategy_configuration_filename).write_text(
        job_files.strategy_configuration_text, encoding="utf-8"
    )

    # 种子库**搬**进来 (`os.replace`, 同卷内原子): 它的内容是按这一轮的合约展开出来的, 别处
    # 再留一份只会被误当成别轮也能用. 临时文件由调度侧建在同一个运行根下, 故这一步不会跨卷.
    os.replace(
        job_files.seed_database_source_path,
        staging_directory / SEED_DATABASE_FILENAME,
    )

    # 只建 Dump 这一层: 其下的 <RunId> 由引擎自己建 (它才是决定要不要建的一方).
    (staging_directory / DUMP_DIRECTORY_NAME).mkdir()


def _ensure_safe_directory_name(run_id: str) -> None:
    """确认这个 id 能安全地当作一个目录段.

    `Path("a") / "C:/x"` 的结果是 `C:/x`——**绝对路径会整个替换掉运行根**. 运行主键本是平台自己
    生成的, 但一个值一旦要落成路径段, 就该在用它之前确认它确实只是一段.
    """

    if not run_id:
        raise ValueError("运行主键不能为空")

    if any(character in run_id for character in FORBIDDEN_DIRECTORY_NAME_CHARACTERS):
        raise ValueError("运行主键不得含路径分隔符或卷标字符")

    if run_id in {".", ".."}:
        raise ValueError("运行主键不能是目录引用")
