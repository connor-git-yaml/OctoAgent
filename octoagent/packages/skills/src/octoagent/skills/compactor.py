"""上下文压缩器 — Feature 064 P2-A。

实现三级渐进式压缩策略，在对话历史 token 数接近上下文窗口阈值时
自动压缩历史，保持推理质量。

三级策略：
  Level 1: 截断 > 2000 字符的 tool role message 为前 500 字符 + ``...[truncated]``
  Level 2: 保留最近 N 轮，早期轮次用 LLM 摘要替换
  Level 3: 丢弃最老的摘要块
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Protocol

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---- 常量 ----

# Level 1 截断阈值和保留长度
_TOOL_OUTPUT_TRUNCATION_THRESHOLD = 2000
_TOOL_OUTPUT_KEEP_CHARS = 500

# Level 2 默认保留最近 N 轮
_DEFAULT_RECENT_TURNS = 8

# LLM 摘要默认模型别名
_DEFAULT_COMPACTION_MODEL_ALIAS = "compaction"


# ---- 枚举与数据类 ----


class CompactionStrategy(StrEnum):
    """压缩策略标识。"""

    NONE = "none"
    LEVEL1 = "level1"
    LEVEL2 = "level2"
    LEVEL3 = "level3"
    FALLBACK_TRUNCATION = "fallback_truncation"


@dataclass
class CompactionResult:
    """压缩结果。"""

    before_tokens: int
    after_tokens: int
    strategy_used: CompactionStrategy
    messages_compressed: int = 0


# ---- 事件发射协议 ----


class EventEmitter(Protocol):
    """简化的事件发射协议，避免依赖完整 EventStore。"""

    async def emit_compaction_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...


class NoopEventEmitter:
    """无操作的事件发射器（默认值）。"""

    async def emit_compaction_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        pass


# ---- token 估算 ----


def estimate_tokens_default(text: str) -> int:
    """默认 token 估算：字符数 / 4。"""
    return max(1, len(text) // 4)


def _estimate_history_tokens(
    history: list[dict[str, Any]],
    token_estimator: Callable[[str], int],
) -> int:
    """估算整个对话历史的 token 数。"""
    total = 0
    for msg in history:
        # 处理常规 role-based 消息
        content = msg.get("content")
        if isinstance(content, str):
            total += token_estimator(content)
        elif isinstance(content, list):
            # Responses API 的嵌套 content
            for item in content:
                if isinstance(item, dict):
                    total += token_estimator(str(item.get("text", "")))

        # 处理 tool_calls 数组（assistant message）
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    total += token_estimator(str(fn.get("name", "")))
                    total += token_estimator(str(fn.get("arguments", "")))

        # 处理 function_call / function_call_output（Responses API）
        if msg.get("type") in ("function_call", "function_call_output"):
            total += token_estimator(str(msg.get("name", "")))
            total += token_estimator(str(msg.get("arguments", "")))
            total += token_estimator(str(msg.get("output", "")))

    return total


# ---- 辅助函数 ----


def _is_system_prompt(msg: dict[str, Any]) -> bool:
    """判断是否是 system prompt。"""
    return str(msg.get("role", "")).lower() == "system"


def _is_tool_role_message(msg: dict[str, Any]) -> bool:
    """判断是否是 tool role 消息。

    Chat Completions: role=tool；Responses API: type=function_call_output。
    """
    if str(msg.get("role", "")).lower() == "tool":
        return True
    if str(msg.get("type", "")) == "function_call_output":
        return True
    return False


def _get_message_content_length(msg: dict[str, Any]) -> int:
    """获取消息内容长度。"""
    content = msg.get("content") or msg.get("output") or ""
    return len(str(content))


def _truncate_message_content(
    msg: dict[str, Any],
    keep_chars: int = _TOOL_OUTPUT_KEEP_CHARS,
) -> dict[str, Any]:
    """截断消息内容，保留前 N 字符。"""
    result = dict(msg)
    content = result.get("content")
    if isinstance(content, str) and len(content) > keep_chars:
        result["content"] = content[:keep_chars] + "\n...[truncated]"
    output = result.get("output")
    if isinstance(output, str) and len(output) > keep_chars:
        result["output"] = output[:keep_chars] + "\n...[truncated]"
    return result


def _identify_turn_boundaries(history: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """识别对话轮次边界。

    一轮 = user message 开始，到下一个 user message 之前结束。
    system prompt 不计入轮次。tool/function_call/function_call_output 归入当前轮次。

    Returns:
        list of (start_index, end_index) tuples，左闭右开。
    """
    turns: list[tuple[int, int]] = []
    turn_start: int | None = None

    for i, msg in enumerate(history):
        if _is_system_prompt(msg):
            continue

        role = str(msg.get("role", "")).lower()
        msg_type = str(msg.get("type", ""))

        # user message 开始新轮次
        if role == "user" and msg_type not in ("function_call", "function_call_output"):
            if turn_start is not None:
                turns.append((turn_start, i))
            turn_start = i

    # 关闭最后一轮
    if turn_start is not None:
        turns.append((turn_start, len(history)))

    return turns


# ---- LLM 摘要调用 ----


async def _summarize_with_llm(
    messages: list[dict[str, Any]],
    model_alias: str,
    proxy_url: str,
    master_key: str,
    timeout_s: float = 30.0,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """通过 LiteLLM Proxy 调用 LLM 生成摘要。

    Feature 064 P3 优化 4: 有外部 http_client 时复用，无则创建临时的。
    """
    # 将消息列表序列化为文本
    text_parts: list[str] = []
    for msg in messages:
        role = msg.get("role", msg.get("type", "unknown"))
        content = msg.get("content") or msg.get("output") or msg.get("arguments") or ""
        if isinstance(content, list):
            # Responses API 嵌套 content
            content = " ".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict)
            )
        text_parts.append(f"[{role}]: {content}")

    conversation_text = "\n".join(text_parts)
    # 限制输入长度，防止摘要请求本身超限
    if len(conversation_text) > 8000:
        conversation_text = conversation_text[:8000] + "\n...[truncated for summarization]"

    body = {
        "model": model_alias,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个对话摘要助手。请将以下对话历史压缩为简洁的摘要，"
                    "保留关键信息（用户意图、重要结论、工具调用结果的要点）。"
                    "使用相同的语言回复。输出纯文本摘要，不要使用 markdown 格式。"
                ),
            },
            {
                "role": "user",
                "content": f"请摘要以下对话历史：\n\n{conversation_text}",
            },
        ],
        "max_tokens": 500,
        "temperature": 0.3,
    }

    url = f"{proxy_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json",
    }

    if http_client is not None:
        # 复用外部 httpx.AsyncClient
        resp = await http_client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    else:
        # 创建临时 httpx.AsyncClient
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

    choices = data.get("choices", [])
    if choices:
        return str(choices[0].get("message", {}).get("content", "")).strip()
    return "[对话摘要生成失败]"


# ---- ContextCompactor ----


class ContextCompactor:
    """上下文压缩器。

    在 LiteLLMSkillClient.generate() 调用前检测对话历史 token 数，
    当接近上下文窗口阈值时执行渐进式三级压缩。

    用法::

        compactor = ContextCompactor(proxy_url="http://...", master_key="sk-...")
        result = await compactor.compact(
            history=history,
            max_tokens=128000,
            threshold_ratio=0.8,
        )
    """

    def __init__(
        self,
        *,
        proxy_url: str = "",
        master_key: str = "",
        token_estimator: Callable[[str], int] | None = None,
        event_emitter: EventEmitter | None = None,
        recent_turns: int = _DEFAULT_RECENT_TURNS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._proxy_url = proxy_url
        self._master_key = master_key
        self._token_estimator = token_estimator or estimate_tokens_default
        self._event_emitter = event_emitter or NoopEventEmitter()
        self._recent_turns = recent_turns
        # Feature 064 P3 优化 4: 可选复用外部 httpx.AsyncClient
        self._http_client = http_client

    async def compact(
        self,
        history: list[dict[str, Any]],
        max_tokens: int,
        threshold_ratio: float = 0.8,
        compaction_model_alias: str | None = None,
    ) -> CompactionResult:
        """执行上下文压缩。

        Args:
            history: 对话历史列表（原地修改）。
            max_tokens: 模型上下文窗口 token 上限。
            threshold_ratio: 触发压缩的阈值比例（默认 0.8）。
                设为 1.0 则永不触发（回滚方案）。
            compaction_model_alias: 摘要生成使用的模型别名。
                默认使用 ``compaction`` alias。

        Returns:
            CompactionResult 数据类。
        """
        before_tokens = _estimate_history_tokens(history, self._token_estimator)

        # threshold_ratio >= 1.0 时永不触发（回滚方案）
        if threshold_ratio >= 1.0:
            return CompactionResult(
                before_tokens=before_tokens,
                after_tokens=before_tokens,
                strategy_used=CompactionStrategy.NONE,
            )

        threshold = int(max_tokens * threshold_ratio)

        # 未达阈值，无需压缩
        if before_tokens <= threshold:
            return CompactionResult(
                before_tokens=before_tokens,
                after_tokens=before_tokens,
                strategy_used=CompactionStrategy.NONE,
            )

        model_alias = compaction_model_alias or _DEFAULT_COMPACTION_MODEL_ALIAS

        try:
            result = await self._do_compact(
                history=history,
                threshold=threshold,
                model_alias=model_alias,
            )
            result.before_tokens = before_tokens

            # 发射成功事件
            await self._event_emitter.emit_compaction_event(
                event_type="CONTEXT_COMPACTION_COMPLETED",
                payload={
                    "before_tokens": result.before_tokens,
                    "after_tokens": result.after_tokens,
                    "strategy_used": result.strategy_used,
                    "messages_compressed": result.messages_compressed,
                },
            )
            return result
        except Exception as exc:
            log.warning(
                "context_compaction_failed",
                error=str(exc),
                before_tokens=before_tokens,
            )

            # 压缩失败降级：简单截断（保留 system prompt + 最近消息）
            fallback_result = self._fallback_truncation(history, threshold)
            fallback_result.before_tokens = before_tokens

            # 发射失败事件
            await self._event_emitter.emit_compaction_event(
                event_type="CONTEXT_COMPACTION_FAILED",
                payload={
                    "before_tokens": before_tokens,
                    "after_tokens": fallback_result.after_tokens,
                    "strategy_used": "fallback_truncation",
                    "error": str(exc),
                },
            )
            return fallback_result

    async def _do_compact(
        self,
        *,
        history: list[dict[str, Any]],
        threshold: int,
        model_alias: str,
    ) -> CompactionResult:
        """执行三级渐进压缩。"""
        total_compressed = 0

        # ---- Level 1: 截断大工具输出 ----
        compressed_count = self._apply_level1(history)
        total_compressed += compressed_count
        current_tokens = _estimate_history_tokens(history, self._token_estimator)
        if current_tokens <= threshold:
            return CompactionResult(
                before_tokens=0,  # 由调用方填充
                after_tokens=current_tokens,
                strategy_used=CompactionStrategy.LEVEL1,
                messages_compressed=total_compressed,
            )

        # ---- Level 2: 早期轮次 LLM 摘要 ----
        compressed_count_l2 = await self._apply_level2(history, model_alias)
        total_compressed += compressed_count_l2
        current_tokens = _estimate_history_tokens(history, self._token_estimator)
        if current_tokens <= threshold:
            return CompactionResult(
                before_tokens=0,
                after_tokens=current_tokens,
                strategy_used=CompactionStrategy.LEVEL2,
                messages_compressed=total_compressed,
            )

        # ---- Level 3: 丢弃最老的摘要块 ----
        compressed_count_l3 = self._apply_level3(history)
        total_compressed += compressed_count_l3
        current_tokens = _estimate_history_tokens(history, self._token_estimator)

        return CompactionResult(
            before_tokens=0,
            after_tokens=current_tokens,
            strategy_used=CompactionStrategy.LEVEL3,
            messages_compressed=total_compressed,
        )

    def _apply_level1(self, history: list[dict[str, Any]]) -> int:
        """Level 1: 截断 > 2000 字符的 tool role message。

        system prompt 和最近一轮永不压缩。
        """
        protected = self._get_protected_indices(history)
        count = 0

        for i in range(len(history)):
            if i in protected:
                continue
            msg = history[i]
            if not _is_tool_role_message(msg):
                continue
            content_len = _get_message_content_length(msg)
            if content_len > _TOOL_OUTPUT_TRUNCATION_THRESHOLD:
                history[i] = _truncate_message_content(msg)
                count += 1

        return count

    async def _apply_level2(
        self,
        history: list[dict[str, Any]],
        model_alias: str,
    ) -> int:
        """Level 2: 保留最近 N 轮，早期轮次用 LLM 摘要替换。"""
        turns = _identify_turn_boundaries(history)

        if len(turns) <= self._recent_turns:
            # 轮次数不足，无需压缩
            return 0

        # 确定需要摘要的早期轮次
        early_turns = turns[: len(turns) - self._recent_turns]
        if not early_turns:
            return 0

        # 确定需要摘要替换的索引范围
        early_start = early_turns[0][0]
        early_end = early_turns[-1][1]

        # 跳过 system prompt
        actual_start = early_start
        while actual_start < early_end and _is_system_prompt(history[actual_start]):
            actual_start += 1

        if actual_start >= early_end:
            return 0

        # 收集早期消息用于摘要
        early_messages = history[actual_start:early_end]
        compressed_count = len(early_messages)

        # 调用 LLM 摘要（有外部 http_client 时复用）
        summary = await _summarize_with_llm(
            messages=early_messages,
            model_alias=model_alias,
            proxy_url=self._proxy_url,
            master_key=self._master_key,
            http_client=self._http_client,
        )

        # 用摘要消息替换早期轮次
        summary_msg: dict[str, Any] = {
            "role": "user",
            "content": f"[Earlier conversation summary]\n{summary}",
        }
        history[actual_start:early_end] = [summary_msg]

        return compressed_count

    def _apply_level3(self, history: list[dict[str, Any]]) -> int:
        """Level 3: 丢弃最老的摘要块。

        保留 system prompt + 最近轮次。
        """
        protected = self._get_protected_indices(history)

        # 从前向后查找非保护的摘要块并丢弃
        to_remove: list[int] = []
        for i in range(len(history)):
            if i in protected:
                continue
            msg = history[i]
            content = str(msg.get("content", ""))
            # 识别 Level 2 生成的摘要块
            if content.startswith("[Earlier conversation summary]"):
                to_remove.append(i)

        # 如果没有摘要块可丢弃，尝试丢弃最老的非保护消息
        if not to_remove:
            for i in range(len(history)):
                if i in protected:
                    continue
                to_remove.append(i)
                # 每次只丢弃少量以避免过度丢失
                if len(to_remove) >= 3:
                    break

        # 反向删除以保持索引正确
        for i in sorted(to_remove, reverse=True):
            history.pop(i)

        return len(to_remove)

    def _fallback_truncation(
        self,
        history: list[dict[str, Any]],
        threshold: int,
    ) -> CompactionResult:
        """降级方案：简单截断。保留 system prompt + 最近消息。"""
        if not history:
            return CompactionResult(
                before_tokens=0,
                after_tokens=0,
                strategy_used=CompactionStrategy.FALLBACK_TRUNCATION,
            )

        # 保留 system prompt
        preserved_front: list[dict[str, Any]] = []
        rest_start = 0
        if _is_system_prompt(history[0]):
            preserved_front.append(history[0])
            rest_start = 1

        # 从后向前保留消息直到达到阈值
        remaining = history[rest_start:]
        preserved_back: list[dict[str, Any]] = []
        running_tokens = _estimate_history_tokens(preserved_front, self._token_estimator)
        removed = 0

        for msg in reversed(remaining):
            msg_tokens = _estimate_history_tokens([msg], self._token_estimator)
            if running_tokens + msg_tokens <= threshold:
                preserved_back.insert(0, msg)
                running_tokens += msg_tokens
            else:
                removed += 1

        # 重建 history
        history.clear()
        history.extend(preserved_front)
        history.extend(preserved_back)

        after_tokens = _estimate_history_tokens(history, self._token_estimator)
        return CompactionResult(
            before_tokens=0,
            after_tokens=after_tokens,
            strategy_used=CompactionStrategy.FALLBACK_TRUNCATION,
            messages_compressed=removed,
        )

    def _get_protected_indices(self, history: list[dict[str, Any]]) -> set[int]:
        """获取受保护的消息索引（system prompt + 最近一轮 user/assistant）。"""
        protected: set[int] = set()

        # 保护 system prompt
        if history and _is_system_prompt(history[0]):
            protected.add(0)

        # 保护最近一轮 user/assistant（从后往前找）
        found_assistant = False
        found_user = False
        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            role = str(msg.get("role", "")).lower()
            msg_type = str(msg.get("type", ""))

            # 跳过 tool/function_call 类型
            if role == "tool" or msg_type in ("function_call", "function_call_output"):
                # 如果在最近一轮内（已找到 assistant 但还没找到 user），也保护
                if found_assistant and not found_user:
                    protected.add(i)
                continue

            if role == "assistant" and not found_assistant:
                protected.add(i)
                found_assistant = True
                continue

            if role == "user" and found_assistant and not found_user:
                protected.add(i)
                found_user = True
                break

            # 最近一轮已完整，停止
            if found_assistant and found_user:
                break

        # 如果还没找到 assistant，至少保护最后一条消息
        if not found_assistant and history:
            protected.add(len(history) - 1)

        return protected
