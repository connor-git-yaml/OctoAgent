"""Feature 061: ApprovalOverride 数据模型测试"""

from datetime import UTC

import pytest
from octoagent.policy.models import ApprovalOverride


class TestApprovalOverride:
    """ApprovalOverride 数据模型测试"""

    def test_construction(self) -> None:
        override = ApprovalOverride(
            agent_runtime_id="agent-1",
            tool_name="docker.run",
            created_at="2026-03-17T00:00:00+00:00",
        )
        assert override.agent_runtime_id == "agent-1"
        assert override.tool_name == "docker.run"
        assert override.decision == "always"
        assert override.id is None

    def test_create_factory(self) -> None:
        """create() 工厂方法正确生成 ISO 时间戳"""
        override = ApprovalOverride.create(
            agent_runtime_id="agent-1",
            tool_name="docker.run",
        )
        assert override.agent_runtime_id == "agent-1"
        assert override.tool_name == "docker.run"
        # F142 范式样例（dirty-equals）：①IsNow(delta=10) 替代手写
        # before/parsed>=before 时间窗比较（自带慢机容忍，pydantic-ai 同款抗
        # flaky 姿势）；②model_dump full-shape 相等钉住序列化契约（模型加字段
        # 本断言红）。函数级 importorskip 防御共享 venv 未装窗口（Codex spec
        # P2：上面的既有断言已执行，只 SKIP 增强段）。
        dirty_equals = pytest.importorskip("dirty_equals")
        assert override.model_dump() == {
            "id": None,
            "agent_runtime_id": "agent-1",
            "tool_name": "docker.run",
            "decision": "always",
            "created_at": dirty_equals.IsNow(iso_string=True, delta=10, tz=UTC),
        }

    def test_serialization_roundtrip(self) -> None:
        override = ApprovalOverride.create(
            agent_runtime_id="agent-1",
            tool_name="docker.run",
        )
        data = override.model_dump()
        restored = ApprovalOverride(**data)
        assert restored == override

    def test_with_id(self) -> None:
        override = ApprovalOverride(
            id=42,
            agent_runtime_id="agent-1",
            tool_name="docker.run",
            created_at="2026-03-17T00:00:00+00:00",
        )
        assert override.id == 42
