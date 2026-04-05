"""Feature 026: automation jobs durable store。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock
from octoagent.core.models import AutomationJob, AutomationJobRun
from pydantic import BaseModel, Field


class AutomationStoreSnapshot(BaseModel):
    version: int = 1
    jobs: list[AutomationJob] = Field(default_factory=list)
    runs: list[AutomationJobRun] = Field(default_factory=list)


class AutomationStore:
    """按 project root 持久化 automation jobs / runs。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._path = project_root / "data" / "control-plane" / "automation-jobs.json"
        self._lock = FileLock(str(self._path) + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AutomationStoreSnapshot:
        if not self._path.exists():
            return AutomationStoreSnapshot()

        with self._lock:
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return AutomationStoreSnapshot()
        return AutomationStoreSnapshot.model_validate(payload)

    def list_jobs(self) -> list[AutomationJob]:
        return self.load().jobs

    def get_job(self, job_id: str) -> AutomationJob | None:
        return next((item for item in self.load().jobs if item.job_id == job_id), None)

    def list_runs(self, job_id: str | None = None, limit: int = 50) -> list[AutomationJobRun]:
        runs = self.load().runs
        if job_id:
            runs = [item for item in runs if item.job_id == job_id]
        runs.sort(key=lambda item: item.started_at, reverse=True)
        return runs[:limit]

    def save_job(self, job: AutomationJob) -> AutomationJob:
        snapshot = self.load()
        jobs = [item for item in snapshot.jobs if item.job_id != job.job_id]
        jobs.append(job.model_copy(update={"updated_at": datetime.now(tz=UTC)}))
        snapshot.jobs = sorted(jobs, key=lambda item: item.created_at)
        self._save(snapshot)
        return job

    def delete_job(self, job_id: str) -> bool:
        snapshot = self.load()
        before = len(snapshot.jobs)
        snapshot.jobs = [item for item in snapshot.jobs if item.job_id != job_id]
        if before == len(snapshot.jobs):
            return False
        self._save(snapshot)
        return True

    def save_run(self, run: AutomationJobRun) -> AutomationJobRun:
        snapshot = self.load()
        runs = [item for item in snapshot.runs if item.run_id != run.run_id]
        runs.append(run)
        runs.sort(key=lambda item: item.started_at, reverse=True)
        snapshot.runs = runs[:500]
        self._save(snapshot)
        return run

    def get_run(self, run_id: str) -> AutomationJobRun | None:
        return next((item for item in self.load().runs if item.run_id == run_id), None)

    def _save(self, snapshot: AutomationStoreSnapshot) -> None:
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
