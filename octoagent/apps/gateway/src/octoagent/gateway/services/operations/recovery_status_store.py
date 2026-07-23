"""Feature 022 recovery / backup 状态持久化。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TypeVar

from filelock import FileLock
from octoagent.core.models import BackupBundle, RecoveryDrillRecord, RecoverySummary
from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class RecoveryStatusStore:
    """CLI / Web 共用的最近一次 backup 与 recovery drill 状态源。"""

    def __init__(self, project_root: Path, *, data_dir: Path | None = None) -> None:
        self._root = project_root.resolve()
        self._data_dir = data_dir.resolve() if data_dir is not None else self._root / "data"
        self._ops_dir = self._data_dir / "ops"
        self._latest_backup_path = self._ops_dir / "latest-backup.json"
        self._recovery_drill_path = self._ops_dir / "recovery-drill.json"
        self._latest_backup_lock = FileLock(str(self._latest_backup_path) + ".lock")
        self._recovery_drill_lock = FileLock(str(self._recovery_drill_path) + ".lock")

    @property
    def latest_backup_path(self) -> Path:
        return self._latest_backup_path

    @property
    def recovery_drill_path(self) -> Path:
        return self._recovery_drill_path

    def load_latest_backup(self) -> BackupBundle | None:
        return self._load_model(
            path=self._latest_backup_path,
            lock=self._latest_backup_lock,
            model_type=BackupBundle,
            default=None,
        )

    def save_latest_backup(self, bundle: BackupBundle) -> None:
        self._save_model(self._latest_backup_path, self._latest_backup_lock, bundle)

    def load_recovery_drill(self) -> RecoveryDrillRecord:
        record = self._load_model(
            path=self._recovery_drill_path,
            lock=self._recovery_drill_lock,
            model_type=RecoveryDrillRecord,
            default=RecoveryDrillRecord(),
        )
        return record or RecoveryDrillRecord()

    def save_recovery_drill(self, record: RecoveryDrillRecord) -> None:
        self._save_model(self._recovery_drill_path, self._recovery_drill_lock, record)

    def load_summary(self) -> RecoverySummary:
        return RecoverySummary.from_records(
            latest_backup=self.load_latest_backup(),
            latest_recovery_drill=self.load_recovery_drill(),
        )

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
