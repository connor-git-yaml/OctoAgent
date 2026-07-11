"""F138 Phase B：ScriptedModelClient 多步决策环编排（AC-4）。

驱动**真** ``SkillRunner``（非 mock runner）：第 1 轮 tool_call A、第 2 轮
tool_call B、第 3 轮 complete，断言脚本按序消费 + 工具按序真派发 + feedback
回灌链路走通。
"""

from __future__ import annotations

import pytest

# pre-merge 窗口防御（spec §3.5）：pre-commit hook 以主仓 master src 收集本文件，
# 彼时 octoagent.skills.testing 尚不存在 → 优雅 SKIP；合入 master 后恒可 import。
pytest.importorskip("octoagent.skills.testing")

from octoagent.skills.models import SkillOutputEnvelope, SkillRunStatus, ToolCallSpec
from octoagent.skills.runner import SkillRunner
from octoagent.skills.testing import ScriptedModelClient

from .conftest import MockEventStore, MockToolBroker, QueueModelClient


async def test_multistep_tool_chain_consumed_in_order(
    echo_manifest, execution_context,
) -> None:
    """AC-4：3 步脚本（工具 A → 工具 B → complete）被决策环按序消费。"""
    tool_broker = MockToolBroker()
    event_store = MockEventStore()
    client = ScriptedModelClient([
        SkillOutputEnvelope(
            content="",
            tool_calls=[ToolCallSpec(tool_name="system.echo", arguments={"text": "第一步"})],
        ),
        SkillOutputEnvelope(
            content="",
            tool_calls=[ToolCallSpec(tool_name="system.file_read", arguments={"path": "b.txt"})],
        ),
        SkillOutputEnvelope(content="全部完成", complete=True),
    ])
    runner = SkillRunner(
        model_client=client,
        tool_broker=tool_broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "multi-step"},
        prompt="多步编排",
    )

    # 断言 1：脚本被完整消费（3 轮 generate）
    assert client.calls == 3, f"AC-4: 决策环应跑 3 轮，实际 {client.calls}"
    # 断言 2：工具按脚本顺序真派发到 broker
    assert [c[0] for c in tool_broker.calls] == ["system.echo", "system.file_read"], (
        f"AC-4: broker 派发顺序应为脚本顺序，实际 {[c[0] for c in tool_broker.calls]}"
    )
    assert tool_broker.calls[0][1] == {"text": "第一步"}
    # 断言 3：终局输出来自第 3 步脚本
    assert result.status == SkillRunStatus.SUCCEEDED
    assert result.output is not None and result.output.content == "全部完成"


async def test_conftest_re_export_is_same_object() -> None:
    """AC-5 前置：conftest 的 QueueModelClient 与上提件为同一对象（re-export 兼容）。

    Phase B 阶段 conftest 尚未翻转（spec §3.5 flip-at-the-end），本断言暂验
    两者行为契约一致；Phase F 翻转后本断言升级为 identity 相等。
    """
    # Phase F 翻转后：QueueModelClient is ScriptedModelClient
    if QueueModelClient is not ScriptedModelClient:
        # 翻转前的过渡窗口：至少保证构造签名与队列行为一致（防两份实现漂移）
        legacy = QueueModelClient([SkillOutputEnvelope(content="x", complete=True)])
        promoted = ScriptedModelClient([SkillOutputEnvelope(content="x", complete=True)])
        legacy_out = await legacy.generate(
            manifest=None, execution_context=None, prompt="", feedback=[], attempt=1, step=1,
        )
        promoted_out = await promoted.generate(
            manifest=None, execution_context=None, prompt="", feedback=[], attempt=1, step=1,
        )
        assert legacy_out == promoted_out
        assert legacy.calls == promoted.calls == 1
    else:
        assert QueueModelClient is ScriptedModelClient


async def test_exhausted_queue_returns_default_complete(
    echo_manifest, execution_context,
) -> None:
    """队列耗尽后 generate 返回默认 complete envelope（上提后语义零变更）。"""
    client = ScriptedModelClient([])
    out = await client.generate(
        manifest=echo_manifest,
        execution_context=execution_context,
        prompt="",
        feedback=[],
        attempt=1,
        step=1,
    )
    assert out.complete is True
    assert out.content == "default"
    assert client.calls == 1
