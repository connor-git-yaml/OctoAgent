"""Feature 064 P2-B: 后台执行与通知服务。

提供 NotificationChannelProtocol、NotificationService 以及内置的
SSENotificationChannel 和 TelegramNotificationChannel 实现。

职责：
1. 管理已注册的 NotificationChannel 列表
2. Task 状态变更时分发通知到所有 channel
3. 通知去重（同一 Task 同一终态只通知一次，基于 notification_id sha256 幂等）
4. Channel 失败降级（单 channel 失败不影响其他 channel，Constitution #6）

FR 覆盖: FR-064-32, FR-064-33, FR-064-34, FR-064-35, FR-064-36
F101 Phase C 扩展:
- FR-B2: 四级优先级模型
- FR-B3/B4: USER.md SoT active_hours quiet hours 过滤
- FR-B5/B6: dismiss 幂等 + 精确一次推送
- FR-B8: sha256 notification_id 生成
- H4: discard 路径写 event_store 审计链
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from ulid import ULID

from octoagent.core.models import ActorType, Event, EventType

if TYPE_CHECKING:
    pass

log = structlog.get_logger()


# ============================================================
# F101 Phase C v2 H-5：notification_id sha256 生成函数（FR-B8）
# ============================================================


def generate_notification_id(
    task_id: str,
    notification_type: str,
    state_transition_event_id: str,
) -> str:
    """生成确定性 notification_id（FR-B8）。

    使用 sha256 前 16 位确保：
    - 同一 task + 同类型 + 同事件 → 同 id（幂等去重）
    - 不同 task 或不同事件 → 不同 id（独立跟踪）

    Args:
        task_id: 任务 ID
        notification_type: 通知类型（如 "task_state_change", "approval_request"）
        state_transition_event_id: 触发通知的事件 ID（event_id 或 event_store 序号）

    Returns:
        16 字符 hex 字符串（sha256 前 16 位）
    """
    key = f"{task_id}:{notification_type}:{state_transition_event_id}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ============================================================
# F101 Phase C v2 H-7：USER.md active_hours 解析辅助函数
# ============================================================

# USER.md 中 active_hours 行的正则：匹配 "- **active_hours**: "09:00-23:00"" 或裸值
_ACTIVE_HOURS_PATTERN = re.compile(
    r"""
    (?:                             # 可选的 "- **active_hours**:" 前缀（USER.md 列表格式）
        \*\*active_hours\*\*        # **active_hours**
        \s*:\s*                     # :
    )?
    "?                              # 可选引号
    (\d{1,2}:\d{2}-\d{1,2}:\d{2})  # 时间范围（捕获组）
    "?                              # 可选引号
    """,
    re.VERBOSE,
)


def extract_active_hours_from_user_md(user_md_content: str | None) -> str | None:
    """从 USER.md 内容中提取 active_hours 值。

    支持格式：
    - ``- **active_hours**: "09:00-23:00"``（标准 USER.md 列表格式）
    - ``active_hours: "09:00-23:00"``（简洁格式）

    Args:
        user_md_content: USER.md 全文字符串，None 时返回 None

    Returns:
        active_hours 字符串（如 "09:00-23:00"），未找到时返回 None
    """
    if not user_md_content:
        return None
    for line in user_md_content.splitlines():
        if "active_hours" not in line:
            continue
        m = _ACTIVE_HOURS_PATTERN.search(line)
        if m:
            return m.group(1)
    return None


# ============================================================
# F101 Phase C: Notification Priority（FR-B2）
# ============================================================


class NotificationPriority(str, Enum):
    """通知优先级四级模型（FR-B2）。

    优先级决定是否被 quiet hours 过滤：
    - CRITICAL：始终推送（审批等待，不受 quiet hours 影响）
    - HIGH：worker 失败，quiet hours 内不推送
    - MEDIUM：worker 长时间运行，quiet hours 内不推送
    - LOW：worker 完成，quiet hours 内不推送
    """

    CRITICAL = "approval_pending"
    HIGH = "worker_failed"
    MEDIUM = "worker_long_running"
    LOW = "worker_completed"


# ============================================================
# Notification Channel Protocol (FR-064-35)
# ============================================================


class NotificationChannelProtocol(Protocol):
    """通知渠道协议。

    所有通知渠道（Telegram, Web SSE 等）实现此接口。
    单个 channel 不可用时应降级处理（Constitution #6），不影响其他 channel。
    """

    @property
    def channel_name(self) -> str:
        """渠道名称标识（如 'telegram', 'web_sse'）。"""
        ...

    async def notify(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送通知。

        Args:
            task_id: 触发通知的任务 ID
            event_type: 事件类型（EventType 枚举值）
            payload: 通知 payload（可包含 task_title, status, duration_ms 等）

        Returns:
            True 表示发送成功，False 表示发送失败（降级处理）
        """
        ...

    async def send_approval_request(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送审批请求通知（含交互按钮）。

        仅 Telegram 等支持交互的渠道需要实现。
        Web SSE 渠道可返回 False（不支持交互式审批推送）。

        Args:
            task_id: 任务 ID
            tool_name: 需审批的工具名
            ask_reason: 审批原因
            payload: 额外信息

        Returns:
            True 表示发送成功
        """
        ...


# ============================================================
# Notification Service (FR-064-35, FR-064-36)
# ============================================================


class NotificationService:
    """通知服务 -- 路由分发 + 去重 + 优先级 + quiet hours + dismiss 幂等。

    通知去重基于 notification_id（sha256 前 16 位，FR-B8）的内存 set：
    同一 task + 同类型 + 同 state_transition_event_id 只通知一次。

    F101 Phase C 扩展：
    - 四级优先级模型（FR-B2）：CRITICAL/HIGH/MEDIUM/LOW
    - quiet hours 过滤（FR-B3）：从 USER.md SoT active_hours 字段解析，CRITICAL 豁免（FR-B4）
    - dismiss 幂等（FR-B5）：跨通道共享 _dismissed_set，重复 dismiss 不报错
    - sha256 notification_id（FR-B8）：确定性 id 生成
    - discard 路径写 event_store 审计链（H4 discard 语义）
    - list_active 支持 Web list API（H3）
    """

    # 去重集合最大容量（防止无界增长）
    _MAX_NOTIFIED_SET_SIZE = 10_000

    def __init__(
        self,
        *,
        snapshot_store: Any | None = None,
        event_store: Any | None = None,
        notification_store: Any | None = None,
    ) -> None:
        """初始化 NotificationService。

        Args:
            snapshot_store: SnapshotStore 实例，用于读取 USER.md live state（FR-B4 SoT）。
                若为 None，active_hours 降级为 None（全时段推送）。
            event_store: EventStore 实例，用于写审计事件（H4 discard 审计链）。
                若为 None，跳过 event_store 写入（Constitution #6 降级）。
            notification_store: F116 SqliteNotificationStore 实例，用于 dismiss/active
                跨重启持久化。若为 None，dismiss/active 仅内存（baseline 行为，降级）。
        """
        self._channels: list[NotificationChannelProtocol] = []
        # 通知去重集合：存储 notification_id（sha256 前 16 位），FR-B8
        self._notified_set: set[str] = set()
        # F101 Phase C T-C-08：dismiss 幂等集合，跨通道共享（FR-B5）
        self._dismissed_set: set[str] = set()
        # H-7：USER.md SoT 注入（读 active_hours）
        self._snapshot_store = snapshot_store
        # H-6：event_store 注入（discard 审计链）
        self._event_store = event_store
        # F116：notification_store 注入（dismiss/active 持久化）
        self._notification_store = notification_store
        # H-4：存储 notification 元数据供 list_active 使用（session_id → list）
        self._active_notifications: dict[str, list[dict[str, Any]]] = {}

    def bind_snapshot_store(self, snapshot_store: Any) -> None:
        """延迟绑定 snapshot_store（bootstrap 顺序修复）。"""
        self._snapshot_store = snapshot_store

    def bind_event_store(self, event_store: Any) -> None:
        """延迟绑定 event_store（bootstrap 顺序修复）。"""
        self._event_store = event_store

    def bind_notification_store(self, notification_store: Any) -> None:
        """延迟绑定 notification_store（F116 bootstrap 顺序）。"""
        self._notification_store = notification_store

    async def rehydrate(self) -> None:
        """从 notification_store 恢复 dismiss/active 状态（F116 跨重启）。

        bootstrap 构造 NotificationService 后调用一次。store 为 None 或读取失败时
        降级为空（等价 baseline 全内存行为，Constitution #6），不阻断启动。
        """
        if self._notification_store is None:
            return
        try:
            self._dismissed_set = await self._notification_store.list_dismissed()
            self._active_notifications = await self._notification_store.list_active_all()
            # Codex H2：去重集合 _notified_set 也必须从持久化状态恢复，否则重启后
            # 同一 task/event 被恢复流程 / 重试 / monitor 再次 notify 时，dedup 命中失败
            # → 重复落 active + 重复推 channel（用户收到重复 Telegram/SSE）。用已落盘的
            # active + dismissed notification_id 作为「曾经派发过」的种子（quiet-hours 被
            # 过滤、从未推送的 id 不在此列，重启后允许在 active 时段重新派发，符合预期）。
            self._notified_set = set(self._dismissed_set)
            for entries in self._active_notifications.values():
                for entry in entries:
                    self._notified_set.add(entry["notification_id"])
            log.info(
                "notification_rehydrated",
                dismissed_count=len(self._dismissed_set),
                active_sessions=len(self._active_notifications),
                notified_seed=len(self._notified_set),
            )
        except Exception:
            log.warning("notification_rehydrate_failed", exc_info=True)

    def register_channel(self, channel: NotificationChannelProtocol) -> None:
        """注册通知渠道。"""
        self._channels.append(channel)
        log.info(
            "notification_channel_registered",
            channel_name=channel.channel_name,
            total_channels=len(self._channels),
        )

    @property
    def channel_count(self) -> int:
        """已注册的渠道数量。"""
        return len(self._channels)

    async def dismiss(self, notification_id: str, source: str = "unknown") -> bool:
        """幂等 dismiss（FR-B5 / AC-B6）+ F116 best-effort 持久化。

        同一 notification_id 第二次 dismiss 不报错，直接返回。
        dismiss 语义：Web 下次刷新不再显示；Telegram 已推送消息不撤回。

        F116 持久化语义（best-effort + 降级，非保证 durable）：先更新内存集合
        （保证 is_dismissed 同步读立即可见），再尝试落盘 notification_store。
        - store 为 None（未配置持久层）→ 返回 False（内存生效，但不 durable）。
        - 落盘抛异常（DB locked / 磁盘满）→ WARNING + 返回 False（内存仍生效，不 crash
          用户操作，Constitution #6 降级）。调用方据返回值向用户暴露 degraded 状态，
          不谎报 durable 成功（Codex H1）。重复 dismiss 即天然重试路径。
        - 落盘成功 → 返回 True（已 durable）。

        Returns:
            True 表示已 durable 落盘；False 表示仅内存生效（无持久层或落盘失败）。
        """
        self._dismissed_set.add(notification_id)
        log.debug(
            "notification_dismissed",
            notification_id=notification_id,
            source=source,
        )
        if self._notification_store is None:
            return False
        try:
            await self._notification_store.record_dismissal(notification_id, source)
            return True
        except Exception:
            log.warning(
                "notification_dismiss_persist_failed",
                notification_id=notification_id,
                source=source,
                exc_info=True,
            )
            return False

    def is_dismissed(self, notification_id: str) -> bool:
        """检查通知是否已 dismiss。"""
        return notification_id in self._dismissed_set

    def list_active(self, session_id: str) -> list[dict[str, Any]]:
        """返回指定 session 的未 dismiss 通知列表（FR-B5 H3 Web refresh）。

        Args:
            session_id: 会话 ID

        Returns:
            未 dismiss 的通知字典列表，每条含 notification_id / task_id / priority / payload 等
        """
        entries = self._active_notifications.get(session_id, [])
        return [e for e in entries if e["notification_id"] not in self._dismissed_set]

    async def _record_active(
        self,
        session_id: str | None,
        notification_id: str,
        task_id: str,
        notification_type: str,
        priority: "NotificationPriority",
        payload: dict[str, Any],
    ) -> None:
        """记录通知到 _active_notifications（供 list_active 使用）+ F116 落盘。

        session_id 为 None 时不记录（list_active 按 session 查询）。
        落盘失败时静默降级（Constitution #6），内存语义不变。
        """
        if session_id is None:
            return
        entry = {
            "notification_id": notification_id,
            "task_id": task_id,
            "notification_type": notification_type,
            "priority": priority.value,
            "payload": payload,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if session_id not in self._active_notifications:
            self._active_notifications[session_id] = []
        self._active_notifications[session_id].append(entry)
        # F116：持久化 active 通知（rehydrate 用）。store entry 额外带 session_id。
        if self._notification_store is not None:
            try:
                await self._notification_store.record_active({**entry, "session_id": session_id})
            except Exception:
                log.warning(
                    "notification_active_persist_failed",
                    notification_id=notification_id,
                    session_id=session_id,
                    exc_info=True,
                )

    def _read_active_hours(self) -> str | None:
        """从 USER.md live state 读取 active_hours（FR-B4 SoT）。

        H-7：通过 snapshot_store.get_live_state("USER.md") 读取，
        再用 extract_active_hours_from_user_md 解析字段值。

        Returns:
            active_hours 字符串（如 "09:00-23:00"），无法读取时返回 None
        """
        if self._snapshot_store is None:
            return None
        try:
            user_md = self._snapshot_store.get_live_state("USER.md")
            return extract_active_hours_from_user_md(user_md)
        except Exception:
            log.debug("notification_read_active_hours_failed", exc_info=True)
            return None

    async def _write_notification_audit_event(
        self,
        *,
        task_id: str,
        notification_id: str,
        notification_type: str,
        priority: "NotificationPriority",
        filtered: bool,
        payload: dict[str, Any],
        channels: frozenset[str] | None = None,
    ) -> None:
        """写 event_store 通知审计事件（H-6 H4 discard 审计链）。

        无论是否被 quiet hours 过滤，均写入 event_store 保留审计链。
        event_store 不可用时静默降级（Constitution #6）。

        Args:
            task_id: 任务 ID
            notification_id: sha256 通知 ID
            notification_type: 通知类型字符串
            priority: 通知优先级
            filtered: True = 被 quiet hours 过滤（channel push 跳过），False = 正常推送
            payload: 通知内容
            channels: F102 SD-6 / FR-B8 per-call channel 过滤集合；None 表示对所有
                已注册 channel push（向后兼容，F101 现有 caller 不传）；
                传入集合时仅推 channel_name ∈ channels 的 channel
        """
        if self._event_store is None:
            return
        try:
            event = Event(
                event_id=f"notif-audit-{ULID()}",
                task_id=task_id,
                task_seq=0,  # 审计事件不占用 task_seq
                ts=datetime.now(UTC),
                type=EventType.NOTIFICATION_DISPATCHED,
                actor=ActorType.SYSTEM,
                payload={
                    "notification_id": notification_id,
                    "notification_type": notification_type,
                    "priority": priority.value,
                    "filtered": filtered,
                    # F102 FR-B8：channels=None 时不写字段（保持向后兼容，避免 F101
                    # 旧 NOTIFICATION_DISPATCHED 事件 schema 出现 channels: null）；
                    # channels 显式传入时按字典序写 list[str]
                    **(
                        {"channels": sorted(channels)} if channels is not None else {}
                    ),
                    **{k: v for k, v in payload.items() if k != "notification_id"},
                },
                trace_id="",
            )
            append_fn = getattr(self._event_store, "append_event_committed", None)
            if append_fn is not None:
                await append_fn(event)
            else:
                await self._event_store.append_event(event)
        except Exception:
            log.debug(
                "notification_audit_event_write_failed",
                task_id=task_id,
                notification_id=notification_id,
                exc_info=True,
            )

    # ----------------------------------------------------------------
    # F101 Phase C T-C-03: quiet hours 解析（FR-B3 第 1 步）
    # ----------------------------------------------------------------

    @staticmethod
    def _parse_active_hours(raw: str | None) -> tuple[time, time] | None:
        """解析 active_hours 字段为 (start_time, end_time) 元组。

        格式：``"HH:MM-HH:MM"``（24 小时制）。
        非法格式或 None → 返回 None（全时段推送，AC-B4 兜底）。
        不抛异常。
        """
        if not raw or not isinstance(raw, str):
            return None
        raw = raw.strip()
        try:
            if "-" not in raw:
                return None
            parts = raw.split("-", 1)
            if len(parts) != 2:
                return None
            start_str, end_str = parts
            sh, sm = start_str.strip().split(":")
            eh, em = end_str.strip().split(":")
            start = time(int(sh), int(sm))
            end = time(int(eh), int(em))
            return start, end
        except (ValueError, AttributeError):
            return None

    # ----------------------------------------------------------------
    # F101 Phase C T-C-04: quiet hours 过滤（FR-B3 第 2 步）
    # ----------------------------------------------------------------

    @staticmethod
    def _is_quiet_hours(
        now: datetime,
        active_hours: str | None,
        priority: NotificationPriority = NotificationPriority.LOW,
    ) -> bool:
        """判断当前时刻是否在 quiet hours 内（即不在 active hours 内）。

        返回 True → 处于 quiet hours，应过滤（CRITICAL 除外）。
        返回 False → 处于 active hours 或未配置，不过滤。

        规则：
        - active_hours 未配置或非法 → 返回 False（全时段推送）
        - CRITICAL 优先级 → 始终返回 False（豁免，AC-B2）
        - 左闭右开 [start, end)：
            - 非跨 midnight（start < end）：active = start <= now < end
            - 跨 midnight（start >= end）：active = now >= start OR now < end
        """
        # CRITICAL 始终豁免（AC-B2）
        if priority == NotificationPriority.CRITICAL:
            return False

        parsed = NotificationService._parse_active_hours(active_hours)
        if parsed is None:
            return False  # 未配置 → 全时段推送，不过滤

        start, end = parsed
        now_t = now.time()

        if start == end:
            # 相等表示 24 小时均为 active，永不过滤
            return False

        if start < end:
            # 不跨 midnight：active = [start, end)
            in_active = start <= now_t < end
        else:
            # 跨 midnight：active = [start, 24:00) ∪ [00:00, end)
            in_active = now_t >= start or now_t < end

        return not in_active  # 不在 active hours 内 = quiet hours

    async def notify_task_state_change(
        self,
        *,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        priority: NotificationPriority = NotificationPriority.LOW,
        active_hours: str | None = None,
        state_transition_event_id: str = "",
        session_id: str | None = None,
        channels: frozenset[str] | None = None,
    ) -> None:
        """Task 状态变更通知（FR-064-32）。

        分发到所有已注册 channel。去重：同一 Task + 同类型 + 同 event_id 只通知一次（FR-B8）。
        Channel 不可用时降级记录日志，不影响 Task 执行（Constitution #6）。

        F101 Phase C v2 扩展：
        - notification_id：sha256(task_id:event_type:state_transition_event_id)[:16]（FR-B8）
        - active_hours：优先从 USER.md SoT 读取（FR-B4），调用方传入的作为 fallback
        - 被过滤的通知不推送 channel，但仍写 event_store（H4 discard 审计链）
        - session_id：用于 list_active 查询（H-4 Web list API）

        F102 SD-6 / FR-B8 扩展：
        - channels：per-call channel 过滤集合（None=全推，向后兼容 F101 所有现有 caller）；
          传入 frozenset 时仅对 channel.channel_name ∈ channels 的 channel 调用 notify。
          F102 daily routine 唯一传 channels 的 caller（基于 USER.md summary_channels 配置）。

        Args:
            task_id: 任务 ID
            event_type: 事件类型（如 STATE_TRANSITION）
            payload: 通知内容（含 task_title, from_status, to_status, duration_ms 等）
            priority: 通知优先级（默认 LOW）
            active_hours: 调用方传入的 active_hours（fallback，优先 USER.md SoT）
            state_transition_event_id: 触发通知的事件 ID，用于 sha256 生成
            session_id: 会话 ID，用于 list_active 查询
            channels: F102 per-call channel 过滤，默认 None=对所有已注册 channel 推送
        """
        # H-5：sha256 notification_id（FR-B8）
        notification_id = generate_notification_id(
            task_id, event_type, state_transition_event_id
        )

        # Codex H2：已 dismiss 的通知绝不再次派发（dispatch 不能只靠 _notified_set，
        # 因重启后该集合即便已 seed，也可能漏掉边界 id；显式查 _dismissed_set 兜底，
        # 保证用户关掉的状态变更通知不会因任何重放而重现到 Telegram/SSE/Web）。
        # 仅作用于 state_change（LOW/MED/HIGH）；approval 走 notify_approval_request
        # 不受此 guard 影响（CRITICAL 审批待办需可重新浮现）。
        if notification_id in self._dismissed_set:
            log.debug(
                "notification_skipped_already_dismissed",
                task_id=task_id,
                event_type=event_type,
                notification_id=notification_id,
            )
            return

        # 去重检查（FR-B8：按 notification_id 去重）
        if notification_id in self._notified_set:
            log.debug(
                "notification_deduplicated",
                task_id=task_id,
                event_type=event_type,
                notification_id=notification_id,
            )
            return
        self._notified_set.add(notification_id)

        # 防止无界增长：超过阈值时清空（简单策略，后续可改 LRU）
        if len(self._notified_set) > self._MAX_NOTIFIED_SET_SIZE:
            self._notified_set.clear()

        # H-7：优先从 USER.md SoT 读 active_hours（FR-B4）
        resolved_active_hours = self._read_active_hours() or active_hours

        # F101 Phase C T-C-05：quiet hours 过滤（FR-B3）
        filtered = self._is_quiet_hours(datetime.now(UTC), resolved_active_hours, priority)

        # H-6：无论是否过滤，先写 event_store 审计事件（H4 discard 审计链）
        await self._write_notification_audit_event(
            task_id=task_id,
            notification_id=notification_id,
            notification_type=event_type,
            priority=priority,
            filtered=filtered,
            payload=payload,
            channels=channels,
        )

        if filtered:
            log.debug(
                "notification_quiet_hours_filtered",
                task_id=task_id,
                event_type=event_type,
                notification_id=notification_id,
                priority=priority.value,
            )
            return

        # H-4：记录到 _active_notifications（供 Web list_active）
        await self._record_active(
            session_id=session_id,
            notification_id=notification_id,
            task_id=task_id,
            notification_type=event_type,
            priority=priority,
            payload=payload,
        )

        if not self._channels:
            return

        # H-3：将 notification_id 注入 payload 供 TelegramNotificationChannel 构建 dismiss 按钮
        channel_payload = {**payload, "notification_id": notification_id}

        for channel in self._channels:
            # F102 FR-B8：channels=None 时不过滤（向后兼容）；非 None 时仅推匹配
            if channels is not None and channel.channel_name not in channels:
                continue
            try:
                await channel.notify(task_id, event_type, channel_payload)
            except Exception:
                log.warning(
                    "notification_channel_failed",
                    channel=channel.channel_name,
                    task_id=task_id,
                    event_type=event_type,
                    exc_info=True,
                )

    async def notify_approval_request(
        self,
        *,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
        priority: NotificationPriority = NotificationPriority.CRITICAL,
        active_hours: str | None = None,
        state_transition_event_id: str = "",
        session_id: str | None = None,
    ) -> None:
        """审批请求通知（FR-064-33）。

        审批通知默认优先级为 CRITICAL（始终推送，不被 quiet hours 过滤）。
        同一 Task 可能多次请求审批，故不以 notification_id 去重（每次都推送）。

        F101 Phase C v2 扩展：
        - notification_id：sha256(task_id:"approval_request":state_transition_event_id)[:16]
        - event_store 审计事件（H4 discard 审计链）

        Args:
            task_id: 任务 ID
            tool_name: 需审批的工具名
            ask_reason: 审批原因
            payload: 额外信息
            priority: 通知优先级（默认 CRITICAL，确保 quiet hours 内也推送）
            active_hours: 调用方传入的 active_hours（fallback，优先 USER.md SoT）
            state_transition_event_id: 触发通知的事件 ID
            session_id: 会话 ID，用于 list_active 查询
        """
        # H-5：sha256 notification_id（FR-B8）
        notification_id = generate_notification_id(
            task_id, "approval_request", state_transition_event_id
        )

        # H-7：优先从 USER.md SoT 读 active_hours（FR-B4）
        resolved_active_hours = self._read_active_hours() or active_hours

        # F101 Phase C T-C-05：quiet hours 过滤（审批请求默认 CRITICAL，豁免）
        filtered = self._is_quiet_hours(datetime.now(UTC), resolved_active_hours, priority)

        # H-6：无论是否过滤，写 event_store 审计事件
        await self._write_notification_audit_event(
            task_id=task_id,
            notification_id=notification_id,
            notification_type="approval_request",
            priority=priority,
            filtered=filtered,
            payload={**payload, "tool_name": tool_name, "ask_reason": ask_reason},
        )

        if filtered:
            log.debug(
                "approval_notification_quiet_hours_filtered",
                task_id=task_id,
                tool_name=tool_name,
                notification_id=notification_id,
                priority=priority.value,
            )
            return

        # H-4：记录到 _active_notifications（供 Web list_active）
        await self._record_active(
            session_id=session_id,
            notification_id=notification_id,
            task_id=task_id,
            notification_type="approval_request",
            priority=priority,
            payload={**payload, "tool_name": tool_name, "ask_reason": ask_reason},
        )

        if not self._channels:
            return

        for channel in self._channels:
            try:
                await channel.send_approval_request(
                    task_id, tool_name, ask_reason, payload,
                )
            except Exception:
                log.warning(
                    "approval_notification_failed",
                    channel=channel.channel_name,
                    task_id=task_id,
                    tool_name=tool_name,
                    exc_info=True,
                )

    async def notify_heartbeat(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        """心跳进度通知（FR-064-34）。

        心跳通知不去重（定期发送）。

        Args:
            task_id: 任务 ID
            payload: 心跳 payload（含 loop_step, summary 等）
        """
        if not self._channels:
            return

        for channel in self._channels:
            try:
                await channel.notify(task_id, "TASK_HEARTBEAT", payload)
            except Exception:
                log.warning(
                    "heartbeat_notification_failed",
                    channel=channel.channel_name,
                    task_id=task_id,
                    exc_info=True,
                )


# ============================================================
# SSE Notification Channel (FR-064-32)
# ============================================================


class SSENotificationChannel:
    """基于已有 SSEHub 的通知渠道实现。

    将通知转化为 SSE 事件广播到 Web UI 订阅者。
    SSE 渠道不支持交互式审批推送（send_approval_request 返回 False）。
    """

    def __init__(self, sse_hub) -> None:
        """初始化 SSE 通知渠道。

        Args:
            sse_hub: SSEHub 实例（apps/gateway/services/sse_hub.py）
        """
        self._sse_hub = sse_hub

    @property
    def channel_name(self) -> str:
        return "web_sse"

    async def notify(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """通过 SSE 广播通知事件。

        SSE 渠道利用已有的 SSEHub.broadcast() 机制，
        订阅对应 task_id 的 Web UI 客户端会实时收到通知。
        """
        if self._sse_hub is None:
            return False

        try:
            event = Event(
                event_id=f"notif-{ULID()}",
                task_id=task_id,
                task_seq=0,  # 通知事件不占用 task_seq
                ts=datetime.now(UTC),
                type=EventType.STATE_TRANSITION,
                actor=ActorType.SYSTEM,
                payload=payload,
                trace_id="",
            )
            await self._sse_hub.broadcast(task_id, event)
            return True
        except Exception:
            log.warning(
                "sse_notification_broadcast_failed",
                task_id=task_id,
                event_type=event_type,
                exc_info=True,
            )
            return False

    async def send_approval_request(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        """SSE 渠道不支持交互式审批推送。"""
        return False


# ============================================================
# Telegram Notification Channel (FR-064-32, FR-064-33)
# ============================================================

# Task 终态中文显示映射
_STATUS_DISPLAY: dict[str, str] = {
    "SUCCEEDED": "已完成",
    "FAILED": "执行失败",
    "CANCELLED": "已取消",
    "REJECTED": "已拒绝",
    "WAITING_APPROVAL": "等待审批",
    "RUNNING": "执行中",
}


class TelegramNotificationChannel:
    """Telegram 渠道通知实现。

    - Task 终态（SUCCEEDED/FAILED/CANCELLED）推送通知
    - WAITING_APPROVAL 时发送审批消息含 inline keyboard（批准/拒绝按钮）
    - 使用中文，包含 Task 标题 + 状态 + 耗时
    - Telegram 不可用时降级（仅记录日志，Constitution #6）

    注意：telegram_bot 和 chat_id 由外部注入。Gateway 层只定义接口，
    实际 Telegram bot 调用可用 stub（aiogram 依赖在 plugins/channels/telegram 中）。
    """

    def __init__(
        self,
        *,
        send_message_fn: Any | None = None,
        chat_id: str | None = None,
    ) -> None:
        """初始化 Telegram 通知渠道。

        Args:
            send_message_fn: 异步发送消息函数，签名
                async (chat_id: str, text: str, reply_markup: dict | None) -> Any
                如果为 None 则所有通知降级为日志记录。
            chat_id: 默认接收通知的 Telegram chat ID
        """
        self._send_message_fn = send_message_fn
        self._chat_id = chat_id

    @property
    def channel_name(self) -> str:
        return "telegram"

    def _format_duration(self, duration_ms: int | None) -> str:
        """将毫秒格式化为人类可读的耗时字符串。"""
        if duration_ms is None:
            return ""
        seconds = duration_ms / 1000
        if seconds < 60:
            return f"{seconds:.1f}秒"
        minutes = seconds / 60
        if minutes < 60:
            return f"{minutes:.1f}分钟"
        hours = minutes / 60
        return f"{hours:.1f}小时"

    def _build_state_change_text(self, payload: dict[str, Any]) -> str:
        """构建状态变更通知的消息文本。"""
        task_title = payload.get("task_title", "未命名任务")
        to_status = payload.get("to_status", "")
        status_text = _STATUS_DISPLAY.get(to_status, to_status)
        duration_ms = payload.get("duration_ms")

        lines = [
            "📋 任务通知",
            f"任务: {task_title}",
            f"状态: {status_text}",
        ]

        if duration_ms is not None:
            lines.append(f"耗时: {self._format_duration(duration_ms)}")

        summary = payload.get("summary", "")
        if summary:
            # 截断过长摘要
            if len(summary) > 200:
                summary = summary[:200] + "..."
            lines.append(f"摘要: {summary}")

        return "\n".join(lines)

    def _build_approval_text(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> str:
        """构建审批请求通知的消息文本。"""
        task_title = payload.get("task_title", "未命名任务")
        lines = [
            "🔐 审批请求",
            f"任务: {task_title}",
            f"工具: {tool_name}",
            f"原因: {ask_reason}",
        ]
        timeout = payload.get("timeout_seconds", 300)
        lines.append(f"超时: {timeout}秒")
        return "\n".join(lines)

    def _build_approval_keyboard(self, task_id: str) -> dict[str, Any]:
        """构建审批 inline keyboard（批准/拒绝按钮）。"""
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ 批准",
                        "callback_data": f"approve:{task_id}",
                    },
                    {
                        "text": "❌ 拒绝",
                        "callback_data": f"reject:{task_id}",
                    },
                ]
            ]
        }

    def _build_dismiss_keyboard(self, notification_id: str) -> dict[str, Any] | None:
        """构建通知 dismiss inline keyboard（H-3 FR-B5）。

        若 notification_id 为空则返回 None（不添加按钮）。

        Args:
            notification_id: sha256 通知 ID（16 字符 hex）
        """
        if not notification_id:
            return None
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "🔕 关闭",
                        "callback_data": f"dismiss_notif:{notification_id}",
                    }
                ]
            ]
        }

    async def notify(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送 Telegram 通知（含可选 dismiss 按钮）。

        F101 Phase C v2 H-3：若 payload 含 notification_id，
        消息附带 "🔕 关闭" inline keyboard 按钮，
        用户点击后触发 dismiss_notif:<notification_id> callback。
        """
        if self._send_message_fn is None or self._chat_id is None:
            log.debug(
                "telegram_notification_skipped",
                reason="no_send_fn_or_chat_id",
                task_id=task_id,
            )
            return False

        text = self._build_state_change_text(payload)
        # H-3：从 payload 取 notification_id，构建 dismiss keyboard
        notification_id = payload.get("notification_id", "")
        keyboard = self._build_dismiss_keyboard(notification_id)
        try:
            await self._send_message_fn(
                self._chat_id, text, keyboard,
            )
            return True
        except Exception:
            log.warning(
                "telegram_notification_send_failed",
                task_id=task_id,
                event_type=event_type,
                exc_info=True,
            )
            return False

    async def send_approval_request(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送审批请求消息含 inline keyboard（FR-064-33）。"""
        if self._send_message_fn is None or self._chat_id is None:
            log.debug(
                "telegram_approval_skipped",
                reason="no_send_fn_or_chat_id",
                task_id=task_id,
            )
            return False

        text = self._build_approval_text(task_id, tool_name, ask_reason, payload)
        keyboard = self._build_approval_keyboard(task_id)
        try:
            await self._send_message_fn(
                self._chat_id, text, keyboard,
            )
            return True
        except Exception:
            log.warning(
                "telegram_approval_send_failed",
                task_id=task_id,
                tool_name=tool_name,
                exc_info=True,
            )
            return False
