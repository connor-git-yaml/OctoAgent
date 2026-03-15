"""TaskJournalService -- Feature 011 FR-014, FR-015, FR-016

实时聚合 Task Journal 查询服务：每次请求从 TaskStore + EventStore 动态聚合，
按分组规则（contracts/rest-api.md）将非终态任务分为四类：
running / stalled / drifted / waiting_approval

分组优先级（按契约文档顺序）：
1. WAITING_APPROVAL 任务始终独立归组
2. 有 DRIFT 事件且仍无进展 -> drifted
3. 有 DRIFT 事件但已恢复进展 -> running
4. 无 DRIFT 事件且超过阈值无进展 -> stalled
5. 其余非终态 -> running
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, Field

from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.core.store import StoreGroup

from .watchdog.config import WatchdogConfig
from .watchdog.detectors import PROGRESS_EVENT_TYPES
from .watchdog.models import NON_TERMINAL_STATUSES, DriftSummary, JournalState, TaskJournalEntry

log = structlog.get_logger()

# 进展事件类型列表（用于 Task Journal 分组判断，来源同 detectors.PROGRESS_EVENT_TYPES）
_PROGRESS_EVENT_TYPES_LIST: list[EventType] = list(PROGRESS_EVENT_TYPES)


class JournalSummary(BaseModel):
    """Journal 摘要统计（FR-014）"""

    total: int = Field(description="非终态任务总数")
    running: int = Field(description="正常运行中任务数")
    stalled: int = Field(description="疑似卡死任务数")
    drifted: int = Field(description="已检测到漂移事件任务数")
    waiting_approval: int = Field(description="待审批任务数")


class JournalGroups(BaseModel):
    """Journal 分组（FR-014）"""

    running: list[dict[str, Any]] = Field(default_factory=list)
    stalled: list[dict[str, Any]] = Field(default_factory=list)
    drifted: list[dict[str, Any]] = Field(default_factory=list)
    waiting_approval: list[dict[str, Any]] = Field(default_factory=list)


class JournalResponse(BaseModel):
    """Task Journal API 响应体（FR-014）"""

    generated_at: str = Field(description="Journal 生成时间戳（ISO 8601）")
    summary: JournalSummary
    groups: JournalGroups


def _entry_to_dict(entry: TaskJournalEntry) -> dict[str, Any]:
    """将 TaskJournalEntry 转换为 API 响应字典"""
    drift_summary_dict = None
    if entry.drift_summary is not None:
        drift_summary_dict = {
            "drift_type": entry.drift_summary.drift_type,
            "stall_duration_seconds": entry.drift_summary.stall_duration_seconds,
            "detected_at": entry.drift_summary.detected_at,
            "failure_count": entry.drift_summary.failure_count,
        }
    return {
        "task_id": entry.task_id,
        "task_status": entry.task_status,
        "journal_state": entry.journal_state,
        "last_event_ts": entry.last_event_ts,
        "drift_summary": drift_summary_dict,
        "drift_artifact_id": entry.drift_artifact_id,
        "suggested_actions": entry.suggested_actions,
    }


class TaskJournalService:
    """Task Journal 实时聚合查询服务（FR-014, FR-015, FR-016）

    每次调用 get_journal() 动态从 TaskStore + EventStore 聚合，
    无物化缓存，适合活跃任务 <= 200 的 MVP 场景。
    """

    def __init__(self, store_group: StoreGroup) -> None:
        self._store_group = store_group

    async def get_journal(self, config: WatchdogConfig) -> JournalResponse:
        """生成 Task Journal 视图（实时聚合，FR-014）

        Args:
            config: WatchdogConfig 提供 no_progress_threshold_seconds

        Returns:
            JournalResponse 包含四分组和摘要统计
        """
        now = datetime.now(UTC)
        generated_at = now.isoformat()
        threshold = config.no_progress_threshold_seconds
        since_ts_for_progress = now - timedelta(seconds=threshold)
        # DRIFT 事件查询窗口：查询最近一段时间内的 DRIFT 事件（使用 failure_window_seconds 或固定窗口）
        drift_since_ts = now - timedelta(seconds=config.failure_window_seconds)

        # 1. 获取所有非终态任务（单次原子查询）
        tasks = await self._store_group.task_store.list_tasks_by_statuses(
            NON_TERMINAL_STATUSES
        )

        running_entries: list[dict] = []
        stalled_entries: list[dict] = []
        drifted_entries: list[dict] = []
        waiting_approval_entries: list[dict] = []

        for task in tasks:
            # 排除系统内部任务（如 ops-control-plane 审计日志载体）
            if task.task_id.startswith("ops-"):
                continue
            task_status = TaskStatus(task.status)

            # 2. 获取最近事件时间戳（FR-015 last_event_ts 字段）
            latest_event_ts = await self._store_group.event_store.get_latest_event_ts(
                task.task_id
            )
            last_event_ts_iso = latest_event_ts.isoformat() if latest_event_ts else None

            # 3. 获取 DRIFT 事件历史（判断分组）
            drift_events = await self._store_group.event_store.get_events_by_types_since(
                task_id=task.task_id,
                event_types=[EventType.TASK_DRIFT_DETECTED],
                since_ts=drift_since_ts,
            )

            # 4. 按分组规则确定 journal_state（优先级严格按 contracts/rest-api.md）
            journal_state: JournalState
            suggested_actions: list[str] = []
            drift_summary: DriftSummary | None = None
            drift_artifact_id: str | None = None

            # 规则 2: WAITING_APPROVAL 始终独立归组
            if task_status == TaskStatus.WAITING_APPROVAL:
                journal_state = "waiting_approval"
                suggested_actions = ["review_approval_request"]

            elif drift_events:
                # 有 DRIFT 事件，需判断是否已恢复进展
                progress_events = await self._store_group.event_store.get_events_by_types_since(
                    task_id=task.task_id,
                    event_types=_PROGRESS_EVENT_TYPES_LIST,
                    since_ts=since_ts_for_progress,
                )

                # 取最近一次 DRIFT 事件构建摘要
                latest_drift = max(drift_events, key=lambda e: e.ts)
                drift_payload = latest_drift.payload
                # artifact_ref 来自 DRIFT 事件 payload
                drift_artifact_id = drift_payload.get("artifact_ref")

                if not progress_events:
                    # 规则 3: 有 DRIFT 事件且仍无进展 -> drifted
                    journal_state = "drifted"
                    suggested_actions = drift_payload.get(
                        "suggested_actions", ["check_worker_logs"]
                    )
                    drift_summary = DriftSummary(
                        drift_type=drift_payload.get("drift_type", "no_progress"),
                        stall_duration_seconds=drift_payload.get("stall_duration_seconds", 0.0),
                        detected_at=latest_drift.ts.isoformat(),
                        failure_count=drift_payload.get("failure_count"),
                    )
                else:
                    # 规则 4: 有 DRIFT 事件但已恢复进展 -> running
                    journal_state = "running"
                    suggested_actions = []
                    drift_summary = None  # 已恢复，不展示漂移摘要
                    drift_artifact_id = None

            else:
                # 无 DRIFT 事件
                progress_events = await self._store_group.event_store.get_events_by_types_since(
                    task_id=task.task_id,
                    event_types=_PROGRESS_EVENT_TYPES_LIST,
                    since_ts=since_ts_for_progress,
                )

                if not progress_events:
                    # 规则 5: 无 DRIFT 事件且超过阈值无进展 -> stalled
                    # 额外检查：需确保任务驻留足够旧（避免刚创建的任务误判为 stalled）
                    updated_at = task.updated_at
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=UTC)
                    stall_duration = (now - updated_at).total_seconds()

                    if stall_duration >= threshold:
                        journal_state = "stalled"
                        suggested_actions = ["check_worker_logs", "cancel_task_if_confirmed"]
                    else:
                        # 刚创建或近期更新，归为 running
                        journal_state = "running"
                        suggested_actions = []
                else:
                    # 规则 6: 有进展事件 -> running
                    journal_state = "running"
                    suggested_actions = []

            entry = TaskJournalEntry(
                task_id=task.task_id,
                # FR-015: task_status 使用内部 TaskStatus（不降级为 A2A）
                task_status=task.status,
                journal_state=journal_state,
                last_event_ts=last_event_ts_iso,
                suggested_actions=suggested_actions,
                drift_summary=drift_summary,
                drift_artifact_id=drift_artifact_id,
            )

            entry_dict = _entry_to_dict(entry)
            if journal_state == "running":
                running_entries.append(entry_dict)
            elif journal_state == "stalled":
                stalled_entries.append(entry_dict)
            elif journal_state == "drifted":
                drifted_entries.append(entry_dict)
            elif journal_state == "waiting_approval":
                waiting_approval_entries.append(entry_dict)

        total = len(tasks)
        return JournalResponse(
            generated_at=generated_at,
            summary=JournalSummary(
                total=total,
                running=len(running_entries),
                stalled=len(stalled_entries),
                drifted=len(drifted_entries),
                waiting_approval=len(waiting_approval_entries),
            ),
            groups=JournalGroups(
                running=running_entries,
                stalled=stalled_entries,
                drifted=drifted_entries,
                waiting_approval=waiting_approval_entries,
            ),
        )
