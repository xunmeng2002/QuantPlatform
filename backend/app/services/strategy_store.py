"""策略库的落盘与版本落库.

布局见 `platform-plan.md` §7.4:

```text
<user_library_root>/<user_id>/strategies/<strategy_id>/<version_no>/
├── <entry_filename>   # 上传原文, 平台永不改写
└── manifest.json      # 该版本的 manifest 快照
```

版本目录**只追加、永不改写**: 历史运行的复现性依赖它, 改动一次, 那轮结果就再也对不上.

`StoragePath` 存的是**相对 `user_library_root`** 的路径, 故换机器、换根目录都不必改库;
读取方一律以 `settings.user_library_root / version.storage_path` 还原.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.models import StrategyModel, StrategyVersionModel, UserModel
from ..config import PlatformSettings
from ..clock import utc_now
from ..errors import ConflictError
from ..ids import generate_identifier
from ..manifest import StrategyManifest


STRATEGY_LIBRARY_DIRECTORY_NAME = "strategies"
MANIFEST_FILENAME = "manifest.json"
STAGING_DIRECTORY_PREFIX = ".staging-"

VERSION_CONFLICT_MESSAGE = "该策略有并发上传占用了同一个版本号, 请重试"


def compute_source_hash(source_bytes: bytes) -> str:
    """源码的 SHA-256 十六进制摘要."""

    return hashlib.sha256(source_bytes).hexdigest()


def build_version_directory(user_id: str, strategy_id: str, version_no: int) -> Path:
    """版本目录**相对 `user_library_root`** 的路径.

    故意只回相对路径: 入库的 `StoragePath` 要的就是它 (换机器、换根目录都不必改库), 而需要
    绝对路径的调用点自己在前面拼根——一个函数回两种路径, 读的人分不清哪个是哪个.
    """

    return (
        Path(user_id) / STRATEGY_LIBRARY_DIRECTORY_NAME / strategy_id / str(version_no)
    )


def write_strategy_version(
    storage_path: Path,
    entry_filename: str,
    source_bytes: bytes,
    manifest_json: str,
) -> None:
    """把上传原文与 manifest 快照写进版本目录.

    先写同级的临时目录、再整目录改名: 于是失败时唯一的删除动作只落在自己刚建的临时目录上,
    碰不到任何既有版本目录; 改名也保证读侧永远看不到半个版本.
    """

    storage_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = storage_path.parent / f"{STAGING_DIRECTORY_PREFIX}{generate_identifier()}"

    try:
        staging_path.mkdir()
        (staging_path / entry_filename).write_bytes(source_bytes)
        (staging_path / MANIFEST_FILENAME).write_text(manifest_json, encoding="utf-8")
        staging_path.rename(storage_path)
    except OSError:
        shutil.rmtree(staging_path, ignore_errors=True)
        raise


def remove_strategy_version(storage_path: Path, user_library_root: Path) -> None:
    """撤掉一个刚写成的版本目录, 用于落库失败后的回滚.

    只接受位于策略库根之内、且末段为纯数字版本号的路径. 版本目录删掉即不可追溯, 故这里宁可
    在路径不合预期时直接拒绝——真拒了说明调用点算错了路径, 那是必须立刻暴露的缺陷.
    """

    if not storage_path.is_relative_to(user_library_root):
        raise ValueError(f"拒绝删除策略库之外的路径: {storage_path}")

    if not storage_path.name.isdigit():
        raise ValueError(f"拒绝删除非版本号目录: {storage_path}")

    shutil.rmtree(storage_path, ignore_errors=True)


async def _find_identical_version(
    session: AsyncSession, strategy_id: str, source_hash: str, manifest_json: str
) -> StrategyVersionModel | None:
    """取同策略下的同内容版本.

    判同必须连 manifest 一起比: manifest 决定入口文件名与配置文件渲染, 同一份源码配不同
    manifest 是一份**新**版本. 只比源码会把它错判成"内容未变"而吞掉这次上传.
    """

    return await session.scalar(
        select(StrategyVersionModel)
        .where(StrategyVersionModel.strategy_id == strategy_id)
        .where(StrategyVersionModel.source_hash == source_hash)
        .where(StrategyVersionModel.manifest_json == manifest_json)
    )


async def _next_version_no(session: AsyncSession, strategy_id: str) -> int:
    """取下一个版本号."""

    highest_version_no = await session.scalar(
        select(func.max(StrategyVersionModel.version_no)).where(
            StrategyVersionModel.strategy_id == strategy_id
        )
    )

    return (highest_version_no or 0) + 1


async def store_strategy_version(
    session: AsyncSession,
    settings: PlatformSettings,
    strategy: StrategyModel,
    uploaded_by: UserModel,
    source_bytes: bytes,
    manifest: StrategyManifest,
) -> StrategyVersionModel:
    """把一次上传落成一个版本; 内容与既有版本完全相同则复用该版本, 不占新版本号.

    提交也在此完成, 不留给调用点: 落盘与落库必须同生共死, 分在两层写就会出现"文件在而库里
    无记录"的孤儿目录, 而孤儿目录会占住那个版本号, 使下一次同号重试的改名失败——一次瞬时
    故障 (磁盘满、忙等超时) 就此变成该策略的永久故障. 故回滚接的是**提交失败这一层**, 不是
    某个具体的异常类型; 只有完整性冲突才另译成 409.

    删目录排在 `rollback()` **之前**: 两者都是收尾, 但删目录只依赖文件系统, 而 `rollback()`
    要依赖连接还活着. 排在后面的话, 连接已断这类让回滚本身也失败的情形会让目录留下——正好
    把这段代码要防的那个故障又放了进来.

    版本号是"读 max 加一", 两个并发上传会算出同一个号. 认号靠的正是那次整目录改名:
    改名成功才算占到号, 撞上了就是 `FileExistsError`, 译成 409 让调用方重试. 这条路径必须
    在写盘处接, 接到提交处就永远走不到——改名先于提交失败. **这套语义是 Windows 的**:
    目标存在即 `FileExistsError`. POSIX 的 `rename(2)` 会静默替换空目录目标, 撞号不再报错
    而变成"两次上传共用一个目录", 后一次的回滚会删掉前一次仍在用的目录. 本平台绑死
    Windows + Python 3.14 (`.pyd` 是 `cp314-win_amd64`, 见 `PROGRESS.md` 备注), 故不为此加分支; 若日后换平台,
    这里必须先改成 `mkdir(exist_ok=False)` 之类的显式占位.

    复用路径上不碰 `UpdatedAt`: 没有任何新内容落库, 把"最近变更"往前推会让这个字段说谎.
    """

    manifest_json = manifest.model_dump_json()
    source_hash = compute_source_hash(source_bytes)

    identical_version = await _find_identical_version(
        session, strategy.id, source_hash, manifest_json
    )

    if identical_version is not None:
        return identical_version

    version_no = await _next_version_no(session, strategy.id)
    relative_directory = build_version_directory(
        strategy.owner_user_id, strategy.id, version_no
    )
    storage_path = settings.user_library_root / relative_directory

    try:
        write_strategy_version(
            storage_path, manifest.entry_filename, source_bytes, manifest_json
        )
    except FileExistsError as error:
        raise ConflictError(VERSION_CONFLICT_MESSAGE) from error

    version = StrategyVersionModel(
        id=generate_identifier(),
        strategy_id=strategy.id,
        version_no=version_no,
        entry_filename=manifest.entry_filename,
        config_filename=manifest.config_filename,
        manifest_json=manifest_json,
        source_hash=source_hash,
        storage_path=relative_directory.as_posix(),
        uploaded_by_user_id=uploaded_by.id,
    )
    session.add(version)

    strategy.updated_at = utc_now()

    try:
        await session.commit()
    except Exception as error:
        remove_strategy_version(storage_path, settings.user_library_root)

        await session.rollback()

        if isinstance(error, IntegrityError):
            raise ConflictError(VERSION_CONFLICT_MESSAGE) from error

        raise

    return version
