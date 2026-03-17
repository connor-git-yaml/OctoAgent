"""Feature 060: Worker 进度笔记工具 -- 记录任务执行的关键里程碑。

供 Worker 在执行长任务过程中记录结构化进度笔记。
记录持久化到 Artifact Store，上下文构建时自动注入最近 N 条笔记。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger()

# ---------- 工具元数据 ----------

TOOL_META = {
    "name": "progress_note",
    "description": (
        "记录任务执行的关键里程碑。每完成一个有意义的步骤后调用此工具，"
        "确保上下文压缩或进程重启后能从断点继续。"
    ),
    "side_effect_level": "none",
    "category": "agent_internal",
    "tool_profile": "minimal",
}


# ---------- Input / Output 模型 ----------


class ProgressNoteInput(BaseModel):
    """进度笔记工具的输入参数。"""

    step_id: str = Field(
        min_length=1,
        description="步骤标识（如 'step_1', 'data_collection', 'api_integration'）",
    )
    description: str = Field(
        min_length=1,
        description="本步骤做了什么",
    )
    status: Literal["completed", "in_progress", "blocked"] = Field(
        default="completed",
        description="步骤状态",
    )
    key_decisions: list[str] = Field(
        default_factory=list,
        description="本步骤的关键决策（如'选择了方案 B'）",
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="接下来需要做什么",
    )


class ProgressNoteOutput(BaseModel):
    """进度笔记工具的输出。"""

    note_id: str = Field(description="笔记唯一 ID")
    persisted: bool = Field(description="是否成功持久化到 Artifact Store")


# ---------- 执行逻辑 ----------

# 进度笔记合并阈值和注入限制的默认值
DEFAULT_MERGE_THRESHOLD = 50
DEFAULT_INJECT_LIMIT = 5


async def execute_progress_note(
    *,
    input_data: ProgressNoteInput,
    task_id: str,
    agent_session_id: str = "",
    artifact_store: Any | None = None,
    conn: Any | None = None,
    merge_threshold: int = DEFAULT_MERGE_THRESHOLD,
) -> ProgressNoteOutput:
    """执行进度笔记工具：构造 Artifact 并写入 Artifact Store。

    Args:
        input_data: 工具输入参数
        task_id: 当前任务 ID
        agent_session_id: 当前 Agent Session ID
        artifact_store: Artifact Store 实例（可为 None，降级处理）
        conn: 数据库连接（用于 commit）
        merge_threshold: 触发自动合并的笔记数量阈值

    Returns:
        ProgressNoteOutput 包含 note_id 和 persisted 状态
    """
    from ulid import ULID

    note_id = f"pn-{task_id[:8]}-{input_data.step_id}-{ULID()}"
    now = datetime.now(UTC)

    note_content = {
        "note_id": note_id,
        "task_id": task_id,
        "agent_session_id": agent_session_id,
        "step_id": input_data.step_id,
        "description": input_data.description,
        "status": input_data.status,
        "key_decisions": input_data.key_decisions,
        "next_steps": input_data.next_steps,
        "created_at": now.isoformat(),
    }

    if artifact_store is None:
        return ProgressNoteOutput(note_id=note_id, persisted=False)

    try:
        from octoagent.core.models import Artifact, ArtifactPart, PartType

        content_json = json.dumps(note_content, ensure_ascii=False)
        artifact = Artifact(
            artifact_id=note_id,
            task_id=task_id,
            ts=now,
            name=f"progress-note:{input_data.step_id}",
            description=f"Progress note: {input_data.description[:80]}",
            parts=[
                ArtifactPart(
                    type=PartType.JSON,
                    mime="application/json",
                    content=content_json,
                ),
            ],
        )
        await artifact_store.put_artifact(artifact, content_json.encode("utf-8"))
        if conn is not None:
            await conn.commit()

        # 检查是否需要自动合并旧笔记
        await _maybe_merge_old_notes(
            task_id=task_id,
            agent_session_id=agent_session_id,
            artifact_store=artifact_store,
            conn=conn,
            threshold=merge_threshold,
        )

        return ProgressNoteOutput(note_id=note_id, persisted=True)
    except Exception as exc:
        # Artifact Store 不可用时降级：返回 persisted=False，不阻断 Worker 执行
        log.warning(
            "progress_note_write_failed",
            task_id=task_id,
            note_id=note_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return ProgressNoteOutput(note_id=note_id, persisted=False)


# ---------- 进度笔记加载 ----------


async def load_recent_progress_notes(
    *,
    task_id: str,
    artifact_store: Any,
    limit: int = DEFAULT_INJECT_LIMIT,
) -> list[dict[str, Any]]:
    """加载最近的进度笔记内容。

    Args:
        task_id: 任务 ID
        artifact_store: Artifact Store 实例
        limit: 返回最近 N 条

    Returns:
        笔记内容字典列表（按创建时间正序），每条包含
        step_id / description / status / key_decisions / next_steps / created_at
    """
    try:
        # 查询 task 的所有 artifact，筛选 progress-note 类型
        artifacts = await artifact_store.list_artifacts_for_task(task_id)
        notes: list[dict[str, Any]] = []
        for artifact in artifacts:
            if not artifact.name.startswith("progress-note:"):
                continue
            # 解析 JSON part 内容
            for part in artifact.parts:
                if part.content:
                    try:
                        note_data = json.loads(part.content)
                        notes.append(note_data)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break

        # 按 created_at 排序，返回最近 N 条
        notes.sort(key=lambda n: n.get("created_at", ""))
        return notes[-limit:]
    except Exception as exc:
        log.warning(
            "progress_note_load_failed",
            task_id=task_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return []


# ---------- 自动合并 ----------


async def _maybe_merge_old_notes(
    *,
    task_id: str,
    agent_session_id: str,
    artifact_store: Any,
    conn: Any | None = None,
    threshold: int = DEFAULT_MERGE_THRESHOLD,
) -> None:
    """当笔记超过阈值时，合并旧笔记为一条汇总 Artifact。

    保留最近 10 条笔记，将更旧的笔记合并为一条 [历史里程碑汇总]。
    """
    try:
        artifacts = await artifact_store.list_artifacts_for_task(task_id)
        note_artifacts = [a for a in artifacts if a.name.startswith("progress-note:")]
        if len(note_artifacts) <= threshold:
            return

        # 按时间排序
        note_artifacts.sort(key=lambda a: a.ts)

        # 保留最近 10 条，合并更旧的
        keep_count = 10
        old_artifacts = note_artifacts[:-keep_count]

        # 构建汇总内容
        milestones: list[str] = []
        for artifact in old_artifacts:
            for part in artifact.parts:
                if part.content:
                    try:
                        note = json.loads(part.content)
                        milestones.append(
                            f"[{note.get('step_id', '?')}] "
                            f"{note.get('status', '?')}: "
                            f"{note.get('description', '')[:100]}"
                        )
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break

        if not milestones:
            return

        # 创建汇总 Artifact
        from ulid import ULID
        from octoagent.core.models import Artifact, ArtifactPart, PartType

        merged_content = json.dumps(
            {
                "note_id": f"pn-merged-{task_id[:8]}-{ULID()}",
                "task_id": task_id,
                "agent_session_id": agent_session_id,
                "step_id": "__merged_history__",
                "description": f"历史里程碑汇总（{len(milestones)} 条笔记已合并）",
                "status": "completed",
                "key_decisions": [],
                "next_steps": [],
                "milestones": milestones,
                "created_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        merged_artifact = Artifact(
            artifact_id=f"pn-merged-{task_id[:8]}-{ULID()}",
            task_id=task_id,
            ts=datetime.now(UTC),
            name="progress-note:__merged_history__",
            description=f"历史里程碑汇总（{len(milestones)} 条笔记已合并）",
            parts=[
                ArtifactPart(
                    type=PartType.JSON,
                    mime="application/json",
                    content=merged_content,
                ),
            ],
        )
        await artifact_store.put_artifact(
            merged_artifact, merged_content.encode("utf-8"),
        )

        # 删除已合并的旧笔记 Artifact
        for old_artifact in old_artifacts:
            try:
                if hasattr(artifact_store, "delete_artifact"):
                    await artifact_store.delete_artifact(old_artifact.artifact_id)
                elif hasattr(artifact_store, "remove_artifact"):
                    await artifact_store.remove_artifact(old_artifact.artifact_id)
            except Exception as del_exc:
                log.warning(
                    "progress_note_delete_old_failed",
                    artifact_id=old_artifact.artifact_id,
                    error_type=type(del_exc).__name__,
                )

        if conn is not None:
            await conn.commit()
    except Exception as exc:
        # 合并失败不阻断主流程
        log.warning(
            "progress_note_merge_failed",
            task_id=task_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )


# ---------- 上下文注入格式化 ----------


def format_progress_notes_block(
    notes: list[dict[str, Any]],
    limit: int = DEFAULT_INJECT_LIMIT,
) -> str:
    """将进度笔记列表格式化为系统块文本。

    Args:
        notes: 笔记内容字典列表
        limit: 最多展示多少条

    Returns:
        格式化后的 Markdown 文本，可直接作为系统块内容注入。
    """
    if not notes:
        return ""
    display_notes = notes[-limit:]
    lines = ["## Progress Notes\n"]
    for note in display_notes:
        step_id = note.get("step_id", "?")
        status = note.get("status", "?")
        description = note.get("description", "")
        next_steps = note.get("next_steps", [])
        lines.append(f"- [{step_id}] {status}: {description}")
        if next_steps:
            lines.append(f"  Next: {', '.join(next_steps)}")
    return "\n".join(lines)
