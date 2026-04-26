"""Feature 080 Phase 3：ProviderModelClient 集成测试。

覆盖 SkillRunner 调用 ProviderModelClient 的端到端路径：
- 第一次 generate 创建 history（system + user）
- 第二次 generate 从 history 复用 + 累加 tool_result feedback
- task_scope 锁定（同 task 改 yaml 不切 provider）
- 不同 task_scope 之间互不影响
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.provider_router import ProviderRouter
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
)
from octoagent.skills.provider_model_client import ProviderModelClient


class _EchoInput(BaseModel):
    text: str = ""


class _EchoOutput(BaseModel):
    response: str = ""


def _write_config(project_root: Path, content: str) -> None:
    (project_root / "octoagent.yaml").write_text(textwrap.dedent(content), encoding="utf-8")


def _make_manifest(model_alias: str = "main") -> SkillManifest:
    return SkillManifest(
        skill_id="test.echo",
        version="0.1.0",
        input_model=_EchoInput,
        output_model=_EchoOutput,
        model_alias=model_alias,
        tools_allowed=[],
    )


def _make_ctx(task_id: str = "task-1", trace_id: str = "trace-1") -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id=task_id,
        trace_id=trace_id,
        conversation_messages=[],
        metadata={},
    )


@pytest.mark.asyncio
async def test_generate_initial_creates_history_and_calls_provider(tmp_path: Path) -> None:
    """第一次 generate 创建 (system + user) history，并调用 router.resolve_for_alias 路由到 provider。"""
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
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
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        # mock ProviderClient.call 不真发 HTTP
        async def _fake_call(**kwargs: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
            return ("Echoed: hi", [], {"token_usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})

        client = ProviderModelClient(provider_router=router, tool_broker=None)
        manifest = _make_manifest()
        # 直接 patch ProviderClient.call 触发的内部链
        from octoagent.provider import ProviderClient

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(ProviderClient, "call", AsyncMock(side_effect=_fake_call))
            envelope = await client.generate(
                manifest=manifest,
                execution_context=_make_ctx(),
                prompt="hi",
                feedback=[],
                attempt=1,
                step=1,
            )

        assert envelope.complete is True
        assert envelope.content == "Echoed: hi"
        # history 已被构造：system + user + assistant
        history = client._histories["task-1:trace-1"]
        roles = [m.get("role") for m in history]
        assert roles[:2] == ["system", "user"]
        assert "assistant" in roles
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_generate_task_scope_locks_alias_within_same_task(tmp_path: Path) -> None:
    """F1 关键集成回归：同 task_id+trace_id 内多次 generate，即便 yaml 中途改 alias
    映射，仍然路由到同一个 provider/model。"""
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-14B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        called_models: list[str] = []

        async def _record_call(**kwargs: Any):
            called_models.append(kwargs.get("model_name"))
            return ("ok", [], {"token_usage": {}})

        client = ProviderModelClient(provider_router=router, tool_broker=None)
        manifest = _make_manifest()
        ctx = _make_ctx(task_id="t-fixed", trace_id="tr-fixed")

        from octoagent.provider import ProviderClient

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(ProviderClient, "call", AsyncMock(side_effect=_record_call))

            # step 1：触发解析，钉死 model
            await client.generate(
                manifest=manifest,
                execution_context=ctx,
                prompt="hi",
                feedback=[],
                attempt=1,
                step=1,
            )

            # 用户在 task 进行中改了 yaml（极端但合法的状态）
            _write_config(
                tmp_path,
                """
                config_version: 1
                updated_at: "2026-04-26"
                providers:
                  - id: siliconflow
                    name: SiliconFlow
                    auth_type: api_key
                    api_key_env: SILICONFLOW_API_KEY
                    enabled: true
                model_aliases:
                  main:
                    provider: siliconflow
                    model: Qwen/Qwen3.5-72B
                """,
            )

            # step 2：同 task，期待 model 仍是 Qwen3.5-14B（钉死）
            await client.generate(
                manifest=manifest,
                execution_context=ctx,
                prompt="continue",
                feedback=[],
                attempt=1,
                step=2,
            )

        assert called_models == ["Qwen/Qwen3.5-14B", "Qwen/Qwen3.5-14B"], (
            f"task scope 应该钉死 model，但实际 {called_models}"
        )
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_generate_clear_history_invalidates_router_cache(tmp_path: Path) -> None:
    """clear_history 同时清掉 router 的 task scope alias 缓存。"""
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-14B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        called_models: list[str] = []

        async def _record_call(**kwargs: Any):
            called_models.append(kwargs.get("model_name"))
            return ("ok", [], {"token_usage": {}})

        client = ProviderModelClient(provider_router=router, tool_broker=None)
        manifest = _make_manifest()

        from octoagent.provider import ProviderClient

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(ProviderClient, "call", AsyncMock(side_effect=_record_call))

            ctx = _make_ctx(task_id="t-clear", trace_id="tr-clear")
            await client.generate(
                manifest=manifest, execution_context=ctx, prompt="hi",
                feedback=[], attempt=1, step=1,
            )
            assert called_models[-1] == "Qwen/Qwen3.5-14B"

            # 改 yaml + 清 history → 新一次 generate 应该读最新 yaml
            _write_config(
                tmp_path,
                """
                config_version: 1
                updated_at: "2026-04-26"
                providers:
                  - id: siliconflow
                    name: SiliconFlow
                    auth_type: api_key
                    api_key_env: SILICONFLOW_API_KEY
                    enabled: true
                model_aliases:
                  main:
                    provider: siliconflow
                    model: Qwen/Qwen3.5-72B
                """,
            )
            client.clear_history("t-clear:tr-clear")

            # task 重新开始，期待 model = 72B
            new_ctx = _make_ctx(task_id="t-clear", trace_id="tr-clear")
            await client.generate(
                manifest=manifest, execution_context=new_ctx, prompt="restart",
                feedback=[], attempt=1, step=1,
            )
            assert called_models[-1] == "Qwen/Qwen3.5-72B"
    finally:
        await router.aclose()
