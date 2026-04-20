"""Codex CLI bridge -- Feature 078 Phase 2

验证：
- read_codex_cli_auth 正常解析 ~/.codex/auth.json
- 文件不存在 / 权限过宽 / 损坏 / 缺字段均返回 None（不抛）
- JWT exp / account_id 提取
- _is_safe_to_adopt 身份 gate 规则
"""

from __future__ import annotations

import base64
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import SecretStr

from octoagent.provider.auth.codex_cli_bridge import (
    _extract_account_id_from_jwt,
    _extract_exp_from_jwt,
    _is_safe_to_adopt,
    read_codex_cli_auth,
)
from octoagent.provider.auth.credentials import OAuthCredential


def _make_jwt(*, exp: int, account_id: str = "acc-123") -> str:
    """构造一个合法 shape 的 JWT（签名随便填，只测 payload 解析）。"""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=")
    payload = {
        "exp": exp,
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return b".".join([header, body, b"signature"]).decode()


def _write_codex_auth(
    home: Path,
    *,
    access_token: str = "at",
    refresh_token: str = "rt",
    account_id: str = "acc-123",
    mode: int = 0o600,
) -> Path:
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    auth_path = codex_dir / "auth.json"
    payload = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": "id-xxx",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
        },
        "last_refresh": "2026-04-19T13:37:49Z",
    }
    auth_path.write_text(json.dumps(payload))
    os.chmod(auth_path, mode)
    return auth_path


# ─────────────────────────── read_codex_cli_auth ─────────────────────────


def test_read_success_with_valid_jwt(tmp_path: Path) -> None:
    future = int((datetime.now(tz=UTC) + timedelta(days=10)).timestamp())
    _write_codex_auth(tmp_path, access_token=_make_jwt(exp=future))

    cred = read_codex_cli_auth(home_override=tmp_path)

    assert cred is not None
    assert cred.provider == "openai-codex"
    assert cred.refresh_token.get_secret_value() == "rt"
    assert cred.account_id == "acc-123"
    assert cred.expires_at.timestamp() == future


def test_read_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert read_codex_cli_auth(home_override=tmp_path) is None


def test_read_returns_none_when_permission_too_wide(tmp_path: Path) -> None:
    future = int((datetime.now(tz=UTC) + timedelta(days=1)).timestamp())
    _write_codex_auth(tmp_path, access_token=_make_jwt(exp=future), mode=0o644)
    assert read_codex_cli_auth(home_override=tmp_path) is None


def test_read_returns_none_when_json_malformed(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("not json {")
    os.chmod(codex_dir / "auth.json", 0o600)
    assert read_codex_cli_auth(home_override=tmp_path) is None


def test_read_returns_none_when_tokens_missing(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    auth_path = codex_dir / "auth.json"
    auth_path.write_text(json.dumps({"auth_mode": "chatgpt"}))
    os.chmod(auth_path, 0o600)
    assert read_codex_cli_auth(home_override=tmp_path) is None


def test_read_returns_none_when_access_token_empty(tmp_path: Path) -> None:
    _write_codex_auth(tmp_path, access_token="")
    assert read_codex_cli_auth(home_override=tmp_path) is None


def test_read_returns_none_when_jwt_exp_unreadable(tmp_path: Path) -> None:
    _write_codex_auth(tmp_path, access_token="not.a.valid.jwt")
    assert read_codex_cli_auth(home_override=tmp_path) is None


# ───────────────────────────── JWT helpers ──────────────────────────────


def test_extract_exp_from_jwt() -> None:
    exp_ts = 1_800_000_000
    token = _make_jwt(exp=exp_ts)
    result = _extract_exp_from_jwt(token)
    assert result is not None
    assert int(result.timestamp()) == exp_ts
    assert result.tzinfo is UTC


def test_extract_account_id_from_jwt() -> None:
    token = _make_jwt(exp=1_800_000_000, account_id="e974eefd-xxx")
    assert _extract_account_id_from_jwt(token) == "e974eefd-xxx"


def test_extract_exp_from_invalid_jwt_returns_none() -> None:
    assert _extract_exp_from_jwt("bogus") is None


# ────────────────────────── _is_safe_to_adopt ────────────────────────────


def _mk_cred(acct: str | None) -> OAuthCredential:
    return OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("at"),
        refresh_token=SecretStr("rt"),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        account_id=acct,
    )


def test_safe_to_adopt_no_existing() -> None:
    allowed, reason = _is_safe_to_adopt(existing=None, incoming=_mk_cred("acc-1"))
    assert allowed is True
    assert reason == "no_existing_credential"


def test_safe_to_adopt_account_match() -> None:
    allowed, reason = _is_safe_to_adopt(
        existing=_mk_cred("acc-1"), incoming=_mk_cred("acc-1"),
    )
    assert allowed is True
    assert reason == "account_match"


def test_safe_to_adopt_account_mismatch() -> None:
    allowed, reason = _is_safe_to_adopt(
        existing=_mk_cred("acc-1"), incoming=_mk_cred("acc-2"),
    )
    assert allowed is False
    assert reason == "account_mismatch"


def test_safe_to_adopt_when_existing_has_no_identity() -> None:
    allowed, reason = _is_safe_to_adopt(
        existing=_mk_cred(None), incoming=_mk_cred("acc-1"),
    )
    assert allowed is True
    assert reason == "no_identity_to_compare"


def test_safe_to_adopt_when_incoming_has_no_identity() -> None:
    allowed, reason = _is_safe_to_adopt(
        existing=_mk_cred("acc-1"), incoming=_mk_cred(None),
    )
    assert allowed is True
    assert reason == "incoming_identity_unknown"
