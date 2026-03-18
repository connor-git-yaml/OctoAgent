"""主 Agent / Worker 上下文组装与压缩。"""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

import structlog
from octoagent.core.models import EventType

log = structlog.get_logger()

# ---------- tiktoken 模块级初始化（一次性） ----------
_tiktoken_encoder = None
try:
    import tiktoken as _tiktoken_mod

    _tiktoken_encoder = _tiktoken_mod.get_encoding("cl100k_base")
except (ImportError, Exception):
    pass

_FALSE_VALUES = {"0", "false", "no", "off"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ContextCompactionConfig:
    """上下文压缩配置。"""

    enabled: bool = True
    max_input_tokens: int = 75000
    soft_limit_ratio: float = 0.75
    target_ratio: float = 0.55
    recent_turns: int = 2
    min_turns_to_compact: int = 4
    summary_max_chars: int = 4000
    summarizer_alias: str = "summarizer"
    compaction_alias: str = "compaction"
    # Feature 060 Phase 2: 两阶段压缩配置
    large_message_ratio: float = 0.3
    json_smart_truncate: bool = True
    # Feature 060 Phase 3: 三层压缩配置
    recent_ratio: float = 0.50
    compressed_ratio: float = 0.30
    archive_ratio: float = 0.20
    compressed_window_size: int = 4  # 每 4 个 turn（2 轮 user+assistant 对）为一组
    # Feature 060 Phase 4: 异步后台压缩配置
    async_compaction_timeout: float = 10.0  # 后台压缩超时秒数

    @classmethod
    def from_env(cls) -> ContextCompactionConfig:
        return cls(
            enabled=_env_bool("OCTOAGENT_CONTEXT_COMPACTION_ENABLED", True),
            max_input_tokens=_env_int("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", 75000, minimum=64),
            soft_limit_ratio=_env_float(
                "OCTOAGENT_CONTEXT_COMPACTION_SOFT_RATIO",
                0.75,
                minimum=0.5,
                maximum=0.95,
            ),
            target_ratio=_env_float(
                "OCTOAGENT_CONTEXT_COMPACTION_TARGET_RATIO",
                0.55,
                minimum=0.25,
                maximum=0.9,
            ),
            recent_turns=_env_int("OCTOAGENT_CONTEXT_RECENT_TURNS", 2, minimum=1),
            min_turns_to_compact=_env_int(
                "OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT",
                4,
                minimum=2,
            ),
            summary_max_chars=_env_int(
                "OCTOAGENT_CONTEXT_SUMMARY_MAX_CHARS",
                4000,
                minimum=256,
            ),
            summarizer_alias=os.environ.get(
                "OCTOAGENT_CONTEXT_SUMMARIZER_ALIAS",
                "summarizer",
            ).strip()
            or "summarizer",
            compaction_alias=os.environ.get(
                "OCTOAGENT_CONTEXT_COMPACTION_ALIAS",
                "compaction",
            ).strip()
            or "compaction",
            recent_ratio=_env_float(
                "OCTOAGENT_CONTEXT_RECENT_RATIO", 0.50, minimum=0.1, maximum=0.9,
            ),
            compressed_ratio=_env_float(
                "OCTOAGENT_CONTEXT_COMPRESSED_RATIO", 0.30, minimum=0.05, maximum=0.8,
            ),
            archive_ratio=_env_float(
                "OCTOAGENT_CONTEXT_ARCHIVE_RATIO", 0.20, minimum=0.05, maximum=0.8,
            ),
            compressed_window_size=_env_int(
                "OCTOAGENT_CONTEXT_COMPRESSED_WINDOW", 4, minimum=2,
            ),
            async_compaction_timeout=_env_float(
                "OCTOAGENT_CONTEXT_ASYNC_COMPACTION_TIMEOUT", 10.0, minimum=1.0, maximum=60.0,
            ),
        )

    @property
    def soft_limit_tokens(self) -> int:
        return max(1, math.floor(self.max_input_tokens * self.soft_limit_ratio))

    @property
    def target_tokens(self) -> int:
        return max(1, math.floor(self.max_input_tokens * self.target_ratio))


@dataclass(frozen=True)
class ConversationTurn:
    """从任务事件重建出的对话轮次。"""

    role: str
    content: str
    source_event_id: str
    artifact_ref: str = ""


@dataclass(frozen=True)
class CompactionPhaseResult:
    """两阶段压缩的单阶段结果。"""

    phase: str
    messages_affected: int = 0
    tokens_saved: int = 0
    model_used: str = ""


@dataclass(frozen=True)
class ContextLayer:
    """描述一个压缩层级（Recent / Compressed / Archive）。"""

    layer_id: str  # "recent" | "compressed" | "archive"
    turns: int  # 该层覆盖的原始轮次数
    token_count: int  # 该层实际占用 token 数
    max_tokens: int  # 该层 token 预算上限
    entry_count: int  # 该层的消息条目数


@dataclass(frozen=True)
class SummarizerCallResult:
    """_call_summarizer 的 fallback 链追踪结果（线程安全，不依赖实例可变状态）。"""

    alias_used: str = ""
    fallback_used: bool = False
    fallback_chain: list[str] = field(default_factory=list)


# 空 SummarizerCallResult 单例，避免热路径中重复创建
_EMPTY_SUMMARIZER_RESULT = SummarizerCallResult()


@dataclass(frozen=True)
class CompiledTaskContext:
    """最终喂给主模型的上下文。"""

    messages: list[dict[str, str]]
    request_summary: str
    snapshot_text: str
    raw_tokens: int
    final_tokens: int
    delivery_tokens: int
    latest_user_text: str
    compacted: bool = False
    compaction_reason: str = ""
    summary_text: str = ""
    summary_model_alias: str = ""
    fallback_used: bool = False
    fallback_chain: list[str] = field(default_factory=list)
    compressed_turn_count: int = 0
    kept_turn_count: int = 0
    context_frame_id: str = ""
    effective_agent_profile_id: str = ""
    effective_agent_runtime_id: str = ""
    effective_agent_session_id: str = ""
    # Feature 061: Agent 权限 Preset（从 AgentRuntime 继承）
    permission_preset: str = "normal"
    system_blocks: list[dict[str, str]] = field(default_factory=list)
    recent_summary: str = ""
    recall_frame_id: str = ""
    memory_namespace_ids: list[str] = field(default_factory=list)
    memory_hits: list[dict[str, Any]] = field(default_factory=list)
    degraded_reason: str = ""
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    compaction_phases: list[dict[str, Any]] = field(default_factory=list)
    # Feature 060 Phase 3: 三层压缩审计信息
    layers: list[dict[str, Any]] = field(default_factory=list)
    compaction_version: str = ""  # "v1" | "v2"


class ContextCompactionService:
    """从任务事件重建上下文，并在超预算时调用小模型压缩。"""

    def __init__(self, store_group, *, config: ContextCompactionConfig | None = None) -> None:
        self._stores = store_group
        self._config = config or ContextCompactionConfig.from_env()
        # Feature 060 Phase 4: 异步后台压缩
        self._compaction_locks: dict[str, asyncio.Lock] = {}
        self._pending_compactions: dict[str, asyncio.Task] = {}

    # ---------- Feature 060 Phase 4: 异步后台压缩 ----------

    async def schedule_background_compaction(
        self,
        *,
        agent_session_id: str,
        task_id: str,
        llm_service: Any,
        conversation_budget: int,
        dispatch_metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
        existing_archive_text: str = "",
        existing_compressed_layers: list[dict[str, Any]] | None = None,
        existing_compaction_version: str = "v1",
    ) -> None:
        """在后台启动压缩任务（预判下一轮可能超限时调用）。

        如果该 session 已有后台任务运行中，不重复启动（幂等）。
        后台压缩通过 per-session Lock 保护写入，防止与其他写入操作并发冲突。
        """
        if agent_session_id in self._pending_compactions:
            existing_task = self._pending_compactions[agent_session_id]
            if not existing_task.done():
                return  # 已有后台任务运行中，不重复启动

        lock = self._compaction_locks.setdefault(agent_session_id, asyncio.Lock())
        timeout = self._config.async_compaction_timeout

        async def _bg_compact() -> CompiledTaskContext | None:
            try:
                async with lock:
                    result = await asyncio.wait_for(
                        self.build_context(
                            task_id=task_id,
                            fallback_user_text="",
                            llm_service=llm_service,
                            dispatch_metadata=dispatch_metadata,
                            worker_capability=worker_capability,
                            tool_profile=tool_profile,
                            conversation_budget=conversation_budget,
                            existing_archive_text=existing_archive_text,
                            existing_compressed_layers=existing_compressed_layers,
                            existing_compaction_version=existing_compaction_version,
                        ),
                        timeout=timeout,
                    )
                    return result
            except asyncio.TimeoutError:
                log.warning(
                    "background_compaction_timeout",
                    session=agent_session_id,
                    timeout=timeout,
                )
                return None
            except Exception as exc:
                log.warning(
                    "background_compaction_failed",
                    session=agent_session_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                return None
            finally:
                self._pending_compactions.pop(agent_session_id, None)
                # W6: 清理不再使用的 Lock，防止长期运行中 Lock 对象累积
                if agent_session_id not in self._pending_compactions:
                    self._compaction_locks.pop(agent_session_id, None)

        task = asyncio.create_task(_bg_compact())
        self._pending_compactions[agent_session_id] = task

    async def await_compaction_result(
        self,
        agent_session_id: str,
        timeout: float | None = None,
    ) -> CompiledTaskContext | None:
        """等待后台压缩完成并返回结果。

        Args:
            agent_session_id: 会话 ID
            timeout: 等待超时秒数，默认使用配置值

        Returns:
            CompiledTaskContext 如果后台压缩成功完成，None 如果无待处理任务、超时或失败。
        """
        task = self._pending_compactions.get(agent_session_id)
        if task is None:
            return None  # 无待处理任务
        if task.done():
            self._pending_compactions.pop(agent_session_id, None)
            try:
                return task.result()
            except Exception:
                return None

        effective_timeout = timeout if timeout is not None else self._config.async_compaction_timeout
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=effective_timeout,
            )
            return result
        except asyncio.TimeoutError:
            log.warning(
                "await_compaction_timeout",
                session=agent_session_id,
                timeout=effective_timeout,
            )
            return None
        except Exception:
            return None

    def get_compaction_lock(self, agent_session_id: str) -> asyncio.Lock:
        """获取 session 级别的压缩锁，供外部模块（如 record_response_context）使用。"""
        return self._compaction_locks.setdefault(agent_session_id, asyncio.Lock())

    def has_pending_compaction(self, agent_session_id: str) -> bool:
        """检查是否有未完成的后台压缩任务。"""
        task = self._pending_compactions.get(agent_session_id)
        return task is not None and not task.done()

    async def build_context(
        self,
        *,
        task_id: str,
        fallback_user_text: str,
        llm_service,
        dispatch_metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
        conversation_budget: int | None = None,
        existing_archive_text: str = "",
        existing_compressed_layers: list[dict[str, Any]] | None = None,
        existing_compaction_version: str = "v1",
    ) -> CompiledTaskContext:
        """构建最终请求上下文（Feature 060: 三层压缩版）。

        Args:
            conversation_budget: 由 ContextBudgetPlanner 提供的对话预算。
                传入时压缩目标基于此值而非 max_input_tokens。
                未传入时回退到 max_input_tokens（向后兼容）。
            existing_archive_text: 已有的 Archive 层文本（来自 AgentSession.rolling_summary）。
            existing_compressed_layers: 已有的 Compressed 层条目列表。
            existing_compaction_version: 当前 session 的 compaction 版本（v1/v2）。
        """

        dispatch_metadata = dispatch_metadata or {}
        existing_compressed_layers = existing_compressed_layers or []
        turns = await self._load_conversation_turns(task_id)
        if not turns:
            turns = [ConversationTurn(role="user", content=fallback_user_text, source_event_id="")]
        elif turns[-1].role != "user":
            turns.append(
                ConversationTurn(role="user", content=fallback_user_text, source_event_id="")
            )

        latest_user_text = next(
            (turn.content for turn in reversed(turns) if turn.role == "user"),
            fallback_user_text,
        )
        messages = [{"role": turn.role, "content": turn.content} for turn in turns]
        raw_tokens = estimate_messages_tokens(messages)
        default_summary = _summarize_request(latest_user_text)

        # Feature 060: 使用 conversation_budget 替代 max_input_tokens 计算限制
        effective_budget = conversation_budget if conversation_budget is not None else self._config.max_input_tokens
        effective_soft_limit = (
            max(1, math.floor(effective_budget * self._config.soft_limit_ratio))
        )
        effective_target = (
            max(1, math.floor(effective_budget * self._config.target_ratio))
        )

        if not self._should_compact(
            raw_tokens=raw_tokens,
            turns=turns,
            dispatch_metadata=dispatch_metadata,
            worker_capability=worker_capability,
            soft_limit_override=effective_soft_limit,
        ):
            snapshot = self._render_snapshot(
                compacted=False,
                summary_text="",
                messages=messages,
                raw_tokens=raw_tokens,
                final_tokens=raw_tokens,
            )
            return CompiledTaskContext(
                messages=messages,
                request_summary=default_summary,
                snapshot_text=snapshot,
                raw_tokens=raw_tokens,
                final_tokens=raw_tokens,
                delivery_tokens=raw_tokens,
                latest_user_text=latest_user_text,
            )

        # Feature 060 Phase 2: 两阶段压缩
        compaction_phases: list[dict[str, Any]] = []

        # 阶段 1：廉价截断
        truncated_messages, truncation_affected = self._cheap_truncation_phase(
            messages, effective_soft_limit,
        )
        tokens_after_truncation = estimate_messages_tokens(truncated_messages)
        truncation_saved = raw_tokens - tokens_after_truncation
        if truncation_affected > 0:
            compaction_phases.append({
                "phase": "cheap_truncation",
                "messages_affected": truncation_affected,
                "tokens_saved": truncation_saved,
            })
            # 更新 turns 内容以反映截断后的文本（用于后续 LLM 摘要）
            truncated_turn_map: dict[int, str] = {}
            for i, (orig, trunc) in enumerate(zip(messages, truncated_messages)):
                if orig["content"] != trunc["content"]:
                    truncated_turn_map[i] = trunc["content"]
            if truncated_turn_map:
                new_turns = []
                for i, turn in enumerate(turns):
                    if i in truncated_turn_map:
                        new_turns.append(ConversationTurn(
                            role=turn.role,
                            content=truncated_turn_map[i],
                            source_event_id=turn.source_event_id,
                            artifact_ref=turn.artifact_ref,
                        ))
                    else:
                        new_turns.append(turn)
                turns = new_turns
                messages = truncated_messages

        # 如果截断后已经在预算内，跳过 LLM 摘要
        if tokens_after_truncation <= effective_soft_limit:
            snapshot = self._render_snapshot(
                compacted=truncation_affected > 0,
                summary_text="",
                messages=messages,
                raw_tokens=raw_tokens,
                final_tokens=tokens_after_truncation,
            )
            return CompiledTaskContext(
                messages=messages,
                request_summary=default_summary if truncation_affected == 0 else (
                    f"上下文已截断（无需 LLM 摘要）；最新用户消息：{_summarize_request(latest_user_text)}"
                ),
                snapshot_text=snapshot,
                raw_tokens=raw_tokens,
                final_tokens=tokens_after_truncation,
                delivery_tokens=tokens_after_truncation,
                latest_user_text=latest_user_text,
                compacted=truncation_affected > 0,
                compaction_reason="cheap_truncation_sufficient" if truncation_affected > 0 else "",
                compaction_phases=compaction_phases,
            )

        # 阶段 2：三层压缩（Recent + Compressed + Archive）
        return await self._build_layered_context(
            turns=turns,
            messages=messages,
            raw_tokens=raw_tokens,
            tokens_after_truncation=tokens_after_truncation,
            effective_budget=effective_budget,
            effective_target=effective_target,
            latest_user_text=latest_user_text,
            default_summary=default_summary,
            truncation_affected=truncation_affected,
            compaction_phases=compaction_phases,
            llm_service=llm_service,
            worker_capability=worker_capability,
            tool_profile=tool_profile,
            existing_archive_text=existing_archive_text,
            existing_compressed_layers=existing_compressed_layers,
            existing_compaction_version=existing_compaction_version,
        )

    async def _build_layered_context(
        self,
        *,
        turns: list[ConversationTurn],
        messages: list[dict[str, str]],
        raw_tokens: int,
        tokens_after_truncation: int,
        effective_budget: int,
        effective_target: int,
        latest_user_text: str,
        default_summary: str,
        truncation_affected: int,
        compaction_phases: list[dict[str, Any]],
        llm_service,
        worker_capability: str | None,
        tool_profile: str | None,
        existing_archive_text: str,
        existing_compressed_layers: list[dict[str, Any]],
        existing_compaction_version: str,
    ) -> CompiledTaskContext:
        """三层压缩核心逻辑：Recent + Compressed + Archive。"""

        # 计算各层预算
        recent_budget = max(1, math.floor(effective_budget * self._config.recent_ratio))
        compressed_budget = max(1, math.floor(effective_budget * self._config.compressed_ratio))
        archive_budget = max(1, math.floor(effective_budget * self._config.archive_ratio))

        # --- Recent 层：保留最近 N 轮原文 ---
        recent_keep = min(len(turns), max(1, self._config.recent_turns * 2))
        while recent_keep > 1:
            kept_turns = turns[-recent_keep:]
            kept_tokens = estimate_messages_tokens(
                [{"role": t.role, "content": t.content} for t in kept_turns]
            )
            if kept_tokens <= recent_budget:
                break
            recent_keep -= 1

        recent_turns = turns[-recent_keep:]
        older_turns = turns[:-recent_keep] if recent_keep < len(turns) else []
        recent_messages = [{"role": t.role, "content": t.content} for t in recent_turns]
        recent_tokens = estimate_messages_tokens(recent_messages)

        layers: list[dict[str, Any]] = [
            {
                "layer_id": "recent",
                "turns": len(recent_turns),
                "token_count": recent_tokens,
                "max_tokens": recent_budget,
                "entry_count": len(recent_messages),
            }
        ]

        # --- 如果没有旧 turns 需要压缩，直接返回 ---
        if not older_turns:
            snapshot = self._render_snapshot(
                compacted=truncation_affected > 0,
                summary_text="",
                messages=recent_messages,
                raw_tokens=raw_tokens,
                final_tokens=recent_tokens,
            )
            return CompiledTaskContext(
                messages=recent_messages,
                request_summary=default_summary,
                snapshot_text=snapshot,
                raw_tokens=raw_tokens,
                final_tokens=recent_tokens,
                delivery_tokens=recent_tokens,
                latest_user_text=latest_user_text,
                compacted=truncation_affected > 0,
                compaction_reason="cheap_truncation_only" if truncation_affected > 0 else "",
                compaction_phases=compaction_phases,
                fallback_used=False,
                fallback_chain=[],
                layers=layers,
                compaction_version="v2",
            )

        # --- Compressed 层：按窗口分组 + LLM 摘要 ---
        groups = self._group_turns_to_compressed(older_turns)

        # 区分新增组和归档组：最近若干组做 LLM 摘要（Compressed），更旧的合并到 Archive
        # 策略：用 compressed_budget 估算能容纳多少组摘要（每组约 200 token）
        est_tokens_per_group = 200  # 经验值：每组摘要约 200 token
        max_compressed_groups = max(1, compressed_budget // est_tokens_per_group)

        if len(groups) <= max_compressed_groups:
            compressed_groups = groups
            archive_groups: list[list[ConversationTurn]] = []
        else:
            compressed_groups = groups[-max_compressed_groups:]
            archive_groups = groups[:-max_compressed_groups]

        # 生成 Compressed 层摘要（并行调用 LLM 减少延迟）
        compressed_turn_count = sum(len(g) for g in compressed_groups)
        last_sr = _EMPTY_SUMMARIZER_RESULT

        async def _summarize_one(group: list[ConversationTurn]) -> tuple[str, SummarizerCallResult] | None:
            try:
                return await self._summarize_turns(
                    older_turns=group,
                    latest_user_text=latest_user_text,
                    llm_service=llm_service,
                    worker_capability=worker_capability,
                    tool_profile=tool_profile,
                )
            except Exception as exc:
                log.warning(
                    "compressed_layer_summary_failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    group_turns=len(group),
                )
                return None

        results = await asyncio.gather(*[_summarize_one(g) for g in compressed_groups])
        compressed_summaries: list[str] = []
        for result in results:
            if result is not None:
                group_summary, sr = result
                last_sr = sr
                if group_summary:
                    compressed_summaries.append(group_summary)

        # 如果所有 Compressed 组 LLM 摘要都失败，降级处理
        if not compressed_summaries and not existing_archive_text and not archive_groups:
            snapshot = self._render_snapshot(
                compacted=truncation_affected > 0,
                summary_text="",
                messages=messages,
                raw_tokens=raw_tokens,
                final_tokens=tokens_after_truncation,
            )
            return CompiledTaskContext(
                messages=messages,
                request_summary=default_summary,
                snapshot_text=snapshot,
                raw_tokens=raw_tokens,
                final_tokens=tokens_after_truncation,
                delivery_tokens=tokens_after_truncation,
                latest_user_text=latest_user_text,
                compacted=truncation_affected > 0,
                compaction_reason="cheap_truncation_only" if truncation_affected > 0 else "",
                compaction_phases=compaction_phases,
                fallback_used=last_sr.fallback_used,
                fallback_chain=list(last_sr.fallback_chain),
            )

        compressed_text = "\n\n".join(compressed_summaries) if compressed_summaries else ""
        compressed_tokens = estimate_text_tokens(compressed_text) if compressed_text else 0

        layers.append({
            "layer_id": "compressed",
            "turns": compressed_turn_count,
            "token_count": compressed_tokens,
            "max_tokens": compressed_budget,
            "entry_count": len(compressed_summaries),
        })

        # --- Archive 层：合并旧组 + 已有 Archive ---
        archive_text = existing_archive_text or ""
        archive_turn_count = 0

        if archive_groups:
            # 合并旧组的 turns 文本（简单合并，不再做 LLM 摘要以节省成本）
            archive_new_segments: list[str] = []
            for group in archive_groups:
                archive_turn_count += len(group)
                for turn in group:
                    if turn.content:
                        archive_new_segments.append(f"{turn.role}: {turn.content[:200]}")

            if archive_new_segments:
                new_archive_text = "\n".join(archive_new_segments)
                # 如果旧 archive + 新段超出 archive_budget，做一次 LLM 合并
                combined_archive = f"{archive_text}\n\n{new_archive_text}" if archive_text else new_archive_text
                combined_tokens = estimate_text_tokens(combined_archive)
                if combined_tokens > archive_budget:
                    # 截断到 summarizer transcript budget 防止超大文本
                    transcript_budget = self._summarizer_transcript_budget_tokens()
                    if combined_tokens > transcript_budget:
                        cpt = _chars_per_token_ratio(combined_archive)
                        combined_archive = truncate_chars(
                            combined_archive, int(transcript_budget * cpt),
                        )
                    try:
                        archive_text, sr = await self._call_summarizer(
                            transcript=combined_archive,
                            latest_user_text=latest_user_text,
                            stage_label="请将以下历史骨架合并为精炼摘要，保留关键里程碑和决策",
                            llm_service=llm_service,
                            worker_capability=worker_capability,
                            tool_profile=tool_profile,
                        )
                        last_sr = sr
                    except Exception as exc:
                        log.warning(
                            "archive_merge_failed",
                            error_type=type(exc).__name__,
                            error=str(exc),
                        )
                        # 降级：截断到预算内
                        cpt = _chars_per_token_ratio(combined_archive)
                        archive_text = truncate_chars(combined_archive, int(archive_budget * cpt))
                else:
                    archive_text = combined_archive

        archive_tokens = estimate_text_tokens(archive_text) if archive_text else 0

        layers.append({
            "layer_id": "archive",
            "turns": archive_turn_count,
            "token_count": archive_tokens,
            "max_tokens": archive_budget,
            "entry_count": 1 if archive_text else 0,
        })

        # --- 组装最终消息 ---
        compiled_messages: list[dict[str, str]] = []

        # Archive 层作为系统消息
        if archive_text:
            compiled_messages.append({
                "role": "system",
                "content": (
                    "以下为历史骨架摘要（Archive 层），仅供理解任务全貌：\n"
                    f"{archive_text}"
                ),
            })

        # Compressed 层作为系统消息
        if compressed_text:
            compiled_messages.append({
                "role": "system",
                "content": (
                    "以下为中期对话决策摘要（Compressed 层），保留关键决策和结论：\n"
                    f"{compressed_text}"
                ),
            })

        # Recent 层保留原始消息
        compiled_messages.extend(recent_messages)

        final_tokens = estimate_messages_tokens(compiled_messages)
        summary_text = f"{archive_text}\n---\n{compressed_text}" if archive_text and compressed_text else (archive_text or compressed_text)
        llm_summary_saved = tokens_after_truncation - final_tokens

        compaction_phases.append({
            "phase": "llm_summary",
            "messages_affected": compressed_turn_count + archive_turn_count,
            "tokens_saved": max(0, llm_summary_saved),
            "model_used": last_sr.alias_used or self._config.compaction_alias,
        })

        snapshot = self._render_snapshot(
            compacted=True,
            summary_text=summary_text,
            messages=compiled_messages,
            raw_tokens=raw_tokens,
            final_tokens=final_tokens,
        )
        return CompiledTaskContext(
            messages=compiled_messages,
            request_summary=(
                f"上下文已压缩（三层）；最新用户消息：{_summarize_request(latest_user_text)}"
            ),
            snapshot_text=snapshot,
            raw_tokens=raw_tokens,
            final_tokens=final_tokens,
            delivery_tokens=final_tokens,
            latest_user_text=latest_user_text,
            compacted=True,
            compaction_reason="layered_compression",
            summary_text=summary_text,
            summary_model_alias=last_sr.alias_used or self._config.compaction_alias,
            fallback_used=last_sr.fallback_used,
            fallback_chain=list(last_sr.fallback_chain),
            compressed_turn_count=compressed_turn_count,
            kept_turn_count=len(recent_turns),
            compaction_phases=compaction_phases,
            layers=layers,
            compaction_version="v2",
        )

    def _should_compact(
        self,
        *,
        raw_tokens: int,
        turns: list[ConversationTurn],
        dispatch_metadata: dict[str, Any],
        worker_capability: str | None,
        soft_limit_override: int | None = None,
    ) -> bool:
        if not self._config.enabled:
            return False
        soft_limit = soft_limit_override if soft_limit_override is not None else self._config.soft_limit_tokens
        if raw_tokens <= soft_limit:
            return False
        if len(turns) < self._config.min_turns_to_compact:
            return False
        target_kind = str(dispatch_metadata.get("target_kind", "")).strip().lower()
        if target_kind == "subagent":
            return False
        return str(worker_capability or "").strip().lower() != "subagent"

    async def _load_conversation_turns(self, task_id: str) -> list[ConversationTurn]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        turns: list[ConversationTurn] = []
        for event in events:
            if event.type is EventType.USER_MESSAGE:
                text = str(event.payload.get("text", "")).strip() or str(
                    event.payload.get("text_preview", "")
                ).strip()
                if not text:
                    continue
                turns.append(
                    ConversationTurn(
                        role="user",
                        content=text,
                        source_event_id=event.event_id,
                        artifact_ref=str(event.payload.get("artifact_ref", "") or ""),
                    )
                )
            if event.type is EventType.MODEL_CALL_COMPLETED:
                content = await self._load_assistant_content(event.payload)
                if not content:
                    continue
                turns.append(
                    ConversationTurn(
                        role="assistant",
                        content=content,
                        source_event_id=event.event_id,
                        artifact_ref=str(event.payload.get("artifact_ref", "") or ""),
                    )
                )
        return turns

    async def _load_assistant_content(self, payload: dict[str, Any]) -> str:
        artifact_ref = str(payload.get("artifact_ref", "") or "").strip()
        if artifact_ref:
            content = await self._stores.artifact_store.get_artifact_content(artifact_ref)
            if content is not None:
                return content.decode("utf-8", errors="ignore").strip()
        return str(payload.get("response_summary", "")).strip()

    async def _summarize_turns(
        self,
        *,
        older_turns: list[ConversationTurn],
        latest_user_text: str,
        llm_service,
        worker_capability: str | None,
        tool_profile: str | None,
    ) -> tuple[str, SummarizerCallResult]:
        _empty = _EMPTY_SUMMARIZER_RESULT
        if not older_turns:
            return "", _empty
        segments = [
            f"{turn.role}: {turn.content}"
            for turn in older_turns
            if turn.content
        ]
        if not segments:
            return "", _empty
        return await self._summarize_segments(
            segments=segments,
            latest_user_text=latest_user_text,
            llm_service=llm_service,
            worker_capability=worker_capability,
            tool_profile=tool_profile,
        )

    async def _summarize_segments(
        self,
        *,
        segments: list[str],
        latest_user_text: str,
        llm_service,
        worker_capability: str | None,
        tool_profile: str | None,
        depth: int = 0,
    ) -> tuple[str, SummarizerCallResult]:
        _empty = _EMPTY_SUMMARIZER_RESULT
        if not segments:
            return "", _empty
        transcript_budget = self._summarizer_transcript_budget_tokens()
        batches = self._chunk_segments_by_token_budget(
            segments,
            transcript_budget,
        )
        if len(batches) == 1:
            transcript = "\n\n".join(batches[0]).strip()
            return await self._call_summarizer(
                transcript=transcript,
                latest_user_text=latest_user_text,
                stage_label="请压缩以下旧对话",
                llm_service=llm_service,
                worker_capability=worker_capability,
                tool_profile=tool_profile,
            )

        partial_summaries: list[str] = []
        last_sr = _empty
        total_batches = len(batches)
        for index, batch in enumerate(batches, start=1):
            transcript = "\n\n".join(batch).strip()
            if not transcript:
                continue
            partial, sr = await self._call_summarizer(
                transcript=transcript,
                latest_user_text=latest_user_text,
                stage_label=f"请压缩第 {index}/{total_batches} 批旧对话",
                llm_service=llm_service,
                worker_capability=worker_capability,
                tool_profile=tool_profile,
            )
            last_sr = sr
            if partial:
                partial_summaries.append(f"[第 {index} 批摘要]\n{partial}")

        if not partial_summaries:
            return "", last_sr

        if len(partial_summaries) == 1:
            return partial_summaries[0], last_sr

        if depth >= 3:
            merged = "\n\n".join(partial_summaries)
            return truncate_chars(merged, self._config.summary_max_chars), last_sr

        return await self._summarize_segments(
            segments=partial_summaries,
            latest_user_text=latest_user_text,
            llm_service=llm_service,
            worker_capability=worker_capability,
            tool_profile=tool_profile,
            depth=depth + 1,
        )

    async def _call_summarizer(
        self,
        *,
        transcript: str,
        latest_user_text: str,
        stage_label: str,
        llm_service,
        worker_capability: str | None,
        tool_profile: str | None,
    ) -> tuple[str, SummarizerCallResult]:
        """调用摘要模型，按 compaction -> summarizer -> main 三级 fallback 链。

        返回 (摘要文本, SummarizerCallResult)。全部失败时返回空字符串（降级保障）。
        """
        _empty = _EMPTY_SUMMARIZER_RESULT
        if not transcript:
            return "", _empty

        messages = [
            {
                "role": "system",
                "content": (
                    "你是 OctoAgent 的上下文压缩器。"
                    "请把旧对话压缩成供主模型继续工作的工作摘要。"
                    "保留：用户目标、确认过的事实、关键约束、已完成工作、未完成事项、明确的路径/命令/ID、风险和待澄清点。"
                    "不要编造，不要代替主模型回答用户，不要输出长篇原文摘抄。"
                    "输出中文，扁平列表或短段落即可。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前最新用户消息：{latest_user_text}\n\n"
                    f"{stage_label}。\n\n"
                    "需要压缩的内容：\n"
                    f"{transcript}"
                ),
            },
        ]
        call_kwargs: dict[str, Any] = dict(
            task_id=None,
            trace_id=None,
            metadata={"selected_tools_json": "[]", "context_compaction": "true"},
            worker_capability=worker_capability,
            tool_profile=tool_profile,
        )

        # Feature 060: compaction -> summarizer -> main 三级 fallback
        fallback_chain = [
            self._config.compaction_alias,
            self._config.summarizer_alias,
            "main",
        ]
        # 去重但保持顺序
        seen: set[str] = set()
        unique_chain: list[str] = []
        for alias in fallback_chain:
            if alias not in seen:
                seen.add(alias)
                unique_chain.append(alias)

        attempted_chain: list[str] = []
        for alias in unique_chain:
            attempted_chain.append(alias)
            try:
                result = await llm_service.call(
                    messages,
                    model_alias=alias,
                    **call_kwargs,
                )
                sr = SummarizerCallResult(
                    alias_used=alias,
                    fallback_used=alias != unique_chain[0],
                    fallback_chain=list(attempted_chain),
                )
                return truncate_chars(result.content.strip(), self._config.summary_max_chars), sr
            except Exception as exc:
                log.warning(
                    "compaction_alias_fallback",
                    failed_alias=alias,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    remaining=[a for a in unique_chain if a not in attempted_chain],
                )
                continue

        # 全部失败 -> 返回空摘要（降级保障）
        return "", SummarizerCallResult(
            alias_used="",
            fallback_used=True,
            fallback_chain=list(attempted_chain),
        )

    # ---------- Feature 060 Phase 3: 三层压缩辅助方法 ----------

    def _group_turns_to_compressed(
        self,
        turns: list[ConversationTurn],
    ) -> list[list[ConversationTurn]]:
        """将中间轮次按固定窗口分组。

        每 compressed_window_size 个 turn 为一组（默认 4 = 2 轮 user+assistant 对）。
        不拆分 user+assistant 原子对：如果 window 边界在 user 之后但 assistant 还未计入，
        则将 assistant 也包含在当前组中。
        """
        window = self._config.compressed_window_size
        groups: list[list[ConversationTurn]] = []
        current_group: list[ConversationTurn] = []

        for turn in turns:
            current_group.append(turn)
            # 满窗口且当前 turn 是 assistant（完整对话轮）时结束一组
            if len(current_group) >= window and turn.role == "assistant":
                groups.append(current_group)
                current_group = []

        # 剩余不足一组的 turns 也作为最后一组
        if current_group:
            groups.append(current_group)

        return groups

    @staticmethod
    def _parse_compaction_state(
        agent_session_metadata: dict[str, Any],
        rolling_summary: str,
    ) -> tuple[str, list[dict[str, Any]], str]:
        """解析 session 的压缩状态。

        Args:
            agent_session_metadata: AgentSession.metadata 字典
            rolling_summary: AgentSession.rolling_summary 文本

        Returns:
            (archive_text, compressed_layers, compaction_version)
        """
        version = agent_session_metadata.get("compaction_version", "v1")
        compressed = agent_session_metadata.get("compressed_layers", [])
        if version == "v2" and isinstance(compressed, list):
            return (rolling_summary, compressed, "v2")
        # v1 兼容：rolling_summary 整体视为 archive
        return (rolling_summary, [], "v1")

    # ---------- Feature 060 Phase 2: 两阶段压缩方法 ----------

    @staticmethod
    def _smart_truncate_json(text: str, max_tokens: int) -> str:
        """JSON 智能精简：解析 JSON -> 递归剪枝，保留关键字段。

        优先保留 status/error/result/message/code/id/name/type 等关键字段，
        数组只保留前 2 项 + 总数提示。解析失败时返回原文（交给 head_tail 处理）。
        """
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text  # 非 JSON，交给 head_tail

        pruned = ContextCompactionService._prune_json_value(parsed, depth=0, max_depth=3)
        result = json.dumps(pruned, ensure_ascii=False, separators=(",", ":"))
        # 如果精简后仍超预算，二次截断
        if estimate_text_tokens(result) > max_tokens:
            cpt = _chars_per_token_ratio(result)
            max_chars = max(64, int(max_tokens * cpt))
            result = truncate_chars(result, max_chars)
        return result

    @staticmethod
    def _prune_json_value(value: Any, depth: int, max_depth: int = 3) -> Any:
        """递归精简 JSON 值。"""
        _priority_keys = {
            "status", "error", "result", "message", "code",
            "id", "name", "type", "title", "description",
        }
        if depth > max_depth:
            if isinstance(value, str):
                return value[:100] + "..." if len(value) > 100 else value
            if isinstance(value, (list, dict)):
                return f"[... {type(value).__name__} depth>{max_depth}]"
            return value

        if isinstance(value, dict):
            pruned: dict[str, Any] = {}
            # 优先保留关键字段
            for key in _priority_keys:
                if key in value:
                    pruned[key] = ContextCompactionService._prune_json_value(
                        value[key], depth + 1, max_depth
                    )
            # 保留其他字段（按顺序，但限制数量）
            remaining = {k: v for k, v in value.items() if k not in _priority_keys}
            for key in list(remaining.keys())[:5]:
                pruned[key] = ContextCompactionService._prune_json_value(
                    remaining[key], depth + 1, max_depth
                )
            if len(remaining) > 5:
                pruned["__truncated_keys__"] = len(remaining) - 5
            return pruned

        if isinstance(value, list):
            if len(value) <= 2:
                return [
                    ContextCompactionService._prune_json_value(item, depth + 1, max_depth)
                    for item in value
                ]
            pruned_list = [
                ContextCompactionService._prune_json_value(item, depth + 1, max_depth)
                for item in value[:2]
            ]
            pruned_list.append(f"[... +{len(value) - 2} items, total {len(value)}]")
            return pruned_list

        if isinstance(value, str) and len(value) > 200:
            return value[:200] + "..."

        return value

    @staticmethod
    def _head_tail_truncate(text: str, max_tokens: int) -> str:
        """非 JSON 文本截断：保留头 40% + 尾 10% + 中间截断标记。"""
        current_tokens = estimate_text_tokens(text)
        if current_tokens <= max_tokens:
            return text
        cpt = _chars_per_token_ratio(text)
        max_chars = max(64, int(max_tokens * cpt))
        head_chars = int(max_chars * 0.4)
        tail_chars = int(max_chars * 0.1)
        # 确保 head + tail + marker 不超过 max_chars
        marker = f"\n[... truncated ~{current_tokens - max_tokens} tokens ...]\n"
        if head_chars + tail_chars + len(marker) >= len(text):
            return truncate_chars(text, max_chars)
        head = text[:head_chars].rstrip()
        tail = text[-tail_chars:].lstrip() if tail_chars > 0 else ""
        return f"{head}{marker}{tail}"

    def _cheap_truncation_phase(
        self,
        messages: list[dict[str, str]],
        conversation_budget: int,
    ) -> tuple[list[dict[str, str]], int]:
        """廉价截断阶段：遍历消息，单条超大时截断。

        返回 (截断后的消息列表, 被截断的消息数)。
        """
        large_threshold = max(50, int(conversation_budget * self._config.large_message_ratio))
        truncated_messages: list[dict[str, str]] = []
        messages_affected = 0

        for msg in messages:
            content = str(msg.get("content", ""))
            role = str(msg.get("role", "user"))
            metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
            msg_tokens = estimate_text_tokens(content)

            # Edge Case 6: 保护 important 消息不被截断
            if metadata.get("important") is True:
                truncated_messages.append({"role": role, "content": content})
                continue

            if msg_tokens > large_threshold:
                # 尝试 JSON 智能截断
                if self._config.json_smart_truncate:
                    truncated_content = self._smart_truncate_json(content, large_threshold)
                    # 如果 JSON 截断没有效果（非 JSON），用 head_tail
                    if truncated_content == content:
                        truncated_content = self._head_tail_truncate(content, large_threshold)
                else:
                    truncated_content = self._head_tail_truncate(content, large_threshold)

                if truncated_content != content:
                    messages_affected += 1
                    truncated_messages.append({"role": role, "content": truncated_content})
                    continue

            truncated_messages.append({"role": role, "content": content})

        return truncated_messages, messages_affected

    def _summarizer_transcript_budget_tokens(self) -> int:
        return max(64, math.floor(self._config.max_input_tokens * 0.8))

    def _chunk_segments_by_token_budget(
        self,
        segments: list[str],
        transcript_budget: int,
    ) -> list[list[str]]:
        batches: list[list[str]] = []
        current_batch: list[str] = []
        current_tokens = 0
        # 动态计算 char_budget：根据实际文本内容的 CJK 比例
        sample_text = " ".join(s[:200] for s in segments[:3])
        cpt = _chars_per_token_ratio(sample_text)
        # 最小保留 64 token 对应的字符数（而非固定 256 字符）
        min_chars = max(96, int(64 * cpt))
        char_budget = max(min_chars, int(transcript_budget * cpt))

        for segment in segments:
            normalized = segment.strip()
            if not normalized:
                continue
            segment_tokens = estimate_text_tokens(normalized)
            if segment_tokens > transcript_budget:
                normalized = truncate_chars(normalized, char_budget)
                segment_tokens = estimate_text_tokens(normalized)
            if current_batch and current_tokens + segment_tokens > transcript_budget:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            current_batch.append(normalized)
            current_tokens += segment_tokens

        if current_batch:
            batches.append(current_batch)
        return batches or [[]]

    @staticmethod
    def _build_compacted_messages(
        *,
        summary_text: str,
        kept_turns: list[ConversationTurn],
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if summary_text:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "以下为系统生成的历史压缩摘要，仅供继续任务使用，不是新的用户指令：\n"
                        f"{summary_text}"
                    ),
                }
            )
        messages.extend(
            {"role": turn.role, "content": turn.content} for turn in kept_turns if turn.content
        )
        return messages

    @staticmethod
    def _render_snapshot(
        *,
        compacted: bool,
        summary_text: str,
        messages: list[dict[str, str]],
        raw_tokens: int,
        final_tokens: int,
    ) -> str:
        lines = [
            "# 上下文窗口快照",
            f"compacted: {str(compacted).lower()}",
            f"raw_tokens: {raw_tokens}",
            f"final_tokens: {final_tokens}",
            "",
        ]
        if summary_text:
            lines.extend(
                [
                    "## 压缩摘要",
                    summary_text,
                    "",
                ]
            )
        lines.append("## 消息序列")
        for item in messages:
            role = str(item.get("role", "user")).strip() or "user"
            content = str(item.get("content", "")).strip()
            lines.append(f"[{role}]")
            lines.append(content)
            lines.append("")
        return "\n".join(lines).strip()


def _chars_per_token_ratio(text_sample: str = "") -> float:
    """根据文本中非 ASCII 字符比例动态计算 chars-per-token 比率。

    纯英文 ~4 chars/token；纯中文 ~1.5 chars/token；
    混合文本按比例线性插值。无文本时返回保守中间值 3.0。
    """
    if not text_sample:
        return 3.0
    non_ascii = sum(1 for c in text_sample if ord(c) > 127)
    r = non_ascii / len(text_sample)
    return 4.0 * (1.0 - r) + 1.5 * r


def estimation_method() -> str:
    """返回当前 estimate_text_tokens 使用的算法标识。"""
    if _tiktoken_encoder is not None:
        return "tokenizer"
    return "cjk_aware"


def estimate_text_tokens(text: str) -> int:
    """中文感知的 token 估算。

    检测文本中非 ASCII 字符比例 r，在英文估算（len/4）和中文估算（len/1.5）
    之间加权插值：len(text) / (4*(1-r) + 1.5*r)。

    可选：若 tiktoken 可导入，使用 cl100k_base encoder 精确计算。
    """
    cleaned = text.strip()
    if not cleaned:
        return 0
    # 尝试精确 tokenizer
    if _tiktoken_encoder is not None:
        return max(1, len(_tiktoken_encoder.encode(cleaned)))
    # CJK 感知估算
    non_ascii = sum(1 for c in cleaned if ord(c) > 127)
    r = non_ascii / len(cleaned) if cleaned else 0.0
    chars_per_token = 4.0 * (1.0 - r) + 1.5 * r
    return max(1, math.ceil(len(cleaned) / chars_per_token))


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_text_tokens(str(item.get("content", ""))) for item in messages)


def truncate_chars(text: str, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 16].rstrip() + "... [truncated]"


def _summarize_request(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= 100:
        return cleaned
    return cleaned[:97].rstrip() + "..."


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))
