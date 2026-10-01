"""策略配置模板: 策略作者与平台之间唯一的那份契约.

上传形态是**两份文件**: 策略源码 `.py`, 以及它在启动时真正去读的那份配置 `.json`. 那份 JSON
就是参数模板——它的键即参数, 每个键在提交页渲染成一个控件, 而**键集由文件固定**, 不可增删.

**平台拿这份 JSON 当底稿渲染**: 上传时它被原样存进版本目录, 提交时在它之上叠用户的编辑, 再
**覆写**三个运行级键 (`ExchangeId` / `InstrumentId` / `BarPreces`). 于是策略读到的键集恒等于
"模板的键集 + 这三个", 没有第三类键; 而模板里平台控不了的键 (对象、数组、`null`) 也照原样
留下, 不会像旧 manifest 那样因为"漏声明"而整个消失.

**参数合法性由策略自己守**: 平台不看取值范围、不看类型、连标题都不认, 控件形态只由该键**当前
取值的 JSON 类型**决定. 这是用户拍板的取舍, 换来的是"上传的 JSON 即所见".

三个运行级键与引擎自带的 `Configs/TestStrategyGrid.json` 同拼写, 逐字保留 (含 `BarPreces` 这个
引擎侧既有拼写, 非笔误). 它们不由用户增删: 模板里没有就新增, 有就覆写.

**本模块是纯函数**: 不碰库、不碰文件系统、不发请求, 成功回值、不合法抛 `ValueError`(原因写在
消息里). 译成 HTTP 400 是调用点的事——上传说"这份配置不合规", 读落库快照说"这个版本作废了",
两句文案不同, 不该由这里决定.
"""

from __future__ import annotations

import json


ENTRY_FILENAME_SUFFIX = ".py"
CONFIGURATION_FILENAME_SUFFIX = ".json"

MAXIMUM_CONFIGURATION_BYTES = 64 * 1024
MAXIMUM_FILENAME_LENGTH = 128
MAXIMUM_CONFIGURATION_KEY_LENGTH = 64
MAXIMUM_CONFIGURATION_KEY_COUNT = 200

# 三个运行级键在策略配置里的固定键名. 用常量而不是散字面量: 渲染与解码取的是同一份, 改一个字母
# 就得两处一起改——而"只改了写的那一处"的症状是策略配置里多出一个谁也不认的键.
EXCHANGE_ID_KEY_NAME = "ExchangeId"
INSTRUMENT_ID_KEY_NAME = "InstrumentId"
BAR_PERIOD_KEY_NAME = "BarPreces"

#: 平台**覆写**的三个键, 次序即模板里缺了它们时的追加次序.
PLATFORM_KEY_NAMES = (
    EXCHANGE_ID_KEY_NAME,
    INSTRUMENT_ID_KEY_NAME,
    BAR_PERIOD_KEY_NAME,
)

FOLDER_SEPARATOR_CHARACTERS = ("/", "\\")
WINDOWS_FORBIDDEN_FILENAME_CHARACTERS = frozenset('<>:"|?*')
DIRECTORY_REFERENCE_NAMES = frozenset({".", ".."})
WINDOWS_RESERVED_FILENAME_BASES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)

# 引擎与平台都会往作业目录根部写文件; 配置与它们重名会**覆盖掉**其中之一 (复制与写入都在起进程
# 之前, 谁后写谁赢, 不报错).
ENGINE_RESERVED_FILENAMES = frozenset(
    {"backtest.json", "sessions.json", "result.json"}
)

CONFIGURATION_NOT_AN_OBJECT_MESSAGE = "策略配置须是一个 JSON 对象"
CONFIGURATION_KEY_COUNT_MESSAGE = (
    f"策略配置的键不得超过 {MAXIMUM_CONFIGURATION_KEY_COUNT} 个"
)
CONFIGURATION_KEY_LENGTH_MESSAGE = (
    f"策略配置的键名不得超过 {MAXIMUM_CONFIGURATION_KEY_LENGTH} 个字符"
)
CONFIGURATION_KEY_EMPTY_MESSAGE = "策略配置的键名不能为空"


def validate_bare_filename(filename: str, required_suffix: str, field_label: str) -> str:
    """校验取值既能落成文件名, 也能当作 `argv[0]`.

    要求"裸文件名"不是洁癖: 引擎日志器按 `strrchr` 找反斜杠、再取首个点之前的部分作日志名,
    拿到带分隔符的路径就拼不出合法路径, `fopen` 失败后**整个进程在启动期终止**.
    见 `job-workspace.md` §3.1.

    非法字符那一条不能省: `a:b.py` 在 Windows 上**不会报错**, 它被当成 NTFS 备用数据流写进
    文件 `a`, 目录里从此只看得见 `a`; 而 `:` 正是 §3.1 表格里点名的致命字符——平台会安静地
    收下一个永远跑不起来的策略.

    长度上限也不是洁癖: 名字会落成盘上的文件名, 而作业目录本身已有一长串前缀, 再加一个任意长
    的名字会撞 Windows 的路径长度上限——那时报错的是 `mkdir`, 归因要翻到作业目录才想得明白.
    库里的两列也正好是这个宽度 (`EntryFilename` / `ConfigFilename` 都是 `String(128)`).

    报错一律不带取值: 值会随响应体进接入层日志, 而定位问题靠的是字段名与原因——本字段一格
    只有一个值, 说清哪一项就够了.

    客户端送回完整路径时**直接拒**, 不悄悄取末段: 文件名是策略源码里 `open()` 的那个名字,
    平台替它认领一份用户没打算用的名字, 症状是"跑起来了但读不到配置".
    """

    if not filename:
        raise ValueError(f"{field_label}不能为空")

    if filename != filename.strip():
        raise ValueError(f"{field_label}首尾不得有空白")

    if len(filename) > MAXIMUM_FILENAME_LENGTH:
        raise ValueError(f"{field_label}不得超过 {MAXIMUM_FILENAME_LENGTH} 个字符")

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


def parse_configuration_template(configuration_text: str) -> dict[str, object]:
    """把一份策略配置文本解析成字典; 不合法即抛 `ValueError`.

    顶层**必须是对象**: 数组或标量在提交页渲染不出任何控件, 而它照样会被原样写进策略配置——
    症状是"参数区是空的, 提交也能过", 用户对着一个没有参数的表单点提交, 然后收到一句策略抛的
    `TypeError`. 拦在这里, 报错能直接说到点子上.

    形状约束只有三条 (键数、键长、键非空), 且都与"表单能不能渲染"直接相关: 一个键一个控件, 而
    键名是那格控件的唯一标识. 取值范围与类型一律不看——那是策略自己的事.
    """

    if len(configuration_text.encode("utf-8")) > MAXIMUM_CONFIGURATION_BYTES:
        raise ValueError(f"策略配置不得超过 {MAXIMUM_CONFIGURATION_BYTES} 字节")

    parsed_configuration = _parse_json_object(configuration_text)
    _check_configuration_keys(parsed_configuration)

    return parsed_configuration


def _parse_json_object(configuration_text: str) -> dict[str, object]:
    """解出顶层对象; 不是 JSON、或顶层不是对象, 都抛 `ValueError`.

    坏数据的原因回显**不含原始文本**: 那份文本会随响应体进接入层日志, 而配置里可能写着账户号
    一类的东西. 行列号足够定位.

    `ast.literal_eval` 与 `eval` 一概不用: 只解析数据, 一行都不执行.
    """

    try:
        parsed_configuration = json.loads(
            configuration_text, parse_constant=_reject_non_finite_constant
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            f"策略配置不是合法 JSON (第 {error.lineno} 行第 {error.colno} 列)"
        ) from error

    if not isinstance(parsed_configuration, dict):
        raise ValueError(CONFIGURATION_NOT_AN_OBJECT_MESSAGE)

    return parsed_configuration


def _reject_non_finite_constant(constant_text: str) -> None:
    """`json.loads` 默认收下 `NaN` / `Infinity` / `-Infinity` 这几个**非 JSON 字面量**.

    它们落进配置之后, 引擎读到的就是一个不可比的数——那一轮结果再怎么看都正常, 只是永远算不对.
    在这里拒掉, 报错比"跑出来数不对"早得多, 也清楚得多.
    """

    raise ValueError(f"策略配置含非有限数值 ({constant_text})")


def _check_configuration_keys(configuration: dict[str, object]) -> None:
    """键数、键长、键非空三条形状约束."""

    if len(configuration) > MAXIMUM_CONFIGURATION_KEY_COUNT:
        raise ValueError(CONFIGURATION_KEY_COUNT_MESSAGE)

    for key_name in configuration:
        if not key_name:
            raise ValueError(CONFIGURATION_KEY_EMPTY_MESSAGE)

        if len(key_name) > MAXIMUM_CONFIGURATION_KEY_LENGTH:
            raise ValueError(CONFIGURATION_KEY_LENGTH_MESSAGE)
