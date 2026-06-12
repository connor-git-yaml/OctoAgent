from __future__ import annotations

import re
from enum import StrEnum


SHARED_BEHAVIOR_FILE_IDS = ("AGENTS.md", "USER.md", "TOOLS.md", "BOOTSTRAP.md")
PROJECT_SHARED_BEHAVIOR_FILE_IDS = ("PROJECT.md", "KNOWLEDGE.md")
AGENT_PRIVATE_BEHAVIOR_FILE_IDS = ("IDENTITY.md", "SOUL.md", "HEARTBEAT.md")
PROJECT_AGENT_OVERLAY_FILE_IDS = ("IDENTITY.md", "SOUL.md", "TOOLS.md", "PROJECT.md")
INSTRUCTION_BOOTSTRAP_FILE_IDS = ("README.md",)
CORE_BEHAVIOR_FILE_IDS = (
    "AGENTS.md",
    "USER.md",
    "PROJECT.md",
    "KNOWLEDGE.md",
    "TOOLS.md",
    "BOOTSTRAP.md",
)
ADVANCED_BEHAVIOR_FILE_IDS = AGENT_PRIVATE_BEHAVIOR_FILE_IDS
ALL_BEHAVIOR_FILE_IDS = CORE_BEHAVIOR_FILE_IDS + ADVANCED_BEHAVIOR_FILE_IDS
BEHAVIOR_FILE_BUDGETS = {
    "AGENTS.md": 3200,
    "USER.md": 1800,
    "PROJECT.md": 2400,
    "KNOWLEDGE.md": 2200,
    "TOOLS.md": 4000,
    "BOOTSTRAP.md": 2200,
    "SOUL.md": 1600,
    "IDENTITY.md": 1600,
    "HEARTBEAT.md": 1600,
}
BEHAVIOR_OVERLAY_ORDER = (
    "default_template",
    "system_file",
    "system_local_file",
    "agent_file",
    "agent_local_file",
    "project_file",
    "project_local_file",
    "project_agent_file",
    "project_agent_local_file",
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")

SHARED_BOOTSTRAP_TEMPLATE_IDS = tuple(
    f"behavior:system:{file_id}" for file_id in SHARED_BEHAVIOR_FILE_IDS
)
AGENT_PRIVATE_BOOTSTRAP_TEMPLATE_IDS = tuple(
    f"behavior:agent:{file_id}" for file_id in AGENT_PRIVATE_BEHAVIOR_FILE_IDS
)
PROJECT_SHARED_BOOTSTRAP_TEMPLATE_IDS = tuple(
    [f"behavior:project:{file_id}" for file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS]
    + [f"behavior:project:instructions/{file_id}" for file_id in INSTRUCTION_BOOTSTRAP_FILE_IDS]
)
PROJECT_AGENT_BOOTSTRAP_TEMPLATE_IDS = tuple(
    f"behavior:project_agent:{file_id}" for file_id in PROJECT_AGENT_OVERLAY_FILE_IDS
)

# Feature 063: Bootstrap 完成标记
BOOTSTRAP_COMPLETED_MARKER = "<!-- COMPLETED -->"

# Feature 063: 行为文件总大小警告阈值（字符）
_BEHAVIOR_SIZE_WARNING_THRESHOLD = 15000
_BEHAVIOR_TEMPLATE_PACKAGE = "octoagent.core.behavior_templates"
_BEHAVIOR_TEMPLATE_VARIANTS = {
    ("IDENTITY.md", False): "IDENTITY.main.md",
    ("IDENTITY.md", True): "IDENTITY.worker.md",
    # F095 Phase B: SOUL/HEARTBEAT worker variant 派发——
    # is_worker_profile=True 时使用 SOUL.worker.md / HEARTBEAT.worker.md
    # 模板内容显式约束 Worker 不主动与用户对话（H1 哲学守护）+ 通过 A2A 回报主 Agent
    ("SOUL.md", True): "SOUL.worker.md",
    ("HEARTBEAT.md", True): "HEARTBEAT.worker.md",
}


# ---------------------------------------------------------------------------
# Feature 063: BehaviorLoadProfile — 差异化加载
# ---------------------------------------------------------------------------


class BehaviorLoadProfile(StrEnum):
    """Agent 角色对应的行为文件加载级别。"""

    FULL = "full"  # 主 Agent：全部 9 个文件
    # F095 Phase C: WORKER 白名单从 5 文件扩到 8 文件，含 USER + SOUL + HEARTBEAT；
    # 不含 BOOTSTRAP（实测内容是主 Agent 用户首次见面对话脚本，违反 H1）。
    # IDENTITY/SOUL/HEARTBEAT 通过 _BEHAVIOR_TEMPLATE_VARIANTS 派发 worker variant
    # 模板（已在 Phase B 就位），扩白名单不会让 Worker 看到主 Agent 通用模板。
    WORKER = "worker"  # Worker：AGENTS + TOOLS + IDENTITY + PROJECT + KNOWLEDGE + USER + SOUL + HEARTBEAT
    MINIMAL = "minimal"  # Subagent：AGENTS + TOOLS + IDENTITY + USER


_PROFILE_ALLOWLIST: dict[BehaviorLoadProfile, frozenset[str]] = {
    BehaviorLoadProfile.FULL: frozenset(ALL_BEHAVIOR_FILE_IDS),
    BehaviorLoadProfile.WORKER: frozenset({
        "AGENTS.md", "TOOLS.md", "IDENTITY.md", "PROJECT.md", "KNOWLEDGE.md",
        # F095 Phase C 新增：USER（用户长期偏好）+ SOUL（worker variant）+
        # HEARTBEAT（worker variant）；H1 哲学由 SOUL.worker.md 内容守护。
        "USER.md", "SOUL.md", "HEARTBEAT.md",
    }),
    BehaviorLoadProfile.MINIMAL: frozenset({
        "AGENTS.md", "TOOLS.md", "IDENTITY.md", "USER.md",
    }),
}


def get_profile_allowlist(profile: BehaviorLoadProfile) -> frozenset[str]:
    """返回指定 load_profile 对应的 file_id 白名单（公共 API）。"""
    return _PROFILE_ALLOWLIST[profile]
