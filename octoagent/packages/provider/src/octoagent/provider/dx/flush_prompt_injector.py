"""Feature 065 Phase 2: Flush Prompt 静默 agentic turn 注入器 (US-5)。

在 Compaction 触发前注入一次静默 LLM 调用，让模型审视当前对话并决定
哪些信息值得持久化。LLM 输出结构化 JSON，逐条调用 memory.write 治理流程写入 SoR。
LLM 不可用或失败时降级到原有 Compaction 摘要 Flush。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from .llm_common import LlmServiceProtocol, parse_llm_json_array, resolve_default_model_alias

_log = structlog.get_logger()


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FlushPromptResult:
    """Flush Prompt 注入结果。"""

    writes_attempted: int = 0
    writes_committed: int = 0
    skipped: bool = False
    errors: list[str] = field(default_factory=list)
    fallback_to_summary: bool = False


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------


_FLUSH_SYSTEM_PROMPT = """\
你是一个记忆管理助手。在对话即将压缩之前，请审视当前对话内容，
判断是否有值得长期记住的信息。

## 判断标准

值得保存的信息：
- 用户明确表达的偏好、习惯、喜好
- 重要的个人事实（生日、住址、工作信息等）
- 关键的项目决策和结论
- 用户未来的计划或承诺
- 纠正之前错误认知的新信息

不需要保存的信息：
- 纯粹的问答过程（"怎么做 X？" -> 答案）
- 临时状态（"我正在等回复"）
- 已经被保存过的重复信息
- 闲聊寒暄

## 输出格式

输出一个 JSON 数组，每个元素是一个需要保存的记忆：
```json
[
  {
    "subject_key": "主题/子主题",
    "content": "完整的陈述句",
    "partition": "work"
  }
]
```

partition 可选值：work, personal, health, finance, profile

如果当前对话中没有值得长期保存的新信息，输出空数组 `[]`。
"""


_FLUSH_USER_PROMPT_TEMPLATE = """\
以下是即将被压缩的对话内容，请审视并决定哪些信息值得作为长期记忆保存：

{conversation_summary}
"""


# ---------------------------------------------------------------------------
# FlushPromptInjector
# ---------------------------------------------------------------------------


class FlushPromptInjector:
    """Compaction 前的静默 agentic turn 注入器。

    注入 system + user 消息让 LLM 审视当前对话，
    通过 memory_write_fn 回调保存重要信息。
    """

    def __init__(
        self,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None:
        self._llm_service = llm_service
        self._project_root = project_root

    async def run_flush_turn(
        self,
        *,
        conversation_messages: list[dict[str, str]],
        scope_id: str,
        memory_write_fn: Callable[..., Awaitable[str]],
        model_alias: str = "",
    ) -> FlushPromptResult:
        """注入静默 turn 并执行 LLM 返回的 memory.write 调用。

        Args:
            conversation_messages: 当前对话消息列表（用于 LLM 审视）
            scope_id: 当前 scope
            memory_write_fn: memory.write 工具的调用函数，
                签名: (subject_key, content, partition, evidence_refs=None) -> str
            model_alias: LLM 模型别名

        Returns:
            FlushPromptResult -- 所有异常内部捕获，不抛出。
        """
        # 1. 对话为空 -> 跳过
        if not conversation_messages:
            return FlushPromptResult(skipped=True)

        # 2. LLM 不可用 -> 降级
        if self._llm_service is None:
            return FlushPromptResult(
                fallback_to_summary=True,
                errors=["LLM 服务未配置"],
            )

        # 3. 构建 prompt
        conversation_summary = self._format_conversation(conversation_messages)
        user_content = _FLUSH_USER_PROMPT_TEMPLATE.format(
            conversation_summary=conversation_summary,
        )
        messages = [
            {"role": "system", "content": _FLUSH_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # 4. 调用 LLM
        resolved_alias = model_alias or self._resolve_default_model_alias()
        try:
            result = await self._llm_service.call_with_fallback(
                messages=messages,
                model_alias=resolved_alias,
                temperature=0.3,
                max_tokens=2048,
            )
            response_text = result.content.strip()
        except Exception as exc:
            _log.warning(
                "flush_prompt_llm_failed",
                scope_id=scope_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return FlushPromptResult(
                fallback_to_summary=True,
                errors=[f"LLM 调用失败: {exc}"],
            )

        # 5. 解析 JSON
        items = self._parse_response(response_text)
        if items is None:
            _log.warning(
                "flush_prompt_parse_failed",
                scope_id=scope_id,
                response=response_text[:200],
            )
            return FlushPromptResult(
                fallback_to_summary=True,
                errors=["LLM 输出格式错误，无法解析为 JSON 数组"],
            )

        # 6. 空数组 -> 无需保存
        if not items:
            return FlushPromptResult(skipped=True)

        # 7. 逐条调用 memory_write_fn
        writes_attempted = 0
        writes_committed = 0
        errors: list[str] = []

        for item in items:
            subject_key = item.get("subject_key", "").strip()
            content = item.get("content", "").strip()
            partition = item.get("partition", "work").strip()

            if not subject_key or not content:
                continue

            writes_attempted += 1
            try:
                await memory_write_fn(
                    subject_key=subject_key,
                    content=content,
                    partition=partition,
                    evidence_refs=None,
                )
                writes_committed += 1
            except Exception as exc:
                errors.append(f"写入 '{subject_key}' 失败: {exc}")

        _log.info(
            "flush_prompt_completed",
            scope_id=scope_id,
            writes_attempted=writes_attempted,
            writes_committed=writes_committed,
            skipped=False,
        )

        return FlushPromptResult(
            writes_attempted=writes_attempted,
            writes_committed=writes_committed,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _format_conversation(messages: list[dict[str, str]]) -> str:
        """将对话消息列表格式化为文本摘要。"""
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # 截断过长的单条消息
            if len(content) > 2000:
                content = content[:2000] + "..."
            lines.append(f"[{role}] {content}")
        return "\n\n".join(lines)

    @staticmethod
    def _parse_response(text: str) -> list[dict[str, Any]] | None:
        return parse_llm_json_array(text)

    def _resolve_default_model_alias(self) -> str:
        return resolve_default_model_alias(self._project_root)
