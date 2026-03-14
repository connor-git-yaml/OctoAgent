"""MemU 本地命令桥接实现。"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from datetime import UTC, datetime
from typing import Any

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
    MemorySyncBatch,
    MemorySyncResult,
    SorRecord,
    VaultRecord,
)


class CommandMemUBridge:
    """基于本地命令的 MemU transport bridge。"""

    def __init__(
        self,
        *,
        command: str,
        cwd: str = "",
        project_id: str = "",
        workspace_id: str = "",
        project_binding: str = "",
        timeout_seconds: float = 15.0,
        environ: dict[str, str] | None = None,
    ) -> None:
        normalized = command.strip()
        if not normalized:
            raise ValueError("MemU bridge command 不能为空。")
        self._command = normalized
        self._cwd = cwd.strip()
        self._project_id = project_id
        self._workspace_id = workspace_id
        self._project_binding = project_binding
        self._timeout_seconds = timeout_seconds
        self._environ = dict(environ) if environ is not None else dict(os.environ)
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
            payload = await self._invoke_json(
                "health",
                {
                    "project_id": self._project_id,
                    "workspace_id": self._workspace_id,
                    "project_binding": self._project_binding,
                },
            )
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
                "MEMU_STATUS_COMMAND_FAILED",
                str(exc) or "MemU command health probe 失败。",
            )

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]:
        payload = {
            "scope_id": scope_id,
            "query": query,
            "limit": limit,
            "policy": policy.model_dump(mode="json") if policy is not None else None,
        }
        try:
            raw = await self._invoke_json("query", payload)
            items = self._unwrap_items(raw, "items")
            self._record_success()
            return [MemorySearchHit.model_validate(item) for item in items]
        except Exception as exc:
            self._record_failure(
                "MEMU_QUERY_COMMAND_FAILED",
                str(exc) or "MemU command query 失败。",
            )
            raise

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        try:
            raw = await self._invoke_json("sync", batch.model_dump(mode="json"))
            result = MemorySyncResult.model_validate(self._unwrap_payload(raw, "result"))
            self._record_success(
                backend_state=result.backend_state,
                pending_replay_count=max(0, result.replayed_tombstones),
            )
            return result
        except Exception as exc:
            self._record_failure(
                "MEMU_SYNC_COMMAND_FAILED",
                str(exc) or "MemU command sync 失败。",
            )
            raise

    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult:
        try:
            raw = await self._invoke_json("ingest", batch.model_dump(mode="json"))
            result = MemoryIngestResult.model_validate(self._unwrap_payload(raw, "result"))
            self._record_success(
                backend_state=result.backend_state,
                last_ingest_at=datetime.now(UTC),
            )
            return result
        except Exception as exc:
            self._record_failure(
                "MEMU_INGEST_COMMAND_FAILED",
                str(exc) or "MemU command ingest 失败。",
            )
            raise

    async def list_derivations(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        try:
            raw = await self._invoke_json("derivations", query.model_dump(mode="json"))
            projection = MemoryDerivedProjection.model_validate(
                self._unwrap_payload(raw, "result")
            )
            self._record_success(backend_state=projection.backend_state)
            return projection
        except Exception as exc:
            self._record_failure(
                "MEMU_DERIVATIONS_COMMAND_FAILED",
                str(exc) or "MemU command derivations 查询失败。",
            )
            raise

    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        try:
            raw = await self._invoke_json("evidence", query.model_dump(mode="json"))
            projection = MemoryEvidenceProjection.model_validate(
                self._unwrap_payload(raw, "result")
            )
            self._record_success()
            return projection
        except Exception as exc:
            self._record_failure(
                "MEMU_EVIDENCE_COMMAND_FAILED",
                str(exc) or "MemU command evidence 失败。",
            )
            raise

    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        try:
            raw = await self._invoke_json("maintenance", command.model_dump(mode="json"))
            run = MemoryMaintenanceRun.model_validate(self._unwrap_payload(raw, "result"))
            self._record_success(
                backend_state=run.backend_state,
                last_maintenance_at=datetime.now(UTC),
            )
            return run
        except Exception as exc:
            self._record_failure(
                "MEMU_MAINTENANCE_COMMAND_FAILED",
                str(exc) or "MemU command maintenance 执行失败。",
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

    async def _invoke_json(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command = [*shlex.split(self._command), action]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd or None,
            env=self._command_environ(),
        )
        input_bytes = None
        if payload is not None:
            input_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_bytes),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError(
                f"MemU command {action} 超时（>{self._timeout_seconds:.1f}s）"
            ) from exc
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            output = stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or output or f"MemU command {action} 执行失败")
        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return {}
        body = json.loads(raw)
        if isinstance(body, dict):
            return body
        return {"items": body}

    def _command_environ(self) -> dict[str, str]:
        environ = dict(self._environ)
        if self._project_id:
            environ["OCTOAGENT_PROJECT_ID"] = self._project_id
        if self._workspace_id:
            environ["OCTOAGENT_WORKSPACE_ID"] = self._workspace_id
        if self._project_binding:
            environ["OCTOAGENT_BRIDGE_BINDING"] = self._project_binding
        return environ

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
