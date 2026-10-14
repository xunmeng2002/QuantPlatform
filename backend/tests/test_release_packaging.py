"""发布打包脚本里「清空输出目录」那道闸.

这条闸管的是**不可逆**操作: `--force` 会递归删掉 `--output` 指向的目录. 而打包目录常常就选在
部署根上, 部署根里住着 `runs/`、`users/`、`backend/data/`、`backend/.env` —— git 管不着,
删了取回不来. 故这里逐条钉住「什么情况下必须拒绝」.

打包脚本本身不在本包里 (发布包只收 `backend/app` 与 `reference_seed`), 故这些用例只在仓内跑.
"""

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType
from typing import NoReturn

import pytest

PACKAGING_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "package_release.py"


def load_packaging_module() -> ModuleType:
    """按路径加载打包脚本.

    不按模块名 import: 它是仓根下的一个脚本, 不在本包的 `pythonpath` 上. 加载它只定义常量与
    函数, 打包动作全在 `main()` 里, 没有副作用.
    """

    specification = importlib.util.spec_from_file_location("package_release", PACKAGING_SCRIPT_PATH)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    return module


@pytest.fixture(name="packaging_module")
def packaging_module_fixture() -> ModuleType:
    return load_packaging_module()


@pytest.fixture(name="deletion_trap")
def deletion_trap_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `shutil.rmtree` 换成「只记账」的替身.

    这条闸的用例要证的是「必须在动手删除**之前**就拒绝」. 万一闸门有洞, 真让它删下去毁掉的
    就是跑测试这台机器上的数据 —— 故宁可让用例失败, 也不给删除留任何机会.
    """

    def refuse_deletion(target: object, *arguments: object, **keywords: object) -> NoReturn:
        raise AssertionError(f"护栏没拦住, 走到了删除: {target}")

    monkeypatch.setattr(shutil, "rmtree", refuse_deletion)


@pytest.mark.parametrize("requested_directory", ["", ".", ".."])
def test_an_unspecified_output_directory_is_refused(
    packaging_module: ModuleType, deletion_trap: None, requested_directory: str
) -> None:
    with pytest.raises(ValueError, match="不接受空值"):
        packaging_module.resolve_output_directory(requested_directory, True)


def test_the_repository_root_is_refused(packaging_module: ModuleType, deletion_trap: None) -> None:
    repository_root = packaging_module.SCRIPT_DIRECTORY

    with pytest.raises(ValueError, match="仓根或家目录"):
        packaging_module.resolve_output_directory(str(repository_root), True)

    assert repository_root.is_dir()


def test_the_home_directory_is_refused(packaging_module: ModuleType, deletion_trap: None) -> None:
    home_directory = Path.home()

    with pytest.raises(ValueError, match="仓根或家目录"):
        packaging_module.resolve_output_directory(str(home_directory), True)

    assert home_directory.is_dir()


def test_an_existing_file_as_output_is_refused(packaging_module: ModuleType, deletion_trap: None, tmp_path: Path) -> None:
    existing_file = tmp_path / "not-a-directory.txt"
    existing_file.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="不是目录"):
        packaging_module.resolve_output_directory(str(existing_file), True)


def test_an_output_directory_holding_foreign_entries_is_refused(
    packaging_module: ModuleType, deletion_trap: None, tmp_path: Path
) -> None:
    deployment_root = tmp_path / "QuantPlatform"
    deployment_root.mkdir()
    (deployment_root / "backend").mkdir()
    (deployment_root / "catalog.db").write_text("运行数据", encoding="utf-8")

    with pytest.raises(ValueError, match="不认识的条目"):
        packaging_module.resolve_output_directory(str(deployment_root), True)

    assert (deployment_root / "catalog.db").is_file()


def test_clearing_its_own_output_requires_the_force_flag(packaging_module: ModuleType, tmp_path: Path) -> None:
    release_root = tmp_path / "QuantPlatform"
    (release_root / "backend").mkdir(parents=True)
    (release_root / "docs").mkdir()
    (release_root / "start-platform.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(ValueError, match="已存在且非空"):
        packaging_module.resolve_output_directory(str(release_root), False)

    assert (release_root / "docs").is_dir()

    resolved_root = packaging_module.resolve_output_directory(str(release_root), True)

    assert resolved_root == release_root
    assert resolved_root.is_dir()
    assert not any(resolved_root.iterdir())
