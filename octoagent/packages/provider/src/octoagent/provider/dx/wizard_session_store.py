"""Feature 025: CLI wizard session 持久化。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, Field

from .control_plane_models import WizardSessionDocument


class WizardSessionRecord(BaseModel):
    """CLI wizard 的本地持久化记录。"""

    session_id: str
    project_id: str
    surface: str = "cli"
    document_version: str = "026-a"
    schema_version: str = "1"
    current_step_id: str = "project"
    status: str = "pending"
    blocking_reason: str = ""
    draft_config: dict[str, Any] = Field(default_factory=dict)
    draft_secret_bindings: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    document: WizardSessionDocument


class WizardSessionStore:
    """项目级 wizard session store。"""

    def __init__(self, project_root: Path, *, surface: str = "cli") -> None:
        self._root = project_root.resolve()
        self._path = self._root / "data" / f"wizard-session-{surface}.json"
        self._lock = FileLock(str(self._path) + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> WizardSessionRecord | None:
        if not self._path.exists():
            return None
        with self._lock:
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                return WizardSessionRecord.model_validate(payload)
            except Exception:
                corrupted = self._path.with_suffix(self._path.suffix + ".corrupted")
                shutil.copy2(self._path, corrupted)
                return None

    def save(self, record: WizardSessionRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = record.model_dump_json(indent=2)
        with self._lock:
            fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                Path(tmp_path).replace(self._path)
            finally:
                tmp = Path(tmp_path)
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    def reset(self) -> None:
        if not self._path.exists():
            return
        with self._lock:
            backup = self._path.with_suffix(self._path.suffix + ".bak")
            shutil.copy2(self._path, backup)
            self._path.unlink(missing_ok=True)
