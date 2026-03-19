"""Feature 064 P2-A: ContextCompactor 设计契约。

定义上下文压缩器的接口和行为规范。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# CompactionResult
# ============================================================


class CompactionResult(BaseModel):
    """上下文压缩结果。"""

    compressed: bool = Field(description="是否执行了压缩")
    history: list[dict[str, Any]] = Field(description="压缩后的对话历史")
    before_tokens: int = Field(description="压缩前估算 token 数", ge=0)
    after_tokens: int = Field(description="压缩后估算 token 数", ge=0)
    strategies_used: list[str] = Field(
        default_factory=list,
        description="使用的压缩策略列表",
    )
    turns_removed: int = Field(default=0, description="被移除/合并的对话轮次数", ge=0)
    error: str | None = Field(default=None, description="压缩过程中的错误信息（降级时）")


# ============================================================
# ContextCompactor 设计
# ============================================================


class ContextCompactorSpec:
    """ContextCompactor 行为规范。

    位置（新建文件）: packages/skills/src/octoagent/skills/compactor.py

    职责:
    1. 估算对话历史 token 数
    2. 当超过阈值时执行三级压缩策略
    3. 保护 system prompt 和最近 N 轮不被压缩
    4. 压缩失败时降级为简单截断
    5. 通过 event_store 发射压缩事件

    三级压缩策略:
    - Level 1: 截断大工具输出（> 2000 字符 → 前 500 字符 + '...[truncated]'）
    - Level 2: 早期对话轮次用 LLM 生成摘要替换（保留最近 N 轮）
    - Level 3: 丢弃最老的摘要块

    保护区域:
    - system prompt（history[0] if role=system）永不压缩
    - 最近一轮 user/assistant 永不压缩
    - tool_call_id 引用的完整性：被压缩的 tool role message 会丢失 tool_call_id，
      但由于 LLM 不会引用已过去很多轮的 tool_call_id，这是可接受的

    伪代码:

    class ContextCompactor:
        def __init__(
            self,
            proxy_url: str,
            master_key: str,
            default_model_alias: str = "compaction",
            timeout_s: float = 30.0,
        ):
            self._proxy_url = proxy_url
            self._master_key = master_key
            self._default_model_alias = default_model_alias
            self._timeout_s = timeout_s

        def estimate_tokens(self, history: list[dict]) -> int:
            '''估算对话历史 token 数。默认使用字符数 / 4 近似。'''
            total_chars = sum(len(str(msg.get("content", ""))) for msg in history)
            return total_chars // 4

        async def compact(
            self,
            history: list[dict],
            max_tokens: int,
            *,
            threshold_ratio: float = 0.8,
            recent_turns: int = 8,
            compaction_model_alias: str | None = None,
        ) -> CompactionResult:
            '''执行上下文压缩。'''
            current_tokens = self.estimate_tokens(history)
            threshold = int(max_tokens * threshold_ratio)

            if current_tokens < threshold:
                return CompactionResult(
                    compressed=False,
                    history=history,
                    before_tokens=current_tokens,
                    after_tokens=current_tokens,
                )

            before_tokens = current_tokens
            strategies_used = []
            working_history = list(history)

            # Level 1: 截断大工具输出
            working_history = self._truncate_large_outputs(working_history)
            current_tokens = self.estimate_tokens(working_history)
            if current_tokens < threshold:
                strategies_used.append("truncate_large_output")
                return CompactionResult(
                    compressed=True,
                    history=working_history,
                    before_tokens=before_tokens,
                    after_tokens=current_tokens,
                    strategies_used=strategies_used,
                )
            strategies_used.append("truncate_large_output")

            # Level 2: 早期对话轮次用 LLM 摘要替换
            try:
                working_history = await self._summarize_early_turns(
                    working_history, recent_turns, compaction_model_alias
                )
                strategies_used.append("summarize_early_turns")
            except Exception as exc:
                # 降级：LLM 摘要失败，跳过 Level 2
                strategies_used.append("summarize_early_turns_failed")

            current_tokens = self.estimate_tokens(working_history)
            if current_tokens < threshold:
                return CompactionResult(
                    compressed=True,
                    history=working_history,
                    before_tokens=before_tokens,
                    after_tokens=current_tokens,
                    strategies_used=strategies_used,
                )

            # Level 3: 丢弃最老的摘要块
            working_history = self._drop_oldest_summaries(working_history, recent_turns)
            strategies_used.append("drop_oldest_summary")
            current_tokens = self.estimate_tokens(working_history)

            return CompactionResult(
                compressed=True,
                history=working_history,
                before_tokens=before_tokens,
                after_tokens=current_tokens,
                strategies_used=strategies_used,
            )

        def _truncate_large_outputs(
            self, history: list[dict], max_chars: int = 2000, keep_chars: int = 500
        ) -> list[dict]:
            '''Level 1: 截断超过 max_chars 的 tool role message 内容。'''
            result = []
            for msg in history:
                if msg.get("role") == "tool" and len(str(msg.get("content", ""))) > max_chars:
                    truncated = dict(msg)
                    truncated["content"] = str(msg["content"])[:keep_chars] + "...[truncated]"
                    result.append(truncated)
                else:
                    result.append(msg)
            return result

        async def _summarize_early_turns(
            self, history: list[dict], recent_turns: int, model_alias: str | None
        ) -> list[dict]:
            '''Level 2: 保留 system prompt + 最近 N 轮，早期轮次用 LLM 摘要替换。'''
            # 识别保护区域
            system_msg = history[0] if history and history[0].get("role") == "system" else None
            protected_start = 1 if system_msg else 0
            protected_end = max(protected_start, len(history) - recent_turns * 2)

            early_messages = history[protected_start:protected_end]
            if not early_messages:
                return history

            # 用 LLM 生成摘要
            summary_text = await self._generate_summary(early_messages, model_alias)

            # 重组历史
            new_history = []
            if system_msg:
                new_history.append(system_msg)
            new_history.append({
                "role": "user",
                "content": f"[Previous conversation summary]\n{summary_text}",
            })
            new_history.extend(history[protected_end:])
            return new_history

        def _drop_oldest_summaries(
            self, history: list[dict], recent_turns: int
        ) -> list[dict]:
            '''Level 3: 丢弃最老的摘要块，只保留 system prompt + 最近 N 轮。'''
            system_msg = history[0] if history and history[0].get("role") == "system" else None
            recent = history[-(recent_turns * 2):]
            result = []
            if system_msg:
                result.append(system_msg)
            result.extend(recent)
            return result

        async def _generate_summary(
            self, messages: list[dict], model_alias: str | None
        ) -> str:
            '''调用 LLM 生成早期对话摘要。使用独立 httpx 调用。'''
            alias = model_alias or self._default_model_alias
            body = {
                "model": alias,
                "messages": [
                    {"role": "system", "content": "Summarize the following conversation concisely. Focus on key decisions, facts, and outcomes. Keep the summary under 500 words."},
                    {"role": "user", "content": "\\n".join(
                        f"{msg.get('role', 'user')}: {str(msg.get('content', ''))[:500]}"
                        for msg in messages
                    )},
                ],
                "max_tokens": 1000,
            }
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    f"{self._proxy_url}/v1/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._master_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
    """

    pass
