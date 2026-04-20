"""Feature 079 Phase 4 —— auth-profiles ↔ octoagent.yaml drift 检测。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import SecretStr

from octoagent.gateway.services.config.drift_check import (
    DriftRecord,
    detect_auth_config_drift,
)
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore


def _write_config(project_root: Path, content: str) -> None:
    (project_root / "octoagent.yaml").write_text(content, encoding="utf-8")


def _seed_oauth_profile(
    store: CredentialStore,
    *,
    provider: str = "openai-codex",
    name: str = "openai-codex-default",
) -> None:
    now = datetime.now(tz=UTC)
    cred = OAuthCredential(
        provider=provider,
        access_token=SecretStr("tok"),
        refresh_token=SecretStr("rt"),
        expires_at=now + timedelta(hours=8),
        account_id="acc-x",
    )
    store.set_profile(
        ProviderProfile(
            name=name,
            provider=provider,
            auth_mode="oauth",
            credential=cred,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
    )


def test_oauth_profile_not_in_config_reported(tmp_path: Path) -> None:
    """auth-profiles 有 openai-codex 凭证，但 octoagent.yaml 没启用该 provider。"""
    _write_config(
        tmp_path,
        """config_version: 1
updated_at: "2026-04-20"
providers:
  - id: siliconflow
    name: SiliconFlow
    auth_type: api_key
    api_key_env: SILICONFLOW_API_KEY
    enabled: true
model_aliases:
  main:
    provider: siliconflow
    model: Qwen/Qwen3.5-32B
""",
    )
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_oauth_profile(store)

    records = detect_auth_config_drift(tmp_path, credential_store=store)
    assert len(records) >= 1
    drift_types = {r.drift_type for r in records}
    assert "oauth_profile_not_in_config" in drift_types
    [item] = [r for r in records if r.drift_type == "oauth_profile_not_in_config"]
    assert item.provider == "openai-codex"
    assert item.severity == "high"
    assert "openai-codex" in item.summary
    assert item.details.get("profile_name") == "openai-codex-default"


def test_config_provider_no_credential_reported(tmp_path: Path) -> None:
    """octoagent.yaml 声明 openai-codex OAuth，但 auth-profiles 为空。"""
    _write_config(
        tmp_path,
        """config_version: 1
updated_at: "2026-04-20"
providers:
  - id: openai-codex
    name: OpenAI Codex
    auth_type: oauth
    api_key_env: OPENAI_API_KEY
    enabled: true
model_aliases:
  main:
    provider: openai-codex
    model: gpt-5.4
""",
    )
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")

    records = detect_auth_config_drift(tmp_path, credential_store=store)
    types = {r.drift_type for r in records}
    assert "config_provider_no_credential" in types


def test_alias_provider_disabled_differentiated(tmp_path: Path) -> None:
    """provider 在 list 但 enabled=false → 归为 disabled_or_missing。"""
    _write_config(
        tmp_path,
        """config_version: 1
updated_at: "2026-04-20"
providers:
  - id: siliconflow
    name: SiliconFlow
    auth_type: api_key
    api_key_env: SILICONFLOW_API_KEY
    enabled: true
  - id: openai-codex
    name: OpenAI Codex
    auth_type: oauth
    api_key_env: OPENAI_API_KEY
    enabled: false
model_aliases:
  main:
    provider: openai-codex
    model: gpt-5.4
""",
    )
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    records = detect_auth_config_drift(tmp_path, credential_store=store)
    types = {r.drift_type for r in records}
    assert "alias_provider_disabled_or_missing" in types


def test_clean_config_returns_empty(tmp_path: Path) -> None:
    """auth-profiles 与 octoagent.yaml 完全对齐 → 无 drift。"""
    _write_config(
        tmp_path,
        """config_version: 1
updated_at: "2026-04-20"
providers:
  - id: openai-codex
    name: OpenAI Codex
    auth_type: oauth
    api_key_env: OPENAI_API_KEY
    enabled: true
model_aliases:
  main:
    provider: openai-codex
    model: gpt-5.4
""",
    )
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_oauth_profile(store)

    records = detect_auth_config_drift(tmp_path, credential_store=store)
    assert records == []


def test_drift_record_to_payload_structure() -> None:
    """DriftRecord.to_payload 返回诊断 API 需要的字段结构。"""
    record = DriftRecord(
        drift_type="oauth_profile_not_in_config",
        severity="high",
        provider="openai-codex",
        summary="test",
        recommended_action="do something",
        details={"profile_name": "p-x"},
    )
    payload = record.to_payload()
    assert payload["drift_type"] == "oauth_profile_not_in_config"
    assert payload["provider"] == "openai-codex"
    assert payload["details"] == {"profile_name": "p-x"}
