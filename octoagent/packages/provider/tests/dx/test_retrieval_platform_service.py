from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from octoagent.core.models import (
    CorpusKind,
    IndexGeneration,
    IndexGenerationStatus,
    Project,
    Workspace,
)
from octoagent.core.store import create_store_group
from octoagent.memory import init_memory_db
from octoagent.provider.dx.config_schema import (
    MemoryConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
)
from octoagent.provider.dx.retrieval_platform_service import (
    RetrievalPlatformError,
    RetrievalPlatformService,
)
from ulid import ULID


@pytest_asyncio.fixture
async def provider_store_group(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "retrieval-platform.db"),
        tmp_path / "data" / "artifacts",
    )
    yield store_group
    await store_group.conn.close()


async def _seed_project(store_group):
    await init_memory_db(store_group.conn)
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


def _write_config(
    tmp_path: Path,
    *,
    embedding_alias: str = "",
    embedding_model: str = "openrouter/text-embedding-3-small",
) -> None:
    config = OctoAgentConfig(
        updated_at="2026-03-15",
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
            "knowledge-embed": ModelAlias(
                provider="openrouter",
                model=embedding_model,
                description="知识检索 embedding",
            ),
        },
        memory=MemoryConfig(embedding_model_alias=embedding_alias),
    )
    (tmp_path / "octoagent.yaml").write_text(config.to_yaml(), encoding="utf-8")


async def test_retrieval_platform_uses_projection_only_metadata_for_active_generation(
    provider_store_group,
    tmp_path: Path,
) -> None:
    await _seed_project(provider_store_group)
    _write_config(tmp_path)
    service = RetrievalPlatformService(tmp_path, store_group=provider_store_group)

    document = await service.get_document()

    active_generation = next(
        item for item in document.generations if item.corpus_kind == CorpusKind.MEMORY and item.is_active
    )

    assert active_generation.metadata["projection_store"] == "shared-retrieval-platform"
    assert active_generation.profile_target == "engine-default"
    assert document.corpora[0].corpus_kind == CorpusKind.MEMORY
    assert document.corpora[1].corpus_kind == CorpusKind.KNOWLEDGE_BASE
    assert document.corpora[1].state == "reserved"


async def test_retrieval_platform_reuses_generation_contract_for_knowledge_base_corpus(
    provider_store_group,
    tmp_path: Path,
) -> None:
    await _seed_project(provider_store_group)
    _write_config(tmp_path)
    service = RetrievalPlatformService(tmp_path, store_group=provider_store_group)

    await service.get_document()
    snapshot = service._store.load()  # noqa: SLF001 - 针对持久化 contract 的定向测试
    now = datetime.now(tz=UTC)
    snapshot.generations.append(
        IndexGeneration(
            generation_id=f"gen-knowledge_base-{ULID()}",
            corpus_kind=CorpusKind.KNOWLEDGE_BASE,
            profile_id="builtin:sqlite-metadata",
            profile_target="sqlite-metadata",
            label="知识库 · 内建默认层（当前先走本地元数据）",
            status=IndexGenerationStatus.ACTIVE,
            is_active=True,
            created_at=now,
            updated_at=now,
            activated_at=now,
            completed_at=now,
            metadata={"projection_store": "shared-retrieval-platform"},
        )
    )
    service._store.save(snapshot)  # noqa: SLF001 - 针对持久化 contract 的定向测试

    _write_config(tmp_path, embedding_alias="knowledge-embed")
    updated = await service.get_document()

    memory_corpus = next(item for item in updated.corpora if item.corpus_kind == CorpusKind.MEMORY)
    knowledge_corpus = next(
        item for item in updated.corpora if item.corpus_kind == CorpusKind.KNOWLEDGE_BASE
    )
    pending_generations = [
        item
        for item in updated.generations
        if item.status == IndexGenerationStatus.QUEUED
        and item.profile_target == "knowledge-embed"
    ]
    pending_jobs = [
        item
        for item in updated.build_jobs
        if item.stage.value == "queued" and item.metadata["projection_store"] == "shared-retrieval-platform"
    ]

    assert memory_corpus.state == "migration_running"
    assert knowledge_corpus.state == "migration_running"
    assert knowledge_corpus.pending_generation_id
    assert len(pending_generations) == 2
    assert {item.corpus_kind for item in pending_generations} == {
        CorpusKind.MEMORY,
        CorpusKind.KNOWLEDGE_BASE,
    }
    assert len({item.build_job_id for item in pending_generations}) == 2
    assert len(pending_jobs) >= 2


async def test_retrieval_platform_creates_new_generation_when_embedding_alias_definition_changes(
    provider_store_group,
    tmp_path: Path,
) -> None:
    await _seed_project(provider_store_group)
    _write_config(
        tmp_path,
        embedding_alias="knowledge-embed",
        embedding_model="openrouter/text-embedding-3-small",
    )
    service = RetrievalPlatformService(tmp_path, store_group=provider_store_group)

    baseline = await service.get_document()
    baseline_memory = next(
        item for item in baseline.corpora if item.corpus_kind == CorpusKind.MEMORY
    )
    assert baseline_memory.active_profile_id.startswith("alias:knowledge-embed:")
    assert baseline_memory.pending_generation_id == ""

    _write_config(
        tmp_path,
        embedding_alias="knowledge-embed",
        embedding_model="openrouter/text-embedding-3-large",
    )
    updated = await service.get_document()
    updated_memory = next(item for item in updated.corpora if item.corpus_kind == CorpusKind.MEMORY)

    assert updated_memory.state == "migration_running"
    assert updated_memory.pending_generation_id
    assert updated_memory.active_profile_id != updated_memory.desired_profile_id
    pending_generation = next(
        item for item in updated.generations if item.generation_id == updated_memory.pending_generation_id
    )
    assert pending_generation.profile_id == updated_memory.desired_profile_id
    assert pending_generation.profile_id.startswith("alias:knowledge-embed:")


async def test_retrieval_platform_rejects_rollback_for_cancelled_generation(
    provider_store_group,
    tmp_path: Path,
) -> None:
    project, workspace = await _seed_project(provider_store_group)
    _write_config(tmp_path)
    service = RetrievalPlatformService(tmp_path, store_group=provider_store_group)

    await service.get_document()
    _write_config(tmp_path, embedding_alias="knowledge-embed")
    pending = await service.get_document()
    pending_memory = next(item for item in pending.corpora if item.corpus_kind == CorpusKind.MEMORY)
    cancelled_generation_id = pending_memory.pending_generation_id
    assert cancelled_generation_id

    await service.cancel_generation(
        generation_id=cancelled_generation_id,
        project_id=project.project_id,
        workspace_id=workspace.workspace_id,
    )

    with pytest.raises(RetrievalPlatformError) as exc_info:
        await service.rollback_generation(
            generation_id=cancelled_generation_id,
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
        )

    assert exc_info.value.code == "RETRIEVAL_GENERATION_NOT_ROLLBACKABLE"
