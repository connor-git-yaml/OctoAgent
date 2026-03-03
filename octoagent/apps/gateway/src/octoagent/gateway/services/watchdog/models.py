"""Watchdog 内部模型（值对象）-- Feature 011

DriftResult: 漂移检测中间结果，由检测器产生传递给事件写入器
DriftSummary: 漂移摘要（嵌套在 TaskJournalEntry 中）
TaskJournalEntry: Task Journal API 返回单元
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from octoagent.core.models.enums import TaskStatus

# Watchdog 扫描的非终态状态集合（排除终态，scanner.py 和 task_journal.py 共用）
NON_TERMINAL_STATUSES: list[TaskStatus] = [
    TaskStatus.CREATED,
    TaskStatus.RUNNING,
    TaskStatus.QUEUED,
    TaskStatus.WAITING_INPUT,
    TaskStatus.WAITING_APPROVAL,
    TaskStatus.PAUSED,
]

# 漂移类型联合字面量
DriftType = Literal["no_progress", "state_machine_stall", "repeated_failure"]

# Journal 状态联合字面量
JournalState = Literal["running", "stalled", "drifted", "waiting_approval"]


@dataclass
class DriftResult:
    """漂移检测中间结果值对象（不持久化，spec Key Entities）

    由漂移检测器产生，传递给 WatchdogScanner._emit_drift_event()。
    内部传输用，不直接序列化为 API 响应。
    """

    task_id: str
    drift_type: DriftType
    detected_at: datetime
    stall_duration_seconds: float
    suggested_actions: list[str]
    last_progress_ts: datetime | None = None
    # 重复失败模式专属字段（FR-012）
    failure_count: int | None = None
    failure_event_types: list[str] = field(default_factory=list)
    # 状态机漂移专属字段（FR-011）
    current_status: str | None = None


@dataclass
class DriftSummary:
    """漂移摘要（嵌套在 TaskJournalEntry 中，FR-016）

    API 只返回摘要字段，详细诊断通过 drift_artifact_id 引用访问。
    """

    drift_type: str
    stall_duration_seconds: float
    detected_at: str  # ISO 8601 字符串
    failure_count: int | None = None


@dataclass
class TaskJournalEntry:
    """Task Journal API 的返回单元（FR-015 + spec Key Entities）

    摘要 + artifact 引用模式：API 只返回摘要字段，
    详细诊断通过 drift_artifact_id 引用访问（Constitution 原则 11）。
    task_status 使用内部完整 TaskStatus（FR-015，Constitution 原则 14，禁止映射为 A2A 状态）。
    """

    task_id: str
    task_status: str                          # 内部 TaskStatus 值（不映射为 A2A 状态）
    journal_state: JournalState
    last_event_ts: str | None                 # 最近事件时间戳（ISO 8601），无则 None
    suggested_actions: list[str]
    drift_summary: DriftSummary | None = None
    drift_artifact_id: str | None = None      # 详细诊断 artifact 引用（可选）
