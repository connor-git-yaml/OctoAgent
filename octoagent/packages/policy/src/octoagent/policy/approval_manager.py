"""ApprovalManager -- Two-Phase Approval 管理器

对齐 FR-007 (幂等注册), FR-008 (三种审批决策), FR-009 (宽限期),
FR-010 (超时自动 deny), FR-011 (持久化与恢复)。

管理审批请求的注册、等待、解决和消费。
内存状态 + Event Store 双写。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

import aiosqlite
from octoagent.core.models.enums import ActorType, EventType, SideEffectLevel
from octoagent.core.models.event import Event, EventCausality
from ulid import ULID

from .approval_override_store import ApprovalOverrideCache, ApprovalOverrideRepository
from .models import (
    ApprovalDecision,
    ApprovalExpiredEventPayload,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalRequestedEventPayload,
    ApprovalResolvedEventPayload,
    ApprovalStatus,
    PendingApproval,
)

logger = logging.getLogger(__name__)


class EventStoreProtocol(Protocol):
    """Event Store 最小接口（Feature 006 依赖）"""

    async def append_event(self, event: Any) -> None:
        """追加事件"""
        ...

    async def get_next_task_seq(self, task_id: str) -> int:
        """获取指定 task 的下一个序列号"""
        ...

    async def get_all_events(self) -> list[Any]:
        """查询全量事件（用于启动恢复）"""
        ...


class SSEBroadcasterProtocol(Protocol):
    """SSE 广播器最小接口（Feature 006 依赖）"""

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        """广播 SSE 事件"""
        ...


class ApprovalManager:
    """Two-Phase Approval 管理器

    管理审批请求的注册、等待、解决和消费。
    内存状态 + Event Store 双写。

    对齐 FR: FR-007, FR-008, FR-009, FR-010, FR-011
    """

    def __init__(
        self,
        event_store: EventStoreProtocol | None = None,
        sse_broadcaster: SSEBroadcasterProtocol | None = None,
        default_timeout_s: float = 600.0,  # 10 分钟，给用户充足的审批时间
        grace_period_s: float = 30.0,
        *,
        override_repo: ApprovalOverrideRepository | None = None,
        override_cache: ApprovalOverrideCache | None = None,
    ) -> None:
        self._event_store = event_store
        self._sse_broadcaster = sse_broadcaster
        self._default_timeout_s = default_timeout_s
        self._grace_period_s = grace_period_s

        # 内存状态: approval_id -> PendingApproval
        self._pending: dict[str, PendingApproval] = {}

        # Feature 061: always 覆盖委托给 Cache + Repository
        # 旧的 _allow_always 全局 dict 保留为兼容 fallback
        self._override_repo = override_repo
        self._override_cache = override_cache or ApprovalOverrideCache()
        # 兼容旧逻辑: 无 agent_runtime_id 时回退到全局白名单
        self._allow_always: dict[str, bool] = {}

    # ============================================================
    # Phase 1: 幂等注册 (FR-007)
    # ============================================================

    async def register(self, request: ApprovalRequest | dict) -> ApprovalRecord:
        """Phase 1: 幂等注册审批请求

        Args:
            request: 审批请求（ApprovalRequest 实例或等价 dict）。
                     dict 输入由 tooling/permission.py 传入以避免循环导入。

        Returns:
            ApprovalRecord（新建或已有）

        行为约定:
            - 同一 approval_id 重复注册返回已有 record（幂等）
            - 注册时检查 allow-always 白名单，命中则直接返回 APPROVED record
            - 写入 APPROVAL_REQUESTED 事件到 Event Store
            - 推送 SSE 'approval:requested' 事件
            - 启动超时定时器（call_later）
        """
        if not isinstance(request, ApprovalRequest):
            request = ApprovalRequest(**request)
        # 幂等: 如果已注册，直接返回已有记录
        if request.approval_id in self._pending:
            logger.debug(
                "审批 '%s' 已注册，返回已有记录（幂等）",
                request.approval_id,
            )
            return self._pending[request.approval_id].record

        # Feature 061: 检查 always 覆盖（优先使用 Cache，兼容旧全局白名单）
        agent_rid = getattr(request, "agent_runtime_id", "") or ""
        if (
            agent_rid
            and self._override_cache.has(agent_rid, request.tool_name)
        ) or self._allow_always.get(request.tool_name):
            logger.info(
                "工具 '%s' 在 always 覆盖中（agent=%s），自动批准",
                request.tool_name,
                agent_rid or "global",
            )
            record = ApprovalRecord(
                request=request,
                status=ApprovalStatus.APPROVED,
                decision=ApprovalDecision.ALLOW_ALWAYS,
                resolved_at=datetime.now(UTC),
                resolved_by="system:allow-always",
            )
            return record

        # Event Store 双写（失败时不落内存状态，避免“有 pending 无事件”）
        await self._write_approval_requested_event(request)

        # 创建新的 PendingApproval
        record = ApprovalRecord(request=request)
        pending = PendingApproval(record=record)
        self._pending[request.approval_id] = pending

        # SSE 推送
        await self._broadcast_approval_event(
            "approval:requested",
            ApprovalRequestedEventPayload(
                approval_id=request.approval_id,
                task_id=request.task_id,
                tool_name=request.tool_name,
                tool_args_summary=request.tool_args_summary,
                risk_explanation=request.risk_explanation,
                policy_label=request.policy_label,
                expires_at=request.expires_at.isoformat(),
            ).model_dump(),
            task_id=request.task_id,
        )

        # 启动超时定时器
        self._start_timeout_timer(request.approval_id, request)

        logger.info(
            "审批请求已注册: id=%s, tool=%s, task=%s",
            request.approval_id,
            request.tool_name,
            request.task_id,
        )
        return record

    # ============================================================
    # Phase 2: 异步等待 (FR-007)
    # ============================================================

    async def wait_for_decision(
        self,
        approval_id: str,
        timeout_s: float | None = None,
    ) -> ApprovalDecision | None:
        """Phase 2: 异步等待用户决策

        Args:
            approval_id: 审批 ID
            timeout_s: 等待超时（覆盖默认值）

        Returns:
            用户决策，超时返回 None
        """
        pending = self._pending.get(approval_id)
        if pending is None:
            logger.warning("等待未知审批 '%s'", approval_id)
            return None

        # 如果已解决（宽限期内），直接返回
        if pending.record.status != ApprovalStatus.PENDING:
            return pending.record.decision

        timeout = timeout_s or self._default_timeout_s

        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout)
        except TimeoutError:
            # 超时由 timer 处理，这里只返回 None
            logger.debug("等待审批 '%s' 超时", approval_id)
            return None

        # 等待结束后返回决策
        return pending.record.decision

    # ============================================================
    # 解决审批 (FR-008)
    # ============================================================

    async def resolve(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        resolved_by: str = "user:web",
    ) -> bool:
        """解决审批请求

        Args:
            approval_id: 审批 ID
            decision: 用户决策
            resolved_by: 解决者标识

        Returns:
            True 成功，False 审批不存在或已解决
        """
        pending = self._pending.get(approval_id)
        if pending is None:
            logger.warning("尝试解决不存在的审批 '%s'", approval_id)
            return False

        # 已解决则拒绝（竞态防护 EC-2）
        if pending.record.status != ApprovalStatus.PENDING:
            logger.warning(
                "审批 '%s' 已解决（当前状态: %s），拒绝重复解决",
                approval_id,
                pending.record.status,
            )
            return False

        now = datetime.now(UTC)

        # 更新记录（先更新内存态，再写事件；写失败则回滚内存态）
        prev_status = pending.record.status
        prev_decision = pending.record.decision
        prev_resolved_at = pending.record.resolved_at
        prev_resolved_by = pending.record.resolved_by

        if decision == ApprovalDecision.DENY:
            pending.record.status = ApprovalStatus.REJECTED
        else:
            pending.record.status = ApprovalStatus.APPROVED

        pending.record.decision = decision
        pending.record.resolved_at = now
        pending.record.resolved_by = resolved_by

        # Event Store 双写
        event_type_str = (
            "APPROVAL_APPROVED"
            if pending.record.status == ApprovalStatus.APPROVED
            else "APPROVAL_REJECTED"
        )
        try:
            await self._write_approval_resolved_event(
                approval_id=approval_id,
                task_id=pending.record.request.task_id,
                tool_name=pending.record.request.tool_name,
                decision=decision,
                resolved_by=resolved_by,
                resolved_at=now,
                event_type_str=event_type_str,
            )
        except Exception:
            pending.record.status = prev_status
            pending.record.decision = prev_decision
            pending.record.resolved_at = prev_resolved_at
            pending.record.resolved_by = prev_resolved_by
            raise

        # 取消超时定时器
        if pending.timer_handle is not None:
            pending.timer_handle.cancel()
            pending.timer_handle = None

        # Feature 061: allow-always 写入 Cache + Repository（Agent 实例级隔离）
        if decision == ApprovalDecision.ALLOW_ALWAYS:
            tool_name = pending.record.request.tool_name
            agent_rid = getattr(pending.record.request, "agent_runtime_id", "") or ""
            if agent_rid:
                # 写入内存缓存
                self._override_cache.set(agent_rid, tool_name)
                # 异步写入 SQLite 持久化
                if self._override_repo is not None:
                    try:
                        await self._override_repo.save_override(agent_rid, tool_name)
                    except Exception:
                        logger.warning(
                            "always 覆盖写入持久化失败（缓存已更新）",
                            exc_info=True,
                        )
                logger.info(
                    "工具 '%s' 已加入 always 覆盖（agent=%s）",
                    tool_name,
                    agent_rid,
                )
            else:
                # 兼容旧逻辑: 无 agent_runtime_id 时回退全局白名单
                self._allow_always[tool_name] = True
                logger.info(
                    "工具 '%s' 已加入 allow-always 全局白名单",
                    tool_name,
                )

        # 设置 asyncio.Event（唤醒等待方）
        pending.event.set()

        # SSE 推送
        await self._broadcast_approval_event(
            "approval:resolved",
            ApprovalResolvedEventPayload(
                approval_id=approval_id,
                task_id=pending.record.request.task_id,
                decision=decision.value,
                resolved_by=resolved_by,
                resolved_at=now.isoformat(),
            ).model_dump(),
            task_id=pending.record.request.task_id,
        )

        # 宽限期后清理
        self._schedule_cleanup(approval_id)

        logger.info(
            "审批 '%s' 已解决: decision=%s, by=%s",
            approval_id,
            decision,
            resolved_by,
        )
        return True

    # ============================================================
    # 原子消费 (FR-008)
    # ============================================================

    def consume_allow_once(self, approval_id: str) -> bool:
        """原子消费一次性审批令牌

        Args:
            approval_id: 审批 ID

        Returns:
            True 成功消费，False 审批不是 allow-once 或已消费
        """
        pending = self._pending.get(approval_id)
        if pending is None:
            return False

        record = pending.record
        if record.decision != ApprovalDecision.ALLOW_ONCE:
            return False

        if record.consumed:
            logger.warning(
                "审批 '%s' 的 allow-once 令牌已被消费，拒绝重放",
                approval_id,
            )
            return False

        record.consumed = True
        return True

    # ============================================================
    # 超时处理 (FR-010)
    # ============================================================

    def _start_timeout_timer(
        self,
        approval_id: str,
        request: ApprovalRequest,
    ) -> None:
        """启动超时定时器"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有事件循环（测试环境），跳过定时器
            logger.debug("无事件循环，跳过超时定时器: %s", approval_id)
            return

        timeout_s = self._default_timeout_s
        handle = loop.call_later(
            timeout_s,
            lambda: asyncio.ensure_future(self._handle_timeout(approval_id)),
        )

        pending = self._pending.get(approval_id)
        if pending is not None:
            pending.timer_handle = handle

    async def _handle_timeout(self, approval_id: str) -> None:
        """超时回调: 自动按 deny 处理"""
        pending = self._pending.get(approval_id)
        if pending is None:
            return

        if pending.record.status != ApprovalStatus.PENDING:
            return  # 已解决，忽略

        now = datetime.now(UTC)

        # 标记过期（写事件失败则回滚）
        prev_status = pending.record.status
        prev_resolved_at = pending.record.resolved_at
        prev_resolved_by = pending.record.resolved_by

        pending.record.status = ApprovalStatus.EXPIRED
        pending.record.resolved_at = now
        pending.record.resolved_by = "system:timeout"

        # Event Store 双写
        try:
            await self._write_approval_expired_event(
                approval_id=approval_id,
                task_id=pending.record.request.task_id,
                expired_at=now,
            )
        except Exception:
            pending.record.status = prev_status
            pending.record.resolved_at = prev_resolved_at
            pending.record.resolved_by = prev_resolved_by
            raise

        # 唤醒等待方
        pending.event.set()

        # SSE 推送
        await self._broadcast_approval_event(
            "approval:expired",
            ApprovalExpiredEventPayload(
                approval_id=approval_id,
                task_id=pending.record.request.task_id,
                expired_at=now.isoformat(),
            ).model_dump(),
            task_id=pending.record.request.task_id,
        )

        logger.info("审批 '%s' 已超时过期", approval_id)

    # ============================================================
    # 宽限期清理 (FR-009)
    # ============================================================

    def _schedule_cleanup(self, approval_id: str) -> None:
        """宽限期后清理 pending 记录"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        loop.call_later(
            self._grace_period_s,
            lambda: self._cleanup(approval_id),
        )

    def _cleanup(self, approval_id: str) -> None:
        """清理已解决的审批记录"""
        if approval_id in self._pending:
            del self._pending[approval_id]
            logger.debug("审批 '%s' 宽限期到期，已清理", approval_id)

    # ============================================================
    # 启动恢复 (FR-011)
    # ============================================================

    async def recover_from_store(self) -> int:
        """启动恢复: 从 Event Store 恢复未完成的审批 + 从 SQLite 恢复 always 覆盖

        Returns:
            恢复的 pending 审批数量

        """
        # Feature 061: 从 ApprovalOverrideRepository 恢复 always 覆盖到缓存
        await self._recover_overrides_from_repo()

        recovered = 0
        now = datetime.now(UTC)

        getter = getattr(self._event_store, "get_all_events", None) if self._event_store else None
        if not callable(getter):
            return await self._recover_from_memory_pending(now)

        self._pending.clear()
        events = await self._load_events_for_recovery()
        if not events:
            logger.info("审批恢复完成: 恢复 %d 个 pending 审批", recovered)
            return recovered

        requested: dict[str, ApprovalRequest] = {}
        resolved: set[str] = set()

        for event in events:
            event_type = event.type
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event_type == EventType.APPROVAL_REQUESTED:
                req = self._approval_request_from_event(event)
                if req is not None:
                    requested[req.approval_id] = req
            elif event_type in {
                EventType.APPROVAL_APPROVED,
                EventType.APPROVAL_REJECTED,
                EventType.APPROVAL_EXPIRED,
            }:
                approval_id = str(payload.get("approval_id", "")).strip()
                if approval_id:
                    resolved.add(approval_id)
                decision = str(payload.get("decision", "")).strip()
                tool_name = str(payload.get("tool_name", "")).strip()
                if decision == ApprovalDecision.ALLOW_ALWAYS.value and tool_name:
                    self._allow_always[tool_name] = True

        for approval_id, request in requested.items():
            if approval_id in resolved:
                continue

            pending = PendingApproval(record=ApprovalRecord(request=request))

            if request.expires_at < now:
                pending.record.status = ApprovalStatus.EXPIRED
                pending.record.resolved_at = now
                pending.record.resolved_by = "system:recovery-expired"
                pending.event.set()
                await self._write_approval_expired_event(
                    approval_id=approval_id,
                    task_id=request.task_id,
                    expired_at=now,
                )
                continue

            self._pending[approval_id] = pending
            self._rearm_timeout_timer(approval_id, request.expires_at, now)
            recovered += 1

        logger.info("审批恢复完成: 恢复 %d 个 pending 审批", recovered)
        return recovered

    async def _recover_from_memory_pending(self, now: datetime) -> int:
        """兼容无 Event Store 场景（测试桩）"""
        recovered = 0
        for approval_id, pending in list(self._pending.items()):
            if pending.record.status != ApprovalStatus.PENDING:
                continue
            if pending.record.request.expires_at < now:
                pending.record.status = ApprovalStatus.EXPIRED
                pending.record.resolved_at = now
                pending.record.resolved_by = "system:recovery-expired"
                pending.event.set()
                continue
            self._rearm_timeout_timer(approval_id, pending.record.request.expires_at, now)
            recovered += 1
        logger.info("审批恢复完成: 恢复 %d 个 pending 审批", recovered)
        return recovered

    async def _recover_overrides_from_repo(self) -> None:
        """Feature 061: 从 ApprovalOverrideRepository 恢复 always 覆盖到内存缓存"""
        if self._override_repo is None:
            return
        try:
            all_overrides = await self._override_repo.load_all_overrides()
            if all_overrides:
                self._override_cache.load_from_records(all_overrides)
                logger.info(
                    "always 覆盖恢复完成: 加载 %d 条记录到缓存",
                    len(all_overrides),
                )
        except Exception:
            logger.warning(
                "always 覆盖恢复失败，缓存可能不完整",
                exc_info=True,
            )

    # ============================================================
    # 查询方法（供 REST API 使用）
    # ============================================================

    def get_pending_approvals(self) -> list[ApprovalRecord]:
        """获取所有 pending 状态的审批记录"""
        return [
            p.record
            for p in self._pending.values()
            if p.record.status == ApprovalStatus.PENDING
        ]

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """获取指定审批记录（含宽限期内已解决的）"""
        pending = self._pending.get(approval_id)
        return pending.record if pending is not None else None

    # ============================================================
    # 内部辅助方法
    # ============================================================

    async def _write_event(
        self,
        task_id: str,
        event_type: EventType,
        actor: ActorType,
        payload: dict[str, Any],
        idempotency_key: str,
        ts: datetime | None = None,
    ) -> None:
        """通用事件写入 helper（减少三个 _write_approval_*_event 的重复代码）"""
        if self._event_store is None:
            return
        for attempt in range(1, 4):
            seq = await self._event_store.get_next_task_seq(task_id)
            event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=ts or datetime.now(UTC),
                type=event_type,
                actor=actor,
                payload=payload,
                trace_id=task_id,
                causality=EventCausality(idempotency_key=idempotency_key),
            )
            try:
                await self._event_store.append_event(event)
                await self._commit_event_store()
                return
            except aiosqlite.IntegrityError as e:
                await self._rollback_event_store()
                if self._is_task_seq_conflict(e) and attempt < 3:
                    continue
                raise
            except Exception:
                await self._rollback_event_store()
                raise

    async def _write_approval_requested_event(
        self,
        request: ApprovalRequest,
    ) -> None:
        """写入 APPROVAL_REQUESTED 事件"""
        await self._write_event(
            task_id=request.task_id,
            event_type=EventType.APPROVAL_REQUESTED,
            actor=ActorType.SYSTEM,
            payload=ApprovalRequestedEventPayload(
                approval_id=request.approval_id,
                task_id=request.task_id,
                tool_name=request.tool_name,
                tool_args_summary=request.tool_args_summary,
                risk_explanation=request.risk_explanation,
                policy_label=request.policy_label,
                side_effect_level=request.side_effect_level.value,
                expires_at=request.expires_at.isoformat(),
                created_at=request.created_at.isoformat(),
            ).model_dump(),
            idempotency_key=f"approval-req-{request.approval_id}",
        )

    async def _write_approval_resolved_event(
        self,
        approval_id: str,
        task_id: str,
        tool_name: str,
        decision: ApprovalDecision,
        resolved_by: str,
        resolved_at: datetime,
        event_type_str: str,
    ) -> None:
        """写入 APPROVAL_APPROVED 或 APPROVAL_REJECTED 事件"""
        await self._write_event(
            task_id=task_id,
            event_type=EventType(event_type_str),
            actor=ActorType.USER if "user" in resolved_by else ActorType.SYSTEM,
            payload=ApprovalResolvedEventPayload(
                approval_id=approval_id,
                task_id=task_id,
                tool_name=tool_name,
                decision=decision.value,
                resolved_by=resolved_by,
                resolved_at=resolved_at.isoformat(),
            ).model_dump(),
            idempotency_key=f"approval-resolve-{approval_id}",
            ts=resolved_at,
        )

    async def _write_approval_expired_event(
        self,
        approval_id: str,
        task_id: str,
        expired_at: datetime,
    ) -> None:
        """写入 APPROVAL_EXPIRED 事件"""
        await self._write_event(
            task_id=task_id,
            event_type=EventType.APPROVAL_EXPIRED,
            actor=ActorType.SYSTEM,
            payload=ApprovalExpiredEventPayload(
                approval_id=approval_id,
                task_id=task_id,
                expired_at=expired_at.isoformat(),
            ).model_dump(),
            idempotency_key=f"approval-expired-{approval_id}",
            ts=expired_at,
        )

    async def _broadcast_approval_event(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        """广播 SSE 审批事件"""
        if self._sse_broadcaster is None:
            return

        try:
            await self._sse_broadcaster.broadcast(
                event_type=event_type,
                data=data,
                task_id=task_id,
            )
        except Exception as e:
            logger.error("广播 SSE 事件 '%s' 失败: %s", event_type, e)

    async def _load_events_for_recovery(self) -> list[Any]:
        if self._event_store is None:
            return []
        getter = getattr(self._event_store, "get_all_events", None)
        if not callable(getter):
            return []
        events = await getter()
        return [
            e
            for e in events
            if e.type
            in {
                EventType.APPROVAL_REQUESTED,
                EventType.APPROVAL_APPROVED,
                EventType.APPROVAL_REJECTED,
                EventType.APPROVAL_EXPIRED,
            }
        ]

    def _approval_request_from_event(self, event: Any) -> ApprovalRequest | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        approval_id = str(payload.get("approval_id", "")).strip()
        task_id = str(payload.get("task_id", "")).strip()
        tool_name = str(payload.get("tool_name", "")).strip()
        if not approval_id or not task_id or not tool_name:
            return None

        expires_at_text = str(payload.get("expires_at", "")).strip()
        created_at_text = str(payload.get("created_at", "")).strip()
        try:
            expires_at = datetime.fromisoformat(expires_at_text)
        except ValueError:
            return None

        created_at = event.ts
        if created_at_text:
            with contextlib.suppress(ValueError):
                created_at = datetime.fromisoformat(created_at_text)

        side_effect_raw = str(payload.get("side_effect_level", "")).strip().lower()
        side_effect = SideEffectLevel.IRREVERSIBLE
        if side_effect_raw:
            with contextlib.suppress(ValueError):
                side_effect = SideEffectLevel(side_effect_raw)

        return ApprovalRequest(
            approval_id=approval_id,
            task_id=task_id,
            tool_name=tool_name,
            tool_args_summary=str(payload.get("tool_args_summary", "")),
            risk_explanation=str(payload.get("risk_explanation", "")),
            policy_label=str(payload.get("policy_label", "")),
            side_effect_level=side_effect,
            expires_at=expires_at,
            created_at=created_at,
        )

    def _rearm_timeout_timer(
        self,
        approval_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        remaining = (expires_at - now).total_seconds()
        if remaining <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        handle = loop.call_later(
            remaining,
            lambda aid=approval_id: asyncio.ensure_future(self._handle_timeout(aid)),
        )
        pending = self._pending.get(approval_id)
        if pending is not None:
            pending.timer_handle = handle
            pending.event = asyncio.Event()

    @staticmethod
    def _is_task_seq_conflict(error: Exception) -> bool:
        if not isinstance(error, aiosqlite.IntegrityError):
            return False
        text = str(error)
        return "idx_events_task_seq" in text or "events.task_id, events.task_seq" in text

    async def _commit_event_store(self) -> None:
        conn = getattr(self._event_store, "_conn", None)
        if conn is not None and hasattr(conn, "commit"):
            await conn.commit()

    async def _rollback_event_store(self) -> None:
        conn = getattr(self._event_store, "_conn", None)
        if conn is not None and hasattr(conn, "rollback"):
            with contextlib.suppress(Exception):
                await conn.rollback()
