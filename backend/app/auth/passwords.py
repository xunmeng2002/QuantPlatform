"""口令散列与校验.

用标准库的 PBKDF2-HMAC-SHA256, 不引第三方库. 存储格式为
`pbkdf2_sha256$<迭代数>$<盐十六进制>$<散列十六进制>`: 迭代数随记录一起存, 日后提高强度
不会作废既有口令.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 260000
PBKDF2_MAX_ITERATIONS = 2000000
SALT_BYTES = 16
HASH_BYTES = 32
HASH_NAME = "sha256"
FIELD_SEPARATOR = "$"
STORED_FIELD_COUNT = 4


def hash_password(password: str) -> str:
    """加盐散列口令, 返回可直接入库的字符串."""

    salt = secrets.token_bytes(SALT_BYTES)
    derived_key = hashlib.pbkdf2_hmac(
        HASH_NAME, password.encode("utf-8"), salt, PBKDF2_ITERATIONS, HASH_BYTES
    )

    return FIELD_SEPARATOR.join(
        (PBKDF2_ALGORITHM, str(PBKDF2_ITERATIONS), salt.hex(), derived_key.hex())
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """校验口令.

    存储串格式非法、散列长度不符或迭代数越界时一律返回 False. 散列长度必须精确等于
    HASH_BYTES: 若只判非空, 空的散列字段会让 pbkdf2 用长度 0 派生, 两个空串比对相等,
    任何口令都能通过.
    """

    fields = stored_hash.split(FIELD_SEPARATOR)

    if len(fields) != STORED_FIELD_COUNT or fields[0] != PBKDF2_ALGORITHM:
        return False

    if not fields[1].isdigit():
        return False

    iterations = int(fields[1])

    if iterations < 1 or iterations > PBKDF2_MAX_ITERATIONS:
        return False

    try:
        salt = bytes.fromhex(fields[2])
        expected_key = bytes.fromhex(fields[3])
    except ValueError:
        return False

    if not salt or len(expected_key) != HASH_BYTES:
        return False

    derived_key = hashlib.pbkdf2_hmac(
        HASH_NAME, password.encode("utf-8"), salt, iterations, HASH_BYTES
    )

    return hmac.compare_digest(derived_key, expected_key)
