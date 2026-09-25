"""口令散列与校验.

重点是格式非法时的拒绝路径: 掩码里最要命的一种是散列字段为空——若只判非空, pbkdf2 会用
长度 0 派生, 两个空串比对相等, 于是任何口令都能通过.
"""

from __future__ import annotations

import pytest

from app.auth.passwords import PBKDF2_ITERATIONS, hash_password, verify_password


CORRECT_PASSWORD = "correct-horse-battery-staple"


def test_verify_password_accepts_matching_password() -> None:
    assert verify_password(CORRECT_PASSWORD, hash_password(CORRECT_PASSWORD)) is True


def test_verify_password_rejects_wrong_password() -> None:
    assert verify_password("wrong-horse-battery", hash_password(CORRECT_PASSWORD)) is False


def test_hash_password_uses_fresh_salt_each_time() -> None:
    assert hash_password(CORRECT_PASSWORD) != hash_password(CORRECT_PASSWORD)


def test_stored_hash_embeds_iteration_count() -> None:
    assert hash_password(CORRECT_PASSWORD).split("$")[1] == str(PBKDF2_ITERATIONS)


def test_stored_hash_is_ascii_safe_for_column_storage() -> None:
    assert hash_password(CORRECT_PASSWORD).isascii()


@pytest.mark.parametrize(
    "malformed_hash",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$260000$deadbeef",
        "pbkdf2_sha256$260000$deadbeef$",
        "pbkdf2_sha256$$deadbeef$" + "00" * 32,
        "pbkdf2_sha256$0$deadbeef$" + "00" * 32,
        "pbkdf2_sha256$99999999$deadbeef$" + "00" * 32,
        "pbkdf2_sha256$260000$$" + "00" * 32,
        "pbkdf2_sha256$260000$deadbeef$00ff",
        "pbkdf2_sha256$260000$zzzz$" + "00" * 32,
        "bcrypt$260000$deadbeef$" + "00" * 32,
    ],
)
def test_verify_password_rejects_malformed_stored_hash(malformed_hash: str) -> None:
    assert verify_password(CORRECT_PASSWORD, malformed_hash) is False
