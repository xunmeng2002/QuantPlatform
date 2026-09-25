"""策略 manifest: 策略作者与平台之间的契约.

manifest 声明平台运行该策略所需的三件事: 入口文件名 (它决定 job 目录里的裸文件名与
`argv[0]`)、该策略读取的配置文件名、以及它支持哪些行情模式.

`params` 的类型枚举、取值范围、分组与联动**尚未定案** (见 PROGRESS.md ❓), 故此处只固定两件
在任何方案下都成立的事: 每项是一个 JSON 对象, 且 `key` 非空、互不重复. 其余键经
`extra="allow"` 原样保留并存入 `ManifestJson`, 定案后补上类型化字段即可, 已上传的 manifest
不必改写. 顶层用 `extra="forbid"` 收严, 参数项用 `extra="allow"` 放开——这一紧一松是刻意的:
顶层的键名写错 (如 `param`) 会让参数整批静默落空, 而参数项里未知的键此刻恰恰是待定案的载体.
"""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .catalog.enums import MarketDataType
from .errors import InvalidRequestError


ENTRY_FILENAME_SUFFIX = ".py"
CONFIG_FILENAME_SUFFIX = ".json"

MAXIMUM_MANIFEST_BYTES = 64 * 1024
MAXIMUM_FILENAME_LENGTH = 128
STRATEGY_PARAMETER_KEY_PATTERN = r"^[A-Za-z0-9_.-]+$"

FOLDER_SEPARATOR_CHARACTERS = ("/", "\\")
WINDOWS_FORBIDDEN_FILENAME_CHARACTERS = frozenset('<>:"|?*')
DIRECTORY_REFERENCE_NAMES = frozenset({".", ".."})
WINDOWS_RESERVED_FILENAME_BASES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)

ENGINE_RESERVED_FILENAMES = frozenset(
    {"backtest.json", "sessions.json", "result.json"}
)

MANIFEST_TOO_LARGE_MESSAGE = f"manifest 不得超过 {MAXIMUM_MANIFEST_BYTES} 字节"
MANIFEST_INVALID_MESSAGE = "manifest 不合法"


class StrategyParameter(BaseModel):
    """manifest 里的一个策略参数.

    目前只钉 `key`; 类型、默认值、范围与枚举待定案后补, 其余键原样保留.
    """

    model_config = ConfigDict(extra="allow")

    key: str = Field(
        min_length=1, max_length=64, pattern=STRATEGY_PARAMETER_KEY_PATTERN
    )


class StrategyManifest(BaseModel):
    """策略 manifest 的顶层结构."""

    model_config = ConfigDict(extra="forbid")

    entry_filename: str = Field(max_length=MAXIMUM_FILENAME_LENGTH)
    config_filename: str = Field(max_length=MAXIMUM_FILENAME_LENGTH)
    supported_match_modes: list[MarketDataType] = Field(min_length=1)
    params: list[StrategyParameter] = Field(default_factory=list)

    @field_validator("entry_filename")
    @classmethod
    def _check_entry_filename(cls, value: str) -> str:
        return _validate_bare_filename(value, ENTRY_FILENAME_SUFFIX, "入口文件名")

    @field_validator("config_filename")
    @classmethod
    def _check_config_filename(cls, value: str) -> str:
        return _validate_bare_filename(value, CONFIG_FILENAME_SUFFIX, "配置文件名")

    @field_validator("supported_match_modes")
    @classmethod
    def _check_supported_match_modes(
        cls, values: list[MarketDataType]
    ) -> list[MarketDataType]:
        if len(set(values)) != len(values):
            raise ValueError("支持的行情模式不得重复")

        return values

    @model_validator(mode="after")
    def _check_parameter_keys_are_unique(self) -> StrategyManifest:
        parameter_keys = [parameter.key for parameter in self.params]

        if len(set(parameter_keys)) != len(parameter_keys):
            raise ValueError("参数 key 不得重复")

        return self


def _validate_bare_filename(
    filename: str, required_suffix: str, field_label: str
) -> str:
    """校验取值既能落成文件名, 也能当作 `argv[0]`.

    要求"裸文件名"不是洁癖: 引擎日志器按 `strrchr` 找反斜杠、再取首个点之前的部分作日志名,
    拿到带分隔符的路径就拼不出合法路径, `fopen` 失败后**整个进程在启动期终止**.
    见 `job-workspace.md` §3.1.

    非法字符那一条不能省: `a:b.py` 在 Windows 上**不会报错**, 它被当成 NTFS 备用数据流写进
    文件 `a`, 目录里从此只看得见 `a`; 而 `:` 正是 §3.1 表格里点名的致命字符——平台会安静地
    收下一个永远跑不起来的策略.

    报错一律不带取值: 值会随响应体进接入层日志, 而定位问题靠的是字段名与原因——本字段一格
    只有一个值, 说清哪一项就够了.
    """

    if not filename:
        raise ValueError(f"{field_label}不能为空")

    if filename != filename.strip():
        raise ValueError(f"{field_label}首尾不得有空白")

    if any(character in filename for character in FOLDER_SEPARATOR_CHARACTERS):
        raise ValueError(f"{field_label}只能是文件名, 不得带路径分隔符")

    if any(
        character in filename for character in WINDOWS_FORBIDDEN_FILENAME_CHARACTERS
    ):
        raise ValueError(f"{field_label}含 Windows 文件名不允许的字符")

    if filename in DIRECTORY_REFERENCE_NAMES:
        raise ValueError(f"{field_label}不能是目录引用")

    if any(ord(character) < 32 for character in filename):
        raise ValueError(f"{field_label}不得含控制字符")

    if not filename.casefold().endswith(required_suffix):
        raise ValueError(f"{field_label}须以 {required_suffix} 结尾")

    if filename.casefold() in ENGINE_RESERVED_FILENAMES:
        raise ValueError(f"{field_label}与引擎自身要读写的文件重名")

    if filename.split(".", 1)[0].casefold() in WINDOWS_RESERVED_FILENAME_BASES:
        raise ValueError(f"{field_label}是 Windows 保留设备名, 落不了盘")

    return filename


def _describe_validation_errors(validation_error: ValidationError) -> str:
    """把 pydantic 的报错压成一行, 只留出错位置与原因, 不回显字段值.

    回显值等于把整份 manifest 抄进响应体与接入层日志, 而定问题靠的是位置与原因, 不是值.
    """

    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in validation_error.errors()
    )


def parse_strategy_manifest(manifest_text: str) -> StrategyManifest:
    """解析并校验上传的 manifest, 不合法即抛 InvalidRequestError."""

    if len(manifest_text.encode("utf-8")) > MAXIMUM_MANIFEST_BYTES:
        raise InvalidRequestError(MANIFEST_TOO_LARGE_MESSAGE)

    try:
        return StrategyManifest.model_validate_json(manifest_text)
    except ValidationError as error:
        raise InvalidRequestError(
            f"{MANIFEST_INVALID_MESSAGE}: {_describe_validation_errors(error)}"
        ) from error
