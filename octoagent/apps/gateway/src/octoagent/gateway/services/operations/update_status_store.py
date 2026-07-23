"""Feature 024 update / runtime 状态持久化。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from filelock import FileLock
from octoagent.core.models import (
    ManagedRuntimeDescriptor,
    RuntimeStateSnapshot,
    UpdateAttempt,
    UpdateAttemptSummary,
    UpdateOverallStatus,
)
from octoagent.gateway.services.operations.backup_service import (
    resolve_data_dir,
    resolve_project_root,
)
from octoagent.gateway.services.operations.runtime_descriptor_defaults import (
    normalize_runtime_descriptor,
)
from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)

# 活跃 update 超过此秒数视为孤儿进程残留，自动清理
_STALE_ACTIVE_ATTEMPT_SECONDS = 600  # 10 分钟


class UpdateStatusStore:
    """CLI / Web 共用的 canonical update 状态源。"""

    def __init__(self, project_root: Path, *, data_dir: Path | None = None) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._data_dir = (
            data_dir.resolve() if data_dir is not None else resolve_data_dir(self._root)
        )
        self._ops_dir = self._data_dir / "ops"
        self._history_dir = self._ops_dir / "update-history"
        self._descriptor_path = self._ops_dir / "managed-runtime.json"
        self._runtime_state_path = self._ops_dir / "runtime-state.json"
        self._latest_attempt_path = self._ops_dir / "latest-update.json"
        self._active_attempt_path = self._ops_dir / "active-update.json"
        self._descriptor_lock = FileLock(str(self._descriptor_path) + ".lock")
        self._runtime_state_lock = FileLock(str(self._runtime_state_path) + ".lock")
        self._latest_attempt_lock = FileLock(str(self._latest_attempt_path) + ".lock")
        self._active_attempt_lock = FileLock(str(self._active_attempt_path) + ".lock")

    @property
    def descriptor_path(self) -> Path:
        return self._descriptor_path

    @property
    def runtime_state_path(self) -> Path:
        return self._runtime_state_path

    @property
    def latest_attempt_path(self) -> Path:
        return self._latest_attempt_path

    @property
    def active_attempt_path(self) -> Path:
        return self._active_attempt_path

    def load_runtime_descriptor(self) -> ManagedRuntimeDescriptor | None:
        """只读加载canonical descriptor；不迁移、不隔离坏文件、不创建lock。"""
        return self._load_model_read_only(
            path=self._descriptor_path,
            model_type=ManagedRuntimeDescriptor,
            default=None,
        )

    def migrate_runtime_descriptor_for_install(self) -> ManagedRuntimeDescriptor | None:
        """仅供显式install/update使用的descriptor迁移入口。"""
        descriptor = self.load_runtime_descriptor()
        if descriptor is not None:
            return self.migrate_runtime_descriptor(descriptor)
        legacy_path = self._root / "app" / "octoagent" / "data" / "ops" / "managed-runtime.json"
        if legacy_path == self._descriptor_path or not legacy_path.exists():
            return None
        descriptor = self._load_model_read_only(
            path=legacy_path,
            model_type=ManagedRuntimeDescriptor,
            default=None,
        )
        if descriptor is None:
            return None
        normalized = self.migrate_runtime_descriptor(descriptor)
        if not self._descriptor_path.exists():
            self._save_model(self._descriptor_path, self._descriptor_lock, normalized)
        return normalized

    def save_runtime_descriptor(self, descriptor: ManagedRuntimeDescriptor) -> None:
        self._save_model(self._descriptor_path, self._descriptor_lock, descriptor)

    def load_runtime_state(self) -> RuntimeStateSnapshot | None:
        return self._load_model(
            path=self._runtime_state_path,
            lock=self._runtime_state_lock,
            model_type=RuntimeStateSnapshot,
            default=None,
        )

    def save_runtime_state(self, snapshot: RuntimeStateSnapshot) -> None:
        self._save_model(self._runtime_state_path, self._runtime_state_lock, snapshot)

    def clear_runtime_state(self) -> None:
        self._delete_path(self._runtime_state_path, self._runtime_state_lock)

    def load_latest_attempt(self) -> UpdateAttempt | None:
        return self._load_model(
            path=self._latest_attempt_path,
            lock=self._latest_attempt_lock,
            model_type=UpdateAttempt,
            default=None,
        )

    def save_latest_attempt(self, attempt: UpdateAttempt) -> None:
        self._save_model(self._latest_attempt_path, self._latest_attempt_lock, attempt)
        self._save_history_attempt(attempt)

    def load_active_attempt(self) -> UpdateAttempt | None:
        attempt = self._load_model(
            path=self._active_attempt_path,
            lock=self._active_attempt_lock,
            model_type=UpdateAttempt,
            default=None,
        )
        if attempt is None:
            return None
        if attempt.overall_status in (
            UpdateOverallStatus.SUCCEEDED,
            UpdateOverallStatus.FAILED,
            UpdateOverallStatus.ACTION_REQUIRED,
        ):
            self.clear_active_attempt()
            return None
        # 孤儿进程检测：如果活跃 attempt 超时且状态仍非终态，说明进程已被 kill，自动清理
        age = (datetime.now(UTC) - attempt.started_at).total_seconds()
        if age > _STALE_ACTIVE_ATTEMPT_SECONDS:
            self.clear_active_attempt()
            return None
        return attempt

    def save_active_attempt(self, attempt: UpdateAttempt) -> None:
        self._save_model(self._active_attempt_path, self._active_attempt_lock, attempt)

    def try_claim_active_attempt(self, attempt: UpdateAttempt) -> str | None:
        """Atomically persist ``attempt`` only when no active owner exists."""
        with self._active_attempt_lock:
            if self._active_attempt_path.exists():
                return None
            return self._write_model_unlocked(self._active_attempt_path, attempt)

    def load_active_attempt_with_token(self) -> tuple[UpdateAttempt, str] | None:
        with self._active_attempt_lock:
            return self._read_active_attempt_unlocked()

    def update_active_attempt(
        self,
        attempt: UpdateAttempt,
        *,
        compare_token: str,
    ) -> str | None:
        with self._active_attempt_lock:
            current = self._read_active_attempt_unlocked()
            if current is None:
                return None
            active, token = current
            if active.attempt_id != attempt.attempt_id or token != compare_token:
                return None
            return self._write_model_unlocked(self._active_attempt_path, attempt)

    def release_active_attempt(self, owner_id: str, *, compare_token: str) -> bool:
        with self._active_attempt_lock:
            current = self._read_active_attempt_unlocked()
            if current is None:
                return False
            active, token = current
            if active.attempt_id != owner_id or token != compare_token:
                return False
            self._active_attempt_path.unlink()
            return True

    def clear_active_attempt(self) -> None:
        self._delete_path(self._active_attempt_path, self._active_attempt_lock)

    def load_summary(self) -> UpdateAttemptSummary:
        return UpdateAttemptSummary.from_attempt(self.load_latest_attempt())

    def _save_history_attempt(self, attempt: UpdateAttempt) -> None:
        self._history_dir.mkdir(parents=True, exist_ok=True)
        history_path = self._history_dir / f"{attempt.attempt_id}.json"
        history_lock = FileLock(str(history_path) + ".lock")
        self._save_model(history_path, history_lock, attempt)

    def migrate_runtime_descriptor(
        self,
        descriptor: ManagedRuntimeDescriptor,
    ) -> ManagedRuntimeDescriptor:
        normalized, changed = normalize_runtime_descriptor(descriptor)
        if changed:
            self.save_runtime_descriptor(normalized)
        return normalized

    def _load_model(
        self,
        *,
        path: Path,
        lock: FileLock,
        model_type: type[ModelT],
        default: ModelT | None,
    ) -> ModelT | None:
        if not path.exists():
            return default

        with lock:
            try:
                raw = path.read_text(encoding="utf-8")
                payload = json.loads(raw)
                return model_type.model_validate(payload)
            except Exception:
                corrupted = path.with_suffix(path.suffix + ".corrupted")
                shutil.copy2(path, corrupted)
                return default

    @staticmethod
    def _load_model_read_only(
        *,
        path: Path,
        model_type: type[ModelT],
        default: ModelT | None,
    ) -> ModelT | None:
        try:
            raw = path.read_bytes()
        except (FileNotFoundError, OSError):
            return default
        try:
            return model_type.model_validate_json(raw)
        except Exception:
            return default

    def _save_model(self, path: Path, lock: FileLock, model: BaseModel) -> None:
        with lock:
            self._write_model_unlocked(path, model)

    def _write_model_unlocked(self, path: Path, model: BaseModel) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = model.model_dump_json(indent=2)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            Path(tmp_path).replace(path)
        finally:
            tmp = Path(tmp_path)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return hashlib.sha256(text.encode()).hexdigest()

    def _read_active_attempt_unlocked(self) -> tuple[UpdateAttempt, str] | None:
        if not self._active_attempt_path.exists():
            return None
        raw = self._active_attempt_path.read_bytes()
        attempt = UpdateAttempt.model_validate_json(raw)
        return attempt, hashlib.sha256(raw).hexdigest()

    def _delete_path(self, path: Path, lock: FileLock) -> None:
        with lock:
            path.unlink(missing_ok=True)
