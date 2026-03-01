"""Credential Store 单元测试 -- T023

覆盖: CRUD / filelock / 原子写入 / 文件权限 / 文件损坏恢复
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import (
    ApiKeyCredential,
    OAuthCredential,
    TokenCredential,
)
from octoagent.provider.auth.profile import CredentialStoreData, ProviderProfile
from octoagent.provider.auth.store import CredentialStore


def _make_profile(
    name: str = "test-profile",
    provider: str = "openrouter",
    is_default: bool = False,
) -> ProviderProfile:
    """辅助函数：创建测试 profile"""
    now = datetime.now(tz=timezone.utc)
    return ProviderProfile(
        name=name,
        provider=provider,
        auth_mode="api_key",
        credential=ApiKeyCredential(
            provider=provider,
            key=SecretStr("sk-or-v1-test"),
        ),
        is_default=is_default,
        created_at=now,
        updated_at=now,
    )


class TestCredentialStoreLoad:
    """load() 行为"""

    def test_load_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在时返回空 store"""
        store = CredentialStore(store_path=tmp_path / "nonexistent.json")
        data = store.load()
        assert data.version == 1
        assert data.profiles == {}

    def test_load_valid_file(self, tmp_path: Path) -> None:
        """加载有效 JSON 文件"""
        store_path = tmp_path / "store.json"
        store = CredentialStore(store_path=store_path)
        # 先写入
        profile = _make_profile()
        store.set_profile(profile)
        # 再读取
        data = store.load()
        assert "test-profile" in data.profiles

    def test_load_corrupted_file_recovers(self, tmp_path: Path) -> None:
        """文件损坏时备份并返回空 store (EC-2)"""
        store_path = tmp_path / "store.json"
        store_path.write_text("not valid json {{{", encoding="utf-8")
        store = CredentialStore(store_path=store_path)
        data = store.load()
        assert data.profiles == {}
        # 确认备份文件已创建
        backup = store_path.with_suffix(".json.corrupted")
        assert backup.exists()


class TestCredentialStoreSave:
    """save() 行为"""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        """保存时自动创建文件和目录"""
        store_path = tmp_path / "subdir" / "store.json"
        store = CredentialStore(store_path=store_path)
        store.save(CredentialStoreData())
        assert store_path.exists()

    def test_save_file_permission(self, tmp_path: Path) -> None:
        """保存后文件权限为 0o600"""
        store_path = tmp_path / "store.json"
        store = CredentialStore(store_path=store_path)
        store.save(CredentialStoreData())
        mode = stat.S_IMODE(os.stat(store_path).st_mode)
        assert mode == 0o600

    def test_save_atomic_write(self, tmp_path: Path) -> None:
        """原子写入：文件内容完整"""
        store_path = tmp_path / "store.json"
        store = CredentialStore(store_path=store_path)
        data = CredentialStoreData()
        data.profiles["test"] = _make_profile()
        store.save(data)
        # 验证 JSON 可解析
        raw = store_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["version"] == 1
        assert "test" in parsed["profiles"]


class TestCredentialStoreCRUD:
    """CRUD 操作"""

    def test_set_and_get_profile(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "store.json")
        profile = _make_profile(name="my-profile")
        store.set_profile(profile)
        result = store.get_profile("my-profile")
        assert result is not None
        assert result.name == "my-profile"

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "store.json")
        assert store.get_profile("nonexistent") is None

    def test_remove_profile(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "store.json")
        store.set_profile(_make_profile(name="to-remove"))
        assert store.remove_profile("to-remove") is True
        assert store.get_profile("to-remove") is None

    def test_remove_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "store.json")
        assert store.remove_profile("nonexistent") is False

    def test_get_default_profile(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "store.json")
        store.set_profile(_make_profile(name="non-default", is_default=False))
        store.set_profile(_make_profile(name="default", is_default=True))
        result = store.get_default_profile()
        assert result is not None
        assert result.name == "default"

    def test_get_default_profile_none(self, tmp_path: Path) -> None:
        """无默认 profile 时返回 None"""
        store = CredentialStore(store_path=tmp_path / "store.json")
        store.set_profile(_make_profile(name="non-default", is_default=False))
        assert store.get_default_profile() is None

    def test_list_profiles(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "store.json")
        store.set_profile(_make_profile(name="p1"))
        store.set_profile(_make_profile(name="p2"))
        profiles = store.list_profiles()
        assert len(profiles) == 2
        names = {p.name for p in profiles}
        assert names == {"p1", "p2"}

    def test_update_existing_profile(self, tmp_path: Path) -> None:
        """更新已有 profile"""
        store = CredentialStore(store_path=tmp_path / "store.json")
        store.set_profile(_make_profile(name="update-me", provider="openai"))
        store.set_profile(_make_profile(name="update-me", provider="anthropic"))
        result = store.get_profile("update-me")
        assert result is not None
        assert result.provider == "anthropic"


class TestProviderIdMigration:
    """Provider ID 迁移策略"""

    def test_oauth_openai_display_id_migrates_to_canonical(self, tmp_path: Path) -> None:
        """OAuth profile 中 openai -> openai-codex"""
        now = datetime.now(tz=timezone.utc)
        store = CredentialStore(store_path=tmp_path / "store.json")
        store.set_profile(
            ProviderProfile(
                name="openai-oauth",
                provider="openai",
                auth_mode="oauth",
                credential=OAuthCredential(
                    provider="openai",
                    access_token=SecretStr("access"),
                    expires_at=now,
                ),
                is_default=True,
                created_at=now,
                updated_at=now,
            )
        )

        loaded = store.get_default_profile()
        assert loaded is not None
        assert loaded.provider == "openai-codex"
        assert loaded.credential.provider == "openai-codex"

    def test_api_key_openai_provider_kept_as_openai(self, tmp_path: Path) -> None:
        """非 OAuth profile 不做 openai -> openai-codex 迁移"""
        store = CredentialStore(store_path=tmp_path / "store.json")
        store.set_profile(_make_profile(name="openai-api-key", provider="openai"))

        loaded = store.get_profile("openai-api-key")
        assert loaded is not None
        assert loaded.provider == "openai"
        assert loaded.credential.provider == "openai"
