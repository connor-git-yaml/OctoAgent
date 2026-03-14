"""MemU HTTP bridge 实现。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import SecretStr

from ..models import (
    DerivedMemoryQuery,
    FragmentRecord,
    MemoryAccessPolicy,
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
    MemorySearchOptions,
    MemorySyncBatch,
    MemorySyncResult,
    SorRecord,
    VaultRecord,
)


class HttpMemUBridge:
    """基于 HTTP 的 MemU transport bridge。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr | None = None,
        project_id: str = "",
        workspace_id: str = "",
        project_binding: str = "",
        timeout_seconds: float = 5.0,
        health_path: str = "/health",
        search_path: str = "/memory/search",
        sync_path: str = "/memory/sync",
        ingest_path: str = "/memory/ingest",
        derivations_path: str = "/memory/derivations/query",
        evidence_path: str = "/memory/evidence/resolve",
        maintenance_path: str = "/memory/maintenance",
        api_key_header: str = "Authorization",
        api_key_scheme: str = "Bearer",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise ValueError("MemU bridge base_url 不能为空。")
        self._base_url = normalized
        self._api_key = api_key
        self._project_id = project_id
        self._workspace_id = workspace_id
        self._project_binding = project_binding
        self._timeout_seconds = timeout_seconds
        self._health_path = health_path
        self._search_path = search_path
        self._sync_path = sync_path
        self._ingest_path = ingest_path
        self._derivations_path = derivations_path
        self._evidence_path = evidence_path
        self._maintenance_path = maintenance_path
        self._api_key_header = api_key_header.strip() or "Authorization"
        self._api_key_scheme = api_key_scheme.strip()
        self._client_factory = client_factory
        self._last_status = MemoryBackendStatus(
            backend_id="memu",
            memory_engine_contract_version="1.0.0",
            state=MemoryBackendState.RECOVERING,
            active_backend="memu",
            project_binding=project_binding,
        )

    async def is_available(self) -> bool:
        status = await self.get_status()
        return status.state is MemoryBackendState.HEALTHY

    async def get_status(self) -> MemoryBackendStatus:
        try:
            payload = await self._request_json("GET", self._health_path)
            raw = self._unwrap_payload(payload, "status")
            status = MemoryBackendStatus.model_validate(
                {
                    **raw,
                    "backend_id": raw.get("backend_id") or "memu",
                    "memory_engine_contract_version": raw.get(
                        "memory_engine_contract_version",
                        "1.0.0",
                    ),
                    "active_backend": raw.get("active_backend") or "memu",
                    "project_binding": raw.get("project_binding")
                    or self._project_binding,
                }
            )
            return self._record_success(status)
        except Exception as exc:
            return self._record_failure(
                "MEMU_STATUS_REQUEST_FAILED",
                str(exc) or "MemU health probe 失败。",
            )

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        payload = {
            "scope_id": scope_id,
            "query": query,
            "limit": limit,
            "policy": policy.model_dump(mode="json") if policy is not None else None,
            "search_options": (
                search_options.model_dump(mode="json") if search_options is not None else None
            ),
        }
        try:
            raw = await self._request_json("POST", self._search_path, payload)
            items = self._unwrap_items(raw, "items")
            self._record_success()
            return [MemorySearchHit.model_validate(item) for item in items]
        except Exception as exc:
            self._record_failure(
                "MEMU_SEARCH_FAILED",
                str(exc) or "MemU search 失败。",
            )
            raise

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        try:
            raw = await self._request_json(
                "POST",
                self._sync_path,
                batch.model_dump(mode="json"),
            )
            result = MemorySyncResult.model_validate(self._unwrap_payload(raw, "result"))
            self._record_success(
                backend_state=result.backend_state,
                pending_replay_count=max(0, result.replayed_tombstones),
            )
            return result
        except Exception as exc:
            self._record_failure(
                "MEMU_SYNC_FAILED",
                str(exc) or "MemU sync 失败。",
            )
            raise

    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult:
        try:
            raw = await self._request_json(
                "POST",
                self._ingest_path,
                batch.model_dump(mode="json"),
            )
            result = MemoryIngestResult.model_validate(self._unwrap_payload(raw, "result"))
            self._record_success(
                backend_state=result.backend_state,
                last_ingest_at=datetime.now(UTC),
            )
            return result
        except Exception as exc:
            self._record_failure(
                "MEMU_INGEST_FAILED",
                str(exc) or "MemU ingest 失败。",
            )
            raise

    async def list_derivations(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        try:
            raw = await self._request_json(
                "POST",
                self._derivations_path,
                query.model_dump(mode="json"),
            )
            projection = MemoryDerivedProjection.model_validate(
                self._unwrap_payload(raw, "result")
            )
            self._record_success(backend_state=projection.backend_state)
            return projection
        except Exception as exc:
            self._record_failure(
                "MEMU_DERIVATIONS_FAILED",
                str(exc) or "MemU derivations 查询失败。",
            )
            raise

    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        try:
            raw = await self._request_json(
                "POST",
                self._evidence_path,
                query.model_dump(mode="json"),
            )
            projection = MemoryEvidenceProjection.model_validate(
                self._unwrap_payload(raw, "result")
            )
            self._record_success()
            return projection
        except Exception as exc:
            self._record_failure(
                "MEMU_EVIDENCE_FAILED",
                str(exc) or "MemU evidence resolve 失败。",
            )
            raise

    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        try:
            raw = await self._request_json(
                "POST",
                self._maintenance_path,
                command.model_dump(mode="json"),
            )
            run = MemoryMaintenanceRun.model_validate(self._unwrap_payload(raw, "result"))
            self._record_success(
                backend_state=run.backend_state,
                last_maintenance_at=datetime.now(UTC),
            )
            return run
        except Exception as exc:
            self._record_failure(
                "MEMU_MAINTENANCE_FAILED",
                str(exc) or "MemU maintenance 执行失败。",
            )
            raise

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        await self.sync_batch(
            MemorySyncBatch(
                batch_id=f"sync-fragment:{fragment.fragment_id}",
                scope_id=fragment.scope_id,
                fragments=[fragment],
                created_at=datetime.now(UTC),
            )
        )

    async def sync_sor(self, record: SorRecord) -> None:
        await self.sync_batch(
            MemorySyncBatch(
                batch_id=f"sync-sor:{record.memory_id}",
                scope_id=record.scope_id,
                sor_records=[record],
                created_at=datetime.now(UTC),
            )
        )

    async def sync_vault(self, record: VaultRecord) -> None:
        await self.sync_batch(
            MemorySyncBatch(
                batch_id=f"sync-vault:{record.vault_id}",
                scope_id=record.scope_id,
                vault_records=[record],
                created_at=datetime.now(UTC),
            )
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._make_client() as client:
            response = await client.request(
                method,
                self._resolve_url(path),
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            if not response.content:
                return {}
            body = response.json()
            if isinstance(body, dict):
                return body
            return {"items": body}

    def _make_client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=self._timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._project_id:
            headers["X-OctoAgent-Project-ID"] = self._project_id
        if self._workspace_id:
            headers["X-OctoAgent-Workspace-ID"] = self._workspace_id
        if self._project_binding:
            headers["X-OctoAgent-Bridge-Binding"] = self._project_binding
        if self._api_key is not None:
            secret = self._api_key.get_secret_value()
            if self._api_key_header.lower() == "authorization":
                prefix = f"{self._api_key_scheme} " if self._api_key_scheme else ""
                headers[self._api_key_header] = f"{prefix}{secret}"
            else:
                headers[self._api_key_header] = secret
        return headers

    def _resolve_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self._base_url}{normalized}"

    def _record_success(
        self,
        status: MemoryBackendStatus | None = None,
        *,
        backend_state: MemoryBackendState = MemoryBackendState.HEALTHY,
        pending_replay_count: int | None = None,
        last_ingest_at: datetime | None = None,
        last_maintenance_at: datetime | None = None,
    ) -> MemoryBackendStatus:
        now = datetime.now(UTC)
        if status is None:
            status = self._last_status.model_copy(
                update={
                    "state": backend_state,
                    "backend_id": "memu",
                    "active_backend": "memu",
                    "failure_code": "",
                    "message": "",
                    "last_success_at": now,
                    "project_binding": self._project_binding,
                }
            )
        else:
            status = status.model_copy(
                update={
                    "backend_id": status.backend_id or "memu",
                    "active_backend": status.active_backend or "memu",
                    "failure_code": status.failure_code,
                    "message": status.message,
                    "last_success_at": status.last_success_at or now,
                    "project_binding": status.project_binding or self._project_binding,
                }
            )
        updates: dict[str, Any] = {}
        if pending_replay_count is not None:
            updates["pending_replay_count"] = pending_replay_count
            updates["sync_backlog"] = max(status.sync_backlog, pending_replay_count)
        if last_ingest_at is not None:
            updates["last_ingest_at"] = last_ingest_at
        if last_maintenance_at is not None:
            updates["last_maintenance_at"] = last_maintenance_at
        self._last_status = status.model_copy(update=updates) if updates else status
        return self._last_status

    def _record_failure(self, code: str, message: str) -> MemoryBackendStatus:
        self._last_status = self._last_status.model_copy(
            update={
                "backend_id": "memu",
                "state": MemoryBackendState.UNAVAILABLE,
                "active_backend": "sqlite-metadata",
                "failure_code": code,
                "message": message,
                "last_failure_at": datetime.now(UTC),
                "project_binding": self._project_binding,
            }
        )
        return self._last_status

    @staticmethod
    def _unwrap_payload(payload: dict[str, Any], key: str) -> dict[str, Any]:
        if key in payload and isinstance(payload[key], dict):
            return payload[key]
        return payload

    @staticmethod
    def _unwrap_items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        raw = payload.get(key, payload)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []
