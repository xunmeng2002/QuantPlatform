"""策略 manifest: 策略作者与平台之间的契约.

manifest 声明平台运行该策略所需的四件事: 入口文件名 (它决定 job 目录里的裸文件名与
`argv[0]`)、该策略读取的配置文件名、它支持哪些行情模式、以及运行级字段写进策略配置时的键名.
`params` 声明该策略自己的参数: 类型、取值范围、可选取值与默认值.

**平台按 manifest 渲染策略配置, 不读取策略自带的配置文件**——上传只携带源码与 manifest, 版本
目录里没有配置文件可作底稿. 故策略读到的每一个键都必须由 manifest 声明: 漏声明的键不会"保持
原样", 它会整个消失, 而策略多半是 `config["X"]` 直接取用, 于是以未捕获异常 (退出码 1) 收场.

参数项用 `extra="allow"` 放开, 顶层与 `run_field_keys` 用 `extra="forbid"` 收严——这一松一紧是
刻意的: 顶层的键名写错 (如 `param`) 会让参数整批静默落空, 而参数项里额外的键此刻仍是待扩展的
载体 (界面提示、分组图标之类).
"""

from __future__ import annotations

import re
from enum import StrEnum

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
MAXIMUM_CONFIGURATION_KEY_LENGTH = 64
MAXIMUM_PARAMETER_LABEL_LENGTH = 128
MAXIMUM_PARAMETER_GROUP_LENGTH = 64
CONFIGURATION_KEY_PATTERN = r"^[A-Za-z0-9_.-]+$"
CONFIGURATION_KEY_REGEX = re.compile(CONFIGURATION_KEY_PATTERN)

# 策略配置里由平台从运行级字段写入的键, 只允许这三个名字: 引擎不认识 exchange / instrument,
# 它们只有策略用; bar_period 另有一份必须与 BackTest.json 同值 (那份由平台写引擎配置时取同一个
# 取值, 一致性因此是结构性的, 不靠两处各写一遍).
EXCHANGE_ID_FIELD_NAME = "exchange_id"
INSTRUMENT_ID_FIELD_NAME = "instrument_id"
BAR_PERIOD_FIELD_NAME = "bar_period"

RUN_LEVEL_FIELD_NAMES = (
    EXCHANGE_ID_FIELD_NAME,
    INSTRUMENT_ID_FIELD_NAME,
    BAR_PERIOD_FIELD_NAME,
)

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


class StrategyParameterType(StrEnum):
    """参数取值类型. 每一项都对应一种真实的表单控件."""

    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"


NUMERIC_PARAMETER_TYPES = frozenset(
    {StrategyParameterType.INTEGER, StrategyParameterType.NUMBER}
)


class StrategyParameterOption(BaseModel):
    """参数的一个可选取值."""

    model_config = ConfigDict(extra="forbid")

    value: object
    label: str = Field(default="", max_length=MAXIMUM_PARAMETER_LABEL_LENGTH)


class StrategyParameter(BaseModel):
    """manifest 里的一个策略参数.

    **没有 `required` 标志**: `required: true` 配 `default: 10` 是自相矛盾的组合, 而"没有
    `default` 就必须由提交方给出"已经完整表达了同一件事, 且不可能写出矛盾组合.
    """

    model_config = ConfigDict(extra="allow")

    key: str = Field(
        min_length=1,
        max_length=MAXIMUM_CONFIGURATION_KEY_LENGTH,
        pattern=CONFIGURATION_KEY_PATTERN,
    )
    label: str = Field(default="", max_length=MAXIMUM_PARAMETER_LABEL_LENGTH)
    type: StrategyParameterType = StrategyParameterType.STRING
    default: object | None = None
    minimum: float | None = None
    maximum: float | None = None
    options: list[StrategyParameterOption] = Field(default_factory=list)
    group: str = Field(default="", max_length=MAXIMUM_PARAMETER_GROUP_LENGTH)

    @model_validator(mode="after")
    def _check_declaration_is_self_consistent(self) -> StrategyParameter:
        """范围与可选取值只对声明得出来的类型开放, 且默认值自己必须合法.

        默认值走的是与提交值**同一个**校验函数: 分成两处写就会造出"默认值自己不合法、提交方
        照抄默认值反被拒"的参数声明——而漏声明的默认值恰恰是最容易被照抄的那一个.
        """

        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("minimum 不得大于 maximum")

        if self.type not in NUMERIC_PARAMETER_TYPES and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("只有 integer 与 number 参数可以声明 minimum / maximum")

        if self.default is not None:
            try:
                validate_parameter_value(self, self.default)
            except ValueError as error:
                raise ValueError(f"default 不满足声明: {error}") from error

        return self


class RunFieldKeys(BaseModel):
    """运行级字段在策略配置里的键名.

    三项都可省略; **省略即该字段不写进策略配置**. 键名不得与任一 `params[].key` 重复——否则
    同一份策略配置里有两个写入者, 值以谁为准无从说起.
    """

    model_config = ConfigDict(extra="forbid")

    exchange_id: str | None = None
    instrument_id: str | None = None
    bar_period: str | None = None

    @field_validator(*RUN_LEVEL_FIELD_NAMES)
    @classmethod
    def _check_key_name(cls, value: str | None) -> str | None:
        if value is None:
            return None

        return _validate_configuration_key(value, "运行级字段的键名")


class StrategyManifest(BaseModel):
    """策略 manifest 的顶层结构."""

    model_config = ConfigDict(extra="forbid")

    entry_filename: str = Field(max_length=MAXIMUM_FILENAME_LENGTH)
    config_filename: str = Field(max_length=MAXIMUM_FILENAME_LENGTH)
    supported_match_modes: list[MarketDataType] = Field(min_length=1)
    run_field_keys: RunFieldKeys = Field(default_factory=RunFieldKeys)
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
    def _check_declared_keys_do_not_collide(self) -> StrategyManifest:
        """参数 key 互不重复, 且不与运行级字段的键名相撞."""

        parameter_keys = [parameter.key for parameter in self.params]

        if len(set(parameter_keys)) != len(parameter_keys):
            raise ValueError("参数 key 不得重复")

        run_field_key_names = self.named_run_field_keys().values()
        collided_keys = sorted(set(parameter_keys) & set(run_field_key_names))

        if collided_keys:
            raise ValueError(
                "运行级字段的键名与参数 key 相撞: " + ", ".join(collided_keys)
            )

        return self

    def named_run_field_keys(self) -> dict[str, str]:
        """已声明映射的运行级字段: 字段名 → 策略配置里的键名."""

        return {
            field_name: key_name
            for field_name in RUN_LEVEL_FIELD_NAMES
            if (key_name := getattr(self.run_field_keys, field_name)) is not None
        }


def validate_parameter_value(parameter: StrategyParameter, value: object) -> None:
    """校验一个取值是否满足参数声明, 不满足即抛 ValueError.

    提交值与 manifest 里的默认值共用本函数, 故二者对"什么算合法"永远一致. 报错只说原因不带
    取值: 值可能源自调用方输入, 经响应体进接入层日志.
    """

    if parameter.type is StrategyParameterType.INTEGER:
        # bool 是 int 的子类, JSON 的 true 会被 isinstance(..., int) 收下——必须显式挡掉,
        # 否则 `GridCount: true` 会被当成 1 通过校验.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("需为整数")

    elif parameter.type is StrategyParameterType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("需为数值")

    elif parameter.type is StrategyParameterType.BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError("需为 true 或 false")

    elif not isinstance(value, str):
        raise ValueError("需为字符串")

    # 到这里数值型取值已被收成 int 或 float, 且 bool 已挡掉, 故可直接比大小.
    if parameter.minimum is not None and float(value) < parameter.minimum:  # type: ignore[arg-type]
        raise ValueError(f"不得小于 {parameter.minimum}")

    if parameter.maximum is not None and float(value) > parameter.maximum:  # type: ignore[arg-type]
        raise ValueError(f"不得大于 {parameter.maximum}")

    if parameter.options and not any(
        _is_same_option_value(value, option.value) for option in parameter.options
    ):
        raise ValueError("不在可选取值之内")


def _is_same_option_value(value: object, option_value: object) -> bool:
    """取值是否等于某个可选取值.

    按"同类型才相等"比: JSON 里 `1` 与 `true` 在 Python 中是 `1 == True`, 若按 `==` 比,
    一个 `type: integer` 的参数会在 `options` 含 `{"value": true}` 时被放行——而这在类型检查
    那一关就该被拒了.
    """

    if isinstance(value, bool) != isinstance(option_value, bool):
        return False

    return value == option_value


def _validate_configuration_key(key_name: str, field_label: str) -> str:
    """校验策略配置里的一个键名.

    与参数 key 同一套规则: 它要落进 JSON 对象, 故不需要文件名那套限制, 但含空白或过长的键名
    只会让渲染出来的配置无法被策略正确读取.
    """

    if len(key_name) > MAXIMUM_CONFIGURATION_KEY_LENGTH:
        raise ValueError(f"{field_label}不得超过 {MAXIMUM_CONFIGURATION_KEY_LENGTH} 个字符")

    if not CONFIGURATION_KEY_REGEX.fullmatch(key_name):
        raise ValueError(f"{field_label}只能由字母、数字与 . _ - 组成")

    return key_name


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
