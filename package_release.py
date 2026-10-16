#!/usr/bin/env python3
"""打平台发布包: 把后端源码、前端构建产物与部署文档收进一个目录, 并按需封装成一个 zip.

与引擎侧的两个打包脚本同骨架 (显式白名单、缺项点名并非零退出、单层目录、zip 只装那一层、
自检全过才产出), 差异全部来自产物性质:

- 平台是**纯 Python 源码 + 静态文件**, 不含任何平台二进制, 故一份包两个平台通吃
  (Windows 的部署根与 WSL 的部署根各解压同一个包, 只是 `.env` 取值与启动脚本不同).
- 依赖**不进包**, 包内给的是**冻结过**的 `requirements.deploy.txt`: 打包时现建一个干净 venv
  把两份依赖清单装一遍, 记下那一次解析出的版本. 复查一轮历史结果要复现的正是"当时那一套
  依赖", 光有 `requirements.txt` 里的 `>=` 下限复现不出来.
- 冻结与自检都必须带上**行情组件**那四个依赖 (baostock / numpy / pandas / pyarrow): 平台跑
  组件的命令行时传的是 `sys.executable` (`services/quote_hub.py`), 也就是说用的是**后端的
  解释器**. 漏了它们, 打包与部署都不会报错, 只会在"下载行情"那一步才炸.

用法: `py -3.14 package_release.py [选项]`

    --output <目录>          发布目录 (默认 <仓根>/dist/QuantPlatform)
    --zip                    自检通过后, 另打一个 zip (落在发布目录的**同级**)
    --force                  输出目录已存在且非空时先清空它 (只清本脚本自己写下的条目)
    --skip-frontend-build    不跑前端构建, 复用现有的 frontend/dist
    --quote-hub-root <目录>  取它的依赖清单参与冻结 (默认 <仓根>/../QuoteHub)
    -h, --help               打印本帮助

`--zip` 的包内是**一个**顶层目录, 名字固定为 `QuantPlatform` 且**不带版本号**: 部署根里还要
并列放引擎包与行情组件, 而平台自己的 `PLATFORM_ROOT` 是后端源码位置推出来的, 那一层换了名字
全套默认路径就都指错了. 版本号落在 zip 文件名与包内 `RELEASE-INFO.txt` 上.
"""

import argparse
import ast
import datetime
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
PACKAGE_ROOT_NAME = "QuantPlatform"
DIST_DIRECTORY_NAME = "dist"
BACKEND_DIRECTORY_NAME = "backend"
DOCUMENTS_DIRECTORY_NAME = "docs"
FRONTEND_DIRECTORY_NAME = "frontend"

BACKEND_SOURCE_DIRECTORY_NAME = "app"
REFERENCE_SEED_DIRECTORY_NAME = "reference_seed"
BUILTIN_STRATEGY_DIRECTORY_NAME = "builtin_strategies"
BUILTIN_STRATEGY_TEMPLATE_FILENAME = "grid_strategy.json"
BACKEND_ROOT_FILENAMES = ("requirements.txt", "pyproject.toml", ".env.example")
FRONTEND_BUILD_DIRECTORY_NAME = "dist"
FRONTEND_ASSET_DIRECTORY_NAME = "assets"
DOCUMENT_FILENAMES = ("deployment.md", "acceptance-checklist.md", "job-workspace.md")
START_SCRIPT_FILENAMES = ("start-platform.ps1", "start-platform.sh")

APPLICATION_VERSION_FILENAME = "main.py"
APPLICATION_VERSION_NAME = "APPLICATION_VERSION"
REQUIREMENTS_FILENAME = "requirements.txt"
DEPENDENCY_LOCK_FILENAME = "requirements.deploy.txt"
RELEASE_INFORMATION_FILENAME = "RELEASE-INFO.txt"
FRONTEND_INDEX_FILENAME = "index.html"

# 冻结时剔掉的东西: venv 里那几件工具与本包无关, 钉进锁文件只会让"这份锁对应哪次构建"更难读.
EXCLUDED_LOCK_PREFIXES = ("pip==", "setuptools==", "wheel==")

# `index.html` 里以 `/` 开头的引用 (Vite 给资源的路径), 逐条回包内核对.
REFERENCED_ASSET_PATTERN = re.compile(r'(?:src|href)="(/[^"]+)"')

# 条目权限位一律自己写, 不沿用 `ZipInfo.from_file` 的结果: 在 Windows 上它读的是 MS-DOS
# 属性 (普通文件就是 0x20), POSIX 权限位那 16 位是空的, 解压到 Linux 上就是 "Permission
# denied" —— 而那个现象看起来像脚本本身写坏了.
REGULAR_FILE_ATTRIBUTES = (stat.S_IFREG | 0o644) << 16
EXECUTABLE_FILE_ATTRIBUTES = (stat.S_IFREG | 0o755) << 16

# 条目的"宿主系统". 权限位只在这个字段为 3 (Unix) 时才被 unzip 采纳; `ZipInfo` 在 Windows 上
# 把它写成 0 (MS-DOS), 上面那 16 位会被整段忽略. 故与打包机无关地一律声明成 Unix.
UNIX_CREATE_SYSTEM = 3

FORBIDDEN_TOP_LEVEL_NAMES = frozenset({"runs", "users", "market-data", "node_modules"})
FORBIDDEN_RELATIVE_PREFIXES = ("backend/data",)
FORBIDDEN_DIRECTORY_NAMES = frozenset({"__pycache__"})
FORBIDDEN_SUFFIXES = (".pyc", ".pyo")
ENVIRONMENT_FILE_NAME = ".env"

# 本脚本在发布目录顶层写下的全部条目. `--force` 清空前按它逐项核对, 见 `resolve_output_directory`.
OWNED_OUTPUT_ENTRY_NAMES = frozenset(
    {BACKEND_DIRECTORY_NAME, FRONTEND_DIRECTORY_NAME, DOCUMENTS_DIRECTORY_NAME}
    | set(START_SCRIPT_FILENAMES)
    | {DEPENDENCY_LOCK_FILENAME, RELEASE_INFORMATION_FILENAME}
)

GIT_COMMIT_COMMAND = ("git", "rev-parse", "--short", "HEAD")
GIT_STATUS_COMMAND = ("git", "status", "--porcelain")
DIRTY_TREE_NOTE = "工作区有未提交改动"
UNKNOWN_COMMIT_NOTE = "取不到"


def read_application_version() -> str:
    """取平台版本号.

    从 `backend/app/main.py` 的 `APPLICATION_VERSION` **解析**出来, 不 import: 打包脚本不该
    反过来要求先装好本包的依赖, 更不该因为导入一份源码而产生任何副作用. 也不另设一个 VERSION
    文件 —— 同一件事有两个来源, 迟早会出现"包名说 0.1.0、接口自报 0.2.0".
    """

    version_source = SCRIPT_DIRECTORY / BACKEND_DIRECTORY_NAME / BACKEND_SOURCE_DIRECTORY_NAME / APPLICATION_VERSION_FILENAME
    module = ast.parse(version_source.read_text(encoding="utf-8"))

    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue

        assigned_names = [target.id for target in statement.targets if isinstance(target, ast.Name)]

        if APPLICATION_VERSION_NAME not in assigned_names:
            continue

        version = ast.literal_eval(statement.value)

        if not isinstance(version, str) or not version:
            raise ValueError(f"{version_source} 的 {APPLICATION_VERSION_NAME} 不是非空字符串")

        return version

    raise ValueError(f"{version_source} 里没有 {APPLICATION_VERSION_NAME}")


def run(command: list[str], working_directory: Path | None = None) -> None:
    """跑一条外部命令; 失败即抛出 (由 `main` 统一转成非零退出)."""

    print(f"  $ {' '.join(command)}")
    subprocess.run(command, cwd=working_directory, check=True)


def read_command_output(command: tuple[str, ...]) -> str | None:
    """取一条 git 命令的标准输出; 取不到就是取不到, 不假装知道."""

    try:
        completed = subprocess.run(command, cwd=SCRIPT_DIRECTORY, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None

    return completed.stdout.strip()


def build_frontend() -> None:
    """跑前端构建.

    先看 `node_modules` 在不在再决定要不要 `npm ci`: 后者会整目录删掉重装, 每次打包都付那一分钟
    不值当; 而 `vue-tsc -b` 没有它就跑不起来.
    """

    npm = shutil.which("npm")

    if npm is None:
        raise ValueError("找不到 npm, 本机建不出前端产物 (可加 --skip-frontend-build 复用现有产物)")

    frontend_directory = SCRIPT_DIRECTORY / FRONTEND_DIRECTORY_NAME

    if not (frontend_directory / "node_modules").is_dir():
        run([npm, "ci"], frontend_directory)

    run([npm, "run", "build"], frontend_directory)


def verify_existing_frontend_build() -> None:
    """复用既有产物时, 至少要有一份产物可复用."""

    index_path = SCRIPT_DIRECTORY / FRONTEND_DIRECTORY_NAME / FRONTEND_BUILD_DIRECTORY_NAME / FRONTEND_INDEX_FILENAME

    if not index_path.is_file():
        raise ValueError(f"没有可复用的前端产物: {index_path} 不存在 (去掉 --skip-frontend-build 走构建)")


def venv_python(venv_directory: Path) -> Path:
    """取 venv 里的解释器路径: Windows 与 POSIX 各一套布局."""

    if sys.platform == "win32":
        return venv_directory / "Scripts" / "python.exe"

    return venv_directory / "bin" / "python"


def create_dependency_lock(quote_hub_root: Path, workspace: Path) -> tuple[Path, Path]:
    """现建一个干净 venv 装一遍两份依赖清单, 冻结出锁文件.

    不在**本机那份**环境上冻结: 那里面还躺着 pytest、编辑器所需的一堆包, 冻进来的东西既与
    本包无关, 又会让"这份锁对应哪次构建"变得看不出来. 干净 venv 还顺带成了后面那道自检要用的
    环境, 于是"包里的依赖清单够不够"是被真装过一遍的东西回答的.

    返回锁文件与那个 venv 的解释器.
    """

    quote_hub_requirements = quote_hub_root / REQUIREMENTS_FILENAME

    if not quote_hub_requirements.is_file():
        raise ValueError(f"行情组件的依赖清单不在位: {quote_hub_requirements} (用 --quote-hub-root 指定它的仓根)")

    venv_directory = workspace / "venv"
    run([sys.executable, "-m", "venv", str(venv_directory)])

    interpreter = venv_python(venv_directory)
    install_command = [str(interpreter), "-m", "pip", "install", "--quiet"]

    for requirements_path in (SCRIPT_DIRECTORY / BACKEND_DIRECTORY_NAME / REQUIREMENTS_FILENAME, quote_hub_requirements):
        install_command += ["-r", str(requirements_path)]

    run(install_command)

    frozen_output = subprocess.run(
        [str(interpreter), "-m", "pip", "freeze"], capture_output=True, text=True, check=True
    ).stdout

    locked_packages = sorted(
        line
        for line in frozen_output.splitlines()
        if line.strip() and not line.lower().startswith(EXCLUDED_LOCK_PREFIXES)
    )

    if not locked_packages:
        raise ValueError("冻结出来的依赖清单是空的: 那一次安装没有装上任何东西")

    lock_path = workspace / DEPENDENCY_LOCK_FILENAME
    lock_path.write_text("\n".join(locked_packages) + "\n", encoding="utf-8", newline="\n")

    return lock_path, interpreter


def copy_backend(release_root: Path) -> None:
    backend_destination = release_root / BACKEND_DIRECTORY_NAME
    backend_source = SCRIPT_DIRECTORY / BACKEND_DIRECTORY_NAME

    shutil.copytree(
        backend_source / BACKEND_SOURCE_DIRECTORY_NAME,
        backend_destination / BACKEND_SOURCE_DIRECTORY_NAME,
        ignore=shutil.ignore_patterns(*FORBIDDEN_DIRECTORY_NAMES),
    )
    shutil.copytree(backend_source / REFERENCE_SEED_DIRECTORY_NAME, backend_destination / REFERENCE_SEED_DIRECTORY_NAME)
    shutil.copytree(backend_source / BUILTIN_STRATEGY_DIRECTORY_NAME, backend_destination / BUILTIN_STRATEGY_DIRECTORY_NAME)

    for filename in BACKEND_ROOT_FILENAMES:
        shutil.copy2(backend_source / filename, backend_destination / filename)


def copy_frontend(release_root: Path) -> None:
    """整份搬走 `frontend/dist`.

    `frontend/` 下别的东西 (源码、`node_modules`、配置) 一概不进包: 部署机上不跑 Node, 前端
    在这里是**产物**而不是源码.
    """

    shutil.copytree(
        SCRIPT_DIRECTORY / FRONTEND_DIRECTORY_NAME / FRONTEND_BUILD_DIRECTORY_NAME,
        release_root / FRONTEND_DIRECTORY_NAME / FRONTEND_BUILD_DIRECTORY_NAME,
    )


def copy_documents(release_root: Path) -> None:
    """只带部署与验收要用的三份.

    `platform-plan.md` 是设计记录 (上百 KB), 部署机上没有人按它操作; `PROGRESS*.md` 是台账,
    更不是交付物.
    """

    documents_destination = release_root / DOCUMENTS_DIRECTORY_NAME
    documents_destination.mkdir()

    for filename in DOCUMENT_FILENAMES:
        shutil.copy2(SCRIPT_DIRECTORY / DOCUMENTS_DIRECTORY_NAME / filename, documents_destination / filename)


def copy_start_scripts(release_root: Path) -> None:
    for filename in START_SCRIPT_FILENAMES:
        shutil.copy2(SCRIPT_DIRECTORY / filename, release_root / filename)


def build_release_information(version: str) -> str:
    """包内那一页`这是什么`."""

    commit = read_command_output(GIT_COMMIT_COMMAND) or UNKNOWN_COMMIT_NOTE
    status = read_command_output(GIT_STATUS_COMMAND)
    commit_note = f"{commit} ({DIRTY_TREE_NOTE})" if status else commit
    packed_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    return "\n".join(
        (
            f"平台版本: {version}",
            f"打包时间: {packed_at}",
            f"源码提交: {commit_note}",
            "Python 基线: >= 3.14 (见 backend/pyproject.toml)",
            f"依赖锁: {DEPENDENCY_LOCK_FILENAME} (打包时在干净 venv 里现装一遍的解析结果)",
            "本包不含: 引擎与行情组件。部署时用 QUANT_ENGINE_ROOT / QUANT_QUOTE_HUB_ROOT 指定, 见 docs/deployment.md",
            "",
        )
    )


def assemble_release(release_root: Path, lock_path: Path, release_information: str) -> None:
    (release_root / BACKEND_DIRECTORY_NAME).mkdir(parents=True)

    copy_backend(release_root)
    copy_frontend(release_root)
    copy_documents(release_root)
    copy_start_scripts(release_root)
    shutil.copy2(lock_path, release_root / DEPENDENCY_LOCK_FILENAME)
    (release_root / RELEASE_INFORMATION_FILENAME).write_text(release_information, encoding="utf-8", newline="\n")


def verify_installed_dependencies(interpreter: Path, release_backend: Path) -> None:
    """用那份干净的 venv 真的 import 一遍**包里的**副本.

    一条命令同时回答三件事: 依赖清单够不够 (`app.main` 牵出全部运行时依赖)、源码有没有语法错、
    以及行情组件那四个依赖在不在这个环境里. 加 `-B`: 不许把 `__pycache__` 写进刚打好的包里.
    """

    probe = "import app.main; import baostock, numpy, pandas, pyarrow"

    run([str(interpreter), "-B", "-c", probe], working_directory=release_backend)


def verify_frontend_references(release_root: Path) -> None:
    """`index.html` 里引用的每个以 `/` 开头的路径都要在包内存在.

    抓的是"产物的哈希名与源码对不上"这一档: 前端改过而没重跑构建时, 页面引用的还是上一版那串
    名字, 包本身一切正常, 打开却是白屏 —— 这类失败在部署机上排查成本最高.
    """

    dist_root = release_root / FRONTEND_DIRECTORY_NAME / FRONTEND_BUILD_DIRECTORY_NAME
    index_path = dist_root / FRONTEND_INDEX_FILENAME
    referenced_names = REFERENCED_ASSET_PATTERN.findall(index_path.read_text(encoding="utf-8"))

    missing_names = [
        name for name in referenced_names if not (dist_root / name.removeprefix("/")).is_file()
    ]

    if missing_names:
        raise ValueError(f"{FRONTEND_INDEX_FILENAME} 引用的资源不在包内 (产物是旧的吗?): {missing_names}")

    if not (dist_root / FRONTEND_ASSET_DIRECTORY_NAME).is_dir():
        raise ValueError(f"前端产物里没有 {FRONTEND_ASSET_DIRECTORY_NAME}/ 目录: {dist_root}")


def forbidden_entry_reason(relative_path: str) -> str | None:
    """这一条为什么不该出现在包里; 该出现就回 `None`."""

    path_parts = relative_path.split("/")

    if path_parts[0] in FORBIDDEN_TOP_LEVEL_NAMES:
        return "运行数据或依赖目录"

    if any(relative_path.startswith(f"{prefix}/") for prefix in FORBIDDEN_RELATIVE_PREFIXES):
        return "平台库与运行态"

    if any(part in FORBIDDEN_DIRECTORY_NAMES for part in path_parts):
        return "缓存目录"

    if relative_path.endswith(FORBIDDEN_SUFFIXES):
        return "缓存文件"

    if path_parts[-1] == ENVIRONMENT_FILE_NAME:
        return "本机环境文件 (口令在其中)"

    return None


def verify_release_contents(release_root: Path) -> None:
    """逐条点名白名单, 并扫一遍不该进来的东西."""

    required_relative_paths = [
        f"{BACKEND_DIRECTORY_NAME}/{BACKEND_ROOT_FILENAMES[0]}",
        f"{BACKEND_DIRECTORY_NAME}/{BACKEND_SOURCE_DIRECTORY_NAME}/{APPLICATION_VERSION_FILENAME}",
        f"{BACKEND_DIRECTORY_NAME}/{BACKEND_SOURCE_DIRECTORY_NAME}/config.py",
        f"{BACKEND_DIRECTORY_NAME}/{REFERENCE_SEED_DIRECTORY_NAME}/BaseCommissions.csv",
        f"{BACKEND_DIRECTORY_NAME}/{REFERENCE_SEED_DIRECTORY_NAME}/CommissionGroups.csv",
        f"{BACKEND_DIRECTORY_NAME}/{REFERENCE_SEED_DIRECTORY_NAME}/Products.csv",
        f"{BACKEND_DIRECTORY_NAME}/{BUILTIN_STRATEGY_DIRECTORY_NAME}/{BUILTIN_STRATEGY_TEMPLATE_FILENAME}",
        f"{FRONTEND_DIRECTORY_NAME}/{FRONTEND_BUILD_DIRECTORY_NAME}/{FRONTEND_INDEX_FILENAME}",
        DEPENDENCY_LOCK_FILENAME,
        RELEASE_INFORMATION_FILENAME,
        *START_SCRIPT_FILENAMES,
        *(f"{DOCUMENTS_DIRECTORY_NAME}/{filename}" for filename in DOCUMENT_FILENAMES),
    ]

    absent_paths = [name for name in required_relative_paths if not (release_root / name).is_file()]

    if absent_paths:
        raise ValueError(f"白名单里这些文件不在包里: {absent_paths}")

    rejected_entries = [
        (relative_path, reason)
        for relative_path in (path.relative_to(release_root).as_posix() for path in release_root.rglob("*"))
        if (reason := forbidden_entry_reason(relative_path)) is not None
    ]

    if rejected_entries:
        raise ValueError(f"包里混进了不该有的条目: {rejected_entries}")

    verify_frontend_references(release_root)


def create_release_zip(release_root: Path, archive_path: Path) -> int:
    """把发布目录打成一个 zip 并复验; 返回 zip 字节数.

    只装**那一层**: 包内唯一顶层目录与发布目录同名, 解压即得可直接部署的根. 不引入 `zip(1)` /
    `unzip(1)`: 解释器是本包的硬要求, 那两个命令不是.
    """

    archived_files = sorted(path for path in release_root.rglob("*") if path.is_file())

    if not archived_files:
        raise ValueError(f"发布目录是空的, 拒绝打出空包: {release_root}")

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in archived_files:
            entry_name = f"{release_root.name}/{file_path.relative_to(release_root).as_posix()}"
            entry = zipfile.ZipInfo.from_file(file_path, entry_name)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = UNIX_CREATE_SYSTEM
            entry.external_attr = (
                EXECUTABLE_FILE_ATTRIBUTES if file_path.suffix == ".sh" else REGULAR_FILE_ATTRIBUTES
            )

            with file_path.open("rb") as source, archive.open(entry, "w") as target:
                shutil.copyfileobj(source, target)

    with zipfile.ZipFile(archive_path) as archive:
        broken_entry_name = archive.testzip()

        if broken_entry_name is not None:
            raise ValueError(f"zip 校验失败, 首个损坏条目: {broken_entry_name}")

        archived_names = [name for name in archive.namelist() if not name.endswith("/")]

        if len(archived_names) != len(archived_files):
            raise ValueError(f"zip 里条目数 {len(archived_names)} 与发布文件数 {len(archived_files)} 不符")

        stray_names = [
            name for name in archived_names if name.split("/", 1)[0] != release_root.name or name.count("/") < 1
        ]

        if stray_names:
            raise ValueError(f"zip 里出现不在顶层目录下的条目: {stray_names}")

    archive_size = archive_path.stat().st_size
    print(f"zip 包：{archive_path}")
    print(f"包内顶层目录：{release_root.name}/（{len(archived_files)} 个文件）")
    print(f"zip 大小：{archive_size} 字节")

    return archive_size


def is_path_same_or_ancestor_of(candidate_path: Path, descendant_path: Path) -> bool:
    return candidate_path == descendant_path or candidate_path in descendant_path.parents


def resolve_output_directory(requested_directory: str, allow_clearing: bool) -> Path:
    """认下输出目录, 必要时清空它; 返回绝对路径.

    清空是**不可逆**的, 故两道闸. 第一道是结构性的: 空值、`.`、`..`、符号链接、仓根与家目录
    及其上级, 一律拒绝 —— 它们的上级可能是任何东西, 递归清空会落到本脚本管不着的地方.

    第二道才是要害: **只清自己产出的那几件**. 发布目录收的是后端源码、前端产物、三份文档、
    两份启动脚本与两个文本文件, 全在 git 之内, 删了取回得来; 可部署根常常就选在同一个路径上,
    旁边住着 `runs/`、`users/`、`backend/data/`、`backend/.env` 这些 git 管不着的运行数据,
    删了就没了. 故清空前逐项核对顶层条目, 认不出的一个都不动 —— 打偏一次代价是用户的策略与
    全部运行记录, 那不该由"多打了一个 --force"来决定.
    """

    if requested_directory.strip() in ("", ".", ".."):
        raise ValueError(f"--output 不接受空值、. 或 ..: {requested_directory!r} (会清空当前目录或它的上级)")

    requested_path = Path(requested_directory)

    if requested_path.is_symlink():
        raise ValueError(f"拒绝覆盖符号链接目录: {requested_path} (递归清空会落到它指向的目标上)")

    release_root = requested_path.resolve()

    for reserved_directory in (SCRIPT_DIRECTORY, Path.home().resolve()):
        if is_path_same_or_ancestor_of(release_root, reserved_directory):
            raise ValueError(f"--output 指向了仓根或家目录本身及其上级: {release_root}")

    if release_root.exists() and not release_root.is_dir():
        raise ValueError(f"输出路径已存在且不是目录: {release_root}")

    foreign_entry_names = (
        sorted(child.name for child in release_root.iterdir() if child.name not in OWNED_OUTPUT_ENTRY_NAMES)
        if release_root.is_dir()
        else []
    )

    if foreign_entry_names:
        raise ValueError(f"输出目录里有本脚本不认识的条目, 拒绝清空它: {release_root} → {foreign_entry_names}")

    if release_root.is_dir() and any(release_root.iterdir()):
        if not allow_clearing:
            raise ValueError(f"输出目录已存在且非空: {release_root} (要覆盖它请加 --force)")

        print(f"清空输出目录：{release_root}")
        shutil.rmtree(release_root)

    release_root.mkdir(parents=True, exist_ok=True)

    return release_root


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把平台打成一个可部署的目录, 并按需封装成 zip.")

    parser.add_argument(
        "--output", type=Path, default=SCRIPT_DIRECTORY / DIST_DIRECTORY_NAME / PACKAGE_ROOT_NAME, help="发布目录"
    )
    parser.add_argument("--zip", action="store_true", help="自检通过后另打一个 zip (落在发布目录的同级)")
    parser.add_argument("--force", action="store_true", help="输出目录已存在且非空时先清空它 (只清本脚本自己写下的条目)")
    parser.add_argument("--skip-frontend-build", action="store_true", help="不跑前端构建, 复用现有的 frontend/dist")
    parser.add_argument(
        "--quote-hub-root",
        type=Path,
        default=SCRIPT_DIRECTORY.parent / "QuoteHub",
        help="行情组件的仓根 (取它的依赖清单参与冻结)",
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        version = read_application_version()
        print(f"平台版本：{version}")

        release_root = resolve_output_directory(str(arguments.output), arguments.force)

        if arguments.skip_frontend_build:
            verify_existing_frontend_build()
        else:
            build_frontend()

        with tempfile.TemporaryDirectory(prefix="quant-platform-release-") as workspace_name:
            lock_path, interpreter = create_dependency_lock(arguments.quote_hub_root, Path(workspace_name))

            assemble_release(release_root, lock_path, build_release_information(version))
            verify_installed_dependencies(interpreter, release_root / BACKEND_DIRECTORY_NAME)

        verify_release_contents(release_root)

        if arguments.zip:
            create_release_zip(release_root, release_root.parent / f"{release_root.name}-{version}-{datetime.date.today():%Y-%m-%d}.zip")
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"打包失败：{error}", file=sys.stderr)
        return 1

    print(f"发布目录：{release_root}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
