"""引擎版本标识的取数.

两层口径 (版本文件 / 内容摘要) 的优先级与各自边界, 以及"取不到"这一档的取值. 这里的每一条
都是一个**不可见**的行为: 版本号不会让任何东西报错, 取错了只会让库里多一批分不出来源的轮.
"""

from __future__ import annotations

from pathlib import Path

from app.services.engine_probe import (
    ENGINE_RUNTIME_FILENAMES,
    ENGINE_VERSION_FILENAME,
    MAXIMUM_ENGINE_VERSION_LENGTH,
    VERSION_DIGEST_PREFIX,
    read_engine_version,
)


BINDING_FILENAME = "QuantTrading.cp314-win_amd64.pyd"
DIGEST_HEX_LENGTH = 12
OVERLONG_VERSION_LENGTH = 200


def _engine_root(tmp_path: Path) -> Path:
    return tmp_path / "engine"


def _write_binding(engine_root: Path, content: bytes = b"binding") -> None:
    engine_root.mkdir(parents=True, exist_ok=True)
    (engine_root / BINDING_FILENAME).write_bytes(content)


def _write_runtime_libraries(engine_root: Path) -> None:
    engine_root.mkdir(parents=True, exist_ok=True)

    for filename in ENGINE_RUNTIME_FILENAMES:
        (engine_root / filename).write_bytes(filename.encode("utf-8"))


def _write_version_file(engine_root: Path, text: str) -> None:
    engine_root.mkdir(parents=True, exist_ok=True)
    (engine_root / ENGINE_VERSION_FILENAME).write_text(text, encoding="utf-8")


def test_a_missing_engine_root_yields_an_empty_version(tmp_path: Path) -> None:
    """引擎根不在时"不知道"是诚实取值, 不另造哨兵值."""

    assert read_engine_version(_engine_root(tmp_path)) == ""


def test_an_engine_root_without_binaries_yields_an_empty_version(tmp_path: Path) -> None:
    """空目录不能给出"空输入的摘要".

    那是一个确定的十六进制串, 会被读成"这确实是一个构建"; 真相是引擎根下什么都没有, 引擎类
    策略在这个根上根本起不来.
    """

    engine_root = _engine_root(tmp_path)
    engine_root.mkdir(parents=True)

    assert read_engine_version(engine_root) == ""


def test_the_binding_alone_yields_a_prefixed_digest(tmp_path: Path) -> None:
    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root)

    version = read_engine_version(engine_root)

    assert version.startswith(VERSION_DIGEST_PREFIX)
    assert len(version.removeprefix(VERSION_DIGEST_PREFIX)) == DIGEST_HEX_LENGTH


def test_the_digest_is_stable_across_calls(tmp_path: Path) -> None:
    """不缓存也必须稳定: 摘要对次序敏感, 次序不定就会给同一份引擎算出两个值."""

    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root)
    _write_runtime_libraries(engine_root)

    assert read_engine_version(engine_root) == read_engine_version(engine_root)


def test_changing_one_byte_of_the_binding_changes_the_digest(tmp_path: Path) -> None:
    """这条是兜底口径存在的全部意义: 引擎换了构建, 标识必须跟着变."""

    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root, b"build-one")
    _write_runtime_libraries(engine_root)

    before = read_engine_version(engine_root)

    _write_binding(engine_root, b"build-two")

    assert read_engine_version(engine_root) != before


def test_a_runtime_library_change_alone_changes_the_digest(tmp_path: Path) -> None:
    """只换了 DLL 而 `.pyd` 没动, 仍是一次引擎换版, 标识必须跟着变."""

    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root)
    _write_runtime_libraries(engine_root)

    before = read_engine_version(engine_root)

    (engine_root / ENGINE_RUNTIME_FILENAMES[0]).write_bytes(b"rebuilt")

    assert read_engine_version(engine_root) != before


def test_files_outside_the_covered_set_do_not_change_the_digest(tmp_path: Path) -> None:
    """兜底口径盖不住引擎包里别的文件——这是已知边界, 也正是版本文件存在的理由.

    钉住它不是为了"这样就好", 而是为了让边界一旦被改动 (比如日后改成哈希整个目录) 时有人
    被迫重新想一遍: 那个目录混着历次回测的产物, 每次跑完都变, 哈希它等于没有标识.
    """

    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root)
    _write_runtime_libraries(engine_root)

    before = read_engine_version(engine_root)

    (engine_root / "Unrelated.dll").write_bytes(b"not part of the digest")

    assert read_engine_version(engine_root) == before


def test_the_version_file_wins_over_the_digest(tmp_path: Path) -> None:
    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root)
    _write_runtime_libraries(engine_root)
    _write_version_file(engine_root, "build-2026.09.26\n")

    assert read_engine_version(engine_root) == "build-2026.09.26"


def test_the_version_file_is_stripped_and_read_from_its_first_non_empty_line(
    tmp_path: Path,
) -> None:
    """版本文件是给人写的, 故容得下前导空行、两侧空白与后面的说明行."""

    engine_root = _engine_root(tmp_path)
    _write_version_file(engine_root, "\n   \n  build-2026.09.26  \n备注: 换了 GridStep\n")

    assert read_engine_version(engine_root) == "build-2026.09.26"


def test_an_overlong_version_is_truncated_to_the_column_width(tmp_path: Path) -> None:
    """截断而非拒收: 它是一个标识符, 保住前缀远好过整个丢掉."""

    engine_root = _engine_root(tmp_path)
    _write_version_file(engine_root, "v" * OVERLONG_VERSION_LENGTH)

    version = read_engine_version(engine_root)

    assert version == "v" * MAXIMUM_ENGINE_VERSION_LENGTH
    assert len(version) == MAXIMUM_ENGINE_VERSION_LENGTH


def test_a_blank_version_file_falls_back_to_the_digest(tmp_path: Path) -> None:
    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root)
    _write_version_file(engine_root, "   \n\n\t\n")

    assert read_engine_version(engine_root).startswith(VERSION_DIGEST_PREFIX)


def test_an_undecodable_version_file_falls_back_to_the_digest(tmp_path: Path) -> None:
    """一份解不动的版本文件不该让整次探测失败——它只是"读不出人写的版本号"."""

    engine_root = _engine_root(tmp_path)
    _write_binding(engine_root)
    engine_root.mkdir(parents=True, exist_ok=True)
    (engine_root / ENGINE_VERSION_FILENAME).write_bytes(b"\xff\xfe\x00not-utf8")

    assert read_engine_version(engine_root).startswith(VERSION_DIGEST_PREFIX)
