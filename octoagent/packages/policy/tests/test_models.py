"""Feature 061: ApprovalOverride 数据模型测试"""

from datetime import UTC, datetime

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
        before = datetime.now(UTC)
        override = ApprovalOverride.create(
            agent_runtime_id="agent-1",
            tool_name="docker.run",
        )
        assert override.agent_runtime_id == "agent-1"
        assert override.tool_name == "docker.run"
        assert override.decision == "always"
        assert override.id is None
        # 验证时间戳格式
        parsed = datetime.fromisoformat(override.created_at)
        assert parsed >= before

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
