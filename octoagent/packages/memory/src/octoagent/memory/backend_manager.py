"""Memory backend 状态管理——健康探测、降级/恢复、同步。"""

from datetime import UTC, datetime

import aiosqlite
import structlog
from ulid import ULID

from .backends import MemoryBackend, SqliteMemoryBackend
from .models import (
    FragmentRecord,
    MemoryBackendState,
    MemoryBackendStatus,
    MemorySyncBatch,
    SorRecord,
    VaultRecord,
)
from .store.memory_store import SqliteMemoryStore

log = structlog.get_logger(__name__)


class MemoryBackendManager:
    """集中管理 memory backend 的健康状态、降级切换、sync backlog 等逻辑。"""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        store: SqliteMemoryStore,
        backend: MemoryBackend,
        fallback_backend: SqliteMemoryBackend,
    ) -> None:
        self._conn = conn
        self._store = store
        self._backend = backend
        self._fallback_backend = fallback_backend
        self._backend_degraded = False
        self._backend_last_success_at: datetime | None = None
        self._backend_last_failure_at: datetime | None = None
        self._backend_failure_code = ""
        self._backend_failure_message = ""
        self._pending_replay_count = 0

    # ------------------------------------------------------------------
    # 公共属性
    # ------------------------------------------------------------------

    @property
    def backend(self) -> MemoryBackend:
        return self._backend

    @property
    def fallback_backend(self) -> SqliteMemoryBackend:
        return self._fallback_backend

    @property
    def backend_id(self) -> str:
        return self._backend.backend_id

    @property
    def backend_degraded(self) -> bool:
        return self._backend_degraded

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    async def get_backend_status(self) -> MemoryBackendStatus:
        """返回 Memory backend 当前状态。"""

        if self._backend.backend_id == self._fallback_backend.backend_id:
            status = await self._fallback_backend.get_status()
        else:
            try:
                status = await self._backend.get_status()
            except Exception as exc:
                status = MemoryBackendStatus(
                    backend_id=self._backend.backend_id,
                    memory_engine_contract_version=getattr(
                        self._backend,
                        "memory_engine_contract_version",
                        "1.0.0",
                    ),
                    state=MemoryBackendState.UNAVAILABLE,
                    active_backend=self._fallback_backend.backend_id,
                    failure_code="STATUS_FETCH_FAILED",
                    message=str(exc),
                )

        persisted_pending = await self._store.count_pending_sync_backlog()
        updates: dict[str, object] = {}
        if status.last_success_at is None and self._backend_last_success_at is not None:
            updates["last_success_at"] = self._backend_last_success_at
        if status.last_failure_at is None and self._backend_last_failure_at is not None:
            updates["last_failure_at"] = self._backend_last_failure_at
        updates["pending_replay_count"] = max(
            status.pending_replay_count,
            self._pending_replay_count,
            persisted_pending,
        )
        updates["sync_backlog"] = max(
            status.sync_backlog,
            self._pending_replay_count,
            persisted_pending,
        )
        if persisted_pending > 0 and self._backend.backend_id != self._fallback_backend.backend_id:
            updates["active_backend"] = self._fallback_backend.backend_id
            if status.state is MemoryBackendState.HEALTHY:
                updates["state"] = MemoryBackendState.RECOVERING
            elif status.state is not MemoryBackendState.UNAVAILABLE:
                updates["state"] = MemoryBackendState.DEGRADED
            if not status.failure_code:
                updates["failure_code"] = "BACKEND_REPLAY_PENDING"
            if not status.message:
                updates["message"] = (
                    "高级 memory backend 已恢复可连通，但仍存在待 replay backlog，"
                    "继续使用 SQLite fallback。"
                )
            index_health = dict(status.index_health)
            index_health["fallback_backend"] = self._fallback_backend.backend_id
            updates["index_health"] = index_health

        if self._backend_degraded and self._backend.backend_id != self._fallback_backend.backend_id:
            updates["active_backend"] = self._fallback_backend.backend_id
            updates["failure_code"] = self._backend_failure_code or status.failure_code
            updates["message"] = self._backend_failure_message or status.message
            if status.state is MemoryBackendState.HEALTHY:
                updates["state"] = MemoryBackendState.RECOVERING
            elif status.state is not MemoryBackendState.UNAVAILABLE:
                updates["state"] = MemoryBackendState.DEGRADED
            index_health = dict(status.index_health)
            index_health["fallback_backend"] = self._fallback_backend.backend_id
            updates["index_health"] = index_health
        elif not status.active_backend:
            updates["active_backend"] = status.backend_id

        return status.model_copy(update=updates) if updates else status

    # ------------------------------------------------------------------
    # Backend 同步
    # ------------------------------------------------------------------

    async def sync_backend(
        self,
        *,
        fragment: FragmentRecord,
        current_sor_id: str | None,
        current_vault_id: str | None,
    ) -> None:
        """将 fragment/sor/vault 同步到高级 backend，失败时入 backlog。"""

        if self._backend.backend_id == self._fallback_backend.backend_id:
            return

        sor_records: list[SorRecord] = []
        if current_sor_id is not None:
            sor = await self._store.get_sor(current_sor_id)
            if sor is not None:
                sor_records.append(sor)
        vault_records: list[VaultRecord] = []
        if current_vault_id is not None:
            vault = await self._store.get_vault(current_vault_id)
            if vault is not None:
                vault_records.append(vault)
        batch = MemorySyncBatch(
            batch_id=str(ULID()),
            scope_id=fragment.scope_id,
            fragments=[fragment],
            sor_records=sor_records,
            vault_records=vault_records,
            created_at=datetime.now(UTC),
        )

        try:
            if not await self._backend.is_available():
                self.mark_backend_degraded(
                    "BACKEND_UNAVAILABLE",
                    "高级 memory backend 当前不可用，已暂停同步。",
                )
                await self._store.enqueue_sync_backlog(
                    batch,
                    failure_code="BACKEND_UNAVAILABLE",
                )
                await self._conn.commit()
                self._pending_replay_count += 1
                return
            result = await self._backend.sync_batch(batch)
            if result.backend_state in {
                MemoryBackendState.DEGRADED,
                MemoryBackendState.UNAVAILABLE,
            }:
                self.mark_backend_degraded(
                    "BACKEND_SYNC_DEGRADED",
                    "高级 memory backend sync 返回降级状态。",
                )
                await self._store.enqueue_sync_backlog(
                    batch,
                    failure_code="BACKEND_SYNC_DEGRADED",
                )
                await self._conn.commit()
                self._pending_replay_count += 1
                return
            self.mark_backend_healthy()
        except Exception as exc:
            self.mark_backend_degraded(
                "BACKEND_SYNC_FAILED",
                str(exc) or "高级 memory backend sync 失败。",
            )
            await self._store.enqueue_sync_backlog(
                batch,
                failure_code="BACKEND_SYNC_FAILED",
            )
            await self._conn.commit()
            self._pending_replay_count += 1
            log.warning(
                "memory_backend_sync_degraded",
                backend=self._backend.backend_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Backend 选择
    # ------------------------------------------------------------------

    async def select_backend_for_advanced_calls(self) -> MemoryBackend:
        """选择当前应使用的 backend（高级 / fallback）。"""

        if self._backend.backend_id == self._fallback_backend.backend_id:
            return self._fallback_backend
        if await self._should_force_fallback():
            return self._fallback_backend
        try:
            if not await self._backend.is_available():
                self.mark_backend_degraded(
                    "BACKEND_UNAVAILABLE",
                    "高级 memory backend 当前不可用，已切换到 SQLite fallback。",
                )
                return self._fallback_backend
        except Exception as exc:
            self.mark_backend_degraded(
                "BACKEND_STATUS_FAILED",
                str(exc) or "高级 memory backend 健康探测失败。",
            )
            return self._fallback_backend
        return self._backend

    # ------------------------------------------------------------------
    # 健康探测与恢复
    # ------------------------------------------------------------------

    async def probe_backend_recovery(self) -> bool:
        """探测 backend 是否已恢复。"""

        if self._backend.backend_id == self._fallback_backend.backend_id:
            self.mark_backend_healthy()
            return True
        if await self._has_pending_sync_backlog():
            return False
        try:
            return await self._backend.is_available()
        except Exception:
            return False

    def mark_backend_healthy(self) -> None:
        self._backend_degraded = False
        self._backend_last_success_at = datetime.now(UTC)
        self._backend_failure_code = ""
        self._backend_failure_message = ""
        self._pending_replay_count = 0

    def mark_backend_degraded(self, code: str, message: str) -> None:
        self._backend_degraded = True
        self._backend_last_failure_at = datetime.now(UTC)
        self._backend_failure_code = code
        self._backend_failure_message = message

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _has_pending_sync_backlog(self) -> bool:
        return await self._store.count_pending_sync_backlog() > 0

    async def _should_force_fallback(self) -> bool:
        return await self._has_pending_sync_backlog() or (
            self._backend_degraded and not await self.probe_backend_recovery()
        )
