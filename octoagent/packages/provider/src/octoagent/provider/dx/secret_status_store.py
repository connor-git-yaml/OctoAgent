"""Feature 025: secret apply / materialization 状态持久化。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TypeVar

from filelock import FileLock
from pydantic import BaseModel

from .secret_models import RuntimeSecretMaterialization, SecretApplyRun

ModelT = TypeVar("ModelT", bound=BaseModel)


class SecretStatusStore:
    """CLI / doctor / inspect 共用的 secret 生命周期状态源。"""

    def __init__(self, project_root: Path, *, project_id: str | None = None) -> None:
        self._root = project_root.resolve()
        self._project_id = project_id
        base_ops_dir = self._root / "data" / "ops"
        self._ops_dir = (
            base_ops_dir / "projects" / project_id
            if project_id is not None
            else base_ops_dir
        )
        self._apply_path = self._ops_dir / "secret-apply.json"
        self._materialization_path = self._ops_dir / "secret-materialization.json"
        self._apply_lock = FileLock(str(self._apply_path) + ".lock")
        self._materialization_lock = FileLock(str(self._materialization_path) + ".lock")

    def for_project(self, project_id: str) -> SecretStatusStore:
        return SecretStatusStore(self._root, project_id=project_id)

    def load_apply(self) -> SecretApplyRun | None:
        return self._load_model(
            path=self._apply_path,
            lock=self._apply_lock,
            model_type=SecretApplyRun,
        )

    def save_apply(self, run: SecretApplyRun) -> None:
        self._save_model(self._apply_path, self._apply_lock, run)

    def load_materialization(self) -> RuntimeSecretMaterialization | None:
        return self._load_model(
            path=self._materialization_path,
            lock=self._materialization_lock,
            model_type=RuntimeSecretMaterialization,
        )

    def save_materialization(self, snapshot: RuntimeSecretMaterialization) -> None:
        self._save_model(self._materialization_path, self._materialization_lock, snapshot)

    def _load_model(
        self,
        *,
        path: Path,
        lock: FileLock,
        model_type: type[ModelT],
    ) -> ModelT | None:
        if not path.exists():
            return None
        with lock:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return model_type.model_validate(payload)
            except Exception:
                corrupted = path.with_suffix(path.suffix + ".corrupted")
                shutil.copy2(path, corrupted)
                return None

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
