"""F107 W1-B：behavior 文件版本记录 helper（best-effort，写盘成功后调用）。

两调用方共用：``misc_tools.behavior_write_file``（LLM 工具）+ ``control_plane.worker_service``。

设计要点：
- **record-after + 首版 baseline（FR-W1-2b）**：写盘成功后记录新内容为新版；首次记录（key 下无
  任何版本）且 ``old_content`` 非空（写盘前旧盘内容）则先记 baseline 再记新内容。
- **best-effort，不阻断写**：写盘已成功，版本记录是 observability（#8）；记录/事件失败仅 structlog
  降级，不向调用方抛（写本身已 durable）。
- **EventStore 审计（#2 / Codex MED-C）**：emit ``BEHAVIOR_VERSION_RECORDED``。Event 要求 task_id；
  无 task 上下文（如部分 control_plane 写）则跳过事件但版本仍记录。
- **key 派生在调用方**（scope/slug 已知，Codex MED-4）：``behavior_version_key_for`` 按 scope 归零。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.behavior_workspace import behavior_version_key_from_path
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import BehaviorVersionRecordedPayload

log = structlog.get_logger("behavior.versioning")


def read_disk_content(path: Any) -> str | None:
    """读取写盘前的旧内容作 baseline（不存在/读失败 → None，不抛）。"""
    try:
        if path is None or not path.exists():
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


async def record_behavior_version(
    *,
    stores: Any,
    project_root: Any,
    resolved_path: Any,
    new_content: str,
    old_content: str | None,
    task_id: str = "",
    source: str = "",
    strict: bool = False,
) -> None:
    """写盘成功后记录 behavior 版本 + emit 审计事件（默认 best-effort，绝不抛）。

    版本 key 从**实际 resolved 磁盘路径**派生（Opus H1 修正）——保证写 key 与前端读 key
    （同从 `behavior/agents/<slug>/` 路径段派生）逐字一致，AGENT_PRIVATE 历史对自定义 Worker 命中。

    ``strict=True``（F111 Codex round18 P1）：版本记录失败**上抛**——供把"可回滚
    快照"当作成功前置条件的调用方（compact accept：快照失败即还原文件），默认
    False 保持既有调用方（misc_tools / restore）行为零变更。审计事件仍恒 best-effort。
    """
    store = getattr(stores, "behavior_version_store", None)
    if store is None:
        if strict:
            raise RuntimeError("behavior_version_store 不可用，无法记录可回滚快照")
        return
    try:
        key = behavior_version_key_from_path(project_root, resolved_path)
    except (ValueError, OSError):
        if strict:
            raise
        # 路径不在 project_root 下 / 解析失败：写盘已成功，版本记录是 best-effort，静默跳过。
        return
    file_id = key.file_id
    try:
        version_no = await store.record_version(
            key, new_content, baseline_content=old_content
        )
    except Exception as exc:
        log.warning(
            "behavior_version_record_failed",
            file_id=file_id,
            scope=key.scope,
            reason=f"{type(exc).__name__}: {exc}",
        )
        if strict:
            raise
        return

    # 审计事件（best-effort，需 task_id —— Event 模型要求 task_id）。
    event_store = getattr(stores, "event_store", None)
    if event_store is None or not task_id:
        return
    try:
        from ulid import ULID

        payload = BehaviorVersionRecordedPayload(
            scope=key.scope,
            agent_slug=key.agent_slug,
            project_slug=key.project_slug,
            file_id=file_id,
            version_no=version_no,
            source=source,
        )
        next_seq = await event_store.get_next_task_seq(task_id)
        event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=next_seq,
            ts=datetime.now(UTC),
            type=EventType.BEHAVIOR_VERSION_RECORDED,
            actor=ActorType.SYSTEM,
            payload=payload.model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await event_store.append_event_committed(event)
    except Exception as exc:
        log.warning(
            "behavior_version_event_emit_failed",
            file_id=file_id,
            error_type=type(exc).__name__,
        )
