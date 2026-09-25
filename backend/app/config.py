"""平台运行期配置.

集中解析环境变量, 各模块以显式参数接收配置对象, 不在业务代码里散落 os.getenv.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_ROOT = BACKEND_ROOT.parent

DEFAULT_MAX_CONCURRENT_RUNS = 1
DEFAULT_RUN_TIMEOUT_SECONDS = 1800
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 1440
DEFAULT_INITIAL_ADMIN_USERNAME = "admin"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
MAXIMUM_PORT = 65535
JWT_SECRET_KEY_BYTES = 48


def read_integer_environment(name: str, fallback: int) -> int:
    """读取整型环境变量.

    未设置或为空串时取回退值; 设置了但不构成整数则立即报错, 不静默降级成默认值.
    """

    raw_value = os.getenv(name)

    if raw_value is None:
        return fallback

    stripped_value = raw_value.strip()

    if not stripped_value:
        return fallback

    if not stripped_value.lstrip("+-").isdigit():
        raise ValueError(f"环境变量 {name} 需为整数, 实际为 {raw_value!r}")

    return int(stripped_value)


def resolve_jwt_secret_key(secret_file: Path) -> str:
    """取 JWT 签名密钥: 环境变量优先, 否则读取密钥文件, 文件不存在则生成并持久化.

    密钥既不写入源码也不入库; 首次生成后落盘, 使后端重启不会令已签发令牌集体失效.
    """

    environment_key = os.getenv("QUANT_JWT_SECRET_KEY")

    if environment_key:
        return environment_key

    if secret_file.is_file():
        persisted_key = secret_file.read_text(encoding="utf-8").strip()

        if persisted_key:
            return persisted_key

    generated_key = secrets.token_urlsafe(JWT_SECRET_KEY_BYTES)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(generated_key, encoding="utf-8")

    return generated_key


@dataclass(frozen=True)
class PlatformSettings:
    """平台配置快照, 由环境变量一次性解析得到."""

    database_url: str
    engine_root: Path
    runs_root: Path
    user_library_root: Path
    jwt_secret_key: str
    access_token_expire_minutes: int
    max_concurrent_runs: int
    run_timeout_seconds: int
    initial_admin_username: str
    initial_admin_password: str | None
    http_host: str
    http_port: int

    def __post_init__(self) -> None:
        """校验数值项取值, 避免并发闸门为 0 时永久阻塞、超时为 0 时秒杀作业."""

        if self.access_token_expire_minutes < 1:
            raise ValueError("access_token_expire_minutes 需 >= 1")

        if self.max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs 需 >= 1")

        if self.run_timeout_seconds < 1:
            raise ValueError("run_timeout_seconds 需 >= 1")

        if not 1 <= self.http_port <= MAXIMUM_PORT:
            raise ValueError(f"http_port 需在 1..{MAXIMUM_PORT} 之间")

    @classmethod
    def from_environment(cls) -> PlatformSettings:
        """按环境变量构造配置, 缺省值指向本仓的默认布局."""

        catalog_database_path = BACKEND_ROOT / "data" / "catalog.db"

        return cls(
            database_url=os.getenv(
                "QUANT_DATABASE_URL",
                f"sqlite+aiosqlite:///{catalog_database_path.as_posix()}",
            ),
            engine_root=Path(
                os.getenv(
                    "QUANT_ENGINE_ROOT",
                    PLATFORM_ROOT.parent / "QuantTrading" / "bin" / "Release",
                )
            ),
            runs_root=Path(os.getenv("QUANT_RUNS_ROOT", PLATFORM_ROOT / "runs")),
            user_library_root=Path(
                os.getenv("QUANT_USER_LIBRARY_ROOT", PLATFORM_ROOT / "users")
            ),
            jwt_secret_key=resolve_jwt_secret_key(BACKEND_ROOT / "data" / "jwt_secret.key"),
            access_token_expire_minutes=read_integer_environment(
                "QUANT_ACCESS_TOKEN_EXPIRE_MINUTES", DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
            ),
            max_concurrent_runs=read_integer_environment(
                "QUANT_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS
            ),
            run_timeout_seconds=read_integer_environment(
                "QUANT_RUN_TIMEOUT_SECONDS", DEFAULT_RUN_TIMEOUT_SECONDS
            ),
            initial_admin_username=os.getenv(
                "QUANT_INITIAL_ADMIN_USERNAME", DEFAULT_INITIAL_ADMIN_USERNAME
            ),
            initial_admin_password=os.getenv("QUANT_INITIAL_ADMIN_PASSWORD") or None,
            http_host=os.getenv("QUANT_HTTP_HOST", DEFAULT_HTTP_HOST),
            http_port=read_integer_environment("QUANT_HTTP_PORT", DEFAULT_HTTP_PORT),
        )
