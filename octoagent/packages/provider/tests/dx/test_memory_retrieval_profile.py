from __future__ import annotations

import warnings

from octoagent.memory import MemoryBackendState, MemoryBackendStatus
from octoagent.provider.dx.config_schema import (
    MemoryConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
)
from octoagent.provider.dx.memory_retrieval_profile import build_memory_retrieval_profile


def _make_config(*, providers: list[ProviderEntry], aliases: dict[str, ModelAlias], memory: MemoryConfig) -> OctoAgentConfig:
    return OctoAgentConfig(
        updated_at="2026-03-15",
        providers=providers,
        model_aliases=aliases,
        memory=memory,
    )


def test_profile_uses_builtin_engine_and_main_fallbacks_by_default() -> None:
    config = _make_config(
        providers=[
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
                enabled=True,
            )
        ],
        aliases={
            "main": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
            )
        },
        memory=MemoryConfig(),
    )

    profile = build_memory_retrieval_profile(
        config=config,
        backend_status=MemoryBackendStatus(
            backend_id="memu",
            state=MemoryBackendState.HEALTHY,
            active_backend="sqlite-metadata",
        ),
    )

    assert profile.engine_mode == "builtin"
    assert profile.engine_label == "内建记忆引擎"
    assert profile.transport == "builtin"
    assert profile.bindings[0].binding_key == "reasoning"
    assert profile.bindings[0].effective_target == "main"
    assert profile.bindings[0].status == "fallback"
    assert profile.bindings[2].binding_key == "embedding"
    assert profile.bindings[2].effective_target == "engine-default"
    assert profile.bindings[2].effective_label == "Qwen3-Embedding-0.6B（默认）"


def test_profile_marks_disabled_embedding_alias_as_misconfigured() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config = _make_config(
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                ),
                ProviderEntry(
                    id="embed-provider",
                    name="Embed Provider",
                    auth_type="api_key",
                    api_key_env="EMBED_PROVIDER_API_KEY",
                    enabled=False,
                ),
            ],
            aliases={
                "main": ModelAlias(
                    provider="openrouter",
                    model="openrouter/auto",
                ),
                "embed": ModelAlias(
                    provider="embed-provider",
                    model="embed-provider/text-embedding-3-small",
                ),
            },
            memory=MemoryConfig(
                embedding_model_alias="embed",
            ),
        )

    profile = build_memory_retrieval_profile(
        config=config,
        backend_status=MemoryBackendStatus(
            backend_id="memu",
            state=MemoryBackendState.HEALTHY,
            active_backend="sqlite-metadata",
        ),
    )

    embedding = next(item for item in profile.bindings if item.binding_key == "embedding")
    assert profile.engine_mode == "builtin"
    assert embedding.status == "misconfigured"
    assert embedding.configured_alias == "embed"
    assert embedding.effective_target == "engine-default"
    assert embedding.effective_label == "Qwen3-Embedding-0.6B（默认）"
    assert "Provider 当前已停用" in embedding.warnings[0]


def test_profile_always_builtin_even_when_config_is_none() -> None:
    """配置缺失时仍然返回 builtin 引擎模式。"""
    profile = build_memory_retrieval_profile(
        config=None,
        backend_status=MemoryBackendStatus(
            backend_id="memu",
            state=MemoryBackendState.HEALTHY,
            active_backend="memu",
        ),
    )

    assert profile.engine_mode == "builtin"
    assert profile.transport == "builtin"
    embedding = next(item for item in profile.bindings if item.binding_key == "embedding")
    assert embedding.effective_target == "engine-default"


def test_profile_keeps_active_embedding_until_cutover_finishes() -> None:
    config = _make_config(
        providers=[
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
                enabled=True,
            )
        ],
        aliases={
            "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
            "knowledge-embed": ModelAlias(
                provider="openrouter",
                model="openai/text-embedding-3-small",
            ),
        },
        memory=MemoryConfig(embedding_model_alias="knowledge-embed"),
    )

    profile = build_memory_retrieval_profile(
        config=config,
        backend_status=MemoryBackendStatus(
            backend_id="sqlite-metadata",
            state=MemoryBackendState.HEALTHY,
            active_backend="sqlite-metadata",
        ),
        active_embedding_target="sqlite-metadata",
        requested_embedding_target="knowledge-embed",
    )

    embedding = next(item for item in profile.bindings if item.binding_key == "embedding")
    assert embedding.effective_target == "sqlite-metadata"
    assert embedding.status == "active_generation"
    assert "迁移尚未 cutover" in embedding.warnings[0]
