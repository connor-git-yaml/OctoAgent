from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Feature 063: OnboardingState — Bootstrap 生命周期
# ---------------------------------------------------------------------------


@dataclass
class OnboardingState:
    """Bootstrap 引导状态，持久化到 .onboarding-state.json。"""

    bootstrap_seeded_at: str | None = None
    onboarding_completed_at: str | None = None

    def is_completed(self) -> bool:
        return self.onboarding_completed_at is not None


def _onboarding_state_path(project_root: Path) -> Path:
    """返回 onboarding 状态文件路径。"""
    return project_root.resolve() / "behavior" / ".onboarding-state.json"


def load_onboarding_state(
    project_root: Path,
    *,
    bootstrap_file_path: Path | None = None,
) -> OnboardingState:
    """读取 onboarding 状态，含被动完成检测（路径 B：文件删除触发）。

    如果 bootstrap_seeded_at 存在但 BOOTSTRAP.md 已不在磁盘上，
    自动标记 onboarding 完成。
    """
    state_path = _onboarding_state_path(project_root)
    state = OnboardingState()

    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            state.bootstrap_seeded_at = raw.get("bootstrap_seeded_at")
            state.onboarding_completed_at = raw.get("onboarding_completed_at")
        except (json.JSONDecodeError, OSError):
            log.warning("onboarding_state_read_failed", path=str(state_path))

    # 路径 B（T1.5）：文件删除触发完成
    if state.bootstrap_seeded_at and not state.onboarding_completed_at:
        if bootstrap_file_path is None:
            bootstrap_file_path = (
                project_root.resolve() / "behavior" / "system" / "BOOTSTRAP.md"
            )
        if not bootstrap_file_path.exists():
            state.onboarding_completed_at = datetime.now(UTC).isoformat()
            save_onboarding_state(project_root, state)
            log.info(
                "onboarding_completed_via_file_deletion",
                bootstrap_path=str(bootstrap_file_path),
            )

    return state


def save_onboarding_state(project_root: Path, state: OnboardingState) -> None:
    """原子写入 onboarding 状态文件（先写 .tmp 再 rename）。"""
    state_path = _onboarding_state_path(project_root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "bootstrap_seeded_at": state.bootstrap_seeded_at,
        "onboarding_completed_at": state.onboarding_completed_at,
    }
    # 原子写入：先写临时文件再 rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(state_path.parent), suffix=".tmp", prefix=".onboarding-state-",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(state_path))
    except Exception:
        # 清理临时文件
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def mark_onboarding_completed(project_root: Path) -> OnboardingState:
    """将 onboarding 标记为已完成。"""
    state = load_onboarding_state(project_root)
    if not state.onboarding_completed_at:
        state.onboarding_completed_at = datetime.now(UTC).isoformat()
        save_onboarding_state(project_root, state)
        log.info("onboarding_completed_via_marker", project_root=str(project_root))
    return state
