"""Feature 015 onboarding session 持久化。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from filelock import FileLock

from .onboarding_models import OnboardingSession


class OnboardingSessionStore:
    """项目级 onboarding session 存储。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._path = project_root / "data" / "onboarding-session.json"
        self._lock = FileLock(str(self._path) + ".lock")
        self.last_issue: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> OnboardingSession | None:
        self.last_issue = None
        if not self._path.exists():
            return None

        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                return OnboardingSession.model_validate(data)
            except Exception:
                backup = self._path.with_suffix(self._path.suffix + ".corrupted")
                shutil.copy2(self._path, backup)
                self.last_issue = "corrupted"
                return None

    def save(self, session: OnboardingSession) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = session.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2)

        with self._lock:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent),
                suffix=".tmp",
            )
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
        backup = self._path.with_suffix(self._path.suffix + ".bak")
        with self._lock:
            shutil.copy2(self._path, backup)
            self._path.unlink(missing_ok=True)
