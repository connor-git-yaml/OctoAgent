"""MemoryBackendResolver 单元测试。"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest_asyncio
from octoagent.core.models import (
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectSecretBinding,
    SecretBindingStatus,
    SecretRefSourceType,
    SecretTargetKind,
    Workspace,
)
from octoagent.core.store import create_store_group
from octoagent.memory import (
    EvidenceRef,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryIngestBatch,
    MemoryIngestItem,
    MemoryLayer,
    MemoryPartition,
    MemorySearchOptions,
    MemoryService,
    WriteAction,
    init_memory_db,
)
from octoagent.provider.dx.builtin_memu_bridge import BuiltinMemUBridge
from octoagent.provider.dx.config_schema import MemoryConfig, OctoAgentConfig
from octoagent.provider.dx.memory_backend_resolver import MemoryBackendResolver
from ulid import ULID


@pytest_asyncio.fixture
async def provider_store_group(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "test.db"),
        tmp_path / "data" / "artifacts",
    )
    yield store_group
    await store_group.conn.close()


async def _seed_project(store_group):
    now = datetime.now(UTC)
    project = Project(
        project_id="project-alpha",
        slug="alpha",
        name="Alpha",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    workspace = Workspace(
        workspace_id="workspace-primary",
        project_id=project.project_id,
        slug="primary",
        name="Primary",
        root_path="/tmp/project-alpha",
        created_at=now,
        updated_at=now,
    )
    await store_group.project_store.create_project(project)
    await store_group.project_store.create_workspace(workspace)
    await store_group.conn.commit()
    return project, workspace


class TestMemoryBackendResolver:
    async def test_uses_yaml_local_only_mode_when_no_project_binding_exists(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(backend_mode="local_only"),
            ).to_yaml(),
            encoding="utf-8",
        )
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.backend_id == "memu"
        assert status.state.value == "healthy"
        assert status.active_backend == "memu"
        assert status.project_binding == "project-alpha/workspace-primary/octoagent.yaml"
        assert "内建 Memory Engine" in status.message
        assert status.index_health["preferred_embedding_model_id"] == "Qwen/Qwen3-Embedding-0.6B"
        assert status.index_health["preferred_embedding_layer"] == "builtin-qwen3-embedding-0.6b"

    async def test_returns_unavailable_backend_when_bridge_not_configured(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.backend_id == "memu"
        assert status.state.value == "unavailable"
        assert status.failure_code == "MEMU_NOT_CONFIGURED"
        assert status.project_binding.endswith("/memu.primary")

    async def test_prefers_workspace_binding_and_resolves_memory_secret(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        await provider_store_group.project_store.create_binding(
            ProjectBinding(
                binding_id=str(ULID()),
                project_id=project.project_id,
                workspace_id=None,
                binding_type=ProjectBindingType.MEMORY_BRIDGE,
                binding_key="memu.project",
                binding_value="https://project.memu.test",
                source="tests",
                migration_run_id="memory-bridge-test",
            )
        )
        await provider_store_group.project_store.create_binding(
            ProjectBinding(
                binding_id=str(ULID()),
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                binding_type=ProjectBindingType.MEMORY_BRIDGE,
                binding_key="memu.primary",
                binding_value="https://workspace.memu.test",
                source="tests",
                migration_run_id="memory-bridge-test",
                metadata={"api_key_target_key": "memory.memu.api_key"},
            )
        )
        await provider_store_group.project_store.save_secret_binding(
            ProjectSecretBinding(
                binding_id=str(ULID()),
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                target_kind=SecretTargetKind.MEMORY,
                target_key="memory.memu.api_key",
                env_name="MEMU_API_KEY",
                ref_source_type=SecretRefSourceType.ENV,
                ref_locator={"env_name": "MEMU_API_KEY"},
                display_name="MemU API Key",
                status=SecretBindingStatus.APPLIED,
            )
        )
        await provider_store_group.conn.commit()

        seen_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_hosts.append(request.url.host or "")
            assert request.headers["Authorization"] == "Bearer memu-secret"
            return httpx.Response(
                200,
                json={
                    "status": {
                        "backend_id": "memu",
                        "state": "healthy",
                        "active_backend": "memu",
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        resolver = MemoryBackendResolver(
            tmp_path,
            store_group=provider_store_group,
            environ={"MEMU_API_KEY": "memu-secret"},
            client_factory=lambda: httpx.AsyncClient(transport=transport),
        )

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.state.value == "healthy"
        assert status.project_binding == "project-alpha/workspace-primary/memu.primary"
        assert seen_hosts == ["workspace.memu.test"]

    async def test_uses_yaml_memu_bridge_when_project_binding_missing(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(
                    backend_mode="memu",
                    bridge_url="https://yaml.memu.test",
                    bridge_api_key_env="MEMU_API_KEY",
                    bridge_timeout_seconds=8.0,
                    bridge_search_path="/memory/query",
                ),
            ).to_yaml(),
            encoding="utf-8",
        )

        seen_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_hosts.append(request.url.host or "")
            assert request.headers["Authorization"] == "Bearer yaml-secret"
            return httpx.Response(
                200,
                json={
                    "status": {
                        "backend_id": "memu",
                        "state": "healthy",
                        "active_backend": "memu",
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        resolver = MemoryBackendResolver(
            tmp_path,
            store_group=provider_store_group,
            environ={"MEMU_API_KEY": "yaml-secret"},
            client_factory=lambda: httpx.AsyncClient(transport=transport),
        )

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.state.value == "healthy"
        assert status.project_binding == "project-alpha/workspace-primary/octoagent.yaml"
        assert seen_hosts == ["yaml.memu.test"]

    async def test_uses_yaml_memu_command_bridge_when_configured(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        script_path = tmp_path / "memu_bridge.py"
        script_path.write_text(
            """
import json
import os
import sys

action = sys.argv[-1]
payload = json.loads(sys.stdin.read() or "{}")

if action == "health":
    print(json.dumps({
        "status": {
            "backend_id": "memu",
            "state": "healthy",
            "active_backend": "memu",
            "project_binding": os.environ.get("OCTOAGENT_BRIDGE_BINDING", ""),
            "index_health": {"transport": "command"},
        }
    }))
elif action == "query":
    print(json.dumps({
        "items": [
            {
                "record_id": "memu-command-hit",
                "layer": "sor",
                "scope_id": payload["scope_id"],
                "partition": "work",
                "summary": payload.get("query", ""),
                "created_at": "2026-03-14T00:00:00+00:00",
            }
        ]
    }))
else:
    print(json.dumps({"result": {}}))
""".strip(),
            encoding="utf-8",
        )
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(
                    backend_mode="memu",
                    bridge_transport="command",
                    bridge_command=f"{sys.executable} {script_path}",
                    bridge_command_cwd=str(tmp_path),
                    bridge_command_timeout_seconds=4.0,
                ),
            ).to_yaml(),
            encoding="utf-8",
        )
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()
        hits = await backend.search(
            "memory/project-alpha",
            query="running",
            policy=MemoryAccessPolicy(),
        )

        assert status.state is MemoryBackendState.HEALTHY
        assert status.project_binding == "project-alpha/workspace-primary/octoagent.yaml"
        assert status.index_health["transport"] == "command"
        assert hits[0].record_id == "memu-command-hit"
        assert hits[0].layer is MemoryLayer.SOR
        assert hits[0].partition is MemoryPartition.WORK

    async def test_local_only_builtin_engine_can_search_committed_memory(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        await init_memory_db(provider_store_group.conn)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(backend_mode="local_only"),
            ).to_yaml(),
            encoding="utf-8",
        )

        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)
        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        memory = MemoryService(provider_store_group.conn, backend=backend)

        proposal = await memory.propose_write(
            scope_id="memory/project-alpha",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="project.alpha.status",
            content="Alpha 项目需要先整理需求，再推进交付。",
            rationale="seed builtin memory engine",
            confidence=0.78,
            evidence_refs=[
                EvidenceRef(
                    ref_id="artifact-alpha-1",
                    ref_type="artifact",
                    snippet="Alpha 项目需要先整理需求，再推进交付。",
                )
            ],
        )
        await memory.validate_proposal(proposal.proposal_id)
        await memory.commit_memory(proposal.proposal_id)

        hits = await memory.search_memory(
            scope_id="memory/project-alpha",
            query="继续推进 Alpha 交付",
            limit=5,
            search_options=MemorySearchOptions(
                expanded_queries=["继续推进 Alpha 交付"],
                embedding_target="engine-default",
            ),
        )

        assert hits
        assert hits[0].metadata["embedding_target"] == "engine-default"
        status = await backend.get_status()
        assert status.active_backend == "memu"

    async def test_local_only_builtin_engine_prefers_qwen_when_runtime_is_available(
        self,
        provider_store_group,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        await init_memory_db(provider_store_group.conn)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(backend_mode="local_only"),
            ).to_yaml(),
            encoding="utf-8",
        )

        class _FakeSentenceTransformer:
            def __init__(self, model_id: str, *, trust_remote_code: bool, device: str) -> None:
                assert model_id == "Qwen/Qwen3-Embedding-0.6B"
                assert trust_remote_code is True
                assert device == "cpu"

            def encode(self, values, normalize_embeddings: bool = True):
                assert normalize_embeddings is True

                def _embed_one(item: str) -> list[float]:
                    normalized = str(item).lower()
                    if "alpha" in normalized:
                        return [1.0, 0.0, 0.0]
                    if "需求" in normalized or "交付" in normalized:
                        return [0.8, 0.2, 0.0]
                    return [0.0, 1.0, 0.0]

                if isinstance(values, list):
                    return [_embed_one(item) for item in values]
                return _embed_one(values)

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = _FakeSentenceTransformer

        bridge_module = sys.modules[BuiltinMemUBridge.__module__]
        monkeypatch.setattr(bridge_module, "_module_exists", lambda module_name: True)
        monkeypatch.setattr(
            BuiltinMemUBridge,
            "_load_sentence_transformers_module",
            lambda self: fake_module,
        )

        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)
        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        memory = MemoryService(provider_store_group.conn, backend=backend)

        proposal = await memory.propose_write(
            scope_id="memory/project-alpha",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="project.alpha.status",
            content="Alpha 项目需要先整理需求，再推进交付。",
            rationale="seed builtin qwen runtime",
            confidence=0.78,
            evidence_refs=[
                EvidenceRef(
                    ref_id="artifact-alpha-qwen",
                    ref_type="artifact",
                    snippet="Alpha 项目需要先整理需求，再推进交付。",
                )
            ],
        )
        await memory.validate_proposal(proposal.proposal_id)
        await memory.commit_memory(proposal.proposal_id)

        hits = await memory.search_memory(
            scope_id="memory/project-alpha",
            query="继续推进 Alpha 交付",
            limit=5,
            search_options=MemorySearchOptions(
                expanded_queries=["继续推进 Alpha 交付"],
                embedding_target="engine-default",
            ),
        )

        assert hits
        assert hits[0].metadata["builtin_embedding_layer"] == "builtin-qwen3-embedding-0.6b"
        status = await backend.get_status()
        assert status.index_health["embedding_layer"] == "builtin-qwen3-embedding-0.6b"
        assert status.index_health["embedding_runtime_status"] == "ready"

    async def test_local_only_builtin_engine_uses_proxy_embedding_for_active_alias_target(
        self,
        provider_store_group,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        await init_memory_db(provider_store_group.conn)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                providers=[
                    {
                        "id": "openrouter",
                        "name": "OpenRouter",
                        "auth_type": "api_key",
                        "api_key_env": "OPENROUTER_API_KEY",
                        "enabled": True,
                    }
                ],
                model_aliases={
                    "knowledge-embed": {
                        "provider": "openrouter",
                        "model": "openai/text-embedding-3-small",
                    }
                },
                memory=MemoryConfig(backend_mode="local_only"),
            ).to_yaml(),
            encoding="utf-8",
        )

        seen_aliases: list[str] = []

        async def _fake_proxy_embeddings(self, texts, *, target_alias: str, is_query: bool):
            seen_aliases.append(target_alias)
            vectors: list[list[float]] = []
            for item in texts:
                normalized = str(item).lower()
                if "alpha" in normalized:
                    vectors.append([1.0, 0.0, 0.0])
                elif "beta" in normalized:
                    vectors.append([0.0, 1.0, 0.0])
                else:
                    vectors.append([0.2, 0.2, 0.2])
            return vectors

        monkeypatch.setattr(
            BuiltinMemUBridge,
            "_embed_texts_with_proxy_alias",
            _fake_proxy_embeddings,
        )

        resolver = MemoryBackendResolver(
            tmp_path,
            store_group=provider_store_group,
            environ={"OPENROUTER_API_KEY": "test-key"},
        )
        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        memory = MemoryService(provider_store_group.conn, backend=backend)

        proposal = await memory.propose_write(
            scope_id="memory/project-alpha",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="project.alpha.status",
            content="Alpha 项目需要先整理需求，再推进交付。",
            rationale="seed proxy alias embedding",
            confidence=0.78,
            evidence_refs=[
                EvidenceRef(
                    ref_id="artifact-alpha-embed",
                    ref_type="artifact",
                    snippet="Alpha 项目需要先整理需求，再推进交付。",
                )
            ],
        )
        await memory.validate_proposal(proposal.proposal_id)
        await memory.commit_memory(proposal.proposal_id)

        hits = await memory.search_memory(
            scope_id="memory/project-alpha",
            query="继续推进 Alpha 交付",
            limit=5,
            search_options=MemorySearchOptions(
                expanded_queries=["继续推进 Alpha 交付"],
                embedding_target="knowledge-embed",
            ),
        )

        assert hits
        assert seen_aliases == ["knowledge-embed", "knowledge-embed"]
        assert hits[0].metadata["resolved_embedding_target"] == "knowledge-embed"
        assert hits[0].metadata["builtin_embedding_layer"] == "proxy-alias:knowledge-embed"

    async def test_local_only_builtin_engine_warms_qwen_in_background_without_blocking_first_search(
        self,
        provider_store_group,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        await init_memory_db(provider_store_group.conn)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(backend_mode="local_only"),
            ).to_yaml(),
            encoding="utf-8",
        )

        bridge_module = sys.modules[BuiltinMemUBridge.__module__]
        monkeypatch.setattr(bridge_module, "_module_exists", lambda module_name: True)

        async def _fake_warmup(self):
            await asyncio.sleep(0.01)
            self._embedding_runtime.model_loaded = True
            self._embedding_runtime.encoder = object()
            self._embedding_runtime.status = "ready"
            self._embedding_runtime.active_layer = "builtin-qwen3-embedding-0.6b"
            self._embedding_runtime.active_mode = "builtin-qwen3-embedding"
            self._embedding_runtime.summary = "当前优先使用内建 Qwen3-Embedding-0.6B 做双语语义检索。"
            self._embedding_runtime.fallback_reason = ""
            self._embedding_runtime.warmup_task = None

        monkeypatch.setattr(BuiltinMemUBridge, "_warmup_qwen_runtime", _fake_warmup)

        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)
        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        memory = MemoryService(provider_store_group.conn, backend=backend)

        proposal = await memory.propose_write(
            scope_id="memory/project-alpha",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="project.alpha.status",
            content="Alpha 项目需要先整理需求，再推进交付。",
            rationale="seed background warmup",
            confidence=0.78,
            evidence_refs=[
                EvidenceRef(
                    ref_id="artifact-alpha-warmup",
                    ref_type="artifact",
                    snippet="Alpha 项目需要先整理需求，再推进交付。",
                )
            ],
        )
        await memory.validate_proposal(proposal.proposal_id)
        await memory.commit_memory(proposal.proposal_id)

        hits = await memory.search_memory(
            scope_id="memory/project-alpha",
            query="继续推进 Alpha 交付",
            limit=5,
            search_options=MemorySearchOptions(
                expanded_queries=["继续推进 Alpha 交付"],
                embedding_target="engine-default",
            ),
        )

        assert hits
        assert hits[0].metadata["builtin_embedding_layer"] == "builtin-hash-bilingual"

        await asyncio.sleep(0.02)
        status = await backend.get_status()
        assert status.index_health["embedding_runtime_status"] == "ready"

    async def test_local_only_builtin_engine_routes_sensitive_candidates_through_governance(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        await init_memory_db(provider_store_group.conn)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(backend_mode="local_only"),
            ).to_yaml(),
            encoding="utf-8",
        )

        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)
        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        memory = MemoryService(provider_store_group.conn, backend=backend)

        ingest_result = await memory.ingest_memory_batch(
            MemoryIngestBatch(
                ingest_id="ingest-sensitive-1",
                scope_id="memory/project-alpha",
                partition=MemoryPartition.WORK,
                items=[
                    MemoryIngestItem(
                        item_id="item-1",
                        modality="text",
                        artifact_ref="artifact-sensitive-1",
                        metadata={
                            "text": "Connor 的预算上限需要保密处理。",
                            "proposal_subject_key": "profile.connor.finance.budget_limit",
                            "proposal_partition": "finance",
                            "proposal_rationale": "文本里出现了应进入敏感分区的预算信息。",
                        },
                    )
                ],
            )
        )

        assert ingest_result.proposal_drafts
        draft = ingest_result.proposal_drafts[0]
        assert draft.partition is MemoryPartition.FINANCE
        assert draft.metadata["candidate_engine"] == "builtin-memory-engine"
        assert draft.metadata["candidate_kind"] == "vault_candidate"

        proposal = await memory.create_proposal_from_draft(
            scope_id="memory/project-alpha",
            draft=draft,
        )
        validation = await memory.validate_proposal(proposal.proposal_id)
        assert validation.accepted is True
        commit = await memory.commit_memory(proposal.proposal_id)
        assert commit.vault_id

        vault_record = await memory.get_memory(
            commit.vault_id,
            layer=MemoryLayer.VAULT,
            policy=MemoryAccessPolicy(allow_vault=True),
        )
        assert vault_record is not None
