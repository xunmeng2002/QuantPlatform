"""环境变量解析与配置校验.

配置是本项目唯一用异常表达失败的模块: 非法取值一律当场报错, 不静默降级成默认值——
并发闸门读成 0 会让作业永久排队, 而队列看上去一切正常.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import (
    DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
    DEFAULT_HTTP_PORT,
    MAXIMUM_PORT,
    PlatformSettings,
    read_integer_environment,
    resolve_jwt_secret_key,
)


ENVIRONMENT_NAME = "QUANT_TEST_INTEGER"
JWT_SECRET_ENVIRONMENT_NAME = "QUANT_JWT_SECRET_KEY"

ABSOLUTE_TEST_ROOT = Path(__file__).resolve().parent

VALID_SETTINGS_ARGUMENTS = {
    "database_url": "sqlite+aiosqlite:///./catalog.db",
    "engine_root": Path("engine"),
    "runs_root": Path("runs"),
    "user_library_root": Path("users"),
    "jwt_secret_key": "configuration-test-key",
    "access_token_expire_minutes": 60,
    "max_concurrent_runs": 1,
    "run_timeout_seconds": 60,
    "initial_admin_username": "admin",
    "initial_admin_password": None,
    "http_host": "127.0.0.1",
    "http_port": DEFAULT_HTTP_PORT,
    # 三个引擎侧输入必须是绝对路径 (配置自身就拒绝相对值), 而 `Path("/market-data")` 在
    # Windows 上并非绝对路径——故这里从本文件的真实位置派生, 跨平台都成立.
    "market_data_root": ABSOLUTE_TEST_ROOT / "market-data",
    "session_file_path": ABSOLUTE_TEST_ROOT / "engine" / "Sessions.json",
    "seed_database_path": ABSOLUTE_TEST_ROOT / "engine" / "BackTestInit.db",
}


def _build_settings(**overrides: object) -> PlatformSettings:
    return PlatformSettings(**{**VALID_SETTINGS_ARGUMENTS, **overrides})


def test_read_integer_environment_returns_fallback_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENVIRONMENT_NAME, raising=False)

    assert read_integer_environment(ENVIRONMENT_NAME, 7) == 7


@pytest.mark.parametrize("blank_value", ["", "   "])
def test_read_integer_environment_returns_fallback_when_blank(
    monkeypatch: pytest.MonkeyPatch, blank_value: str
) -> None:
    monkeypatch.setenv(ENVIRONMENT_NAME, blank_value)

    assert read_integer_environment(ENVIRONMENT_NAME, 7) == 7


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [("0", 0), ("12", 12), (" 12 ", 12), ("+12", 12), ("-3", -3)],
)
def test_read_integer_environment_parses_integers(
    monkeypatch: pytest.MonkeyPatch, raw_value: str, expected_value: int
) -> None:
    monkeypatch.setenv(ENVIRONMENT_NAME, raw_value)

    assert read_integer_environment(ENVIRONMENT_NAME, 7) == expected_value


@pytest.mark.parametrize("raw_value", ["abc", "1.5", "12abc", "0x10", "--3"])
def test_read_integer_environment_rejects_non_integers(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    monkeypatch.setenv(ENVIRONMENT_NAME, raw_value)

    with pytest.raises(ValueError):
        read_integer_environment(ENVIRONMENT_NAME, 7)


def test_environment_key_takes_precedence_over_the_secret_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret_file = tmp_path / "jwt_secret.key"
    secret_file.write_text("persisted-key", encoding="utf-8")
    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "environment-key")

    assert resolve_jwt_secret_key(secret_file) == "environment-key"
    assert secret_file.read_text(encoding="utf-8") == "persisted-key"


def test_persisted_secret_file_is_reused_across_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """密钥落盘的意义就在于重启后不变, 否则已签发的令牌会集体失效."""

    monkeypatch.delenv(JWT_SECRET_ENVIRONMENT_NAME, raising=False)
    secret_file = tmp_path / "nested" / "jwt_secret.key"

    first_key = resolve_jwt_secret_key(secret_file)
    second_key = resolve_jwt_secret_key(secret_file)

    assert first_key
    assert first_key == second_key
    assert secret_file.is_file()


def test_generated_secret_is_written_to_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(JWT_SECRET_ENVIRONMENT_NAME, raising=False)
    secret_file = tmp_path / "jwt_secret.key"

    generated_key = resolve_jwt_secret_key(secret_file)

    assert secret_file.read_text(encoding="utf-8").strip() == generated_key


def test_blank_persisted_secret_file_is_regenerated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(JWT_SECRET_ENVIRONMENT_NAME, raising=False)
    secret_file = tmp_path / "jwt_secret.key"
    secret_file.write_text("   ", encoding="utf-8")

    regenerated_key = resolve_jwt_secret_key(secret_file)

    assert regenerated_key.strip()
    assert secret_file.read_text(encoding="utf-8") == regenerated_key


def test_valid_settings_are_accepted() -> None:
    settings = _build_settings()

    assert settings.http_port == DEFAULT_HTTP_PORT
    assert settings.initial_admin_password is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"access_token_expire_minutes": 0},
        {"access_token_expire_minutes": -1},
        {"max_concurrent_runs": 0},
        {"max_concurrent_runs": -5},
        {"run_timeout_seconds": 0},
        {"http_port": 0},
        {"http_port": -1},
        {"http_port": MAXIMUM_PORT + 1},
    ],
)
def test_out_of_range_settings_are_rejected(overrides: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        _build_settings(**overrides)


@pytest.mark.parametrize("boundary_port", [1, DEFAULT_HTTP_PORT, MAXIMUM_PORT])
def test_port_boundaries_are_accepted(boundary_port: int) -> None:
    assert _build_settings(http_port=boundary_port).http_port == boundary_port


@pytest.mark.parametrize(
    "relative_field_name",
    ["market_data_root", "session_file_path", "seed_database_path"],
)
def test_relative_engine_input_paths_are_rejected(relative_field_name: str) -> None:
    """三个引擎侧输入留相对值必须当场报错.

    同一个相对值在引擎 (相对 job 目录解析) 与平台 (相对后端 CWD 解析) 下指向不同位置, 且
    都不报错——故障只在作业跑起来之后以"没有行情数据"的面目出现, 那时已归因不到配置上.
    """

    with pytest.raises(ValueError):
        _build_settings(**{relative_field_name: Path("relative-input")})


def test_from_environment_reads_the_three_engine_input_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三个新环境变量必须真的被读进去, 且默认值本就绝对.

    默认引擎根来自 `QUANT_ENGINE_ROOT`, 而 `session_file_path` / `seed_database_path` 由它
    拼接派生——若哪天把默认值改成相对串, 配置构造期就会抛 ValueError, 起不来的是整个后端.
    """

    for name in (
        "QUANT_MARKET_DATA_ROOT",
        "QUANT_SESSION_FILE_PATH",
        "QUANT_SEED_DATABASE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")

    defaulted_settings = PlatformSettings.from_environment()

    assert defaulted_settings.session_file_path.is_absolute()
    assert defaulted_settings.seed_database_path.is_absolute()
    assert defaulted_settings.market_data_root.is_absolute()

    override_root = ABSOLUTE_TEST_ROOT / "overridden-engine-inputs"
    monkeypatch.setenv("QUANT_MARKET_DATA_ROOT", str(override_root))
    monkeypatch.setenv("QUANT_SESSION_FILE_PATH", str(override_root / "Sessions.json"))
    monkeypatch.setenv(
        "QUANT_SEED_DATABASE_PATH", str(override_root / "BackTestInit.db")
    )

    overridden_settings = PlatformSettings.from_environment()

    assert overridden_settings.market_data_root == override_root
    assert overridden_settings.session_file_path == override_root / "Sessions.json"
    assert overridden_settings.seed_database_path == override_root / "BackTestInit.db"


def test_from_environment_applies_documented_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未设任何环境变量时也应能构造出配置, 且默认端口与令牌有效期与文档一致."""

    for name in (
        "QUANT_DATABASE_URL",
        "QUANT_ENGINE_ROOT",
        "QUANT_RUNS_ROOT",
        "QUANT_USER_LIBRARY_ROOT",
        "QUANT_ACCESS_TOKEN_EXPIRE_MINUTES",
        "QUANT_MAX_CONCURRENT_RUNS",
        "QUANT_RUN_TIMEOUT_SECONDS",
        "QUANT_INITIAL_ADMIN_USERNAME",
        "QUANT_INITIAL_ADMIN_PASSWORD",
        "QUANT_HTTP_HOST",
        "QUANT_HTTP_PORT",
        "QUANT_MARKET_DATA_ROOT",
        "QUANT_SESSION_FILE_PATH",
        "QUANT_SEED_DATABASE_PATH",
        "QUANT_MAXIMUM_OUTPUT_TAIL_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")

    settings = PlatformSettings.from_environment()

    assert settings.http_port == DEFAULT_HTTP_PORT
    assert settings.access_token_expire_minutes == DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
    assert settings.initial_admin_password is None
    assert settings.jwt_secret_key == "from-environment-key"
    assert "\\" not in settings.database_url


def test_from_environment_reads_integer_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")
    monkeypatch.setenv("QUANT_MAX_CONCURRENT_RUNS", "4")
    monkeypatch.setenv("QUANT_HTTP_PORT", "9100")

    settings = PlatformSettings.from_environment()

    assert settings.max_concurrent_runs == 4
    assert settings.http_port == 9100


def test_from_environment_rejects_a_non_integer_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")
    monkeypatch.setenv("QUANT_HTTP_PORT", "八零零零")

    with pytest.raises(ValueError):
        PlatformSettings.from_environment()


def test_blank_initial_admin_password_becomes_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空串口令须等同于未设置, 否则播种会建出一个口令为空的账号."""

    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")
    monkeypatch.setenv("QUANT_INITIAL_ADMIN_PASSWORD", "")

    assert PlatformSettings.from_environment().initial_admin_password is None
