"""主 Agent / Worker 上下文组装与压缩。"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

import structlog
from octoagent.core.models import EventType

log = structlog.get_logger()

_FALSE_VALUES = {"0", "false", "no", "off"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ContextCompactionConfig:
    """上下文压缩配置。"""

    enabled: bool = True
    max_input_tokens: int = 6000
    soft_limit_ratio: float = 0.75
    target_ratio: float = 0.55
    recent_turns: int = 2
    min_turns_to_compact: int = 4
    summary_max_chars: int = 4000
    summarizer_alias: str = "summarizer"

    @classmethod
    def from_env(cls) -> ContextCompactionConfig:
        return cls(
            enabled=_env_bool("OCTOAGENT_CONTEXT_COMPACTION_ENABLED", True),
            max_input_tokens=_env_int("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", 6000, minimum=64),
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
class CompiledTaskContext:
    """最终喂给主模型的上下文。"""

    messages: list[dict[str, str]]
    request_summary: str
    snapshot_text: str
    raw_tokens: int
    final_tokens: int
    latest_user_text: str
    compacted: bool = False
    compaction_reason: str = ""
    summary_text: str = ""
    summary_model_alias: str = ""
    compressed_turn_count: int = 0
    kept_turn_count: int = 0


class ContextCompactionService:
    """从任务事件重建上下文，并在超预算时调用小模型压缩。"""

    def __init__(self, store_group, *, config: ContextCompactionConfig | None = None) -> None:
        self._stores = store_group
        self._config = config or ContextCompactionConfig.from_env()

    async def build_context(
        self,
        *,
        task_id: str,
        fallback_user_text: str,
        llm_service,
        dispatch_metadata: dict[str, str] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
    ) -> CompiledTaskContext:
        """构建最终请求上下文。"""

        dispatch_metadata = dispatch_metadata or {}
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

        if not self._should_compact(
            raw_tokens=raw_tokens,
            turns=turns,
            dispatch_metadata=dispatch_metadata,
            worker_capability=worker_capability,
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
                latest_user_text=latest_user_text,
            )

        recent_keep = min(len(turns), max(1, self._config.recent_turns * 2))
        while recent_keep > 1:
            kept_turns = turns[-recent_keep:]
            kept_tokens = estimate_messages_tokens(
                [{"role": turn.role, "content": turn.content} for turn in kept_turns]
            )
            if kept_tokens <= self._config.target_tokens:
                break
            recent_keep -= 1

        summary_text = ""
        kept_turns = turns[-recent_keep:]
        compacted_turn_count = max(0, len(turns) - len(kept_turns))
        if compacted_turn_count > 0:
            try:
                summary_text = await self._summarize_turns(
                    older_turns=turns[:-recent_keep],
                    latest_user_text=latest_user_text,
                    llm_service=llm_service,
                    worker_capability=worker_capability,
                    tool_profile=tool_profile,
                )
            except Exception as exc:
                log.warning(
                    "context_compaction_degraded",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                summary_text = ""

        if compacted_turn_count <= 0 or not summary_text:
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
                latest_user_text=latest_user_text,
            )

        compiled_messages = self._build_compacted_messages(
            summary_text=summary_text,
            kept_turns=kept_turns,
        )
        final_tokens = estimate_messages_tokens(compiled_messages)
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
                f"上下文已压缩；最新用户消息：{_summarize_request(latest_user_text)}"
            ),
            snapshot_text=snapshot,
            raw_tokens=raw_tokens,
            final_tokens=final_tokens,
            latest_user_text=latest_user_text,
            compacted=bool(summary_text),
            compaction_reason="history_over_budget",
            summary_text=summary_text,
            summary_model_alias=self._config.summarizer_alias,
            compressed_turn_count=compacted_turn_count,
            kept_turn_count=len(kept_turns),
        )

    def _should_compact(
        self,
        *,
        raw_tokens: int,
        turns: list[ConversationTurn],
        dispatch_metadata: dict[str, str],
        worker_capability: str | None,
    ) -> bool:
        if not self._config.enabled:
            return False
        if raw_tokens <= self._config.soft_limit_tokens:
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
    ) -> str:
        if not older_turns:
            return ""
        segments = [
            f"{turn.role}: {turn.content}"
            for turn in older_turns
            if turn.content
        ]
        if not segments:
            return ""
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
    ) -> str:
        if not segments:
            return ""
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
        total_batches = len(batches)
        for index, batch in enumerate(batches, start=1):
            transcript = "\n\n".join(batch).strip()
            if not transcript:
                continue
            partial = await self._call_summarizer(
                transcript=transcript,
                latest_user_text=latest_user_text,
                stage_label=f"请压缩第 {index}/{total_batches} 批旧对话",
                llm_service=llm_service,
                worker_capability=worker_capability,
                tool_profile=tool_profile,
            )
            if partial:
                partial_summaries.append(f"[第 {index} 批摘要]\n{partial}")

        if not partial_summaries:
            return ""

        if len(partial_summaries) == 1:
            return partial_summaries[0]

        if depth >= 3:
            merged = "\n\n".join(partial_summaries)
            return truncate_chars(merged, self._config.summary_max_chars)

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
    ) -> str:
        if not transcript:
            return ""
        result = await llm_service.call(
            [
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
            ],
            model_alias=self._config.summarizer_alias,
            task_id=None,
            trace_id=None,
            metadata={"selected_tools_json": "[]", "context_compaction": "true"},
            worker_capability=worker_capability,
            tool_profile=tool_profile,
        )
        return truncate_chars(result.content.strip(), self._config.summary_max_chars)

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
        char_budget = max(256, transcript_budget * 4)

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


def estimate_text_tokens(text: str) -> int:
    """轻量 token 估算。"""
    cleaned = text.strip()
    if not cleaned:
        return 0
    return max(1, math.ceil(len(cleaned) / 4))


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
