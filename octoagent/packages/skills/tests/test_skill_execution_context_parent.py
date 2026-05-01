"""SkillExecutionContext.parent_task_id 字段单测。

从 apps/gateway/tests/test_subagent_executor.py 迁移（F087 followup 死代码清理后
TestModelExtensions 中 SkillExecutionContext 部分与 SubagentExecutor 解耦，
独立保留；SkillManifest.heartbeat_interval_steps / max_concurrent_subagents
两个孤儿字段已一并删除，对应测试不迁移）。
"""

from __future__ import annotations

from octoagent.skills.models import SkillExecutionContext, UsageLimits


class TestSkillExecutionContextParent:
    """验证 SkillExecutionContext parent_task_id 字段。"""

    def test_execution_context_parent_task_id_default(self) -> None:
        ctx = SkillExecutionContext(
            task_id="t1",
            trace_id="tr1",
            usage_limits=UsageLimits(),
        )
        assert ctx.parent_task_id is None

    def test_execution_context_parent_task_id_set(self) -> None:
        ctx = SkillExecutionContext(
            task_id="t1",
            trace_id="tr1",
            parent_task_id="parent-t",
            usage_limits=UsageLimits(),
        )
        assert ctx.parent_task_id == "parent-t"
