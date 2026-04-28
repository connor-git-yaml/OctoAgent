"""user_profile_tools：USER.md 写入流（T026-T029）。

Feature 084 Phase 2 —— D4 命名清晰路径（替代旧的 bootstrap.complete）。

工具列表：
- user_profile.update：add / replace / remove 三种操作
  - add: ThreatScanner.scan() → 通过则原子写入 USER.md → 写 SnapshotRecord
        + MEMORY_ENTRY_ADDED 事件
  - replace / remove: Phase 2 暂未启用（依赖 ApprovalGate Phase 3）；
    返回 status="rejected" + reason="approval_pending"
- user_profile.read：从 SnapshotStore live state 读 USER.md，按 § 分隔
- user_profile.observe：写入 observation_candidates 表（待用户审核）

USER.md 路径约定：project_root / "behavior" / "system" / "USER.md"
USER.md 字符上限：50,000（FR-7.6）

参考：_references/opensource/hermes-agent/ memory.add 模式
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from ulid import ULID

from octoagent.core.models.enums import ActorType, EventType, SideEffectLevel
from octoagent.core.models.event import Event
from octoagent.core.models.tool_results import (
    ObserveResult,
    UserProfileUpdateResult,
    WriteResult,
)
from octoagent.gateway.harness.snapshot_store import CharLimitExceeded
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.gateway.harness.threat_scanner import scan as threat_scan
from octoagent.tooling import reflect_tool_schema, tool_contract

from ..services.builtin_tools._deps import ToolDeps

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

USER_MD_CHAR_LIMIT = 50_000  # FR-7.6
PREVIEW_MAX_LEN = 200  # WriteResult.preview 上限（与 model validator 一致）
OBSERVATION_QUEUE_MAX = 50  # FR-7.4：candidates 队列长度上限
OBSERVATION_TTL_DAYS = 30
MIN_INITIAL_CONFIDENCE = 0.7  # FR-7.4：低置信度直接 skip
ENTRY_SEPARATOR = "\n\n§ "  # USER.md entry 分隔符（与 § 起始符号配对）

# 各工具 entrypoints 声明（与 contracts/tools-contract.md 对齐）
_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "user_profile.update":  frozenset({"agent_runtime", "web", "telegram"}),
    "user_profile.read":    frozenset({"agent_runtime", "web", "telegram"}),
    "user_profile.observe": frozenset({"agent_runtime", "web", "telegram"}),
}


def _user_md_path(project_root: Path) -> Path:
    """统一解析 USER.md 路径（与 behavior_workspace 约定一致）。"""
    return project_root / "behavior" / "system" / "USER.md"


def _make_preview(text: str, limit: int = PREVIEW_MAX_LEN) -> str:
    """生成 ≤ limit 字符的预览（超出时截断 + ellipsis）。"""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _split_entries(content: str) -> list[str]:
    """按 § 分隔符切分 USER.md 条目。

    支持两种情形：
    - 空内容 → 返回 []
    - 含 § 起始符的多条 entry → 切分（保留首条没有 § 前缀的 free-form）
    """
    if not content.strip():
        return []
    # 简化：按 "§" 切分，去掉空段
    parts = [p.strip() for p in content.split("§")]
    return [p for p in parts if p]


def _hash_fact(fact_content: str) -> str:
    """SHA-256 用于 observation_candidates dedup。"""
    return hashlib.sha256(fact_content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# T026: UserProfileUpdateInput schema
# ---------------------------------------------------------------------------


class UserProfileUpdateInput(BaseModel):
    """user_profile.update 输入 schema（contracts/tools-contract.md 对齐）。"""

    operation: Literal["add", "replace", "remove"] = Field(description="操作类型")
    content: str = Field(description="add 时新条目内容；remove 时同 target_text")
    old_text: str | None = Field(default=None, description="replace 时被替换的原文")
    target_text: str | None = Field(default=None, description="remove 时要删除的目标")


# ---------------------------------------------------------------------------
# 注册入口
# ---------------------------------------------------------------------------


async def register(broker, deps: ToolDeps) -> None:
    """注册 user_profile.* 三个工具。"""

    # ----- T027: user_profile.update -----

    @tool_contract(
        name="user_profile.update",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        tool_group="user_profile",
        produces_write=True,
        tags=["user_profile", "memory", "update"],
        manifest_ref="builtin://user_profile.update",
        metadata={
            "entrypoints": ["agent_runtime", "web", "telegram"],
        },
    )
    async def user_profile_update(
        operation: Literal["add", "replace", "remove"],
        content: str,
        old_text: str = "",
        target_text: str = "",
    ) -> UserProfileUpdateResult:
        """写入/更新 USER.md 档案内容。

        - add：直接写入，ThreatScanner 通过即落盘 + 写 SnapshotRecord 事件
        - replace / remove：Phase 2 暂未启用，返回 approval_pending（依赖 ApprovalGate Phase 3）

        Args:
            operation: 操作类型（add/replace/remove）
            content: add 时为新条目内容，remove 时同 target_text
            old_text: replace 时的被替换原文
            target_text: remove 时的目标文本（如同 content 也可）
        """
        user_md = _user_md_path(deps.project_root)
        target_id = str(user_md)

        # 操作类型守门：replace/remove 等 Phase 3 ApprovalGate
        if operation in ("replace", "remove"):
            return UserProfileUpdateResult(
                status="rejected",
                target=target_id,
                reason=(
                    f"approval_pending: {operation} 操作需 ApprovalGate（Phase 3 上线后启用）；"
                    "当前仅支持 add 操作"
                ),
                approval_requested=False,
            )

        # 1) ThreatScanner 扫描
        scan_result = threat_scan(content)
        if scan_result.blocked:
            await _emit_event(
                deps,
                event_type=EventType.MEMORY_ENTRY_BLOCKED,
                payload={
                    "tool": "user_profile.update",
                    "operation": operation,
                    "pattern_id": scan_result.pattern_id,
                    "severity": scan_result.severity,
                    "input_hash": _hash_fact(content),
                    # 不写完整恶意内容，FR-3.4 / Constitution C5
                },
            )
            return UserProfileUpdateResult(
                status="rejected",
                target=target_id,
                reason=f"threat_blocked: {scan_result.matched_pattern_description or scan_result.pattern_id}",
                blocked=True,
                pattern_id=scan_result.pattern_id,
                approval_requested=False,
            )

        # 2)+3) 原子 read-modify-write（防 F21 concurrent add 数据丢失回归）
        # SnapshotStore.append_entry 把 read + 限额检查 + atomic write 全部放进
        # 同一 async + flock 临界区；旧的"先 read 再 write_through"模式会导致
        # 两个并发 add 各自 read 同一旧内容 → 后写者覆盖前写者的条目。
        snapshot_store = deps._snapshot_store
        if snapshot_store is None:
            # T033 lifespan 接入前才会发生：直接拒绝，避免不安全的 read-modify-write
            return UserProfileUpdateResult(
                status="rejected",
                target=target_id,
                reason="snapshot_store_not_bound: F084 Phase 2 T033 lifespan 接入前 user_profile.update 不可用",
                approval_requested=False,
            )
        try:
            new_content, bytes_written = await snapshot_store.append_entry(
                user_md,
                content,
                entry_separator=ENTRY_SEPARATOR,
                first_entry_prefix="§ ",
                char_limit=USER_MD_CHAR_LIMIT,
                live_state_key="USER.md",
            )
        except CharLimitExceeded as exc:
            return UserProfileUpdateResult(
                status="rejected",
                target=target_id,
                reason=f"char_limit_exceeded: USER.md 总字符 {exc.actual} 超过上限 {exc.limit}",
                approval_requested=False,
            )

        # 4) SnapshotRecord 持久化（FR-2.3，记录工具调用回显摘要）
        tool_call_id = str(ULID())
        result_summary = f"USER.md add: {_make_preview(content, 480)}"
        if snapshot_store is not None:
            try:
                await snapshot_store.persist_snapshot_record(
                    tool_call_id=tool_call_id,
                    result_summary=result_summary,
                )
            except Exception as exc:
                # SnapshotRecord 落库失败不阻断主路径（写入已成功）
                import structlog
                structlog.get_logger(__name__).warning(
                    "snapshot_record_persist_failed",
                    tool_call_id=tool_call_id,
                    error=str(exc),
                )

        # 5) 写 MEMORY_ENTRY_ADDED 事件（Constitution C2）
        mtime_iso = (
            datetime.fromtimestamp(user_md.stat().st_mtime, tz=timezone.utc).isoformat()
            if user_md.exists()
            else None
        )
        await _emit_event(
            deps,
            event_type=EventType.MEMORY_ENTRY_ADDED,
            payload={
                "tool": "user_profile.update",
                "operation": operation,
                "target": target_id,
                "preview": _make_preview(content),
                "tool_call_id": tool_call_id,
                "mtime_iso": mtime_iso,
                "bytes_written": bytes_written,
            },
        )

        # 6) 异步触发 OwnerProfile sync（T031 完整接入；当前桩位）
        try:
            from octoagent.core.models.agent_context import sync_owner_profile_from_user_md
            asyncio.create_task(sync_owner_profile_from_user_md(user_md))
        except (ImportError, AttributeError):
            # T030 sync hook 还未实现时降级（不阻断写入）
            pass

        return UserProfileUpdateResult(
            status="written",
            target=target_id,
            preview=_make_preview(content),
            mtime_iso=mtime_iso,
            bytes_written=bytes_written,
            blocked=False,
            pattern_id=None,
            approval_requested=False,
        )

    # ----- T028: user_profile.read -----

    @tool_contract(
        name="user_profile.read",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="user_profile",
        produces_write=False,  # 只读
        tags=["user_profile", "memory", "read"],
        manifest_ref="builtin://user_profile.read",
        metadata={
            "entrypoints": ["agent_runtime", "web", "telegram"],
        },
    )
    async def user_profile_read() -> str:
        """读取 USER.md 当前内容（live state，非冻结快照）。

        Returns:
            JSON 字符串，含 entries / total_chars / char_limit
        """
        snapshot_store = deps._snapshot_store
        if snapshot_store is not None:
            content = snapshot_store.get_live_state("USER.md") or ""
        else:
            user_md = _user_md_path(deps.project_root)
            try:
                content = user_md.read_text(encoding="utf-8") if user_md.exists() else ""
            except OSError:
                content = ""

        entries = _split_entries(content)
        return json.dumps(
            {
                "entries": entries,
                "total_chars": len(content),
                "char_limit": USER_MD_CHAR_LIMIT,
            },
            ensure_ascii=False,
        )

    # ----- T029: user_profile.observe -----

    @tool_contract(
        name="user_profile.observe",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="user_profile",
        produces_write=True,
        tags=["user_profile", "observation", "candidates"],
        manifest_ref="builtin://user_profile.observe",
        metadata={
            "entrypoints": ["agent_runtime", "web", "telegram"],
        },
    )
    async def user_profile_observe(
        fact_content: str,
        source_turn_id: str,
        initial_confidence: float,
    ) -> ObserveResult:
        """将候选事实写入 observation_candidates 队列（待用户审核）。

        三个 skip 闸：
        1) initial_confidence < 0.7 → skipped + low_confidence
        2) candidates 队列 ≥ 50 → skipped + queue_full
        3) source_turn_id + fact_content_hash 已存在 → skipped + duplicate (dedup_hit=True)
        ThreatScanner block → rejected + threat_blocked
        通过 → written + 入队 + OBSERVATION_OBSERVED 事件
        """
        target_id = "observation_candidates"

        # 闸 1: 置信度
        if initial_confidence < MIN_INITIAL_CONFIDENCE:
            return ObserveResult(
                status="skipped",
                target=target_id,
                reason=f"low_confidence: {initial_confidence:.2f} < {MIN_INITIAL_CONFIDENCE}",
                queued=False,
            )

        # 闸 2: ThreatScanner（位于队列长度检查之前——避免恶意内容浪费队列名额）
        scan_result = threat_scan(fact_content)
        if scan_result.blocked:
            await _emit_event(
                deps,
                event_type=EventType.MEMORY_ENTRY_BLOCKED,
                payload={
                    "tool": "user_profile.observe",
                    "pattern_id": scan_result.pattern_id,
                    "severity": scan_result.severity,
                    "input_hash": _hash_fact(fact_content),
                },
            )
            return ObserveResult(
                status="rejected",
                target=target_id,
                reason=f"threat_blocked: {scan_result.matched_pattern_description or scan_result.pattern_id}",
                queued=False,
            )

        conn = getattr(deps.stores, "conn", None)
        if conn is None:
            return ObserveResult(
                status="rejected",
                target=target_id,
                reason="no_db_connection: observation_candidates 表不可用",
                queued=False,
            )

        # 闸 3: 队列长度
        async with conn.execute(
            "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
        ) as cur:
            row = await cur.fetchone()
            count = int(row["cnt"]) if row else 0
        if count >= OBSERVATION_QUEUE_MAX:
            return ObserveResult(
                status="skipped",
                target=target_id,
                reason=f"queue_full: pending {count} ≥ {OBSERVATION_QUEUE_MAX}",
                queued=False,
            )

        # 闸 4: dedup（source_turn_id + fact_content_hash）
        fact_hash = _hash_fact(fact_content)
        async with conn.execute(
            """
            SELECT id FROM observation_candidates
            WHERE source_turn_id = ? AND fact_content_hash = ?
            """,
            (source_turn_id, fact_hash),
        ) as cur:
            dup = await cur.fetchone()
        if dup is not None:
            return ObserveResult(
                status="skipped",
                target=f"observation_candidates:{dup['id']}",
                reason="duplicate: source_turn_id + fact_content_hash 已入队",
                queued=False,
                dedup_hit=True,
                candidate_id=dup["id"],
            )

        # 入队
        candidate_id = str(ULID())
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=OBSERVATION_TTL_DAYS)
        owner_user_id = "owner"  # Phase 2 单用户假设；Phase 3 多用户引入时改为 deps.current_user_id
        await conn.execute(
            """
            INSERT INTO observation_candidates (
                id, fact_content, fact_content_hash, category, confidence, status,
                source_turn_id, edited, created_at, expires_at, user_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?)
            """,
            (
                candidate_id,
                fact_content,
                fact_hash,
                None,
                float(initial_confidence),
                source_turn_id,
                now.isoformat(),
                expires.isoformat(),
                owner_user_id,
            ),
        )
        await conn.commit()

        await _emit_event(
            deps,
            event_type=EventType.OBSERVATION_OBSERVED,
            payload={
                "candidate_id": candidate_id,
                "source_turn_id": source_turn_id,
                "preview": _make_preview(fact_content),
                "initial_confidence": float(initial_confidence),
            },
        )

        return ObserveResult(
            status="written",
            target=f"observation_candidates:{candidate_id}",
            preview=_make_preview(fact_content),
            queued=True,
            candidate_id=candidate_id,
            dedup_hit=False,
        )

    # ---- 注册到 broker + ToolRegistry ----
    for handler in (user_profile_update, user_profile_read, user_profile_observe):
        await broker.try_register(reflect_tool_schema(handler), handler)

    for _name, _handler, _sel in (
        ("user_profile.update",  user_profile_update,  SideEffectLevel.IRREVERSIBLE),
        ("user_profile.read",    user_profile_read,    SideEffectLevel.NONE),
        ("user_profile.observe", user_profile_observe, SideEffectLevel.REVERSIBLE),
    ):
        _registry_register(ToolEntry(
            name=_name,
            entrypoints=_TOOL_ENTRYPOINTS[_name],
            toolset="user_profile",
            handler=_handler,
            schema=BaseModel,
            side_effect_level=_sel,
        ))


# ---------------------------------------------------------------------------
# 内部辅助：写事件
# ---------------------------------------------------------------------------


async def _emit_event(deps: ToolDeps, *, event_type: EventType, payload: dict[str, Any]) -> None:
    """统一写审计事件辅助（Constitution C2 / FR-10）。

    防 F22 回归：
    - 用真实 Event schema 字段：event_id / task_id / task_seq / ts / type / actor
      （旧代码用 event_type / actor_type / actor_id / timestamp 全部不存在，
      会被 Pydantic ValidationError 静默吞掉，导致审计写入消失）
    - 用真实 EventStore API：append_event_committed（旧代码用 append() 不存在）
    - task_seq 通过 get_next_task_seq(task_id) 拿，沿用与 resume_engine /
      operator_actions 一致的模式
    - task_id 缺失时仍要写入，使用 "_user_profile_audit" 作为审计专用占位 task_id
      （类似 operator_actions 的 _OPERATOR_AUDIT_TASK_ID 模式）
    """
    AUDIT_TASK_ID = "_user_profile_audit"  # 与 operator_actions 占位 task_id 同模式
    try:
        from ..services.execution_context import get_current_execution_context
        context = get_current_execution_context()
        task_id = (context.task_id if context else "") or AUDIT_TASK_ID
    except Exception:
        task_id = AUDIT_TASK_ID

    try:
        event_store = deps.stores.event_store
        # task_seq：审计 task_id 与真实 task_id 走同一 get_next_task_seq 路径
        task_seq = await event_store.get_next_task_seq(task_id)
        event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=task_seq,
            ts=datetime.now(timezone.utc),
            type=event_type,
            actor=ActorType.AGENT,
            payload=payload,
        )
        await event_store.append_event_committed(event, update_task_pointer=False)
    except Exception as exc:
        # 仍保留降级——但用 ERROR 级而非 WARNING，让 audit drop 显眼
        import structlog
        structlog.get_logger(__name__).error(
            "user_profile_event_emit_failed",
            event_type=str(event_type),
            error_type=type(exc).__name__,
            error=str(exc),
            hint="Constitution C2 审计事件写入失败：请检查 EventStore schema / API 兼容性",
        )
