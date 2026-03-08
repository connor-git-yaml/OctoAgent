"""Feature 024 update / runtime 状态持久化。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
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
from pydantic import BaseModel

from .backup_service import resolve_data_dir, resolve_project_root

ModelT = TypeVar("ModelT", bound=BaseModel)


class UpdateStatusStore:
    """CLI / Web 共用的 canonical update 状态源。"""

    def __init__(self, project_root: Path, *, data_dir: Path | None = None) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._data_dir = (
            data_dir.resolve()
            if data_dir is not None
            else resolve_data_dir(self._root)
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
        return self._load_model(
            path=self._descriptor_path,
            lock=self._descriptor_lock,
            model_type=ManagedRuntimeDescriptor,
            default=None,
        )

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
        return attempt

    def save_active_attempt(self, attempt: UpdateAttempt) -> None:
        self._save_model(self._active_attempt_path, self._active_attempt_lock, attempt)

    def clear_active_attempt(self) -> None:
        self._delete_path(self._active_attempt_path, self._active_attempt_lock)

    def load_summary(self) -> UpdateAttemptSummary:
        return UpdateAttemptSummary.from_attempt(self.load_latest_attempt())

    def _save_history_attempt(self, attempt: UpdateAttempt) -> None:
        self._history_dir.mkdir(parents=True, exist_ok=True)
        history_path = self._history_dir / f"{attempt.attempt_id}.json"
        history_lock = FileLock(str(history_path) + ".lock")
        self._save_model(history_path, history_lock, attempt)

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

    def _save_model(self, path: Path, lock: FileLock, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = model.model_dump_json(indent=2)

        with lock:
            fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                Path(tmp_path).replace(path)
            finally:
                tmp = Path(tmp_path)
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    def _delete_path(self, path: Path, lock: FileLock) -> None:
        with lock:
            path.unlink(missing_ok=True)
