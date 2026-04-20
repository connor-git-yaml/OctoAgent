"""Codex CLI 外挂凭证桥 -- Feature 078 Phase 2

当 OctoAgent 的 PkceOAuthAdapter.refresh() 失败（invalid_grant / 网络问题），
作为最后一根稻草尝试从用户已有的 Codex CLI 凭证文件（~/.codex/auth.json）
adopt access_token + refresh_token，避免用户重新走完整的 OAuth flow。

设计原则：
- 只读、无副作用：不会修改 ~/.codex/ 下任何文件
- 身份 gate：通过 ``_is_safe_to_adopt`` 比对 account_id，跨账号不接管
- 仅在 refresh 失败时触发：不是每次调用都 stat 外部文件
- 权限位检查：宽于 0o600 的 auth.json 会被拒绝（防止从共享机器拿到别人的 token）
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import SecretStr

from .credentials import OAuthCredential

log = structlog.get_logger()

_CODEX_AUTH_RELATIVE = Path(".codex") / "auth.json"


def read_codex_cli_auth(home_override: Path | None = None) -> OAuthCredential | None:
    """读取 ``~/.codex/auth.json``，构造 OAuthCredential。

    Returns:
        OAuthCredential 实例；文件不存在 / 权限过宽 / 解析失败 / 缺关键字段
        都返回 None（不抛异常，让上游走正常 fallback）
    """
    home = home_override if home_override is not None else Path.home()
    auth_path = home / _CODEX_AUTH_RELATIVE
    if not auth_path.is_file():
        return None

    # 权限位守门：严格要求 0o600 或更严（没有 group / other 权限位）
    try:
        mode = auth_path.stat().st_mode & 0o777
    except OSError:
        return None
    if mode & 0o077:
        log.warning(
            "codex_cli_auth_permissive_perm",
            path=str(auth_path),
            mode=oct(mode),
        )
        return None

    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "codex_cli_auth_read_failed",
            path=str(auth_path),
            error_type=type(exc).__name__,
        )
        return None

    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        return None

    access_token = str(tokens.get("access_token") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        return None

    expires_at = _extract_exp_from_jwt(access_token)
    if expires_at is None:
        # 解不出 exp 就不接管 —— 避免后续 is_expired 判断基础不稳
        log.warning("codex_cli_auth_jwt_exp_missing", path=str(auth_path))
        return None

    account_id = (
        str(tokens.get("account_id") or "").strip()
        or _extract_account_id_from_jwt(access_token)
    )

    return OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr(access_token),
        refresh_token=SecretStr(refresh_token),
        expires_at=expires_at,
        account_id=account_id or None,
    )


def _is_safe_to_adopt(
    *,
    existing: OAuthCredential | None,
    incoming: OAuthCredential,
) -> tuple[bool, str]:
    """身份 gate：判断能否把 incoming 覆盖到 existing。

    规则：
    - existing 为空 / 没有 account_id：允许接管，reason=no_identity_to_compare
    - 两边 account_id 都有且一致：允许，reason=account_match
    - account_id 不一致：拒绝，reason=account_mismatch（防止跨账号误接管）
    - incoming 没有 account_id：允许但记录，reason=incoming_identity_unknown

    Returns:
        (allowed, reason)
    """
    incoming_acct = (incoming.account_id or "").strip()
    if existing is None:
        return (True, "no_existing_credential")

    existing_acct = (existing.account_id or "").strip()
    if not existing_acct:
        return (True, "no_identity_to_compare")
    if not incoming_acct:
        return (True, "incoming_identity_unknown")
    if existing_acct != incoming_acct:
        return (False, "account_mismatch")
    return (True, "account_match")


# ─────────────────────────── JWT 解析 helpers ───────────────────────────


def _decode_jwt_payload(token: str) -> dict | None:
    """解码 JWT 的 payload 段（不校验签名 —— 我们只是读元信息）。"""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        # base64 urlsafe，需要补齐 padding
        padding = "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def _extract_exp_from_jwt(token: str) -> datetime | None:
    payload = _decode_jwt_payload(token)
    if not payload:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(exp), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _extract_account_id_from_jwt(token: str) -> str:
    """从 Codex OAuth JWT 读 chatgpt_account_id（位于自定义 claim 中）。"""
    payload = _decode_jwt_payload(token)
    if not payload:
        return ""
    auth_claim = payload.get("https://api.openai.com/auth") or {}
    if isinstance(auth_claim, dict):
        acct = auth_claim.get("chatgpt_account_id")
        if isinstance(acct, str):
            return acct
    return ""
