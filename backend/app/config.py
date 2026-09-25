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

# 引擎自己认的文件名, 由引擎写死故为常量: 会话表要复制进每个 job 目录, 种子库是只读输入.
SESSION_FILENAME = "Sessions.json"
SEED_DATABASE_FILENAME = "BackTestInit.db"

# 本机布局的默认值, 与 engine_root 的默认值同一性质: 换机器必须由环境变量覆盖.
DEFAULT_MARKET_DATA_ROOT = Path("D:/MdBaoStock")
DEFAULT_MAXIMUM_OUTPUT_TAIL_BYTES = 8 * 1024


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
    market_data_root: Path
    session_file_path: Path
    seed_database_path: Path
    maximum_output_tail_bytes: int = DEFAULT_MAXIMUM_OUTPUT_TAIL_BYTES

    def __post_init__(self) -> None:
        """校验数值项与路径项, 避免并发闸门为 0 时永久阻塞、超时为 0 时秒杀作业."""

        if self.access_token_expire_minutes < 1:
            raise ValueError("access_token_expire_minutes 需 >= 1")

        if self.max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs 需 >= 1")

        if self.run_timeout_seconds < 1:
            raise ValueError("run_timeout_seconds 需 >= 1")

        if self.maximum_output_tail_bytes < 1:
            raise ValueError("maximum_output_tail_bytes 需 >= 1")

        if not 1 <= self.http_port <= MAXIMUM_PORT:
            raise ValueError(f"http_port 需在 1..{MAXIMUM_PORT} 之间")

        # 三个引擎侧路径必须绝对: 引擎相对 **job 目录** 解析读路径, 而平台的复制动作相对
        # **后端进程的 CWD** 解析. 同一个相对值在这两处指向不同的地方, 且都不报错——配置里
        # 留一个相对值, 故障只在作业跑起来之后才以"没有行情数据"的面目出现.
        for field_name in ("market_data_root", "session_file_path", "seed_database_path"):
            if not getattr(self, field_name).is_absolute():
                raise ValueError(f"{field_name} 需为绝对路径")

    @classmethod
    def from_environment(cls) -> PlatformSettings:
        """按环境变量构造配置, 缺省值指向本仓的默认布局."""

        catalog_database_path = BACKEND_ROOT / "data" / "catalog.db"
        engine_root = Path(
            os.getenv(
                "QUANT_ENGINE_ROOT",
                PLATFORM_ROOT.parent / "QuantTrading" / "bin" / "Release",
            )
        )

        return cls(
            database_url=os.getenv(
                "QUANT_DATABASE_URL",
                f"sqlite+aiosqlite:///{catalog_database_path.as_posix()}",
            ),
            engine_root=engine_root,
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
            market_data_root=Path(
                os.getenv("QUANT_MARKET_DATA_ROOT", DEFAULT_MARKET_DATA_ROOT)
            ),
            session_file_path=Path(
                os.getenv("QUANT_SESSION_FILE_PATH", engine_root / SESSION_FILENAME)
            ),
            seed_database_path=Path(
                os.getenv("QUANT_SEED_DATABASE_PATH", engine_root / SEED_DATABASE_FILENAME)
            ),
            maximum_output_tail_bytes=read_integer_environment(
                "QUANT_MAXIMUM_OUTPUT_TAIL_BYTES", DEFAULT_MAXIMUM_OUTPUT_TAIL_BYTES
            ),
        )
