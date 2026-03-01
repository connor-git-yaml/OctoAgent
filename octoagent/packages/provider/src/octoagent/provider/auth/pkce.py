"""PKCE 生成器 -- 对齐 contracts/auth-oauth-pkce-api.md SS1, FR-001

实现 RFC 7636 PKCE (Proof Key for Code Exchange):
- code_verifier: secrets.token_urlsafe(32), 生成 43 字符, 256 bit 熵
- code_challenge: SHA256(verifier) -> base64url 编码（无 padding）
- code_challenge_method: 始终为 S256

安全约束:
- PkcePair 实例 MUST NOT 被序列化、持久化或写入日志
- code_verifier 和 state 生成后不得出现在日志、Event Store、任何持久化存储中
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PkcePair:
    """PKCE 密钥对 -- 临时值，仅在 OAuth 流程期间存在于内存中

    code_verifier: 43 字符, 256 bit 熵
    code_challenge: SHA256(code_verifier) 的 base64url 编码（无 padding）
    """

    code_verifier: str
    code_challenge: str


def generate_pkce() -> PkcePair:
    """生成 PKCE code_verifier 和 code_challenge (S256)

    符合 RFC 7636:
    - code_verifier: 43 字符, 256 bit 熵 (secrets.token_urlsafe(32))
    - code_challenge: SHA256(verifier) -> base64url 编码（无 padding）
    - code_challenge_method: S256

    Returns:
        PkcePair 实例
    """
    verifier = secrets.token_urlsafe(32)  # 生成 43 字符
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return PkcePair(code_verifier=verifier, code_challenge=challenge)


def generate_state() -> str:
    """生成独立的 OAuth state 参数（CSRF 防护）

    使用独立随机值，不复用 code_verifier。
    与 OAuth 流程生命周期绑定，超时后自动失效。

    Returns:
        32 字节的 URL-safe base64 编码字符串（43 字符）
    """
    return secrets.token_urlsafe(32)
