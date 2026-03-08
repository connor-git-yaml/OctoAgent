"""029 source/run durable store。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from filelock import FileLock

from .import_workbench_models import ImportRunDocument, ImportSourceDocument


class ImportSourceStore:
    """基于 JSON 文件的 source / run durable store。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root / "data" / "control-plane" / "imports"
        self._sources_dir = self._root / "sources"
        self._runs_dir = self._root / "runs"

    def save_source(self, document: ImportSourceDocument) -> ImportSourceDocument:
        self._write_json(
            self._sources_dir / f"{document.source_id}.json",
            document.model_dump(mode="json"),
        )
        return document

    def get_source(self, source_id: str) -> ImportSourceDocument | None:
        return self._read_model(self._sources_dir / f"{source_id}.json", ImportSourceDocument)

    def list_sources(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[ImportSourceDocument]:
        items = self._read_all(self._sources_dir, ImportSourceDocument)
        return self._filter_scoped(items, project_id=project_id, workspace_id=workspace_id)

    def save_run(self, document: ImportRunDocument) -> ImportRunDocument:
        self._write_json(
            self._runs_dir / f"{document.resource_id}.json",
            document.model_dump(mode="json"),
        )
        return document

    def get_run(self, run_id: str) -> ImportRunDocument | None:
        return self._read_model(self._runs_dir / f"{run_id}.json", ImportRunDocument)

    def list_runs(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        source_id: str | None = None,
        limit: int = 20,
    ) -> list[ImportRunDocument]:
        items = self._read_all(self._runs_dir, ImportRunDocument)
        scoped = self._filter_scoped(items, project_id=project_id, workspace_id=workspace_id)
        if source_id:
            scoped = [item for item in scoped if item.source_id == source_id]
        scoped.sort(key=lambda item: item.updated_at, reverse=True)
        return scoped[:limit]

    def _read_all(self, directory: Path, model):
        if not directory.exists():
            return []
        items = []
        for path in sorted(directory.glob("*.json")):
            loaded = self._read_model(path, model)
            if loaded is not None:
                items.append(loaded)
        return items

    @staticmethod
    def _filter_scoped(items, *, project_id: str | None, workspace_id: str | None):
        result = []
        for item in items:
            if project_id and getattr(item, "active_project_id", "") != project_id:
                continue
            if workspace_id and getattr(item, "active_workspace_id", "") != workspace_id:
                continue
            result.append(item)
        return result

    @staticmethod
    def _read_model(path: Path, model):
        if not path.exists():
            return None
        lock = FileLock(str(path) + ".lock")
        with lock:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return model.model_validate(payload)

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
