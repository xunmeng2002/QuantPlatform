"""策略版本目录的落盘: 占位语义与失败回滚.

两条性质都无法从接口层面看出来 —— 接口照常返回 201, 而那个版本号对应的目录里可能是**别人
那一版**的文件, 或者干脆是个占住版本号的空壳. 故直接测 `write_strategy_version`.
"""

from pathlib import Path

import pytest

from app.services.strategy_store import UploadedStrategyVersion, write_strategy_version


SOURCE_FILENAME = "grid_strategy.py"
CONFIGURATION_FILENAME = "TestStrategyGrid.json"
SOURCE_TEXT = "def on_bar(context):\n    pass\n"
CONFIGURATION_TEXT = '{"Parameters": {}}'
VERSION_DIRECTORY_PARTS = ("user-one", "strategies", "strategy-one", "1")


def _uploaded_version() -> UploadedStrategyVersion:
    return UploadedStrategyVersion(
        source_bytes=SOURCE_TEXT.encode("utf-8"),
        entry_filename=SOURCE_FILENAME,
        configuration_text=CONFIGURATION_TEXT,
        configuration_filename=CONFIGURATION_FILENAME,
    )


def _version_directory(tmp_path: Path) -> Path:
    return tmp_path.joinpath(*VERSION_DIRECTORY_PARTS)


def test_a_version_directory_holds_both_uploaded_files(tmp_path: Path) -> None:
    storage_path = _version_directory(tmp_path)

    write_strategy_version(storage_path, _uploaded_version())

    assert (storage_path / SOURCE_FILENAME).read_text(encoding="utf-8") == SOURCE_TEXT
    assert (storage_path / CONFIGURATION_FILENAME).read_text(encoding="utf-8") == CONFIGURATION_TEXT


def test_an_occupied_version_number_raises_instead_of_overwriting(tmp_path: Path) -> None:
    """撞号必须报错, 且一个字节都不许落进既有的那个目录.

    认号靠的就是这一次占位. 目录已存在却不报错的写法 (整目录改名在 POSIX 上就是) 会让两次
    上传共用一个目录, 后一次的回滚再把它删掉 —— 前一次仍在用的版本就此消失, 而两次上传都
    返回了成功.
    """

    storage_path = _version_directory(tmp_path)
    storage_path.mkdir(parents=True)
    (storage_path / "已有版本.txt").write_text("上一版", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_strategy_version(storage_path, _uploaded_version())

    assert [entry.name for entry in storage_path.iterdir()] == ["已有版本.txt"]


def test_a_failed_write_removes_the_claimed_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写崩了要连占位一起撤掉.

    留下的空目录会占住那个版本号, 使下一次重试撞上同一号 —— 一次瞬时故障 (磁盘满、忙等超时)
    就此变成该策略的永久故障.
    """

    storage_path = _version_directory(tmp_path)

    def _refuse_to_write(self: Path, data: str, encoding: str | None = None) -> int:
        raise OSError("磁盘满")

    monkeypatch.setattr(Path, "write_text", _refuse_to_write)

    with pytest.raises(OSError):
        write_strategy_version(storage_path, _uploaded_version())

    assert not storage_path.exists()
