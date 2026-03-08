"""Feature 028: project-scoped MemU backend resolver。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import httpx
from octoagent.core.models import (
    Project,
    ProjectBinding,
    ProjectBindingType,
    SecretTargetKind,
    Workspace,
)
from octoagent.memory import (
    DerivedMemoryQuery,
    HttpMemUBridge,
    MemoryAccessPolicy,
    MemoryBackend,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryDerivedProjection,
    MemoryEvidenceProjection,
    MemoryEvidenceQuery,
    MemoryIngestBatch,
    MemoryIngestResult,
    MemoryMaintenanceCommand,
    MemoryMaintenanceRun,
    MemorySearchHit,
    MemorySyncBatch,
    MemorySyncResult,
    MemUBackend,
)

from .backup_service import resolve_project_root
from .secret_models import SecretRef
from .secret_refs import SecretResolutionError, resolve_secret_ref

_DEFAULT_BRIDGE_KEY = "memu.primary"
class MemoryBackendResolver:
    """按 project/workspace 解析 Memory backend。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group,
        environ: dict[str, str] | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._project_root = resolve_project_root(project_root).resolve()
        self._stores = store_group
        self._environ = environ if environ is not None else os.environ
        self._client_factory = client_factory

    async def resolve_backend(
        self,
        *,
        project: Project,
        workspace: Workspace | None = None,
    ) -> MemoryBackend:
        binding = await self._resolve_bridge_binding(project=project, workspace=workspace)
        if binding is None:
            return MemUBackend(
                _StaticMemUBridge(
                    self._unavailable_status(
                        project=project,
                        workspace=workspace,
                        binding_key=_DEFAULT_BRIDGE_KEY,
                        code="MEMU_NOT_CONFIGURED",
                        message="当前 project/workspace 尚未配置 MemU bridge。",
                    )
                )
            )

        base_url = (binding.binding_value or str(binding.metadata.get("base_url", ""))).strip()
        binding_ref = self._binding_ref(
            project=project,
            workspace=workspace,
            binding_key=binding.binding_key,
        )
        if not base_url:
            return MemUBackend(
                _StaticMemUBridge(
                    self._unavailable_status(
                        project=project,
                        workspace=workspace,
                        binding_key=binding.binding_key,
                        code="MEMU_BRIDGE_URL_MISSING",
                        message="MemU bridge binding 缺少 base_url。",
                    )
                )
            )

        api_key = await self._resolve_api_key(
            project=project,
            workspace=workspace,
            binding=binding,
        )
        if isinstance(api_key, MemoryBackendStatus):
            return MemUBackend(_StaticMemUBridge(api_key))

        timeout_seconds = float(binding.metadata.get("timeout_seconds", 5.0) or 5.0)
        api_key_header = str(
            binding.metadata.get("api_key_header", "Authorization") or "Authorization"
        )
        api_key_scheme = str(binding.metadata.get("api_key_scheme", "Bearer") or "Bearer")
        return MemUBackend(
            HttpMemUBridge(
                base_url=base_url,
                api_key=api_key,
                project_id=project.project_id,
                workspace_id=workspace.workspace_id if workspace is not None else "",
                project_binding=binding_ref,
                timeout_seconds=timeout_seconds,
                health_path=str(binding.metadata.get("health_path", "/health") or "/health"),
                search_path=str(
                    binding.metadata.get("search_path", "/memory/search") or "/memory/search"
                ),
                sync_path=str(binding.metadata.get("sync_path", "/memory/sync") or "/memory/sync"),
                ingest_path=str(
                    binding.metadata.get("ingest_path", "/memory/ingest") or "/memory/ingest"
                ),
                derivations_path=str(
                    binding.metadata.get("derivations_path", "/memory/derivations/query")
                    or "/memory/derivations/query"
                ),
                evidence_path=str(
                    binding.metadata.get("evidence_path", "/memory/evidence/resolve")
                    or "/memory/evidence/resolve"
                ),
                maintenance_path=str(
                    binding.metadata.get("maintenance_path", "/memory/maintenance")
                    or "/memory/maintenance"
                ),
                api_key_header=api_key_header,
                api_key_scheme=api_key_scheme,
                client_factory=self._client_factory,
            )
        )

    async def _resolve_bridge_binding(
        self,
        *,
        project: Project,
        workspace: Workspace | None,
    ) -> ProjectBinding | None:
        bindings = await self._stores.project_store.list_bindings(
            project.project_id,
            ProjectBindingType.MEMORY_BRIDGE,
        )
        if not bindings:
            return None
        workspace_id = workspace.workspace_id if workspace is not None else None
        exact = [
            item
            for item in bindings
            if workspace_id and item.workspace_id == workspace_id
        ]
        if exact:
            return sorted(exact, key=lambda item: item.binding_key)[0]
        shared = [item for item in bindings if item.workspace_id in {None, ""}]
        if shared:
            return sorted(shared, key=lambda item: item.binding_key)[0]
        return sorted(bindings, key=lambda item: item.binding_key)[0]

    async def _resolve_api_key(
        self,
        *,
        project: Project,
        workspace: Workspace | None,
        binding: ProjectBinding,
    ):
        metadata = binding.metadata
        target_key = str(metadata.get("api_key_target_key", "") or "").strip()
        if not target_key:
            return None
        secret_binding = await self._stores.project_store.get_secret_binding(
            project.project_id,
            SecretTargetKind.MEMORY,
            target_key,
        )
        if secret_binding is None:
            return self._unavailable_status(
                project=project,
                workspace=workspace,
                binding_key=binding.binding_key,
                code="MEMU_SECRET_BINDING_MISSING",
                message=f"MemU bridge 缺少 secret binding: {target_key}",
            )
        ref = SecretRef(
            source_type=secret_binding.ref_source_type,
            locator=dict(secret_binding.ref_locator),
            display_name=secret_binding.display_name,
            redaction_label=secret_binding.redaction_label,
            metadata=dict(secret_binding.metadata),
        )
        try:
            resolved = resolve_secret_ref(
                ref,
                environ=dict(self._environ),
                cwd=self._project_root,
            )
        except SecretResolutionError as exc:
            return self._unavailable_status(
                project=project,
                workspace=workspace,
                binding_key=binding.binding_key,
                code=f"MEMU_SECRET_{exc.code}",
                message=f"MemU bridge secret 解析失败: {exc.code}",
            )
        return resolved.value

    @staticmethod
    def _binding_ref(
        *,
        project: Project,
        workspace: Workspace | None,
        binding_key: str,
    ) -> str:
        workspace_part = workspace.workspace_id if workspace is not None else "project"
        return f"{project.project_id}/{workspace_part}/{binding_key}"

    def _unavailable_status(
        self,
        *,
        project: Project,
        workspace: Workspace | None,
        binding_key: str,
        code: str,
        message: str,
    ) -> MemoryBackendStatus:
        return MemoryBackendStatus(
            backend_id="memu",
            memory_engine_contract_version="1.0.0",
            state=MemoryBackendState.UNAVAILABLE,
            active_backend="sqlite-metadata",
            failure_code=code,
            message=message,
            project_binding=self._binding_ref(
                project=project,
                workspace=workspace,
                binding_key=binding_key,
            ),
        )


class _StaticMemUBridge:
    """用于未配置/不可解析场景的静态 bridge。"""

    def __init__(self, status: MemoryBackendStatus) -> None:
        self._status = status

    async def is_available(self) -> bool:
        return False

    async def get_status(self) -> MemoryBackendStatus:
        return self._status

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]:
        _ = scope_id, query, policy, limit
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        _ = batch
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult:
        _ = batch
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def list_derivations(self, query: DerivedMemoryQuery) -> MemoryDerivedProjection:
        _ = query
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        _ = query
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        _ = command
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def sync_fragment(self, fragment) -> None:
        _ = fragment
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def sync_sor(self, record) -> None:
        _ = record
        raise RuntimeError(self._status.message or self._status.failure_code)

    async def sync_vault(self, record) -> None:
        _ = record
        raise RuntimeError(self._status.message or self._status.failure_code)
