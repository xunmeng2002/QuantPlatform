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
