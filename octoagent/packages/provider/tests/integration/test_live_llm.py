"""集成测试 -- Feature 014 -- 完整配置生成流程

使用 pytest.mark.skipif 在无 API Key 时跳过。
测试内容：
  - 构造 octoagent.yaml → 生成 litellm-config.yaml → 验证文件格式正确
  - （有 API Key 时）调用真实 LLM cheap 别名，验证返回有效响应（SC-004）

运行条件：
  - 基础测试：无需 API Key，验证配置文件生成格式
  - 实时测试：需要 OPENROUTER_API_KEY 或 ANTHROPIC_API_KEY 环境变量
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from octoagent.provider.dx.config_schema import (
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
)
from octoagent.provider.dx.config_wizard import load_config, save_config
from octoagent.provider.dx.litellm_generator import (
    GENERATED_MARKER,
    check_litellm_sync_status,
    generate_litellm_config,
)

# 环境变量检测
HAS_OPENROUTER_KEY = bool(os.environ.get("OPENROUTER_API_KEY"))
HAS_ANTHROPIC_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
HAS_ANY_KEY = HAS_OPENROUTER_KEY or HAS_ANTHROPIC_KEY


# ---------------------------------------------------------------------------
# 完整流程测试（不需要 API Key，仅验证配置文件格式）
# ---------------------------------------------------------------------------


def test_full_config_generation_openrouter(tmp_path: Path) -> None:
    """完整流程：构造 octoagent.yaml → 生成 litellm-config.yaml → 验证格式正确"""
    # 1. 构造配置
    config = OctoAgentConfig(
        updated_at="2026-03-04",
        providers=[
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
                enabled=True,
            )
        ],
        model_aliases={
            "main": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
                description="主力模型",
            ),
            "cheap": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
                description="低成本模型",
            ),
        },
        runtime=RuntimeConfig(llm_mode="litellm"),
    )

    # 2. 保存 octoagent.yaml
    save_config(config, tmp_path)
    assert (tmp_path / "octoagent.yaml").exists()

    # 3. 重新加载（往返完整性）
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded.providers[0].id == "openrouter"
    assert "main" in loaded.model_aliases
    assert "cheap" in loaded.model_aliases

    # 4. 生成 litellm-config.yaml
    litellm_path = generate_litellm_config(loaded, tmp_path)
    assert litellm_path.exists()

    # 5. 验证生成文件格式
    content = litellm_path.read_text(encoding="utf-8")
    assert GENERATED_MARKER in content

    data = yaml.safe_load(content)
    assert isinstance(data, dict)
    assert "model_list" in data
    assert "general_settings" in data

    # 验证 model_list 条目数
    model_list = data["model_list"]
    assert len(model_list) == 2  # main + cheap

    # 验证 api_key 格式为 os.environ/... 非明文
    for entry in model_list:
        api_key = entry["litellm_params"]["api_key"]
        assert api_key == "os.environ/OPENROUTER_API_KEY", f"api_key 应为引用格式: {api_key}"

    # 验证 master_key 格式
    master_key = data["general_settings"]["master_key"]
    assert master_key == "os.environ/LITELLM_MASTER_KEY"

    # 6. 验证同步状态
    in_sync, diffs = check_litellm_sync_status(loaded, tmp_path)
    assert in_sync is True, f"同步检测失败：{diffs}"


def test_full_config_generation_multi_provider(tmp_path: Path) -> None:
    """多 Provider 完整流程：验证每个 alias 路由到正确 Provider"""
    config = OctoAgentConfig(
        updated_at="2026-03-04",
        providers=[
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
                enabled=True,
            ),
            ProviderEntry(
                id="anthropic",
                name="Anthropic",
                auth_type="api_key",
                api_key_env="ANTHROPIC_API_KEY",
                enabled=True,
            ),
        ],
        model_aliases={
            "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
            "claude": ModelAlias(provider="anthropic", model="claude-opus-4-20250514"),
        },
    )

    save_config(config, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded is not None

    litellm_path = generate_litellm_config(loaded, tmp_path)
    data = yaml.safe_load(litellm_path.read_text(encoding="utf-8"))

    model_api_keys = {
        m["model_name"]: m["litellm_params"]["api_key"]
        for m in data["model_list"]
    }

    assert model_api_keys["main"] == "os.environ/OPENROUTER_API_KEY"
    assert model_api_keys["claude"] == "os.environ/ANTHROPIC_API_KEY"

    # 验证同步一致性
    in_sync, diffs = check_litellm_sync_status(loaded, tmp_path)
    assert in_sync is True, f"多 Provider 同步检测失败：{diffs}"


def test_config_disabled_provider_excluded(tmp_path: Path) -> None:
    """disabled Provider 不出现在 litellm-config.yaml 中"""
    import warnings

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        config = OctoAgentConfig(
            updated_at="2026-03-04",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                ),
                ProviderEntry(
                    id="anthropic",
                    name="Anthropic",
                    auth_type="api_key",
                    api_key_env="ANTHROPIC_API_KEY",
                    enabled=False,
                ),
            ],
            model_aliases={
                "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
                "claude": ModelAlias(provider="anthropic", model="claude-opus-4-20250514"),
            },
        )

    litellm_path = generate_litellm_config(config, tmp_path)
    data = yaml.safe_load(litellm_path.read_text(encoding="utf-8"))

    model_names = [m["model_name"] for m in data["model_list"]]
    assert "main" in model_names
    assert "claude" not in model_names  # disabled provider 排除


# ---------------------------------------------------------------------------
# 实时 LLM 测试（需要 API Key）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_OPENROUTER_KEY, reason="需要 OPENROUTER_API_KEY 环境变量")
def test_live_llm_openrouter_config_valid(tmp_path: Path) -> None:
    """有 OPENROUTER_API_KEY 时：验证生成的配置完整性（不启动 Proxy，仅验证配置）

    此测试验证真实 API Key 环境下的配置生成流程是否正确。
    不实际启动 LiteLLM Proxy，不发起 LLM 调用。
    """
    config = OctoAgentConfig(
        updated_at="2026-03-04",
        providers=[
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
                enabled=True,
            )
        ],
        model_aliases={
            "cheap": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
                description="低成本模型",
            ),
        },
    )

    litellm_path = generate_litellm_config(config, tmp_path)
    data = yaml.safe_load(litellm_path.read_text(encoding="utf-8"))

    # 验证配置格式完整性（SC-004）
    assert len(data["model_list"]) == 1
    cheap_entry = data["model_list"][0]
    assert cheap_entry["model_name"] == "cheap"
    assert cheap_entry["litellm_params"]["api_key"] == "os.environ/OPENROUTER_API_KEY"
    assert cheap_entry["litellm_params"]["model"] == "openrouter/auto"

    # 验证同步状态
    in_sync, diffs = check_litellm_sync_status(config, tmp_path)
    assert in_sync is True


@pytest.mark.skipif(not HAS_ANTHROPIC_KEY, reason="需要 ANTHROPIC_API_KEY 环境变量")
def test_live_llm_anthropic_config_valid(tmp_path: Path) -> None:
    """有 ANTHROPIC_API_KEY 时：验证 Anthropic 配置生成完整性"""
    config = OctoAgentConfig(
        updated_at="2026-03-04",
        providers=[
            ProviderEntry(
                id="anthropic",
                name="Anthropic",
                auth_type="api_key",
                api_key_env="ANTHROPIC_API_KEY",
                enabled=True,
            )
        ],
        model_aliases={
            "main": ModelAlias(
                provider="anthropic",
                model="claude-opus-4-20250514",
                description="主力模型",
            ),
        },
    )

    litellm_path = generate_litellm_config(config, tmp_path)
    data = yaml.safe_load(litellm_path.read_text(encoding="utf-8"))

    assert len(data["model_list"]) == 1
    main_entry = data["model_list"][0]
    assert main_entry["model_name"] == "main"
    assert main_entry["litellm_params"]["api_key"] == "os.environ/ANTHROPIC_API_KEY"
