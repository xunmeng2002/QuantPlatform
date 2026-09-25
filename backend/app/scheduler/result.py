"""`result.json` 的读取与镜像列映射.

引擎写出的 29 个键里, 27 个镜像成 `Runs` 的列 (列表页与对比页要按指标排序与筛选), `RunId`
与行主键同义, `MissingRateKeys` 是长尾数组, 留在结果文件里按需读取.

**读取先 UTF-8 再 GBK, 兜底才是 `errors="replace"`**: 引擎在中文 Windows 上有 GBK 变体, 一个
`UnicodeDecodeError` 会让"引擎报告失败"被误判成"崩溃"——而结果文件明明在盘上, 用户看到的是完全
相反的结论. 只用 `errors="replace"` 能保住结论、却把 `ErrorMsg` 变成一串 `���汨��` 回给详情页;
既然正确文本是可恢复的, 就不该把它换成替换字符. 两条都不成时才退回替换字符 (那时结论仍然正确).

**取值一律按 ORM 列的声明类型收一遍**: 结果文件是用户上传的策略写的, 按不可信输入对待. 少了这
一步, 一个 `"TradeCount": "84"` 会进到 SQLite 的 INTEGER 列 (SQLite 不做类型强制), 然后在
详情接口的响应模型上炸成 500——提交方的笔误变成平台的内部错误.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from ..catalog.models import RunModel


RESULT_FILENAME = "result.json"
RESULT_SUCCESS_KEY = "Success"
RESULT_RUN_ID_KEY = "RunId"

# 结果文件的键 → `RunModel` 的属性名. 两者都不是拼出来的, 故此处逐项写全.
RESULT_MIRROR_COLUMN_NAMES = {
    "AccountId": "account_id",
    "Available": "available",
    "Balance": "balance",
    "BarMarketDataCount": "bar_market_data_count",
    "BasicDataLoaded": "basic_data_loaded",
    "CommissionMissingCount": "commission_missing_count",
    "CommissionZeroRateKeyCount": "commission_zero_rate_key_count",
    "DbPath": "db_path",
    "DepthMarketDataCount": "depth_market_data_count",
    "DumpPath": "dump_path",
    "EndTradingDay": "end_trading_day",
    "ErrorId": "error_id",
    "ErrorMsg": "error_msg",
    "HasCapital": "has_capital",
    "InstrumentCount": "instrument_count",
    "LastTradingDay": "last_trading_day",
    "MarketDataType": "market_data_type",
    "MdSubscribeCount": "md_subscribe_count",
    "OrderCount": "order_count",
    "SchemaVersion": "schema_version",
    "StartTradingDay": "start_trading_day",
    "Success": "is_success",
    "TotalCommission": "total_commission",
    "TotalStampTax": "total_stamp_tax",
    "TotalTransferFee": "total_transfer_fee",
    "TradeCount": "trade_count",
    "VolumeMultipleFallbackProductCount": "volume_multiple_fallback_product_count",
}

logger = logging.getLogger(__name__)


def read_result_file(result_path: Path) -> dict[str, object] | None:
    """读结果文件; 不存在、读不动或不是 JSON 对象时返回 None.

    返回 None 就是"本轮没走完收尾"的唯一判据, 与退出码合起来消歧 (§4.3 的仲裁表).
    """

    try:
        raw_bytes = result_path.read_bytes()
    except OSError:
        return None

    try:
        parsed_result = json.loads(decode_result_bytes(raw_bytes))
    except ValueError:
        return None

    if not isinstance(parsed_result, dict):
        return None

    return parsed_result


def decode_result_bytes(raw_bytes: bytes) -> str:
    """把结果文件的原始字节解成文本.

    UTF-8 优先: 平台自己与绝大多数策略都写 UTF-8. 不成立时试 GBK——中文 Windows 上引擎的
    ErrorMsg 是中文, 那种字节序列在 UTF-8 下会整段变成替换字符, 而正确文本本可恢复. 两者都不成
    才退回替换字符: 那时**结论**仍然正确 (是一份能解析的 JSON), 只是文案有损, 总好过把一个
    "引擎报告失败"报成"宿主崩溃".
    """

    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass

    try:
        return raw_bytes.decode("gbk")
    except UnicodeDecodeError:
        return raw_bytes.decode("utf-8", errors="replace")


def read_reported_success(run_result: Mapping[str, object]) -> bool | None:
    """结果文件里 `Success` 的取值; 缺席或不是布尔即返回 None."""

    reported_success = run_result.get(RESULT_SUCCESS_KEY)

    return reported_success if isinstance(reported_success, bool) else None


def build_mirror_column_values(run_result: Mapping[str, object]) -> dict[str, object]:
    """结果文件 → `Runs` 的镜像列.

    文件里缺席的键不入库, 列保持 NULL 表示"引擎没报"; 取值类型与列的声明不符时同样不入库并记
    一条 warning——那说明策略写坏了结果文件, 而把坏值放进库里只会让它以更难归因的面目出现.
    """

    declared_types = {
        attribute_name: column.type.python_type
        for attribute_name, column in RunModel.__mapper__.columns.items()
    }

    mirrored_values: dict[str, object] = {}

    for result_key, attribute_name in RESULT_MIRROR_COLUMN_NAMES.items():
        if result_key not in run_result:
            continue

        raw_value = run_result[result_key]
        declared_type = declared_types[attribute_name]
        coerced_value = _coerce_to_declared_type(raw_value, declared_type)

        if coerced_value is None and raw_value is not None:
            logger.warning(
                "结果文件的 %s 类型与列声明不符, 已按缺席处理 (声明类型 %s)",
                result_key,
                declared_type.__name__,
            )
            continue

        mirrored_values[attribute_name] = coerced_value

    return mirrored_values


def _coerce_to_declared_type(value: object, declared_type: type) -> object | None:
    """把取值收成列声明的类型; 收不动即返回 None.

    bool 单独挡在前面: 它在 Python 里是 int 的子类, `isinstance(True, int)` 为真, 于是一个
    JSON 的 `true` 会被整数列若无其事地收下.
    """

    if value is None:
        return None

    if declared_type is bool:
        return value if isinstance(value, bool) else None

    if isinstance(value, bool):
        return None

    if declared_type is int:
        return value if isinstance(value, int) else None

    if declared_type is float:
        return float(value) if isinstance(value, (int, float)) else None

    if declared_type is str:
        return value if isinstance(value, str) else None

    return None
