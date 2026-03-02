"""Feature 007 端到端集成测试。

目标：验证 Feature 004/005/006 真实组件可联调：
SkillRunner -> ToolBroker -> PolicyCheckHook -> ApprovalManager。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.policy.models import ApprovalDecision
from octoagent.policy.policy_engine import PolicyEngine
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import SkillExecutionContext, SkillOutputEnvelope, SkillRunStatus
from octoagent.skills.runner import SkillRunner
from octoagent.tooling import (
    SideEffectLevel,
    ToolBroker,
    ToolProfile,
    reflect_tool_schema,
    tool_contract,
)
from pydantic import BaseModel


class DemoInput(BaseModel):
    request: str


class QueueModelClient:
    """受控结构化模型客户端（测试用）。"""

    def __init__(self, outputs: list[SkillOutputEnvelope]) -> None:
        self._outputs = list(outputs)

    async def generate(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
        feedback: list,
        attempt: int,
        step: int,
    ) -> SkillOutputEnvelope:
        if not self._outputs:
            raise RuntimeError("模型输出队列已耗尽")
        return self._outputs.pop(0)


@tool_contract(
    side_effect_level=SideEffectLevel.IRREVERSIBLE,
    tool_profile=ToolProfile.STANDARD,
    tool_group="filesystem",
    name="filesystem.write_text",
)
async def write_text(path: str, content: str) -> str:
    """写入文本到文件（测试桩实现）。"""
    return f"wrote {len(content)} bytes to {path}"


async def _create_task(
    db_path: Path,
    artifacts_dir: Path,
    idempotency_key: str,
) -> tuple:
    store_group = await create_store_group(str(db_path), str(artifacts_dir))
    service = TaskService(store_group, SSEHub())
    msg = NormalizedMessage(text="feature-007", idempotency_key=idempotency_key)
    task_id, created = await service.create_task(msg)
    assert created is True
    return store_group, task_id


class TestFeature007E2EIntegration:
    def test_schema_reflection_contract(self) -> None:
        """真实工具函数的 schema 反射应与签名一致。"""
        meta = reflect_tool_schema(write_text)

        props = meta.parameters_json_schema.get("properties", {})
        required = set(meta.parameters_json_schema.get("required", []))

        assert meta.name == "filesystem.write_text"
        assert meta.side_effect_level == SideEffectLevel.IRREVERSIBLE
        assert {"path", "content"}.issubset(set(props.keys()))
        assert {"path", "content"}.issubset(required)

    async def test_skillrunner_toolbroker_policy_approval_chain(self, tmp_path: Path) -> None:
        """真实链路：SkillRunner -> ToolBroker -> Policy ask/approve -> tool execute。"""
        store_group, task_id = await _create_task(
            db_path=tmp_path / "f007.db",
            artifacts_dir=tmp_path / "artifacts",
            idempotency_key="f007-e2e-001",
        )

        broker = ToolBroker(
            event_store=store_group.event_store,
            artifact_store=store_group.artifact_store,
        )
        tool_meta = reflect_tool_schema(write_text)
        await broker.register(tool_meta, write_text)

        policy_engine = PolicyEngine(event_store=store_group.event_store)
        await policy_engine.startup()
        broker.add_hook(policy_engine.hook)

        model_client = QueueModelClient(
            outputs=[
                SkillOutputEnvelope(
                    content="先执行写文件",
                    complete=False,
                    tool_calls=[
                        {
                            "tool_name": "filesystem.write_text",
                            "arguments": {
                                "path": "/tmp/f007.txt",
                                "content": "hello-f007",
                            },
                        }
                    ],
                ),
                SkillOutputEnvelope(content="执行完成", complete=True),
            ]
        )

        manifest = SkillManifest(
            skill_id="feature007.integration",
            version="0.1.0",
            input_model=DemoInput,
            output_model=SkillOutputEnvelope,
            model_alias="main",
            tools_allowed=["filesystem.write_text"],
            tool_profile=ToolProfile.STANDARD,
        )

        runner = SkillRunner(
            model_client=model_client,
            tool_broker=broker,
            event_store=store_group.event_store,
        )

        context = SkillExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="feature007-test",
        )

        async def approve_later() -> None:
            for _ in range(200):
                pending = policy_engine.approval_manager.get_pending_approvals()
                if pending:
                    await policy_engine.approval_manager.resolve(
                        pending[0].request.approval_id,
                        ApprovalDecision.ALLOW_ONCE,
                        resolved_by="user:test",
                    )
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("未在预期时间内发现待审批请求")

        approver = asyncio.create_task(approve_later())
        result = await runner.run(
            manifest=manifest,
            execution_context=context,
            skill_input={"request": "执行不可逆写入"},
            prompt="请按步骤执行",
        )
        await approver

        assert result.status == SkillRunStatus.SUCCEEDED
        assert result.output is not None
        assert result.output.content == "执行完成"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [e.type.value for e in events]

        assert "POLICY_DECISION" in event_types
        assert "APPROVAL_REQUESTED" in event_types
        assert "APPROVAL_APPROVED" in event_types
        assert "TOOL_CALL_STARTED" in event_types
        assert "TOOL_CALL_COMPLETED" in event_types

        await store_group.conn.close()
