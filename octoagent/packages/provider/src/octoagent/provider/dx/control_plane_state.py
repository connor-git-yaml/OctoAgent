"""Feature 026: control plane durable state。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from filelock import FileLock
from octoagent.core.models import ControlPlaneState


class ControlPlaneStateStore:
    """保存当前 project/workspace/session focus 等最小控制台状态。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._path = project_root / "data" / "control-plane" / "state.json"
        self._lock = FileLock(str(self._path) + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ControlPlaneState:
        if not self._path.exists():
            return ControlPlaneState()

        with self._lock:
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return ControlPlaneState()
        return ControlPlaneState.model_validate(payload)

    def save(self, state: ControlPlaneState) -> ControlPlaneState:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = state.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2)

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
        return state
