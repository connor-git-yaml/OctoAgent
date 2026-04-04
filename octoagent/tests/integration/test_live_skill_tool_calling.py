"""端到端集成测试：LLM → 工具调用 → 结果回传

验证链路：LiteLLM Proxy → LiteLLMSkillClient → SkillRunner → ToolBroker → 真实工具

运行前提：
  - LiteLLM Proxy 在 localhost:4001 运行（octoagent/目录下执行 dotenv -f .env.litellm run -- .venv/bin/litellm --config litellm-config.yaml --port 4001）
  - LITELLM_MASTER_KEY 环境变量已设置（或通过 .env.litellm 加载）

运行方式：
  dotenv -f .env.litellm run -- uv run pytest tests/integration/test_live_skill_tool_calling.py -v -s
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from octoagent.core.models.event import Event
from octoagent.skills.litellm_client import LiteLLMSkillClient
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import SkillExecutionContext, SkillRunStatus
from octoagent.skills.runner import SkillRunner
from octoagent.tooling.broker import ToolBroker
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import SideEffectLevel
from octoagent.tooling.schema import reflect_tool_schema
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 跳过条件：Proxy 未运行时跳过
# ---------------------------------------------------------------------------

# 优先从 octoagent.yaml 读取 proxy_url，环境变量作为 fallback，最后兜底 4001
def _load_proxy_url() -> str:
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).parents[2] / "octoagent.yaml"
    try:
        data = yaml.safe_load(cfg_path.read_text())
        url = data.get("runtime", {}).get("litellm_proxy_url", "")
        if url:
            return url
    except Exception:
        pass
    return os.environ.get("LITELLM_PROXY_URL", "http://localhost:4001")


PROXY_URL = _load_proxy_url()
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")


def _proxy_running() -> bool:
    """检测 LiteLLM Proxy 是否可达。"""
    import httpx

    try:
        resp = httpx.get(f"{PROXY_URL}/health", timeout=2.0)
        return resp.status_code < 500
    except Exception:
        return False


requires_proxy = pytest.mark.skipif(
    not _proxy_running() or not MASTER_KEY,
    reason="LiteLLM Proxy 未运行或 LITELLM_MASTER_KEY 未设置",
)

# ---------------------------------------------------------------------------
# 测试工具定义
# ---------------------------------------------------------------------------


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="math",
)
async def add_numbers(a: int, b: int) -> str:
    """将两个整数相加，返回结果。

    Args:
        a: 第一个整数
        b: 第二个整数
    """
    return str(a + b)


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="math",
)
async def multiply_numbers(a: int, b: int) -> str:
    """将两个整数相乘，返回结果。

    Args:
        a: 第一个整数
        b: 第二个整数
    """
    return str(a * b)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class InMemoryEventStore:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._seq: dict[str, int] = {}

    async def append_event(self, event: Event) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        seq = self._seq.get(task_id, 0) + 1
        self._seq[task_id] = seq
        return seq


class SimpleOutput(BaseModel):
    content: str = ""
    complete: bool = False
    tool_calls: list = []
    skip_remaining_tools: bool = False
    metadata: dict = {}


@pytest_asyncio.fixture
async def tool_broker() -> ToolBroker:
    event_store = InMemoryEventStore()
    broker = ToolBroker(event_store=event_store)
    meta_add = reflect_tool_schema(add_numbers)
    meta_mul = reflect_tool_schema(multiply_numbers)
    await broker.register(meta_add, add_numbers)
    await broker.register(meta_mul, multiply_numbers)
    return broker


@pytest_asyncio.fixture
async def skill_runner(tool_broker: ToolBroker) -> SkillRunner:
    event_store = InMemoryEventStore()
    client = LiteLLMSkillClient(
        proxy_url=PROXY_URL,
        master_key=MASTER_KEY,
        tool_broker=tool_broker,
    )
    return SkillRunner(
        model_client=client,
        tool_broker=tool_broker,
        event_store=event_store,
    )


@pytest_asyncio.fixture
def math_manifest() -> SkillManifest:
    return SkillManifest(
        skill_id="demo.math",
        version="0.1.0",
        input_model=SimpleOutput,
        output_model=SimpleOutput,
        model_alias="cheap",
        description="You are a math assistant. Use the tools provided to calculate results. When you have the answer, return it in plain text with complete=true.",
        tools_allowed=["add_numbers", "multiply_numbers"],
    )


@pytest_asyncio.fixture
def ctx() -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id="live-test-001",
        trace_id="trace-live-001",
        caller="test",
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


@requires_proxy
async def test_llm_calls_add_tool(
    skill_runner: SkillRunner,
    math_manifest: SkillManifest,
    ctx: SkillExecutionContext,
) -> None:
    """LLM 接收「3 + 7 等于多少」，应调用 add_numbers 工具并返回 10。"""
    result = await skill_runner.run(
        manifest=math_manifest,
        execution_context=ctx,
        skill_input={"content": ""},
        prompt="What is 3 + 7? Use the add_numbers tool to calculate.",
    )

    print(f"\n[结果] status={result.status}, steps={result.steps}")
    print(f"[输出] {result.output}")

    assert result.status == SkillRunStatus.SUCCEEDED, (
        f"Skill 失败: {result.error_message}"
    )
    assert result.output is not None
    # 答案应包含 10
    assert "10" in result.output.content, (
        f"期望输出包含 '10'，实际: {result.output.content!r}"
    )


@requires_proxy
async def test_llm_calls_multiply_tool(
    skill_runner: SkillRunner,
    math_manifest: SkillManifest,
    ctx: SkillExecutionContext,
) -> None:
    """LLM 接收「6 × 7 等于多少」，应调用 multiply_numbers 工具并返回 42。"""
    ctx2 = SkillExecutionContext(
        task_id="live-test-002",
        trace_id="trace-live-002",
        caller="test",
    )
    result = await skill_runner.run(
        manifest=math_manifest,
        execution_context=ctx2,
        skill_input={"content": ""},
        prompt="What is 6 multiplied by 7? Use the multiply_numbers tool.",
    )

    print(f"\n[结果] status={result.status}, steps={result.steps}")
    print(f"[输出] {result.output}")

    assert result.status == SkillRunStatus.SUCCEEDED
    assert result.output is not None
    assert "42" in result.output.content, (
        f"期望输出包含 '42'，实际: {result.output.content!r}"
    )
