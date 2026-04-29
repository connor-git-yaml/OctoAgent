"""PolicyGate：工具执行前的 ThreatScanner 统一入口（Feature 084 Phase 2 T035）。

Constitution C10：工具访问控制统一走权限决策函数，工具层不得自行做路径/权限拦截；
所有权限判断收敛到单一入口。

PolicyGate.check() 是工具执行前的内容安全扫描入口：
- BLOCK 级命中：返回拒绝结果 + 写入 MEMORY_ENTRY_BLOCKED 审计事件（不含原始恶意内容）
- WARN 级命中：记录日志，不拦截，返回允许结果
- 未命中：返回允许结果

设计约定：
- PolicyGate 不重复工具内部的业务逻辑（如字符上限），只做内容安全 scan
- 工具层通过 PolicyGate.check() 触发 scan，而非直接调 threat_scan()
- event_store / task_id 由调用方从 ToolDeps 解析后注入（降低耦合）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog
from ulid import ULID

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.gateway.harness.threat_scanner import ThreatScanResult, scan as threat_scan

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

# 审计专用占位 task_id（与 operator_actions / user_profile_tools 一致）
_POLICY_AUDIT_TASK_ID = "_policy_gate_audit"


@dataclass(frozen=True)
class PolicyCheckResult:
    """PolicyGate.check() 返回值。

    allowed=True 表示内容安全，允许继续执行；
    allowed=False 表示 BLOCK 命中，拒绝执行。
    """

    allowed: bool
    """是否允许执行。"""

    reason: str
    """原因描述（日志/错误消息用）。"""

    scan_result: ThreatScanResult | None
    """原始 ThreatScanResult；未扫描时为 None。"""


class PolicyGate:
    """工具执行前内容安全检查的统一入口（Constitution C10）。

    用法：
        gate = PolicyGate(event_store=deps.stores.event_store)
        result = await gate.check(content=user_input, tool_name="user_profile.update")
        if not result.allowed:
            return UserProfileUpdateResult(status="rejected", reason=result.reason)
    """

    def __init__(
        self,
        *,
        event_store: Any | None = None,
        task_store: Any | None = None,
    ) -> None:
        """初始化 PolicyGate。

        Args:
            event_store: EventStore 实例（用于写审计事件）；
                         None 时降级（BLOCK 事件不写库，但 WARN 日志仍输出）。
            task_store: TaskStore 实例（用于无 execution context 时 ensure audit task；
                         防 F24 回归——events 表 FK 到 tasks，audit task 不存在会让
                         INSERT 抛 IntegrityError 被静默吞掉，导致 BLOCK 审计丢失）。
                         None 时降级（仅当 task_id 已存在 task 时才能成功写库）。
        """
        self._event_store = event_store
        self._task_store = task_store
        # 防 _ensure_audit_task 在并发场景重复创建（与 operator_actions 同 pattern）
        self._audit_task_ensured: set[str] = set()

    async def check(
        self,
        *,
        content: str,
        tool_name: str = "",
        task_id: str = "",
        extra_payload: dict[str, Any] | None = None,
    ) -> PolicyCheckResult:
        """执行内容安全扫描（ThreatScanner 统一入口）。

        Constitution C10：所有权限判断收敛到单一入口。
        工具层调用此方法而非直接 import threat_scan。

        Args:
            content: 待扫描的内容字符串。
            tool_name: 调用方工具名（审计事件 payload 用）。
            task_id: 当前任务 ID（审计 event task_id；缺失时用占位 ID）。
            extra_payload: 额外注入到事件 payload 的字段（可选）。

        Returns:
            PolicyCheckResult：allowed=True 表示安全；False 表示 BLOCK 命中。
        """
        scan_result = threat_scan(content)

        if scan_result.blocked:
            # BLOCK 级：写审计事件，不含原始恶意内容（Constitution C5 / FR-3.4）
            input_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            payload: dict[str, Any] = {
                "tool": tool_name,
                "pattern_id": scan_result.pattern_id,
                "severity": scan_result.severity,
                "input_content_hash": input_hash,
            }
            if extra_payload:
                payload.update(extra_payload)
            await self._emit_blocked_event(
                task_id=task_id or _POLICY_AUDIT_TASK_ID,
                payload=payload,
            )
            reason = (
                f"threat_blocked: {scan_result.matched_pattern_description or scan_result.pattern_id}"
            )
            return PolicyCheckResult(
                allowed=False,
                reason=reason,
                scan_result=scan_result,
            )

        if scan_result.severity == "WARN":
            # WARN 级：记录日志，不拦截（FR-3.2）
            log.warning(
                "policy_gate_threat_warn",
                tool_name=tool_name,
                pattern_id=scan_result.pattern_id,
                description=scan_result.matched_pattern_description,
            )

        return PolicyCheckResult(
            allowed=True,
            reason="clean" if not scan_result.severity else f"warn_{scan_result.pattern_id}",
            scan_result=scan_result,
        )

    async def _ensure_audit_task(self, task_id: str) -> bool:
        """确保 audit task 在 tasks 表中存在（防 F24 FK violation）。

        F085 T3：委托至 packages/core/.../store/audit_task.ensure_system_audit_task
        统一 helper（PolicyGate + ApprovalGate 共享，防 F41 schema 必填字段
        遗漏回归）；本方法保留进程内幂等缓存，避免每次 BLOCK 都查 task_store。
        """
        if task_id in self._audit_task_ensured:
            return True
        from octoagent.core.store.audit_task import ensure_system_audit_task

        ok = await ensure_system_audit_task(
            self._task_store,
            task_id,
            title="PolicyGate 审计占位 Task（F084 Phase 2 / F085 T3）",
        )
        if ok:
            self._audit_task_ensured.add(task_id)
            log.info("policy_gate_audit_task_ensured", task_id=task_id)
        else:
            log.warning(
                "policy_gate_audit_task_ensure_failed",
                task_id=task_id,
                hint="task_store 未注入 / 查询失败 / 创建失败",
            )
        return ok

    async def _emit_blocked_event(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        """写入 MEMORY_ENTRY_BLOCKED 审计事件。

        Constitution C2：模型调用、工具调用、状态迁移都必须生成事件记录。
        BLOCK 命中时写入审计事件（不含原始恶意内容完整文本）。

        防 F24 回归：写事件前先 ensure audit task 存在，避免 FK 违反让审计静默消失。
        """
        if self._event_store is None:
            log.warning(
                "policy_gate_blocked_event_no_store",
                task_id=task_id,
                hint="event_store 未注入，BLOCK 审计事件未持久化",
            )
            return

        # F24 修复：fallback audit task 必须先确保存在再写事件
        is_fallback_task = task_id == _POLICY_AUDIT_TASK_ID
        if is_fallback_task:
            ensured = await self._ensure_audit_task(task_id)
            if not ensured:
                log.error(
                    "policy_gate_blocked_event_no_audit_task",
                    task_id=task_id,
                    hint=(
                        "audit task 不存在且 task_store 未注入或创建失败；"
                        "MEMORY_ENTRY_BLOCKED 不会被持久化（Constitution C2 风险）"
                    ),
                )
                return

        try:
            task_seq = await self._event_store.get_next_task_seq(task_id)
            event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=task_seq,
                ts=datetime.now(timezone.utc),
                type=EventType.MEMORY_ENTRY_BLOCKED,
                actor=ActorType.SYSTEM,
                payload=payload,
                trace_id=task_id,  # 审计场景用 task_id 作为 trace_id
            )
            await self._event_store.append_event_committed(event, update_task_pointer=False)
        except Exception as exc:
            log.error(
                "policy_gate_blocked_event_emit_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
                error=str(exc),
                hint="Constitution C2 BLOCK 审计事件写入失败",
            )
