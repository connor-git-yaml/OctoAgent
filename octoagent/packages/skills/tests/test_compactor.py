"""ContextCompactor 单元测试 — Feature 064 P2-A (T-064-20)。

覆盖验收标准：
1. token 未达阈值 → 不压缩
2. Level 1 截断大工具输出后满足阈值 → 停止
3. Level 1 不够 → Level 2 LLM 摘要替换
4. system prompt 永不被压缩
5. 最近一轮 user/assistant 永不被压缩
6. 压缩失败降级为简单截断
7. CONTEXT_COMPACTION_COMPLETED 事件 payload 正确
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from octoagent.skills.compactor import (
    CompactionResult,
    ContextCompactor,
    NoopEventEmitter,
    _DEFAULT_RECENT_TURNS,
    _TOOL_OUTPUT_KEEP_CHARS,
    _TOOL_OUTPUT_TRUNCATION_THRESHOLD,
    _estimate_history_tokens,
    _identify_turn_boundaries,
    estimate_tokens_default,
)


# ---- 辅助函数 ----


def _system(content: str) -> dict[str, Any]:
    return {"role": "system", "content": content}


def _user(content: str) -> dict[str, Any]:
    return {"role": "user", "content": content}


def _assistant(content: str) -> dict[str, Any]:
    return {"role": "assistant", "content": content}


def _tool(content: str, tool_call_id: str = "tc_1") -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _tool_large(chars: int = 3000, tool_call_id: str = "tc_1") -> dict[str, Any]:
    """生成超过截断阈值的大工具输出。"""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": "X" * chars}


class RecordingEventEmitter:
    """记录所有事件调用的测试用 EventEmitter。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit_compaction_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.events.append((event_type, payload))


# ---- 基础工具函数测试 ----


class TestEstimateTokens:
    def test_default_estimator(self):
        assert estimate_tokens_default("") == 1  # 最小值 1
        assert estimate_tokens_default("a" * 100) == 25
        assert estimate_tokens_default("a" * 4) == 1

    def test_history_tokens(self):
        history = [
            _system("You are helpful."),
            _user("Hello"),
            _assistant("Hi there!"),
        ]
        tokens = _estimate_history_tokens(history, estimate_tokens_default)
        assert tokens > 0

    def test_history_tokens_with_tool_calls(self):
        history = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/tmp/test.txt"}',
                        },
                    }
                ],
            }
        ]
        tokens = _estimate_history_tokens(history, estimate_tokens_default)
        assert tokens > 0

class TestTurnBoundaries:
    def test_simple_turns(self):
        history = [
            _system("sys"),
            _user("q1"),
            _assistant("a1"),
            _user("q2"),
            _assistant("a2"),
        ]
        turns = _identify_turn_boundaries(history)
        assert len(turns) == 2
        assert turns[0] == (1, 3)  # user q1 + assistant a1
        assert turns[1] == (3, 5)  # user q2 + assistant a2

    def test_turns_with_tool_messages(self):
        history = [
            _system("sys"),
            _user("do something"),
            _assistant("calling tool"),
            _tool("result"),
            _assistant("done"),
            _user("next"),
            _assistant("ok"),
        ]
        turns = _identify_turn_boundaries(history)
        assert len(turns) == 2

    def test_single_turn(self):
        history = [_system("sys"), _user("hello")]
        turns = _identify_turn_boundaries(history)
        assert len(turns) == 1

    def test_no_user_messages(self):
        history = [_system("sys"), _assistant("hello")]
        turns = _identify_turn_boundaries(history)
        assert len(turns) == 0


# ---- ContextCompactor 核心测试 ----


class TestCompactNoCompression:
    """AC-1: token 未达阈值 → 不压缩。"""

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none_strategy(self):
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        history = [
            _system("You are helpful."),
            _user("Hello"),
            _assistant("Hi!"),
        ]
        original_len = len(history)
        result = await compactor.compact(
            history=history,
            max_tokens=100000,
            threshold_ratio=0.8,
        )
        assert result.strategy_used == "none"
        assert result.before_tokens == result.after_tokens
        assert len(history) == original_len  # 未修改

    @pytest.mark.asyncio
    async def test_threshold_1_0_never_triggers(self):
        """compaction_threshold_ratio=1.0 → 永不触发（回滚方案）。"""
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        # 即使 token 数很多，ratio=1.0 也不触发
        history = [_system("sys"), _user("X" * 10000)]
        result = await compactor.compact(
            history=history,
            max_tokens=100,  # 极小的 max_tokens
            threshold_ratio=1.0,
        )
        assert result.strategy_used == "none"


class TestLevel1Truncation:
    """AC-2: Level 1 截断大工具输出后满足阈值 → 停止。"""

    @pytest.mark.asyncio
    async def test_truncate_large_tool_output(self):
        emitter = RecordingEventEmitter()
        compactor = ContextCompactor(
            proxy_url="http://test",
            master_key="sk-test",
            event_emitter=emitter,
        )
        # 大工具输出不在最近一轮（最近一轮受保护），所以放在早期轮次中
        # 4000 chars ≈ 1000 tokens；max_tokens=1200, threshold=0.8 → 阈值 960 tokens
        large_content = "A" * 4000
        history = [
            _system("sys"),
            _user("first question"),
            _tool(large_content, tool_call_id="tc_1"),
            _assistant("first answer"),
            _user("second question"),
            _assistant("second answer"),
        ]
        result = await compactor.compact(
            history=history,
            max_tokens=1200,
            threshold_ratio=0.8,
        )
        assert result.strategy_used == "level1"
        assert result.after_tokens < result.before_tokens
        assert result.messages_compressed >= 1

        # 验证工具输出被截断
        tool_msg = history[2]
        assert tool_msg["content"].endswith("...[truncated]")
        assert len(tool_msg["content"]) <= _TOOL_OUTPUT_KEEP_CHARS + 20  # +20 for suffix

    @pytest.mark.asyncio
    async def test_small_tool_output_not_truncated(self):
        """小于阈值的工具输出不应被截断。"""
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        small_content = "A" * 100
        history = [
            _system("sys"),
            _user("do something"),
            _tool(small_content, tool_call_id="tc_1"),
            _assistant("done"),
        ]
        original_content = history[2]["content"]
        # 使 token 不超阈值
        result = await compactor.compact(
            history=history,
            max_tokens=100000,
            threshold_ratio=0.8,
        )
        assert result.strategy_used == "none"
        assert history[2]["content"] == original_content


class TestLevel2LLMSummary:
    """AC-3: Level 1 不够 → Level 2 LLM 摘要替换。"""

    @pytest.mark.asyncio
    async def test_level2_replaces_early_turns(self):
        emitter = RecordingEventEmitter()
        compactor = ContextCompactor(
            proxy_url="http://test",
            master_key="sk-test",
            event_emitter=emitter,
            recent_turns=2,  # 只保留最近 2 轮
        )

        # 构造 4 轮对话，每轮 1000 字符
        history = [_system("sys")]
        for i in range(4):
            history.append(_user(f"question {i}: " + "Q" * 1000))
            history.append(_assistant(f"answer {i}: " + "A" * 1000))

        # mock LLM 摘要调用
        mock_summary = "这是早期对话的摘要"
        with patch(
            "octoagent.skills.compactor._summarize_with_llm",
            new_callable=AsyncMock,
            return_value=mock_summary,
        ):
            result = await compactor.compact(
                history=history,
                max_tokens=2000,  # 小阈值强制触发 Level 2
                threshold_ratio=0.8,
            )

        assert result.strategy_used in ("level2", "level3")
        # 历史应被缩减
        assert len(history) < 9  # 原始 1 sys + 8 msg = 9

    @pytest.mark.asyncio
    async def test_level2_preserves_recent_turns(self):
        """Level 2 应保留最近 N 轮。"""
        compactor = ContextCompactor(
            proxy_url="http://test",
            master_key="sk-test",
            recent_turns=2,
        )

        history = [_system("sys")]
        for i in range(5):
            history.append(_user(f"q{i}:" + "Q" * 500))
            history.append(_assistant(f"a{i}:" + "A" * 500))

        # 记住最近 2 轮的内容
        last_user = history[-2]["content"]  # q4
        last_assistant = history[-1]["content"]  # a4
        second_last_user = history[-4]["content"]  # q3

        with patch(
            "octoagent.skills.compactor._summarize_with_llm",
            new_callable=AsyncMock,
            return_value="summary",
        ):
            await compactor.compact(
                history=history,
                max_tokens=1000,
                threshold_ratio=0.8,
            )

        # 最近的对话内容应仍在历史中
        all_contents = [msg.get("content", "") for msg in history]
        assert last_user in all_contents
        assert last_assistant in all_contents
        assert second_last_user in all_contents


class TestSystemPromptProtection:
    """AC-4: system prompt 永不被压缩。"""

    @pytest.mark.asyncio
    async def test_system_prompt_preserved_level1(self):
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        sys_content = "You are a very helpful assistant. " * 100  # 长 system prompt
        history = [
            _system(sys_content),
            _user("first question"),
            _tool("A" * 4000, tool_call_id="tc_1"),
            _assistant("first answer"),
            _user("second question"),
            _assistant("second answer"),
        ]
        await compactor.compact(
            history=history,
            max_tokens=1200,
            threshold_ratio=0.8,
        )
        # system prompt 完整保留
        assert history[0]["role"] == "system"
        assert history[0]["content"] == sys_content

    @pytest.mark.asyncio
    async def test_system_prompt_preserved_fallback(self):
        """降级截断时也保留 system prompt。"""
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        sys_content = "Important system prompt"

        history = [_system(sys_content)]
        for i in range(20):
            history.append(_user(f"q{i}:" + "Q" * 200))
            history.append(_assistant(f"a{i}:" + "A" * 200))

        # 强制触发压缩并使 Level 2 LLM 调用失败 → 降级
        with patch(
            "octoagent.skills.compactor._summarize_with_llm",
            new_callable=AsyncMock,
            side_effect=Exception("LLM call failed"),
        ):
            result = await compactor.compact(
                history=history,
                max_tokens=500,
                threshold_ratio=0.8,
                compaction_model_alias="test-model",
            )

        assert result.strategy_used == "fallback_truncation"
        assert history[0]["role"] == "system"
        assert history[0]["content"] == sys_content


class TestRecentTurnProtection:
    """AC-5: 最近一轮 user/assistant 永不被压缩。"""

    @pytest.mark.asyncio
    async def test_last_turn_preserved_level1(self):
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        # 最近一轮也有大工具输出 → 仍不应被截断（保护最近一轮）
        last_user_content = "my important question"
        last_assistant_content = "my important answer"

        history = [
            _system("sys"),
            _user("old question"),
            _tool("X" * 4000, tool_call_id="tc_old"),
            _assistant("old answer"),
            _user(last_user_content),
            _assistant(last_assistant_content),
        ]

        await compactor.compact(
            history=history,
            max_tokens=2000,
            threshold_ratio=0.8,
        )

        # 最近一轮完整保留
        contents = [msg.get("content", "") for msg in history]
        assert last_user_content in contents
        assert last_assistant_content in contents


class TestFallbackTruncation:
    """AC-6: 压缩失败降级为简单截断。"""

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self):
        emitter = RecordingEventEmitter()
        compactor = ContextCompactor(
            proxy_url="http://test",
            master_key="sk-test",
            event_emitter=emitter,
            recent_turns=2,
        )

        history = [_system("sys")]
        for i in range(10):
            history.append(_user(f"q{i}:" + "Q" * 500))
            history.append(_assistant(f"a{i}:" + "A" * 500))

        # Level 2 的 LLM 调用抛异常
        with patch(
            "octoagent.skills.compactor._summarize_with_llm",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
            result = await compactor.compact(
                history=history,
                max_tokens=500,
                threshold_ratio=0.8,
            )

        assert result.strategy_used == "fallback_truncation"
        # 历史被截断，但仍可用
        assert len(history) >= 1  # 至少保留 system prompt

        # 验证发射了 CONTEXT_COMPACTION_FAILED 事件
        assert len(emitter.events) == 1
        event_type, payload = emitter.events[0]
        assert event_type == "CONTEXT_COMPACTION_FAILED"
        assert "error" in payload
        assert payload["strategy_used"] == "fallback_truncation"


class TestCompactionEventPayload:
    """AC-7: CONTEXT_COMPACTION_COMPLETED 事件 payload 正确。"""

    @pytest.mark.asyncio
    async def test_completed_event_payload(self):
        emitter = RecordingEventEmitter()
        compactor = ContextCompactor(
            proxy_url="http://test",
            master_key="sk-test",
            event_emitter=emitter,
        )

        history = [
            _system("sys"),
            _user("first question"),
            _tool("A" * 4000, tool_call_id="tc_1"),
            _assistant("first answer"),
            _user("second question"),
            _assistant("second answer"),
        ]

        result = await compactor.compact(
            history=history,
            max_tokens=1200,
            threshold_ratio=0.8,
        )

        # 应发射 CONTEXT_COMPACTION_COMPLETED
        assert len(emitter.events) == 1
        event_type, payload = emitter.events[0]
        assert event_type == "CONTEXT_COMPACTION_COMPLETED"
        assert "before_tokens" in payload
        assert "after_tokens" in payload
        assert "strategy_used" in payload
        assert "messages_compressed" in payload
        assert payload["before_tokens"] > payload["after_tokens"]
        assert payload["strategy_used"] == result.strategy_used

    @pytest.mark.asyncio
    async def test_no_event_when_no_compression(self):
        emitter = RecordingEventEmitter()
        compactor = ContextCompactor(
            proxy_url="http://test",
            master_key="sk-test",
            event_emitter=emitter,
        )

        history = [_system("sys"), _user("hello"), _assistant("hi")]
        await compactor.compact(
            history=history,
            max_tokens=100000,
            threshold_ratio=0.8,
        )

        # 未达阈值，不应发射事件
        assert len(emitter.events) == 0


class TestCompactionResult:
    """CompactionResult 数据类测试。"""

    def test_dataclass_fields(self):
        result = CompactionResult(
            before_tokens=1000,
            after_tokens=500,
            strategy_used="level1",
            messages_compressed=3,
        )
        assert result.before_tokens == 1000
        assert result.after_tokens == 500
        assert result.strategy_used == "level1"
        assert result.messages_compressed == 3

    def test_default_messages_compressed(self):
        result = CompactionResult(
            before_tokens=100,
            after_tokens=100,
            strategy_used="none",
        )
        assert result.messages_compressed == 0


class TestEdgeCases:
    """边界情况测试。"""

    @pytest.mark.asyncio
    async def test_empty_history(self):
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        history: list[dict[str, Any]] = []
        result = await compactor.compact(
            history=history,
            max_tokens=1000,
            threshold_ratio=0.8,
        )
        assert result.strategy_used == "none"
        assert len(history) == 0

    @pytest.mark.asyncio
    async def test_only_system_prompt(self):
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        history = [_system("sys")]
        result = await compactor.compact(
            history=history,
            max_tokens=1000,
            threshold_ratio=0.8,
        )
        assert result.strategy_used == "none"

    @pytest.mark.asyncio
    async def test_no_system_prompt(self):
        """没有 system prompt 的历史也能正常处理。"""
        compactor = ContextCompactor(proxy_url="http://test", master_key="sk-test")
        history = [
            _user("first question"),
            _tool("A" * 4000, tool_call_id="tc_1"),
            _assistant("first answer"),
            _user("second question"),
            _assistant("second answer"),
        ]
        result = await compactor.compact(
            history=history,
            max_tokens=1200,
            threshold_ratio=0.8,
        )
        # 应该能正常处理（可能触发 Level 1）
        assert result.strategy_used in ("none", "level1")

    @pytest.mark.asyncio
    async def test_custom_token_estimator(self):
        """自定义 token 估算函数。"""

        def always_high(text: str) -> int:
            return 99999  # 始终返回高值

        compactor = ContextCompactor(
            proxy_url="http://test",
            master_key="sk-test",
            token_estimator=always_high,
        )
        history = [_system("sys"), _user("hello")]

        with patch(
            "octoagent.skills.compactor._summarize_with_llm",
            new_callable=AsyncMock,
            return_value="summary",
        ):
            result = await compactor.compact(
                history=history,
                max_tokens=1000,
                threshold_ratio=0.8,
            )
        # 由于 token 估算始终很高，应触发压缩
        assert result.strategy_used != "none"


class TestSummaryArgumentsRedaction:
    """安全边界：compaction 摘要提示词不得携带原始 tool_call arguments。

    ContextCompactor 可能走独立的轻量 compaction alias；工具参数里常见的
    path / url / command 可能含内部路径或凭据片段，不应扩散到另一条
    模型调用链。
    """

    @pytest.mark.asyncio
    async def test_summary_prompt_excludes_tool_arguments(self):
        """_summarize_with_llm 发送的 body 必须只带工具名，不带原始 arguments。"""
        from octoagent.skills.compactor import _summarize_with_llm

        sensitive_path = "/tmp/secret-credentials.json"
        sensitive_url = "https://internal.example.com/api/token?key=SUPER_SECRET"
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "read secrets"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "fs__read",
                            "arguments": f'{{"path": "{sensitive_path}"}}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "http__get",
                            "arguments": f'{{"url": "{sensitive_url}"}}',
                        },
                    },
                ],
            },
        ]

        captured: dict[str, Any] = {}

        class _FakeResp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {
                    "choices": [{"message": {"content": "summary"}}],
                }

        class _FakeClient:
            async def post(self, url: str, *, json, headers):
                captured["body"] = json
                return _FakeResp()

        await _summarize_with_llm(
            messages=messages,
            model_alias="compaction",
            proxy_url="http://proxy",
            master_key="sk-test",
            http_client=_FakeClient(),
        )

        body_text = str(captured["body"])
        assert sensitive_path not in body_text, (
            "compaction prompt 泄露了 tool_call arguments (path)"
        )
        assert sensitive_url not in body_text, (
            "compaction prompt 泄露了 tool_call arguments (url)"
        )
        assert "SUPER_SECRET" not in body_text, (
            "compaction prompt 泄露了 tool_call arguments (凭据片段)"
        )
        # 工具名应保留（摘要需要调用序列信息）
        assert "fs__read" in body_text
        assert "http__get" in body_text
