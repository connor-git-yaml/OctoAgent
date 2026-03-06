from __future__ import annotations

from pathlib import Path

from octoagent.provider.dx.onboarding_models import OnboardingSession
from octoagent.provider.dx.onboarding_store import OnboardingSessionStore


def test_store_roundtrip(tmp_path: Path) -> None:
    store = OnboardingSessionStore(tmp_path)
    session = OnboardingSession.create(str(tmp_path))
    store.save(session)

    loaded = store.load()
    assert loaded is not None
    assert loaded.project_root == str(tmp_path)


def test_store_corrupted_file_backup(tmp_path: Path) -> None:
    store = OnboardingSessionStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{bad json", encoding="utf-8")

    loaded = store.load()
    assert loaded is None
    assert store.last_issue == "corrupted"
    assert Path(str(store.path) + ".corrupted").exists()


def test_store_reset_creates_backup(tmp_path: Path) -> None:
    store = OnboardingSessionStore(tmp_path)
    session = OnboardingSession.create(str(tmp_path))
    store.save(session)

    store.reset()
    assert not store.path.exists()
    assert Path(str(store.path) + ".bak").exists()
