"""主键生成.

统一 32 位十六进制 UUID4, 与各表 String(32) 的列宽一致. RunId 是例外: 它由调度侧生成,
兼具目录名用途, 见 job 工作目录契约.
"""

from __future__ import annotations

import uuid


def generate_identifier() -> str:
    """生成一个主键."""

    return uuid.uuid4().hex
