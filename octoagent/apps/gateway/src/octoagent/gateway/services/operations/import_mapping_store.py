"""029 import mapping durable store。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from filelock import FileLock
from octoagent.memory import ImportMappingProfile


class ImportMappingStore:
    """基于 JSON 文件保存 project-scoped mapping profile。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root / "data" / "control-plane" / "imports" / "mappings"

    def save(self, profile: ImportMappingProfile) -> ImportMappingProfile:
        path = self._root / f"{profile.mapping_id}.json"
        self._write_json(path, profile.model_dump(mode="json"))
        return profile

    def get(self, mapping_id: str) -> ImportMappingProfile | None:
        path = self._root / f"{mapping_id}.json"
        if not path.exists():
            return None
        lock = FileLock(str(path) + ".lock")
        with lock:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return ImportMappingProfile.model_validate(payload)

    def list(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        source_id: str | None = None,
    ) -> list[ImportMappingProfile]:
        if not self._root.exists():
            return []
        items: list[ImportMappingProfile] = []
        for path in sorted(self._root.glob("*.json")):
            profile = self.get(path.stem)
            if profile is None:
                continue
            if project_id and profile.project_id != project_id:
                continue
            if workspace_id and profile.workspace_id != workspace_id:
                continue
            if source_id and profile.source_id != source_id:
                continue
            items.append(profile)
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items

    def get_latest(
        self,
        *,
        project_id: str,
        workspace_id: str,
        source_id: str,
    ) -> ImportMappingProfile | None:
        items = self.list(
            project_id=project_id,
            workspace_id=workspace_id,
            source_id=source_id,
        )
        return items[0] if items else None

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(path) + ".lock")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
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
