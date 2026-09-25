"""访问令牌的签发与校验.

手写 HS256, 不引第三方 JWT 库. 校验时显式要求头部 alg 为 HS256、typ 为 JWT 且载荷
type 为 access: 不校验 alg 的话, 攻击者可把头部改成 "alg":"none" 或换成非对称算法,
令签名校验形同虚设.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from ..catalog.enums import UserType


TOKEN_ALGORITHM = "HS256"
TOKEN_TYPE = "JWT"
ACCESS_TOKEN_KIND = "access"
SECONDS_PER_MINUTE = 60
TOKEN_SEGMENT_COUNT = 3
BASE64_PADDING = "="


@dataclass(frozen=True)
class AccessTokenClaims:
    """已通过签名与有效期校验的访问令牌载荷."""

    user_id: str
    username: str
    user_type: str
    token_id: str
    issued_at: int
    expires_at: int


def _base64url_encode(raw: bytes) -> str:
    """URL 安全的 base64 编码, 去掉尾部填充."""

    return base64.urlsafe_b64encode(raw).rstrip(BASE64_PADDING.encode("ascii")).decode("ascii")


def _base64url_decode(encoded: str) -> bytes:
    """补回被去掉的填充后再解码."""

    remaining = len(encoded) % 4
    padding = BASE64_PADDING * (4 - remaining) if remaining else ""

    return base64.urlsafe_b64decode(encoded + padding)


def _sign(signing_input: bytes, secret_key: str) -> bytes:
    """以 HMAC-SHA256 对 `头部.载荷` 签名."""

    return hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()


def _encode_segment(segment: dict[str, object]) -> str:
    """把字典编成紧凑 JSON 段. 紧凑分隔符使同一载荷产出稳定字节, 签名可复现."""

    serialized = json.dumps(segment, separators=(",", ":"), sort_keys=True)

    return _base64url_encode(serialized.encode("utf-8"))


def create_access_token(
    user_id: str,
    username: str,
    user_type: str,
    secret_key: str,
    expires_in_minutes: int,
) -> str:
    """签发访问令牌."""

    issued_at = int(time.time())
    header = {"alg": TOKEN_ALGORITHM, "typ": TOKEN_TYPE}
    payload = {
        "exp": issued_at + expires_in_minutes * SECONDS_PER_MINUTE,
        "iat": issued_at,
        "jti": uuid.uuid4().hex,
        "sub": user_id,
        "type": ACCESS_TOKEN_KIND,
        "user_type": user_type,
        "username": username,
    }

    signing_input = f"{_encode_segment(header)}.{_encode_segment(payload)}".encode("ascii")
    signature = _base64url_encode(_sign(signing_input, secret_key))

    return f"{signing_input.decode('ascii')}.{signature}"


def decode_access_token(token: str, secret_key: str) -> AccessTokenClaims | None:
    """校验并解出载荷. 任何一处不合法都返回 None, 不向调用方区分失败原因."""

    segments = token.split(".")

    if len(segments) != TOKEN_SEGMENT_COUNT:
        return None

    encoded_header, encoded_payload, encoded_signature = segments

    try:
        header = json.loads(_base64url_decode(encoded_header))
        payload = json.loads(_base64url_decode(encoded_payload))
        provided_signature = _base64url_decode(encoded_signature)
    except (ValueError, TypeError):
        return None

    if not isinstance(header, dict) or not isinstance(payload, dict):
        return None

    if header.get("alg") != TOKEN_ALGORITHM or header.get("typ") != TOKEN_TYPE:
        return None

    if payload.get("type") != ACCESS_TOKEN_KIND:
        return None

    expected_signature = _sign(f"{encoded_header}.{encoded_payload}".encode("ascii"), secret_key)

    if not hmac.compare_digest(provided_signature, expected_signature):
        return None

    expires_at = payload.get("exp")
    issued_at = payload.get("iat")

    if not isinstance(expires_at, int) or not isinstance(issued_at, int):
        return None

    if time.time() > expires_at:
        return None

    user_id = payload.get("sub")
    username = payload.get("username")
    user_type = payload.get("user_type")
    token_id = payload.get("jti")

    if not isinstance(user_id, str) or not user_id:
        return None

    if not isinstance(username, str) or not isinstance(user_type, str):
        return None

    if not isinstance(token_id, str) or user_type not in set(UserType):
        return None

    return AccessTokenClaims(
        user_id=user_id,
        username=username,
        user_type=user_type,
        token_id=token_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
