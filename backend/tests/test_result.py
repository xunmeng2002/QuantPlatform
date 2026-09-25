"""`result.json` 的读取与镜像列映射.

结果文件是**用户上传的策略**写出来的, 按不可信输入对待. 两处脆点各有一组断言:

- **解码**: 中文 Windows 上引擎有 GBK 变体. 一个 `UnicodeDecodeError` 会让"引擎报告失败"被误判
  成"宿主崩溃"——而结果文件明明在盘上, 用户看到的是完全相反的结论. 只用 `errors="replace"` 能
  保住结论, 却把 `ErrorMsg` 变成一串 `���汨��` 回给详情页, 而正确文本本可恢复.
- **取值类型**: 一个 `"TradeCount": "84"` 会进到 SQLite 的 INTEGER 列 (SQLite 不做类型强制),
  然后在详情接口的响应模型上炸成 500——提交方的笔误变成平台的内部错误.

映射表本身也在这里钉住: 它把结果文件的键名与 ORM 列名各写一遍, 而两者都不是拼出来的, 写错一个
只会在某个指标悄悄不落库时才显形.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.catalog.models import RunModel
from app.scheduler.result import (
    RESULT_MIRROR_COLUMN_NAMES,
    build_mirror_column_values,
    decode_result_bytes,
    read_reported_success,
    read_result_file,
)


MIRRORED_KEY_COUNT = 27

BALANCE_SENTINEL = 998951.4506464996
GBK_ERROR_MESSAGE = "引擎报告: 行情数据缺失"
CHINESE_TEXT = "中文测试"

SUCCESS_FILENAME = "result.json"
UNPARSEABLE_CONTENT = "{这不是 JSON"


def _write_result_file(directory: Path, raw_bytes: bytes) -> Path:
    """把一个结果文件按**原始字节**落盘."""

    result_path = directory / SUCCESS_FILENAME
    result_path.write_bytes(raw_bytes)

    return result_path


def test_every_mirrored_key_maps_to_a_real_run_column() -> None:
    """映射表逐项指向 `RunModel` 上真实存在的列, 且**恰好**覆盖结果文件里该镜像的那些键.

    `RESULT_MIRROR_COLUMN_NAMES` 的键是引擎写的字面量、值是 ORM 属性名, 两者都靠手抄. 拼错一个
    不会报错: 那一列从此恒为 NULL, 而"引擎没报这个键"与"平台抄错了列名"在库里长得一模一样.
    """

    declared_columns = set(RunModel.__mapper__.columns.keys())

    assert len(RESULT_MIRROR_COLUMN_NAMES) == MIRRORED_KEY_COUNT

    for result_key, attribute_name in RESULT_MIRROR_COLUMN_NAMES.items():
        assert attribute_name in declared_columns, result_key

    # 这两个键不镜像: `RunId` 与行主键同义 (以行主键为准), `MissingRateKeys` 是长尾数组.
    assert "RunId" not in RESULT_MIRROR_COLUMN_NAMES
    assert "MissingRateKeys" not in RESULT_MIRROR_COLUMN_NAMES


def test_a_missing_result_file_is_not_a_report(tmp_path: Path) -> None:
    """文件不存在 → None.

    返回 None 就是"本轮没走完收尾"的唯一判据, 与退出码合起来给终态消歧.
    """

    assert read_result_file(tmp_path / SUCCESS_FILENAME) is None


def test_an_unparseable_result_file_is_not_a_report(tmp_path: Path) -> None:
    """内容不是 JSON → None (而不是抛 `JSONDecodeError`)."""

    assert read_result_file(
        _write_result_file(tmp_path, UNPARSEABLE_CONTENT.encode("utf-8"))
    ) is None


def test_a_json_document_that_is_not_an_object_is_not_a_report(tmp_path: Path) -> None:
    """顶层不是对象 → None.

    引擎写的是一份对象; 顶层是数组时后续按键取值会拿到 `TypeError`, 那种失败发生在收尾路径上,
    与"文件坏了"是同一件事的两种面目, 不如在这里合成一种.
    """

    assert read_result_file(_write_result_file(tmp_path, b"[1, 2, 3]")) is None


def test_a_result_file_written_in_utf8_keeps_its_chinese_text(tmp_path: Path) -> None:
    """UTF-8 是首选编码, 中文文案原样读出."""

    report = read_result_file(
        _write_result_file(
            tmp_path,
            json.dumps(
                {"Success": True, "ErrorMsg": GBK_ERROR_MESSAGE}, ensure_ascii=False
            ).encode("utf-8"),
        )
    )

    assert report is not None
    assert report["ErrorMsg"] == GBK_ERROR_MESSAGE


def test_a_result_file_written_in_gbk_is_still_a_report(tmp_path: Path) -> None:
    """GBK 字节 → 仍是可解析的报告, 且中文文案**正确**而不含替换字符.

    这是"引擎报告失败被误判成崩溃"这条错误结论的源头: 字节解不开时, 一个只看得到
    `UnicodeDecodeError` 的实现会认定结果文件不可解析. 而结果文件就在盘上, 用户被告知的是完全
    相反的结论.
    """

    report = read_result_file(
        _write_result_file(
            tmp_path,
            json.dumps(
                {"Success": False, "ErrorMsg": GBK_ERROR_MESSAGE}, ensure_ascii=False
            ).encode("gbk"),
        )
    )

    assert report is not None
    assert read_reported_success(report) is False
    assert report["ErrorMsg"] == GBK_ERROR_MESSAGE
    assert "�" not in str(report["ErrorMsg"])


def test_bytes_that_fit_no_encoding_still_yield_a_report(tmp_path: Path) -> None:
    """两种编码都不成立时才退回替换字符——**结论仍然正确**, 只是文案有损.

    宁可回一段带替换字符的文案, 也不能把一个"引擎报告失败"报成"宿主崩溃".
    """

    undecodable_bytes = b"\xff\xfe"

    report = read_result_file(
        _write_result_file(
            tmp_path,
            b'{"Success": false, "ErrorMsg": "' + undecodable_bytes + b'"}',
        )
    )

    assert report is not None
    assert read_reported_success(report) is False
    assert "�" in str(report["ErrorMsg"])


def test_the_fallback_decoder_only_runs_when_both_encodings_fail() -> None:
    """UTF-8 可解时绝不走 GBK: 一段既是合法 UTF-8 又是合法 GBK 的字节要按 UTF-8 读.

    `"中文"` 的 UTF-8 字节恰好也能按 GBK 解出四个字节 (乱码), 故两条路径的**先后**是可判的.
    """

    raw_bytes = CHINESE_TEXT.encode("utf-8")

    assert decode_result_bytes(raw_bytes) == CHINESE_TEXT


def test_the_reported_success_is_only_a_boolean() -> None:
    """`Success` 缺席或不是布尔即返回 None: "引擎没报结论"不许被读成"失败"."""

    assert read_reported_success({}) is None
    assert read_reported_success({"Success": "true"}) is None
    assert read_reported_success({"Success": 1}) is None
    assert read_reported_success({"Success": False}) is False
    assert read_reported_success({"Success": True}) is True


def test_a_key_absent_from_the_file_stays_null() -> None:
    """文件里缺席的键不入库: 列保持 NULL, 表示"引擎没报"而不是"报了个 0"."""

    assert build_mirror_column_values({}) == {}
    assert build_mirror_column_values({"TradeCount": None}) == {"trade_count": None}


def test_the_balance_is_mirrored_bit_exact() -> None:
    """金额列是 `Float` 而不是 `Numeric`, 故取值逐位相等.

    `Numeric` 会在回读时被量化成 `Decimal`, 于是同一个数在不同驱动下取回不同的形态——而
    "基准余额"是验收判据之一, 差一位就没法比.
    """

    mirrored_values = build_mirror_column_values({"Balance": BALANCE_SENTINEL})

    assert mirrored_values == {"balance": BALANCE_SENTINEL}
    assert mirrored_values["balance"] == BALANCE_SENTINEL


def test_a_value_of_the_wrong_type_is_not_mirrored() -> None:
    """取值与列的声明类型不符时整条不入库 (并记 warning).

    放进库里只会让坏值以更难归因的面目出现: SQLite 不做类型强制, `"84"` 会原样躺进 INTEGER 列,
    直到详情接口把它读出来放进响应模型时才炸成 500.
    """

    assert build_mirror_column_values({"TradeCount": "84"}) == {}
    assert build_mirror_column_values({"DbPath": 17}) == {}


def test_a_boolean_does_not_fit_an_integer_column() -> None:
    """`true` 不许被整数列若无其事地收下.

    Python 里 `bool` 是 `int` 的子类 (`isinstance(True, int)` 为真), 只写 `isinstance(value, int)`
    的实现会把 JSON 的 `true` 变成 `1` 存进指标列.
    """

    assert build_mirror_column_values({"TradeCount": True}) == {}


def test_a_boolean_column_accepts_only_a_boolean() -> None:
    """`Success` 列同理: `1` 不是布尔."""

    assert build_mirror_column_values({"Success": 1}) == {}
    assert build_mirror_column_values({"Success": False}) == {"is_success": False}


def test_an_integer_is_accepted_where_the_column_is_float() -> None:
    """JSON 没有整数与浮点之分 (`1000` 与 `1000.0` 同形), 故整数列值可进浮点列并收成浮点."""

    mirrored_values = build_mirror_column_values({"Balance": 1000})

    assert mirrored_values == {"balance": 1000.0}
    assert isinstance(mirrored_values["balance"], float)
