"""访问令牌的签发与校验.

覆盖三类必须拒绝的令牌: 换密钥签的、载荷被改过的、头部声明不受支持的算法的.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from app.auth.tokens import (
    ACCESS_TOKEN_KIND,
    TOKEN_ALGORITHM,
    TOKEN_TYPE,
    _sign,
    create_access_token,
    decode_access_token,
)
from app.catalog.enums import UserType


SECRET_KEY = "unit-test-signing-key"
OTHER_SECRET_KEY = "another-unit-test-signing-key"
EXPIRES_IN_MINUTES = 30
USER_ID = "user-identifier"
USERNAME = "member-one"


def _encode_segment(segment: dict[str, object]) -> str:
    serialized = json.dumps(segment, separators=(",", ":"))

    return base64.urlsafe_b64encode(serialized.encode("utf-8")).rstrip(b"=").decode("ascii")


def _decode_segment(segment: str) -> dict[str, object]:
    padding = "=" * (-len(segment) % 4)

    return json.loads(base64.urlsafe_b64decode(segment + padding))


def _issue(expires_in_minutes: int = EXPIRES_IN_MINUTES) -> str:
    return create_access_token(
        user_id=USER_ID,
        username=USERNAME,
        user_type=UserType.USER.value,
        secret_key=SECRET_KEY,
        expires_in_minutes=expires_in_minutes,
    )


def test_decode_returns_claims_for_valid_token() -> None:
    claims = decode_access_token(_issue(), SECRET_KEY)

    assert claims is not None
    assert claims.user_id == USER_ID
    assert claims.username == USERNAME
    assert claims.user_type == UserType.USER.value
    assert claims.token_id
    assert claims.expires_at == claims.issued_at + EXPIRES_IN_MINUTES * 60


def test_issued_token_carries_expected_header_and_kind() -> None:
    header, payload, _ = _issue().split(".")

    assert _decode_segment(header) == {"alg": TOKEN_ALGORITHM, "typ": TOKEN_TYPE}
    assert _decode_segment(payload)["type"] == ACCESS_TOKEN_KIND


def test_decode_rejects_token_signed_with_another_key() -> None:
    assert decode_access_token(_issue(), OTHER_SECRET_KEY) is None


def test_decode_rejects_tampered_subject() -> None:
    header, payload, signature = _issue().split(".")

    tampered_payload = {**_decode_segment(payload), "sub": "somebody-else"}

    assert decode_access_token(f"{header}.{_encode_segment(tampered_payload)}.{signature}", SECRET_KEY) is None


def test_decode_rejects_unsigned_algorithm() -> None:
    _, payload, _ = _issue().split(".")

    unsigned_header = _encode_segment({"alg": "none", "typ": TOKEN_TYPE})

    assert decode_access_token(f"{unsigned_header}.{payload}.", SECRET_KEY) is None


def test_decode_rejects_token_whose_kind_is_not_access() -> None:
    issued_at = int(time.time())
    header = _encode_segment({"alg": TOKEN_ALGORITHM, "typ": TOKEN_TYPE})
    payload = _encode_segment(
        {
            "exp": issued_at + 600,
            "iat": issued_at,
            "jti": "token-identifier",
            "sub": USER_ID,
            "type": "refresh",
            "user_type": UserType.USER.value,
            "username": USERNAME,
        }
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = base64.urlsafe_b64encode(_sign(signing_input, SECRET_KEY)).rstrip(b"=").decode("ascii")

    assert decode_access_token(f"{header}.{payload}.{signature}", SECRET_KEY) is None


def test_decode_rejects_expired_token() -> None:
    assert decode_access_token(_issue(expires_in_minutes=-1), SECRET_KEY) is None


@pytest.mark.parametrize(
    "malformed_token",
    ["", "a", "a.b", "a.b.c.d", "not.base64!.signature", "a.b.c"],
)
def test_decode_rejects_malformed_token(malformed_token: str) -> None:
    assert decode_access_token(malformed_token, SECRET_KEY) is None
