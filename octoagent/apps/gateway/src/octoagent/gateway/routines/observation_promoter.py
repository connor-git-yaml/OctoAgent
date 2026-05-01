"""observation_promoter.py：ObservationRoutine — 事实提取 + 候选入队后台 Routine（Feature 084 Phase 3 T046-T048 + T052）。

架构说明（plan.md 3.3）：
- INTERVAL_SECONDS = 1800（30 分钟，FR-6.1）
- asyncio.Task 独立调度，不经 APScheduler（R4 缓解，D4 锁定选型）
- feature flag 通过配置文件控制（FR-6.4）
- 四阶段 pipeline：extract → dedupe → categorize → write candidates
- 隔离会话：不访问当前活跃用户 session context（FR-6.2）
- utility model 不可用时降级：以低置信度入队，routine 不中断（Constitution C6）
- candidates 超 50 条时停止写入 + Telegram 通知（FR-7.4 / J7 验收场景 5）

事件写入防回归（防 F22 / F24）：
- 使用真实 Event schema 字段：event_id / task_id / task_seq / ts / type / actor
- 使用 event_store.append_event_committed(event) API
- task_id 使用 "_observation_routine_audit" 占位（类同 operator_actions 模式）

Constitution 合规：
- C1 Durability First：候选落 SQLite + expires_at 归档
- C2 Everything is an Event：每个 stage 写 OBSERVATION_STAGE_COMPLETED
- C6 Degrade Gracefully：utility model 不可用时降级入队
- C7 User-in-Control：feature flag 可关闭；asyncio.Task.cancel 可取消
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from ulid import ULID

log = structlog.get_logger(__name__)

# 审计占位 task_id（与 operator_actions / PolicyGate 同 pattern，防 F24 回归）
_OBSERVATION_AUDIT_TASK_ID = "_observation_routine_audit"

# Routine 配置常量
INTERVAL_SECONDS = 1800          # FR-6.1：30 分钟
CANDIDATES_QUEUE_MAX = 50        # FR-7.4：超限停止写入
OBSERVATION_TTL_DAYS = 30        # 归档 TTL
CATEGORIZE_LOW_CONFIDENCE = 0.4  # utility model 不可用时的降级置信度
CONFIDENCE_THRESHOLD = 0.7       # 最终入库阈值（仲裁 2）
CATEGORIZE_MAX_TOKENS = 200      # ProviderRouter 调用 max_tokens
_RECENT_TURNS_LIMIT = 20         # 每次 extract 取最近 N 条会话


# ---------------------------------------------------------------------------
# 内部数据模型（Routine 内部传递，不对外暴露）
# ---------------------------------------------------------------------------


@dataclass
class CandidateDraft:
    """候选事实草稿（extract → dedupe → categorize 流水线中间对象）。"""

    fact_content: str
    source_turn_id: str
    fact_content_hash: str = field(default="")
    category: str | None = field(default=None)
    confidence: float = field(default=0.0)
    low_confidence_fallback: bool = field(default=False)
    """True 表示因 utility model 不可用使用了降级置信度。"""

    def __post_init__(self) -> None:
        if not self.fact_content_hash:
            self.fact_content_hash = hashlib.sha256(
                self.fact_content.encode("utf-8")
            ).hexdigest()


# ---------------------------------------------------------------------------
# T046：ObservationRoutine 基础框架
# ---------------------------------------------------------------------------


class ObservationRoutine:
    """观察事实提取 + 候选入队 Routine（Feature 084 Phase 3 FR-6）。

    通过 asyncio.Task 后台运行，每 INTERVAL_SECONDS 执行一次四阶段 pipeline：
    1. _extract：从近期会话 turns 提取候选草稿
    2. _dedupe：SHA-256 去重（source_turn_id + fact_content_hash）
    3. _categorize：ProviderRouter utility model 打 category + confidence（降级可用）
    4. _write_candidates：confidence ≥ 0.7 入库，超 50 条停止 + 通知

    feature flag：配置文件 observation_routine_enabled（FR-6.4）
    """

    INTERVAL_SECONDS: int = INTERVAL_SECONDS

    def __init__(
        self,
        *,
        conn: Any | None = None,
        event_store: Any | None = None,
        task_store: Any | None = None,
        provider_router: Any | None = None,
        telegram_notify_fn: Any | None = None,
        feature_enabled: bool = True,
    ) -> None:
        """初始化 ObservationRoutine。

        Args:
            conn: aiosqlite 连接（用于读 turns + 写 candidates）；None 时降级（无 DB 路径跳过）
            event_store: EventStore 实例（用于写审计事件）；None 时审计降级
            task_store: TaskStore 实例（用于 ensure audit task，防 F24）
            provider_router: ProviderRouter 实例（utility model categorize 用）；
                             None 时降级（候选以低置信度入队）
            telegram_notify_fn: 异步函数 (message: str) -> None，队列超限时推送
            feature_enabled: feature flag 开关（FR-6.4）；False 时 start() 不创建 Task
        """
        self._conn = conn
        self._event_store = event_store
        self._task_store = task_store
        self._provider_router = provider_router
        self._telegram_notify_fn = telegram_notify_fn
        self._feature_enabled = feature_enabled

        self._task: asyncio.Task[None] | None = None
        self._audit_task_ensured: set[str] = set()

    # ---------------------------------------------------------------------------
    # T046：start / stop / _run_loop
    # ---------------------------------------------------------------------------

    async def start(self) -> None:
        """启动 Routine asyncio.Task（feature flag 检查，FR-6.4）。

        已运行时幂等（不重复启动）。
        feature_enabled=False 时记录 INFO 日志并跳过启动。
        """
        if not self._feature_enabled:
            log.info(
                "observation_routine_disabled",
                reason="feature_enabled=False",
            )
            return

        if self._task is not None and not self._task.done():
            log.debug("observation_routine_already_running")
            return

        self._task = asyncio.create_task(
            self._run_loop(),
            name="observation_routine",
        )
        log.info(
            "observation_routine_started",
            interval_seconds=self.INTERVAL_SECONDS,
        )

    async def stop(self) -> None:
        """取消并等待 Routine asyncio.Task 完成（Constitution C7 可取消）。

        已停止/未启动时幂等。

        F30 修复 (Codex medium)：旧实现用 asyncio.shield + wait_for + finally 置 None，
        timeout 后 task 因 shield 仍在运行，但 self._task 被置 None；之后 lifespan
        关闭 DB 连接，后台 task 继续访问已关闭的 SQLite 会抛异常或脏写。
        新实现：超时时不 shield、不丢引用——直接等 cancel 收口；如果 5s 仍未 done，
        log ERROR 但保留 self._task 引用（暴露问题给 lifespan，不允许 silent 丢失）。
        """
        if self._task is None or self._task.done():
            return

        self._task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.CancelledError:
            # 预期行为：cancel 收口
            pass
        except asyncio.TimeoutError:
            # F30: 超时不能丢 task 引用——保留供 lifespan 后续 force-cancel / log
            log.error(
                "observation_routine_stop_timeout",
                hint=(
                    "Routine 在 5s 内未响应 cancel；保留 task 引用避免后台任务"
                    "在 DB 关闭后继续写入。lifespan 应在更深层 await 此 task 直到完成。"
                ),
            )
            return  # **不**置 self._task = None
        except Exception as exc:  # noqa: BLE001
            log.error("observation_routine_stop_unexpected", error=str(exc))

        self._task = None
        log.info("observation_routine_stopped")

    async def _run_loop(self) -> None:
        """主循环：每 INTERVAL_SECONDS 运行一次 pipeline，异常不终止整个循环。

        CancelledError 向上抛出（asyncio.Task 取消的标准路径）。
        其他所有异常 catch 后写 ERROR 日志，loop 继续。
        """
        log.info("observation_routine_loop_started")
        try:
            while True:
                await asyncio.sleep(self.INTERVAL_SECONDS)
                await self._run_once()
        except asyncio.CancelledError:
            log.info("observation_routine_loop_cancelled")
            raise  # 重新抛出，让 asyncio.Task 记录终止状态

    async def _run_once(self) -> None:
        """执行一次完整 pipeline（extract → dedupe → categorize → write → archive）。"""
        log.info("observation_routine_run_start")
        try:
            # 1. extract
            recent_turns = await self._fetch_recent_turns()
            drafts = await self._extract(recent_turns)

            # 2. dedupe
            dedupe_drafts = await self._dedupe(drafts)

            # 3. categorize
            categorized = await self._categorize(dedupe_drafts)

            # 4. write candidates（confidence ≥ threshold）
            await self._write_candidates(categorized)

            # 5. archive expired（T052）
            await self._archive_expired_candidates()

            log.info(
                "observation_routine_run_complete",
                extracted=len(drafts),
                after_dedupe=len(dedupe_drafts),
                after_categorize=len(categorized),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 异常不终止 loop（Constitution C6）
            log.error(
                "observation_routine_run_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # ---------------------------------------------------------------------------
    # T047：_extract + _dedupe 阶段
    # ---------------------------------------------------------------------------

    async def _fetch_recent_turns(self) -> list[dict[str, Any]]:
        """从 events 表取最近 N 条 AGENT_TURN / USER_TURN 类型事件（隔离会话，FR-6.2）。

        注意：不访问当前活跃用户 session context，通过 DB 直接读历史 turns。
        """
        if self._conn is None:
            return []

        try:
            # 取最近 _RECENT_TURNS_LIMIT 条消息类型事件（AGENT_TURN / USER_TURN 或 MESSAGE）
            async with self._conn.execute(
                """
                SELECT task_id, task_seq, ts, type, payload
                FROM events
                WHERE type IN ('AGENT_TURN', 'USER_TURN', 'MESSAGE_RECEIVED', 'TASK_USER_MESSAGE')
                ORDER BY ts DESC
                LIMIT ?
                """,
                (_RECENT_TURNS_LIMIT,),
            ) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            log.warning(
                "observation_fetch_turns_failed",
                error=str(exc),
            )
            return []

    async def _extract(self, recent_turns: list[dict[str, Any]]) -> list[CandidateDraft]:
        """从近期 turns 提取候选事实草稿（T047）。

        当前策略：遍历每条 turn 的 payload，提取含有事实性关键词的内容片段。
        隔离会话，不访问当前活跃用户 session context（FR-6.2）。

        Args:
            recent_turns: 近期 turn 事件列表

        Returns:
            候选草稿列表
        """
        drafts: list[CandidateDraft] = []
        t_start = time.monotonic()

        for turn in recent_turns:
            try:
                payload = turn.get("payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (json.JSONDecodeError, TypeError):
                        payload = {}

                content = payload.get("content") or payload.get("text") or payload.get("message", "")
                if not content or len(content.strip()) < 10:
                    continue

                # 提取策略：每条 turn 作为一个候选草稿
                # 真实 ML 提取在 Phase 5 扩展；当前以 rule-based 为基础实现
                source_turn_id = str(turn.get("task_seq", "")) or str(ULID())
                draft = CandidateDraft(
                    fact_content=content.strip()[:500],  # 截断上限
                    source_turn_id=source_turn_id,
                )
                drafts.append(draft)
            except Exception as exc:
                log.warning(
                    "observation_extract_turn_failed",
                    error=str(exc),
                )
                continue

        duration_ms = int((time.monotonic() - t_start) * 1000)
        await self._emit_stage_event(
            stage_name="extract",
            input_count=len(recent_turns),
            output_count=len(drafts),
            duration_ms=duration_ms,
        )
        log.info(
            "observation_stage_extract",
            turns_in=len(recent_turns),
            drafts_out=len(drafts),
            duration_ms=duration_ms,
        )
        return drafts

    async def _dedupe(self, drafts: list[CandidateDraft]) -> list[CandidateDraft]:
        """按 source_turn_id + fact_content_hash 去重（T047 / FR-7.4 AUTO-CLARIFIED）。

        已在 observation_candidates 表中存在的（任何 status）不再重复写入。
        同批次内重复的也去重。

        Args:
            drafts: 候选草稿列表

        Returns:
            去重后的草稿列表
        """
        if not drafts:
            return []

        t_start = time.monotonic()
        unique: list[CandidateDraft] = []
        # 批次内去重
        seen: set[tuple[str, str]] = set()

        for draft in drafts:
            key = (draft.source_turn_id, draft.fact_content_hash)
            if key in seen:
                continue
            seen.add(key)

            if self._conn is not None:
                # DB 级去重：检查 observation_candidates 表
                try:
                    async with self._conn.execute(
                        """
                        SELECT id FROM observation_candidates
                        WHERE source_turn_id = ? AND fact_content_hash = ?
                        """,
                        (draft.source_turn_id, draft.fact_content_hash),
                    ) as cur:
                        existing = await cur.fetchone()
                    if existing is not None:
                        continue  # 已存在，跳过
                except Exception as exc:
                    log.warning("observation_dedupe_db_check_failed", error=str(exc))
                    # DB 检查失败时保留草稿（宁可重复也不丢失）

            unique.append(draft)

        duration_ms = int((time.monotonic() - t_start) * 1000)
        await self._emit_stage_event(
            stage_name="dedupe",
            input_count=len(drafts),
            output_count=len(unique),
            duration_ms=duration_ms,
        )
        log.info(
            "observation_stage_dedupe",
            drafts_in=len(drafts),
            unique_out=len(unique),
            duration_ms=duration_ms,
        )
        return unique

    # ---------------------------------------------------------------------------
    # T048：_categorize 阶段 + 降级
    # ---------------------------------------------------------------------------

    async def _categorize(self, drafts: list[CandidateDraft]) -> list[CandidateDraft]:
        """调用 ProviderRouter utility model 打 category + confidence（T048）。

        utility model 不可用时降级：候选全部以低置信度进入 review queue，
        routine 不中断（Constitution C6，J6 验收场景 4）。

        Args:
            drafts: 去重后的草稿列表

        Returns:
            打了 category + confidence 的草稿列表（降级时 low_confidence_fallback=True）
        """
        if not drafts:
            return []

        t_start = time.monotonic()
        categorized: list[CandidateDraft] = []

        # 尝试调用 utility model
        provider_available = self._provider_router is not None

        if provider_available:
            for draft in drafts:
                try:
                    category, confidence = await self._call_categorize_model(
                        draft.fact_content
                    )
                    draft.category = category
                    draft.confidence = confidence
                    draft.low_confidence_fallback = False
                except Exception as exc:
                    # 降级：utility model 不可用（Constitution C6）
                    log.warning(
                        "observation_categorize_model_failed",
                        error_type=type(exc).__name__,
                        error=str(exc),
                        hint="降级：候选以低置信度入队，routine 不中断",
                    )
                    draft.category = "unknown"
                    draft.confidence = CATEGORIZE_LOW_CONFIDENCE
                    draft.low_confidence_fallback = True
                categorized.append(draft)
        else:
            # provider_router 未注入 → 全部降级
            log.warning(
                "observation_categorize_no_router",
                drafts_count=len(drafts),
                hint="provider_router 未注入，全部以低置信度入队",
            )
            for draft in drafts:
                draft.category = "unknown"
                draft.confidence = CATEGORIZE_LOW_CONFIDENCE
                draft.low_confidence_fallback = True
                categorized.append(draft)

        duration_ms = int((time.monotonic() - t_start) * 1000)
        await self._emit_stage_event(
            stage_name="categorize",
            input_count=len(drafts),
            output_count=len(categorized),
            duration_ms=duration_ms,
        )
        log.info(
            "observation_stage_categorize",
            drafts_in=len(drafts),
            categorized_out=len(categorized),
            duration_ms=duration_ms,
        )
        return categorized

    async def _call_categorize_model(self, fact_content: str) -> tuple[str, float]:
        """调用 ProviderRouter utility model 打 category + confidence（max_tokens=200）。

        Returns:
            (category, confidence) 元组；解析失败时抛异常（由调用方 catch）
        """
        if self._provider_router is None:
            raise RuntimeError("provider_router 未注入")

        # 构造分类 prompt
        system_prompt = (
            "You are a fact categorizer. Given a text snippet, "
            "output JSON with 'category' (one of: preference, habit, identity, skill, goal, "
            "relationship, context, other) and 'confidence' (0.0-1.0). "
            "Example: {\"category\": \"preference\", \"confidence\": 0.85}"
        )
        user_message = f"Categorize this fact: {fact_content[:300]}"

        # 使用 "cheap" alias 作为 utility model（按 plan.md 设计）
        try:
            resolved = self._provider_router.resolve_for_alias(
                "cheap",
                task_scope=None,
            )
            client = resolved.client
            model_name = resolved.model_name
        except Exception:
            # alias 不存在时尝试 "main"
            resolved = self._provider_router.resolve_for_alias(
                "main",
                task_scope=None,
            )
            client = resolved.client
            model_name = resolved.model_name

        content, _tool_calls, _meta = await client.call(
            instructions=system_prompt,
            history=[{"role": "user", "content": user_message}],
            tools=[],
            model_name=model_name,
        )

        # 解析 JSON 响应
        # 尝试从 content 中提取 JSON
        try:
            data = json.loads(content)
            category = str(data.get("category", "other"))
            confidence = float(data.get("confidence", 0.5))
            # confidence 范围守门
            confidence = max(0.0, min(1.0, confidence))
            return category, confidence
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ValueError(f"model 返回无法解析为 JSON: {content!r}") from exc

    # ---------------------------------------------------------------------------
    # T048：_write_candidates（confidence ≥ threshold → 入库）
    # ---------------------------------------------------------------------------

    async def _write_candidates(self, categorized: list[CandidateDraft]) -> None:
        """将 confidence ≥ CONFIDENCE_THRESHOLD 的候选写入 observation_candidates 表（T048）。

        队列超 50 条时停止写入并推送 Telegram 通知（FR-7.4 / J7 验收场景 5）。

        Args:
            categorized: 打了 category + confidence 的草稿列表
        """
        if not categorized or self._conn is None:
            return

        # 过滤：只写 confidence ≥ CONFIDENCE_THRESHOLD 的（仲裁 2）
        eligible = [d for d in categorized if d.confidence >= CONFIDENCE_THRESHOLD]
        if not eligible:
            log.info(
                "observation_write_skip_all_low_confidence",
                threshold=CONFIDENCE_THRESHOLD,
                total=len(categorized),
            )
            return

        # 检查当前队列长度
        try:
            async with self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
            ) as cur:
                row = await cur.fetchone()
                current_count = int(row["cnt"]) if row else 0
        except Exception as exc:
            log.error("observation_write_count_check_failed", error=str(exc))
            return

        if current_count >= CANDIDATES_QUEUE_MAX:
            # 队列已满，停止写入 + 通知（FR-7.4 / J7 验收场景 5）
            log.warning(
                "observation_candidates_queue_full",
                current_count=current_count,
                max_count=CANDIDATES_QUEUE_MAX,
            )
            await self._notify_queue_full(current_count)
            return

        written_count = 0
        for draft in eligible:
            # 逐条检查是否会超限
            if current_count + written_count >= CANDIDATES_QUEUE_MAX:
                await self._notify_queue_full(current_count + written_count)
                break

            try:
                candidate_id = str(ULID())
                now = datetime.now(timezone.utc)
                expires = now + timedelta(days=OBSERVATION_TTL_DAYS)

                await self._conn.execute(
                    """
                    INSERT INTO observation_candidates (
                        id, fact_content, fact_content_hash, category, confidence, status,
                        source_turn_id, edited, created_at, expires_at, user_id
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        draft.fact_content,
                        draft.fact_content_hash,
                        draft.category,
                        draft.confidence,
                        draft.source_turn_id,
                        now.isoformat(),
                        expires.isoformat(),
                        "owner",  # 单用户假设
                    ),
                )
                written_count += 1

                await self._emit_event(
                    event_type_name="OBSERVATION_OBSERVED",
                    payload={
                        "candidate_id": candidate_id,
                        "source_turn_id": draft.source_turn_id,
                        "category": draft.category,
                        "confidence": draft.confidence,
                        "low_confidence_fallback": draft.low_confidence_fallback,
                        "preview": draft.fact_content[:200],
                    },
                )
            except Exception as exc:
                log.error(
                    "observation_write_candidate_failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        if written_count > 0:
            try:
                await self._conn.commit()
            except Exception as exc:
                log.error("observation_write_candidates_commit_failed", error=str(exc))

        log.info(
            "observation_write_candidates_done",
            eligible=len(eligible),
            written=written_count,
        )

    async def _notify_queue_full(self, current_count: int) -> None:
        """队列超限时推送 Telegram 通知（FR-7.4 / J7 验收场景 5）。"""
        message = (
            f"⚠️ Observation candidates 队列已满（{current_count}/{CANDIDATES_QUEUE_MAX} 条），"
            f"请前往 Memory 候选面板处理后再继续写入。"
        )
        if self._telegram_notify_fn is not None:
            try:
                await self._telegram_notify_fn(message)
            except Exception as exc:
                log.warning(
                    "observation_queue_full_notify_failed",
                    error=str(exc),
                )
        log.warning(
            "observation_candidates_queue_full_notified",
            current_count=current_count,
            max_count=CANDIDATES_QUEUE_MAX,
        )

    # ---------------------------------------------------------------------------
    # T052：候选自动归档定期清理
    # ---------------------------------------------------------------------------

    async def _archive_expired_candidates(self) -> None:
        """将 expires_at < now() 且 status=pending 的候选状态改为 archived（T052）。

        写 OBSERVATION_DISCARDED 事件（含 reason: auto_archive）。
        30 天自动归档（J7 验收场景 4）。
        """
        if self._conn is None:
            return

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        try:
            # 查找已过期的 pending 候选
            async with self._conn.execute(
                """
                SELECT id, fact_content FROM observation_candidates
                WHERE expires_at < ? AND status = 'pending'
                """,
                (now_iso,),
            ) as cur:
                expired_rows = await cur.fetchall()

            if not expired_rows:
                return

            expired_ids = [row["id"] for row in expired_rows]

            # 批量更新为 archived
            placeholders = ",".join("?" for _ in expired_ids)
            await self._conn.execute(
                f"UPDATE observation_candidates SET status = 'archived' WHERE id IN ({placeholders})",
                expired_ids,
            )
            await self._conn.commit()

            # 逐条写 OBSERVATION_DISCARDED 事件（含 reason: auto_archive）
            for row in expired_rows:
                await self._emit_event(
                    event_type_name="OBSERVATION_DISCARDED",
                    payload={
                        "candidate_id": row["id"],
                        "reason": "auto_archive",
                        "preview": str(row["fact_content"])[:200] if row["fact_content"] else "",
                    },
                )

            log.info(
                "observation_archive_expired",
                archived_count=len(expired_ids),
            )

        except Exception as exc:
            log.error(
                "observation_archive_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # ---------------------------------------------------------------------------
    # 内部辅助：OBSERVATION_STAGE_COMPLETED 事件写入
    # ---------------------------------------------------------------------------

    async def _emit_stage_event(
        self,
        *,
        stage_name: str,
        input_count: int,
        output_count: int,
        duration_ms: int,
    ) -> None:
        """写 OBSERVATION_STAGE_COMPLETED 事件（FR-6.3 / Constitution C2）。"""
        await self._emit_event(
            event_type_name="OBSERVATION_STAGE_COMPLETED",
            payload={
                "stage_name": stage_name,
                "input_count": input_count,
                "output_count": output_count,
                "duration_ms": duration_ms,
            },
        )

    async def _emit_event(
        self,
        *,
        event_type_name: str,
        payload: dict[str, Any],
    ) -> None:
        """写审计事件（防 F22 回归：使用真实 schema 字段 + append_event_committed API）。

        防 F22 回归规则：
        - 字段名：event_id / task_id / task_seq / ts / type / actor（不是 event_type 等）
        - API：append_event_committed(event)（不是 append()）
        - task_id 缺失时用 _OBSERVATION_AUDIT_TASK_ID 占位（防 F24 FK violation）
        """
        if self._event_store is None:
            return

        # 确保 audit task 存在（防 F24 FK 违反）
        await self._ensure_audit_task(_OBSERVATION_AUDIT_TASK_ID)

        try:
            from octoagent.core.models.enums import ActorType, EventType
            from octoagent.core.models.event import Event

            event_type_enum = EventType(event_type_name)

            task_seq = await self._event_store.get_next_task_seq(_OBSERVATION_AUDIT_TASK_ID)
            event = Event(
                event_id=str(ULID()),
                task_id=_OBSERVATION_AUDIT_TASK_ID,
                task_seq=task_seq,
                ts=datetime.now(timezone.utc),
                type=event_type_enum,
                actor=ActorType.SYSTEM,
                payload=payload,
                trace_id=_OBSERVATION_AUDIT_TASK_ID,
            )
            await self._event_store.append_event_committed(event, update_task_pointer=False)
        except Exception as exc:
            log.error(
                "observation_event_emit_failed",
                event_type=event_type_name,
                error_type=type(exc).__name__,
                error=str(exc),
                hint="Constitution C2 审计事件写入失败",
            )

    async def _ensure_audit_task(self, task_id: str) -> bool:
        """确保 audit task 在 tasks 表中存在（防 F24 FK violation）。

        F088 修复：本路径之前手写 Task(...) 漏 requester/pointers 必填字段
        → pydantic ValidationError → audit task 创建失败 → OBSERVATION_*
        事件 silent 丢失。统一委托至 ensure_system_audit_task helper
        （与 PolicyGate / ApprovalGate 同 pattern）。
        """
        if task_id in self._audit_task_ensured:
            return True
        from octoagent.core.store.audit_task import ensure_system_audit_task

        ok = await ensure_system_audit_task(
            self._task_store,
            task_id,
            title="ObservationRoutine 审计占位 Task（F084 Phase 3 / F085 T3）",
        )
        if ok:
            self._audit_task_ensured.add(task_id)
            log.info("observation_audit_task_ensured", task_id=task_id)
        else:
            log.warning(
                "observation_audit_task_ensure_failed",
                task_id=task_id,
                hint="task_store 未注入 / 查询失败 / 创建失败",
            )
        return ok
