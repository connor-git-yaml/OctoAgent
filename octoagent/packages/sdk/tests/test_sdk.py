"""OctoAgent SDK 基本测试。"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from octoagent_sdk import Agent, AgentResult, tool
from octoagent_sdk._tool import ToolSpec


# ---------------------------------------------------------------------------
# @tool 装饰器测试
# ---------------------------------------------------------------------------


def test_tool_decorator_basic():
    @tool
    async def greet(name: str) -> str:
        """Say hello"""
        return f"Hello {name}"

    assert isinstance(greet, ToolSpec)
    assert greet.name == "greet"
    assert greet.description == "Say hello"
    assert greet.is_async is True


def test_tool_decorator_with_params():
    @tool(name="my_search", description="Search the web")
    async def search(query: str, limit: int = 10) -> str:
        return f"Results for {query}"

    assert isinstance(search, ToolSpec)
    assert search.name == "my_search"
    assert search.description == "Search the web"


def test_tool_json_schema():
    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers"""
        return a + b

    schema = add.json_schema
    assert schema["type"] == "object"
    assert "a" in schema["properties"]
    assert "b" in schema["properties"]
    assert schema["properties"]["a"]["type"] == "integer"
    assert "a" in schema["required"]
    assert "b" in schema["required"]


def test_tool_openai_format():
    @tool
    async def fetch(url: str) -> str:
        """Fetch a URL"""
        return ""

    openai_tool = fetch.to_openai_tool()
    assert openai_tool["type"] == "function"
    assert openai_tool["function"]["name"] == "fetch"
    assert openai_tool["function"]["description"] == "Fetch a URL"
    assert "url" in openai_tool["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_tool_execute():
    @tool
    async def multiply(a: int, b: int) -> str:
        return str(a * b)

    result = await multiply.execute({"a": 3, "b": 4})
    assert result == "12"


@pytest.mark.asyncio
async def test_tool_execute_sync_fn():
    @tool
    def divide(a: float, b: float) -> str:
        return str(a / b)

    result = await divide.execute({"a": 10.0, "b": 2.0})
    assert result == "5.0"


# ---------------------------------------------------------------------------
# Agent 类测试
# ---------------------------------------------------------------------------


def test_agent_init_minimal():
    agent = Agent()
    assert agent._model == "gpt-4o"
    assert len(agent._tools) == 0


def test_agent_init_with_tools():
    @tool
    async def search(query: str) -> str:
        return "result"

    agent = Agent(model="gpt-3.5-turbo", tools=[search])
    assert agent._model == "gpt-3.5-turbo"
    assert "search" in agent._tools


def test_agent_init_with_bare_function():
    async def my_func(x: str) -> str:
        return x

    agent = Agent(tools=[my_func])
    assert "my_func" in agent._tools


def test_agent_build_messages():
    agent = Agent(system_prompt="You are helpful")
    messages = agent._build_initial_messages("hello")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are helpful"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "hello"


def test_agent_build_messages_no_system():
    agent = Agent()
    messages = agent._build_initial_messages("hello")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_agent_build_tools_schema():
    @tool
    async def search(query: str) -> str:
        """Search"""
        return ""

    agent = Agent(tools=[search])
    schema = agent._build_tools_schema()
    assert schema is not None
    assert len(schema) == 1
    assert schema[0]["type"] == "function"


def test_agent_build_tools_schema_empty():
    agent = Agent()
    schema = agent._build_tools_schema()
    assert schema is None


@pytest.mark.asyncio
async def test_agent_run_simple():
    """模拟 LLM 返回简单文本（无工具调用）。"""
    agent = Agent(model="test")

    mock_response = {
        "choices": [{
            "message": {"content": "你好！", "role": "assistant"},
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": 42},
    }

    with patch.object(agent, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await agent.run("hello")

    assert isinstance(result, AgentResult)
    assert result.text == "你好！"
    assert result.total_tokens == 42
    assert result.tool_calls_count == 0


@pytest.mark.asyncio
async def test_agent_run_with_tool_call():
    """模拟 LLM 先调用工具再返回最终结果。"""

    @tool
    async def get_weather(city: str) -> str:
        """查天气"""
        return f"{city} 26°C 多云"

    agent = Agent(model="test", tools=[get_weather])

    # 第一次 LLM 调用返回工具调用
    tool_call_response = {
        "choices": [{
            "message": {
                "content": None,
                "role": "assistant",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "get_weather",
                        "arguments": json.dumps({"city": "深圳"}),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"total_tokens": 30},
    }

    # 第二次 LLM 调用返回最终结果
    final_response = {
        "choices": [{
            "message": {"content": "深圳今天 26°C 多云", "role": "assistant"},
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": 20},
    }

    call_count = 0

    async def mock_call_llm(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tool_call_response
        return final_response

    with patch.object(agent, "_call_llm", side_effect=mock_call_llm):
        result = await agent.run("深圳天气")

    assert result.text == "深圳今天 26°C 多云"
    assert result.tool_calls_count == 1
    assert result.total_tokens == 50


@pytest.mark.asyncio
async def test_agent_run_with_policy_deny():
    """Policy 拒绝工具调用时返回错误。"""

    @tool
    async def dangerous_tool(cmd: str) -> str:
        return "executed"

    def deny_policy(tool_name: str, arguments: dict) -> bool:
        return False  # 拒绝所有

    agent = Agent(model="test", tools=[dangerous_tool], policy=deny_policy)

    tool_call_response = {
        "choices": [{
            "message": {
                "content": None,
                "role": "assistant",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "dangerous_tool",
                        "arguments": json.dumps({"cmd": "rm -rf /"}),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"total_tokens": 20},
    }

    final_response = {
        "choices": [{
            "message": {"content": "操作被拒绝", "role": "assistant"},
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": 10},
    }

    call_count = 0

    async def mock_call_llm(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tool_call_response
        return final_response

    with patch.object(agent, "_call_llm", side_effect=mock_call_llm):
        result = await agent.run("删除所有文件")

    assert result.tool_calls_count == 1
    assert "拒绝" in result.text or "denied" in result.text.lower() or result.text
