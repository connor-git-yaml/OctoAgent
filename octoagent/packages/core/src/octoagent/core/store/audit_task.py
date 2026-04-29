"""统一系统 audit task 创建 helper（F085 T3）。

防 F41 回归：3 处生产代码（PolicyGate / ApprovalGate / operator_actions）
之前各自实现 audit Task 创建，subagent 在 PolicyGate / ApprovalGate 实现时
漏传 Task 必填字段 (requester / pointers)，导致 ValidationError → audit task
永远创建失败 → MEMORY_ENTRY_BLOCKED / APPROVAL_* 事件全部 silent 丢失。

本 helper 锁定正确字段集合，PolicyGate + ApprovalGate 委托至此。
operator_actions 因有 specific 字段（thread_id / scope_id / TASK_CREATED 事件）
保持独立实现，不归此 helper 管。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from octoagent.core.models.task import RequesterInfo, Task, TaskPointers


async def ensure_system_audit_task(
    task_store: Any,
    task_id: str,
    *,
    title: str = "system audit task",
) -> bool:
    """确保 system 级 audit task 在 tasks 表中存在（防 events 表 FK violation）。

    适用场景：PolicyGate / ApprovalGate 等 harness 组件在无 execution context
    （web 入口直接调用工具）时需要 fallback audit task_id 写审计事件，但 events
    表 FK 到 tasks(task_id)，task 不存在时 INSERT IntegrityError 静默吞掉。

    参照 operator_actions._ensure_operational_task 模式但简化：
    - 不写 TASK_CREATED 事件（PolicyGate / ApprovalGate 不需要）
    - 不带 thread_id / scope_id（系统级 audit，无业务作用域）
    - **必须传** Task 模型必填字段 requester（RequesterInfo）+ pointers（TaskPointers）
      防 F41 schema 必填错误（pydantic ValidationError）

    Args:
        task_store: TaskStore 实例（从 stores.task_store 注入）
        task_id: audit task ID（如 "_policy_gate_audit" / "_approval_gate_audit"）
        title: 任务标题（log + 调试用）

    Returns:
        True：task 已存在或刚创建成功；False：task_store=None / 查询失败 / 创建失败
    """
    if task_store is None:
        return False
    try:
        existing = await task_store.get_task(task_id)
    except Exception:
        return False
    if existing is not None:
        return True

    try:
        now = datetime.now(timezone.utc)
        audit_task = Task(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            title=title,
            trace_id=task_id,
            # F41 防御：必填字段不能省（model 层 ValidationError 会 silent 吞掉
            # 让 audit 链断裂）。channel="system" / sender_id=task_id 与
            # operator_actions 系统 audit task 模式一致。
            requester=RequesterInfo(channel="system", sender_id=task_id),
            pointers=TaskPointers(),
        )
        await task_store.create_task(audit_task)
        return True
    except Exception:
        return False
