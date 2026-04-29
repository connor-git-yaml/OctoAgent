"""approval_gate.py：ApprovalGate — session allowlist + SSE 异步审批路径（Feature 084 Phase 3）。

架构决策（plan.md D6 / FR-4）：
- session 级 allowlist：同 session 批准过的操作类型不再弹审批卡片（FR-4.3）
- request_approval 写 APPROVAL_REQUESTED 审计事件（含 threat_category / pattern_id，FR-4.2 / FR-10.2）
- ApprovalHandle：handle_id (ULID) + asyncio.Event + decision 三元组
- wait_for_decision：Agent 侧异步等待 SSE 回调注入结果
- resolve_approval：API 端点调用，注入决策 → 触发 asyncio.Event → 写 APPROVAL_DECIDED 事件（FR-4.4）
- 拒绝时 Agent 收到明确 rejected（不静默 timeout）

Constitution 合规：
- C4 两阶段记录：APPROVAL_REQUESTED（Plan） → 决策 → APPROVAL_DECIDED（Gate）
- C2 所有写操作有审计事件
- C7 User-in-Control：高风险动作必须可审批

事件写入防回归（防 F22）：
- 使用真实 Event schema 字段：event_id / task_id / task_seq / ts / type / actor
- 使用 event_store.append_event_committed(event) API
- task_id 无 execution context 时用 _APPROVAL_AUDIT_TASK_ID 占位（F24 修复模式）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from ulid import ULID

from octoagent.core.models.enums import ActorType, EventType

log = structlog.get_logger(__name__)

# 审计占位 task_id（与 operator_actions / PolicyGate 同 pattern，防 F24 回归）
_APPROVAL_AUDIT_TASK_ID = "_approval_gate_audit"


# ---------------------------------------------------------------------------
# T042: ApprovalHandle dataclass
# ---------------------------------------------------------------------------


@dataclass
class ApprovalHandle:
    """待决审批请求的句柄（单次使用，用完即废）。

    handle_id：用于 API 端点通过 resolve_approval() 注入决策结果。
    _event：asyncio.Event，wait_for_decision() await 此对象阻塞直到决策注入。
    decision：None（待决）/ "approved" / "rejected"
    """

    handle_id: str = field(default_factory=lambda: str(ULID()))
    """ULID 格式的唯一标识符，API 端点通过此 ID 找到对应 handle。"""

    _event: asyncio.Event = field(default_factory=asyncio.Event)
    """asyncio.Event：SSE 回调注入决策后 set()，wait_for_decision() 收到后解除阻塞。"""

    decision: Literal["approved", "rejected"] | None = field(default=None)
    """审批决策：None 表示尚未决策。"""

    operator: str = field(default="")
    """操作者标识（如用户 ID、"web_ui" 等）。"""


# ---------------------------------------------------------------------------
# T041 + T042: ApprovalGate
# ---------------------------------------------------------------------------


class ApprovalGate:
    """会话级审批闸门（Feature 084 Phase 3 FR-4）。

    职责：
    - 维护 session 级 allowlist（已批准的操作类型不再弹审批请求）
    - request_approval() 创建 ApprovalHandle，写 APPROVAL_REQUESTED 事件，推送 SSE
    - wait_for_decision() 异步等待 API 端点注入决策结果
    - resolve_approval() 由 API 端点调用，注入决策 + 写 APPROVAL_DECIDED 事件

    线程/协程安全：
    - _session_allowlist / _pending_handles 均在同一 event loop 访问，无需额外锁
    - 多 worker 并发时若 session_id 相同可复用 allowlist 缓存

    session 结束时调用 clear_session() 清零 allowlist（不跨 session 持久化）。
    """

    def __init__(
        self,
        *,
        event_store: Any | None = None,
        task_store: Any | None = None,
        sse_push_fn: Any | None = None,
    ) -> None:
        """初始化 ApprovalGate。

        Args:
            event_store: EventStore 实例（用于写审计事件）；None 时审计降级
            task_store: TaskStore 实例（用于 ensure audit task 防 F24）
            sse_push_fn: 异步函数 (session_id, payload) -> None，
                         用于向 Web UI 推送审批卡片；None 时仅写事件（CLI/测试路径）
        """
        self._event_store = event_store
        self._task_store = task_store
        self._sse_push_fn = sse_push_fn

        # session_id → 已批准的操作类型集合（不跨 session 持久化）
        self._session_allowlist: dict[str, set[str]] = {}

        # handle_id → ApprovalHandle（活跃审批请求映射）
        self._pending_handles: dict[str, ApprovalHandle] = {}

        # 防 F24：进程内幂等缓存，避免每次重复查 task_store
        self._audit_task_ensured: set[str] = set()

    # ---------------------------------------------------------------------------
    # T041: allowlist 管理
    # ---------------------------------------------------------------------------

    def check_allowlist(self, session_id: str, operation_type: str) -> bool:
        """检查 session 级 allowlist 中是否已批准此操作类型（FR-4.3）。

        Args:
            session_id: 当前会话 ID。
            operation_type: 操作类型字符串，如 "user_profile.replace"。

        Returns:
            True 表示已在 allowlist，无需再次审批；False 需要审批。
        """
        return operation_type in self._session_allowlist.get(session_id, set())

    def add_to_allowlist(self, session_id: str, operation_type: str) -> None:
        """将操作类型加入 session allowlist（批准后调用）。

        Args:
            session_id: 当前会话 ID。
            operation_type: 已批准的操作类型。
        """
        if session_id not in self._session_allowlist:
            self._session_allowlist[session_id] = set()
        self._session_allowlist[session_id].add(operation_type)
        log.debug(
            "approval_allowlist_added",
            session_id=session_id,
            operation_type=operation_type,
        )

    def clear_session(self, session_id: str) -> None:
        """清除 session 的 allowlist（session 结束时调用，不跨 session 持久化）。

        Args:
            session_id: 要清除的会话 ID。
        """
        removed = self._session_allowlist.pop(session_id, None)
        if removed is not None:
            log.debug("approval_allowlist_cleared", session_id=session_id)

    # ---------------------------------------------------------------------------
    # T041: request_approval（写 APPROVAL_REQUESTED 事件）
    # ---------------------------------------------------------------------------

    async def request_approval(
        self,
        session_id: str,
        tool_name: str,
        scan_result: Any,  # ThreatScanResult（避免循环 import）
        operation_summary: str,
        diff_content: str | None = None,
        task_id: str = "",
    ) -> ApprovalHandle:
        """发起审批请求（FR-4.1 / FR-4.2）。

        1. 创建 ApprovalHandle（handle_id + asyncio.Event）
        2. 注册到 _pending_handles
        3. 写 APPROVAL_REQUESTED 事件（含 threat_category / pattern_id / diff_content）
        4. 通过 sse_push_fn 推送审批卡片到 Web UI

        Args:
            session_id: 当前会话 ID（allowlist 作用域）。
            tool_name: 触发审批的工具名称，如 "user_profile.replace"。
            scan_result: ThreatScanResult 实例（可为 None 表示非 Threat 触发）。
            operation_summary: 人类可读的操作摘要（展示给用户）。
            diff_content: replace/remove 时的 diff（可选，含旧内容对比）。
            task_id: 当前任务 ID（审计事件用）；缺失时用占位 ID。

        Returns:
            ApprovalHandle，供后续 wait_for_decision() 使用。
        """
        handle = ApprovalHandle()
        self._pending_handles[handle.handle_id] = handle

        # 提取 scan_result 字段（兼容 None / ThreatScanResult）
        threat_category: str | None = None
        pattern_id: str | None = None
        if scan_result is not None:
            pattern_id = getattr(scan_result, "pattern_id", None)
            # threat_category 从 pattern_id 前缀推断（PI-001 → "prompt_injection"）
            threat_category = _pattern_id_to_category(pattern_id)

        audit_task_id = task_id or _APPROVAL_AUDIT_TASK_ID

        # 写 APPROVAL_REQUESTED 审计事件（FR-4.2 / FR-10.2 / Constitution C2）
        # F27 修复：同时写 handle_id 和 approval_id 字段（与现有 task_runner /
        # operator_inbox / telegram / payloads.approval_id 命名兼容）；
        # 已经覆盖范围：apps/gateway/services/{task_runner,telegram,operator_inbox} +
        # packages/core/models/{execution,payloads}。
        # 旧消费者读 approval_id，新消费者读 handle_id，两者值相同。
        await self._emit_event(
            task_id=audit_task_id,
            event_type=EventType.APPROVAL_REQUESTED,
            payload={
                "handle_id": handle.handle_id,
                "approval_id": handle.handle_id,  # F27 兼容：现有消费者按 approval_id 解析
                "session_id": session_id,
                "tool_name": tool_name,
                "operation_summary": operation_summary,
                "threat_category": threat_category,
                "pattern_id": pattern_id,
                "diff_content": diff_content,
            },
        )

        # 推送审批卡片到 Web UI（SSE 路径）
        if self._sse_push_fn is not None:
            try:
                await self._sse_push_fn(
                    session_id,
                    {
                        "type": "approval_requested",
                        "handle_id": handle.handle_id,
                        "approval_id": handle.handle_id,  # F27 兼容
                        "tool_name": tool_name,
                        "operation_summary": operation_summary,
                        "threat_category": threat_category,
                        "pattern_id": pattern_id,
                        "diff_content": diff_content,
                    },
                )
            except Exception as exc:
                log.warning(
                    "approval_gate_sse_push_failed",
                    handle_id=handle.handle_id,
                    error=str(exc),
                )

        log.info(
            "approval_requested",
            handle_id=handle.handle_id,
            session_id=session_id,
            tool_name=tool_name,
            threat_category=threat_category,
            pattern_id=pattern_id,
        )

        return handle

    # ---------------------------------------------------------------------------
    # T042: wait_for_decision + resolve_approval（SSE 异步路径）
    # ---------------------------------------------------------------------------

    async def wait_for_decision(
        self,
        handle: ApprovalHandle,
        timeout_seconds: float = 300.0,
    ) -> Literal["approved", "rejected"]:
        """Agent 侧异步等待用户决策（T042 SSE 路径）。

        阻塞等待 handle._event 被 resolve_approval() set()，
        或在 timeout_seconds 后返回 "rejected"（不静默超时）。

        Args:
            handle: request_approval() 返回的 ApprovalHandle。
            timeout_seconds: 最长等待秒数（默认 300s）。

        Returns:
            "approved" 或 "rejected"（超时时返回 "rejected"，不静默）。
        """
        timed_out = False
        try:
            await asyncio.wait_for(handle._event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            log.warning(
                "approval_gate_timeout",
                handle_id=handle.handle_id,
                timeout_seconds=timeout_seconds,
            )
            # 超时时显式标记为 rejected（不静默，Constitution C7）
            handle.decision = "rejected"
            handle.operator = "system_timeout"
            timed_out = True

        # F27 修复：timeout 路径必须写 APPROVAL_DECIDED 终态事件，
        # 否则事件重放时 pending 审批永远悬挂、清理逻辑找不到收口信号。
        if timed_out:
            await self._emit_event(
                task_id=_APPROVAL_AUDIT_TASK_ID,
                event_type=EventType.APPROVAL_DECIDED,
                payload={
                    "handle_id": handle.handle_id,
                    "approval_id": handle.handle_id,
                    "decision": "rejected",
                    "operator": "system_timeout",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": f"timeout_after_{timeout_seconds}s",
                },
            )

        # 清理 pending（无论如何）
        self._pending_handles.pop(handle.handle_id, None)

        decision = handle.decision or "rejected"
        log.info(
            "approval_decision_received",
            handle_id=handle.handle_id,
            decision=decision,
        )
        return decision

    async def resolve_approval(
        self,
        handle_id: str,
        decision: Literal["approved", "rejected"],
        operator: str = "",
        task_id: str = "",
        session_id: str = "",
        operation_type: str = "",
    ) -> bool:
        """由 API 端点调用，注入审批决策结果（T042）。

        1. 查找 pending handle
        2. 设置 handle.decision + handle.operator
        3. set() asyncio.Event → wait_for_decision() 解除阻塞
        4. 写 APPROVAL_DECIDED 审计事件（FR-4.4 / Constitution C2 C4）
        5. 若批准 → 加入 allowlist

        Args:
            handle_id: ApprovalHandle 的 handle_id。
            decision: "approved" 或 "rejected"。
            operator: 操作者标识（如 "web_ui" / 用户 ID）。
            task_id: 当前任务 ID（审计事件用）。
            session_id: 当前会话 ID（allowlist 更新用）。
            operation_type: 操作类型（批准时加入 allowlist）。

        Returns:
            True 表示 handle 找到并已处理；False 表示 handle 不存在（已超时/重复注入）。
        """
        handle = self._pending_handles.get(handle_id)
        if handle is None:
            log.warning(
                "approval_resolve_handle_not_found",
                handle_id=handle_id,
                hint="handle 可能已超时或重复注入",
            )
            return False

        # 注入决策
        handle.decision = decision
        handle.operator = operator

        # 触发 asyncio.Event → wait_for_decision() 解除阻塞
        handle._event.set()

        # 批准时加入 session allowlist（FR-4.3）
        if decision == "approved" and session_id and operation_type:
            self.add_to_allowlist(session_id, operation_type)

        # 写 APPROVAL_DECIDED 事件（FR-4.4）
        # F27 修复：同时写 handle_id 和 approval_id 兼容现有审批消费者
        audit_task_id = task_id or _APPROVAL_AUDIT_TASK_ID
        await self._emit_event(
            task_id=audit_task_id,
            event_type=EventType.APPROVAL_DECIDED,
            payload={
                "handle_id": handle_id,
                "approval_id": handle_id,  # F27 兼容
                "session_id": session_id,
                "decision": decision,
                "operator": operator,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation_type": operation_type,
            },
        )

        log.info(
            "approval_resolved",
            handle_id=handle_id,
            decision=decision,
            operator=operator,
        )
        return True

    # ---------------------------------------------------------------------------
    # 内部辅助：事件写入（防 F22 / F24 回归）
    # ---------------------------------------------------------------------------

    async def _ensure_audit_task(self, task_id: str) -> bool:
        """确保 audit task 在 tasks 表中存在（防 F24 FK violation）。

        events 表 FK 到 tasks(task_id)，fallback audit task 不存在时
        INSERT events 会抛 IntegrityError → audit 静默丢失。

        参照 PolicyGate._ensure_audit_task 同模式。
        """
        if task_id in self._audit_task_ensured:
            return True
        if self._task_store is None:
            return False
        try:
            existing = await self._task_store.get_task(task_id)
        except Exception as exc:
            log.error("approval_gate_audit_task_get_failed", task_id=task_id, error=str(exc))
            return False
        if existing is not None:
            self._audit_task_ensured.add(task_id)
            return True
        try:
            from octoagent.core.models.task import Task

            # F41 修复（同 PolicyGate）：Task 必填字段 requester / pointers
            # 缺失会让 ValidationError 导致 audit task 创建失败 →
            # APPROVAL_REQUESTED / APPROVAL_DECIDED 事件全部 silent 丢失。
            from octoagent.core.models.task import RequesterInfo, TaskPointers
            now = datetime.now(timezone.utc)
            audit_task = Task(
                task_id=task_id,
                created_at=now,
                updated_at=now,
                title="ApprovalGate 审计占位 Task（F084 Phase 3）",
                trace_id=task_id,
                requester=RequesterInfo(channel="system", sender_id=task_id),
                pointers=TaskPointers(),
            )
            await self._task_store.create_task(audit_task)
            self._audit_task_ensured.add(task_id)
            log.info("approval_gate_audit_task_created", task_id=task_id)
            return True
        except Exception as exc:
            log.error(
                "approval_gate_audit_task_create_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return False

    async def _emit_event(
        self,
        *,
        task_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        """写审计事件（防 F22 回归：使用真实 schema 字段 + append_event_committed API）。

        防 F22 回归规则：
        - 字段名：event_id / task_id / task_seq / ts / type / actor（不是 event_type / timestamp 等）
        - API：append_event_committed(event)（不是 append()，会 AttributeError）
        - task_id 缺失时用占位 ID + ensure_audit_task（防 F24 FK 违反）
        """
        if self._event_store is None:
            log.warning(
                "approval_gate_event_no_store",
                event_type=str(event_type),
                hint="event_store 未注入，审批审计事件未持久化",
            )
            return

        # fallback audit task 确保存在（防 F24）
        is_fallback = task_id == _APPROVAL_AUDIT_TASK_ID
        if is_fallback:
            ensured = await self._ensure_audit_task(task_id)
            if not ensured:
                log.error(
                    "approval_gate_event_no_audit_task",
                    task_id=task_id,
                    event_type=str(event_type),
                    hint="Constitution C2 审批审计事件写入失败：audit task 不存在",
                )
                return

        try:
            from octoagent.core.models.event import Event

            task_seq = await self._event_store.get_next_task_seq(task_id)
            event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=task_seq,
                ts=datetime.now(timezone.utc),
                type=event_type,
                actor=ActorType.SYSTEM,
                payload=payload,
                trace_id=task_id,
            )
            await self._event_store.append_event_committed(event, update_task_pointer=False)
        except Exception as exc:
            log.error(
                "approval_gate_event_emit_failed",
                event_type=str(event_type),
                error_type=type(exc).__name__,
                error=str(exc),
                hint="Constitution C2 审批审计事件写入失败",
            )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _pattern_id_to_category(pattern_id: str | None) -> str | None:
    """从 pattern_id 前缀推断威胁分类（用于 APPROVAL_REQUESTED 事件 threat_category 字段）。

    PI-xxx → "prompt_injection"
    RH-xxx → "role_hijacking"
    EX-xxx → "exfiltration"
    B64-xxx → "base64_payload"
    SO-xxx → "system_override"
    MI-xxx → "memory_injection"
    INVIS-xxx → "invisible_unicode"
    """
    if not pattern_id:
        return None
    prefix = pattern_id.split("-")[0].upper()
    _MAP = {
        "PI": "prompt_injection",
        "RH": "role_hijacking",
        "EX": "exfiltration",
        "B64": "base64_payload",
        "SO": "system_override",
        "MI": "memory_injection",
        "INVIS": "invisible_unicode",
    }
    return _MAP.get(prefix, "unknown")
