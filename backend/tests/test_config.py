"""环境变量解析与配置校验.

配置是本项目唯一用异常表达失败的模块: 非法取值一律当场报错, 不静默降级成默认值——
并发闸门读成 0 会让作业永久排队, 而队列看上去一切正常.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from app.config import (
    DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
    DEFAULT_HTTP_PORT,
    DEFAULT_LOGIN_THROTTLE_LOCK_MINUTES,
    DEFAULT_LOGIN_THROTTLE_MAXIMUM_FAILURES,
    DEFAULT_RETAINED_RUNS_PER_USER,
    DEFAULT_RUN_RETENTION_ENABLED,
    MAXIMUM_PORT,
    PlatformSettings,
    load_environment_file,
    parse_environment_file,
    read_boolean_environment,
    read_integer_environment,
    resolve_jwt_secret_key,
)


ENVIRONMENT_NAME = "QUANT_TEST_INTEGER"
BOOLEAN_ENVIRONMENT_NAME = "QUANT_TEST_BOOLEAN"
JWT_SECRET_ENVIRONMENT_NAME = "QUANT_JWT_SECRET_KEY"
ENVIRONMENT_FILE_KEY = "QUANT_TEST_FROM_FILE"
ENVIRONMENT_FILE_SECOND_KEY = "QUANT_TEST_FROM_FILE_SECOND"
NON_PREFIXED_KEY = "PLATFORM_TEST_WITHOUT_PREFIX"

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
    # 两个引擎侧输入必须是绝对路径 (配置自身就拒绝相对值), 而 `Path("/market-data")` 在
    # Windows 上并非绝对路径——故这里从本文件的真实位置派生, 跨平台都成立.
    "market_data_root": ABSOLUTE_TEST_ROOT / "market-data",
    "session_file_path": ABSOLUTE_TEST_ROOT / "engine" / "Sessions.json",
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


@pytest.mark.parametrize("blank_value", ["", "   "])
def test_read_boolean_environment_returns_fallback_when_blank(
    monkeypatch: pytest.MonkeyPatch, blank_value: str
) -> None:
    monkeypatch.setenv(BOOLEAN_ENVIRONMENT_NAME, blank_value)

    assert read_boolean_environment(BOOLEAN_ENVIRONMENT_NAME, True) is True


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        ("true", True),
        ("TRUE", True),
        (" True ", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_read_boolean_environment_parses_booleans(
    monkeypatch: pytest.MonkeyPatch, raw_value: str, expected_value: bool
) -> None:
    monkeypatch.setenv(BOOLEAN_ENVIRONMENT_NAME, raw_value)

    parsed_value = read_boolean_environment(BOOLEAN_ENVIRONMENT_NAME, not expected_value)

    assert parsed_value is expected_value


@pytest.mark.parametrize("raw_value", ["enable", "enabled", "2", "t", "是", "n/a"])
def test_read_boolean_environment_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    """非法布尔取值必须当场报错, 不能静默当成 false.

    保留策略那一项静默翻转的后果是**不可逆**的: 写 `=enable` 的人以为开着, 后端安静地什么都不
    删; 反过来把 `=disable` 当成 false 也不是它想表达的意思. 两种方向都不该猜.
    """

    monkeypatch.setenv(BOOLEAN_ENVIRONMENT_NAME, raw_value)

    with pytest.raises(ValueError):
        read_boolean_environment(BOOLEAN_ENVIRONMENT_NAME, False)


def test_unset_boolean_environment_returns_its_own_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回退值原样返回, 不做 True/False 归一: 回退值是调用方给的默认策略."""

    monkeypatch.delenv(BOOLEAN_ENVIRONMENT_NAME, raising=False)

    assert read_boolean_environment(BOOLEAN_ENVIRONMENT_NAME, True) is True
    assert read_boolean_environment(BOOLEAN_ENVIRONMENT_NAME, False) is False


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
    # 保留策略默认关, 且默认值本身合法: 不显式配置时升级行为与升级前一致.
    assert settings.run_retention_enabled is DEFAULT_RUN_RETENTION_ENABLED
    assert settings.retained_runs_per_user == DEFAULT_RETAINED_RUNS_PER_USER
    # 节流默认开着 (上云后是安全底线), 白名单默认为空 (谁都不信, 只用对端地址).
    assert settings.login_throttle_maximum_failures == DEFAULT_LOGIN_THROTTLE_MAXIMUM_FAILURES
    assert settings.login_throttle_lock_minutes == DEFAULT_LOGIN_THROTTLE_LOCK_MINUTES
    assert settings.trusted_proxy_addresses == ()


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
        # 保留 0 轮 = 一个不留, 而算法读到的正是这个数; 想不留就关掉保留策略.
        {"retained_runs_per_user": 0},
        {"retained_runs_per_user": -3},
        # 锁定 0 分钟等于没锁, 而现象是"配了节流却没生效"; 想关掉就把失败次数调大.
        {"login_throttle_maximum_failures": 0},
        {"login_throttle_maximum_failures": -2},
        {"login_throttle_lock_minutes": 0},
        {"login_throttle_lock_minutes": -1},
        # 白名单里的错别字必须当场报错: 静默跳过会让它形同不存在, 症状是"全站共用一个来源桶".
        {"trusted_proxy_addresses": ("not-a-network",)},
        {"trusted_proxy_addresses": ("127.0.0.1", "300.1.1.1")},
    ],
)
def test_out_of_range_settings_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _build_settings(**overrides)


@pytest.mark.parametrize("boundary_port", [1, DEFAULT_HTTP_PORT, MAXIMUM_PORT])
def test_port_boundaries_are_accepted(boundary_port: int) -> None:
    assert _build_settings(http_port=boundary_port).http_port == boundary_port


@pytest.mark.parametrize(
    "relative_field_name",
    ["market_data_root", "session_file_path"],
)
def test_relative_engine_input_paths_are_rejected(relative_field_name: str) -> None:
    """引擎侧输入留相对值必须当场报错.

    同一个相对值在引擎 (相对 job 目录解析) 与平台 (相对后端 CWD 解析) 下指向不同位置, 且
    都不报错——故障只在作业跑起来之后以"没有行情数据"的面目出现, 那时已归因不到配置上.
    """

    with pytest.raises(ValueError):
        _build_settings(**{relative_field_name: Path("relative-input")})


def test_from_environment_reads_the_engine_input_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个环境变量必须真的被读进去, 且默认值本就绝对.

    默认引擎根来自 `QUANT_ENGINE_ROOT`, 而 `session_file_path` 由它拼接派生——若哪天把默认值
    改成相对串, 配置构造期就会抛 ValueError, 起不来的是整个后端.
    """

    for name in (
        "QUANT_MARKET_DATA_ROOT",
        "QUANT_SESSION_FILE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")

    defaulted_settings = PlatformSettings.from_environment()

    assert defaulted_settings.session_file_path.is_absolute()
    assert defaulted_settings.market_data_root.is_absolute()

    override_root = ABSOLUTE_TEST_ROOT / "overridden-engine-inputs"
    monkeypatch.setenv("QUANT_MARKET_DATA_ROOT", str(override_root))
    monkeypatch.setenv("QUANT_SESSION_FILE_PATH", str(override_root / "Sessions.json"))

    overridden_settings = PlatformSettings.from_environment()

    assert overridden_settings.market_data_root == override_root
    assert overridden_settings.session_file_path == override_root / "Sessions.json"


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
        "QUANT_MAXIMUM_OUTPUT_TAIL_BYTES",
        "QUANT_RUN_RETENTION_ENABLED",
        "QUANT_RETAINED_RUNS_PER_USER",
        "QUANT_LOGIN_THROTTLE_MAXIMUM_FAILURES",
        "QUANT_LOGIN_THROTTLE_LOCK_MINUTES",
        "QUANT_TRUSTED_PROXY_ADDRESSES",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")

    settings = PlatformSettings.from_environment()

    assert settings.http_port == DEFAULT_HTTP_PORT
    assert settings.access_token_expire_minutes == DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
    assert settings.initial_admin_password is None
    assert settings.jwt_secret_key == "from-environment-key"
    assert "\\" not in settings.database_url
    assert settings.run_retention_enabled is False
    assert settings.retained_runs_per_user == DEFAULT_RETAINED_RUNS_PER_USER
    assert settings.login_throttle_maximum_failures == DEFAULT_LOGIN_THROTTLE_MAXIMUM_FAILURES
    assert settings.login_throttle_lock_minutes == DEFAULT_LOGIN_THROTTLE_LOCK_MINUTES
    assert settings.trusted_proxy_addresses == ()


def test_from_environment_reads_the_login_throttle_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三个节流项都要真的从环境里读出来.

    白名单那一项是逗号分隔的地址表, 空串与只有空白/逗号的取值都要收成空表——把空串切成
    `("",)` 会让 `ip_network("")` 在构造配置时直接抛, 后端起不来.
    """

    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")
    monkeypatch.setenv("QUANT_LOGIN_THROTTLE_MAXIMUM_FAILURES", "3")
    monkeypatch.setenv("QUANT_LOGIN_THROTTLE_LOCK_MINUTES", "2")
    monkeypatch.setenv(
        "QUANT_TRUSTED_PROXY_ADDRESSES", " 127.0.0.1 , 10.0.0.0/8 , "
    )

    settings = PlatformSettings.from_environment()

    assert settings.login_throttle_maximum_failures == 3
    assert settings.login_throttle_lock_minutes == 2
    assert settings.trusted_proxy_addresses == ("127.0.0.1", "10.0.0.0/8")

    monkeypatch.setenv("QUANT_TRUSTED_PROXY_ADDRESSES", "   ")

    assert PlatformSettings.from_environment().trusted_proxy_addresses == ()


def test_from_environment_reads_integer_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")
    monkeypatch.setenv("QUANT_MAX_CONCURRENT_RUNS", "4")
    monkeypatch.setenv("QUANT_HTTP_PORT", "9100")

    settings = PlatformSettings.from_environment()

    assert settings.max_concurrent_runs == 4
    assert settings.http_port == 9100


def test_from_environment_reads_the_retention_switches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个保留项要真的从环境里读出来: 默认关的那一项, 只能靠环境变量打开."""

    monkeypatch.setenv(JWT_SECRET_ENVIRONMENT_NAME, "from-environment-key")
    monkeypatch.setenv("QUANT_RUN_RETENTION_ENABLED", "true")
    monkeypatch.setenv("QUANT_RETAINED_RUNS_PER_USER", "5")

    settings = PlatformSettings.from_environment()

    assert settings.run_retention_enabled is True
    assert settings.retained_runs_per_user == 5


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


def _write_environment_file(tmp_path: Path, text: str) -> Path:
    environment_file = tmp_path / "test.env"
    environment_file.write_text(text, encoding="utf-8")

    return environment_file


def _clear_environment_file_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """先记录"这些键本来没设", 用例跑完由 monkeypatch 复原.

    `load_environment_file` 直接写 `os.environ`, 不经 monkeypatch, 故只有"先删一次"才能让
    复位生效——不先删的话, 复原的是用例自己写进去的那个值, 键会漏给同 worker 的其它用例.
    """

    for name in (
        ENVIRONMENT_FILE_KEY,
        ENVIRONMENT_FILE_SECOND_KEY,
        NON_PREFIXED_KEY,
    ):
        monkeypatch.delenv(name, raising=False)


def test_environment_file_parses_assignments_and_skips_comments_and_blanks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注释与空行要跳过, 取值两侧空白与成对引号要剥掉."""

    _clear_environment_file_keys(monkeypatch)

    environment_file = _write_environment_file(
        tmp_path,
        "\n".join(
            [
                "# 整行注释",
                "",
                "   ",
                f'{ENVIRONMENT_FILE_KEY}="quoted value"',
                f"{ENVIRONMENT_FILE_SECOND_KEY} =  spaced  ",
                f"{NON_PREFIXED_KEY}=1",
            ]
        ),
    )

    assert load_environment_file(environment_file) == 3
    assert os.environ[ENVIRONMENT_FILE_KEY] == "quoted value"
    assert os.environ[ENVIRONMENT_FILE_SECOND_KEY] == "spaced"
    assert os.environ[NON_PREFIXED_KEY] == "1"


def test_environment_file_keeps_a_hash_inside_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取值里的 `#` 不是注释起点: 把它当注释会静默截断口令."""

    _clear_environment_file_keys(monkeypatch)
    monkeypatch.setenv(ENVIRONMENT_FILE_KEY, "占位")

    environment_file = _write_environment_file(
        tmp_path, f"{ENVIRONMENT_FILE_KEY}=pa#ssword\n"
    )

    # 该键已被真实环境变量占用, 故文件里的取值不生效——这条断言同时也是"不覆盖"的证据.
    load_environment_file(environment_file)

    assert os.environ[ENVIRONMENT_FILE_KEY] == "占位"
    assert parse_environment_file(environment_file.read_text(encoding="utf-8")) == {
        ENVIRONMENT_FILE_KEY: "pa#ssword"
    }


def test_environment_file_does_not_override_real_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命令行上临时给的那一个必须赢过文件里写的那个."""

    _clear_environment_file_keys(monkeypatch)
    monkeypatch.setenv(ENVIRONMENT_FILE_KEY, "命令行上的口令")

    environment_file = _write_environment_file(
        tmp_path, f"{ENVIRONMENT_FILE_KEY}=文件里的口令\n"
    )

    assert load_environment_file(environment_file) == 0
    assert os.environ[ENVIRONMENT_FILE_KEY] == "命令行上的口令"


def test_missing_environment_file_is_a_no_op(tmp_path: Path) -> None:
    """没配这个文件是正常情况, 不是错误."""

    assert load_environment_file(tmp_path / "not-there.env") == 0


@pytest.mark.parametrize(
    "malformed_line",
    ["QUANT_TEST_BARE_KEY", "export QUANT_TEST_EXPORTED=1", "=无键名"],
)
def test_malformed_environment_file_lines_are_rejected(
    tmp_path: Path, malformed_line: str
) -> None:
    """非法行当场抛错而不是跳过: 静默降级会让人以为配置已经生效.

    `export KEY=VALUE` 那条是有意拒绝的——它看着像 shell, 而键名会变成 `export KEY`,
    程序永远不会去读; 与其让写文件的人对着"口令没生效"查很久, 不如报出来.
    """

    environment_file = _write_environment_file(
        tmp_path, f"{malformed_line}\n"
    )

    with pytest.raises(ValueError):
        parse_environment_file(environment_file.read_text(encoding="utf-8"))


def test_non_prefixed_environment_file_keys_are_warned_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """没有 `QUANT_` 前缀的键会被记一条 warning——多半是名字打错了."""

    _clear_environment_file_keys(monkeypatch)

    environment_file = _write_environment_file(
        tmp_path, f"{NON_PREFIXED_KEY}=1\n{ENVIRONMENT_FILE_KEY}=2\n"
    )

    with caplog.at_level(logging.WARNING):
        load_environment_file(environment_file)

    assert NON_PREFIXED_KEY in caplog.text
    assert ENVIRONMENT_FILE_KEY not in caplog.text
