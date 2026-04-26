"""Feature 081 P2：raw YAML legacy schema 检测单测（修 Codex F2）。

覆盖：
- v1 yaml（含 runtime.llm_mode）→ 触发 detection
- v1 yaml（仅 config_version 缺失）→ 触发 detection
- v1 yaml（providers[*] 用 auth_type/api_key_env/base_url）→ 触发 detection
- v2 yaml（已 migrate）→ 不触发
- 损坏的 raw（None / 非 dict）→ 安全降级返回空列表
"""

from __future__ import annotations

from octoagent.gateway.services.config.config_schema import (
    CONFIG_VERSION_CURRENT,
    detect_legacy_yaml_keys,
)


def test_detects_runtime_legacy_keys() -> None:
    raw = {
        "config_version": 1,
        "runtime": {
            "llm_mode": "litellm",
            "litellm_proxy_url": "http://localhost:4000",
            "master_key_env": "LITELLM_MASTER_KEY",
        },
        "providers": [],
    }
    keys = detect_legacy_yaml_keys(raw)
    assert "runtime.llm_mode" in keys
    assert "runtime.litellm_proxy_url" in keys
    assert "runtime.master_key_env" in keys
    assert any(k.startswith("config_version<") for k in keys)


def test_detects_provider_legacy_fields() -> None:
    raw = {
        "config_version": 2,  # 即使 version 是 2，provider 用旧字段也算 legacy
        "providers": [
            {"id": "p1", "name": "P1", "auth_type": "api_key", "api_key_env": "K1"},
            {"id": "p2", "name": "P2", "base_url": "https://x.com"},
        ],
    }
    keys = detect_legacy_yaml_keys(raw)
    assert "providers[0].auth_type" in keys
    assert "providers[0].api_key_env" in keys
    assert "providers[1].base_url" in keys


def test_v2_yaml_no_legacy_keys() -> None:
    raw = {
        "config_version": CONFIG_VERSION_CURRENT,
        "runtime": {},
        "providers": [
            {
                "id": "p1",
                "name": "P1",
                "transport": "openai_chat",
                "api_base": "https://api.example.com",
                "auth": {"kind": "api_key", "env": "K1"},
            }
        ],
    }
    assert detect_legacy_yaml_keys(raw) == []


def test_missing_config_version_treated_as_legacy() -> None:
    raw = {
        "runtime": {},
        "providers": [],
    }
    keys = detect_legacy_yaml_keys(raw)
    # 缺失 config_version 默认为 1，触发版本 legacy
    assert any(k.startswith("config_version<") for k in keys)


def test_invalid_raw_returns_empty() -> None:
    assert detect_legacy_yaml_keys(None) == []  # type: ignore[arg-type]
    assert detect_legacy_yaml_keys("not a dict") == []  # type: ignore[arg-type]
    assert detect_legacy_yaml_keys([]) == []  # type: ignore[arg-type]


def test_invalid_config_version_value() -> None:
    raw = {"config_version": "abc"}
    keys = detect_legacy_yaml_keys(raw)
    assert "config_version<invalid" in keys


def test_partial_v1_only_provider_legacy() -> None:
    """v2 config_version + 只有 provider 用旧字段 → 仅触发 provider legacy。"""
    raw = {
        "config_version": 2,
        "runtime": {},
        "providers": [{"id": "p1", "name": "P1", "auth_type": "oauth"}],
    }
    keys = detect_legacy_yaml_keys(raw)
    assert "providers[0].auth_type" in keys
    # runtime 没有 legacy keys
    assert not any(k.startswith("runtime.") for k in keys)
    # config_version 已是 v2
    assert not any(k.startswith("config_version<") for k in keys)
