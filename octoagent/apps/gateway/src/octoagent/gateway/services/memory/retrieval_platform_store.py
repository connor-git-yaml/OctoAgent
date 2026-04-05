"""Feature 054: retrieval platform durable store。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from filelock import FileLock
from octoagent.core.models import EmbeddingProfile, IndexBuildJob, IndexGeneration
from pydantic import BaseModel, Field


class RetrievalPlatformStoreSnapshot(BaseModel):
    version: int = 1
    profiles: list[EmbeddingProfile] = Field(default_factory=list)
    generations: list[IndexGeneration] = Field(default_factory=list)
    build_jobs: list[IndexBuildJob] = Field(default_factory=list)
    cancelled_targets: dict[str, str] = Field(default_factory=dict)


class RetrievalPlatformStore:
    """按 project root 持久化 retrieval platform generations / jobs。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._path = project_root / "data" / "control-plane" / "retrieval-platform.json"
        self._lock = FileLock(str(self._path) + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> RetrievalPlatformStoreSnapshot:
        if not self._path.exists():
            return RetrievalPlatformStoreSnapshot()

        with self._lock:
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return RetrievalPlatformStoreSnapshot()
        return RetrievalPlatformStoreSnapshot.model_validate(payload)

    def save(self, snapshot: RetrievalPlatformStoreSnapshot) -> RetrievalPlatformStoreSnapshot:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2)

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
        return snapshot
