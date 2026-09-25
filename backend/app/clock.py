"""统一时间源.

平台内所有时间戳一律为 UTC 朴素时间 (naive). SQLite 的 DATETIME 不保留时区偏移,
存入带时区的值会在往返中被静默剥掉, 故此处统一去掉, 由前端按 UTC 解释后本地化.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """取当前 UTC 时间, 去时区标记以匹配 SQLite 的存储行为."""

    return datetime.now(timezone.utc).replace(tzinfo=None)
