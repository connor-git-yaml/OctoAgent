"""Feature 064 数据模型扩展契约。

定义所有需要新增或扩展的字段，精确对应源码文件中的改动位置。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# ToolCallSpec 扩展（skills/models.py 第 157-161 行）
# ============================================================


class ToolCallSpecExtension(BaseModel):
    """ToolCallSpec 新增字段。

    位置: packages/skills/src/octoagent/skills/models.py::ToolCallSpec
    """

    tool_call_id: str = Field(
        default="",
        description=(
            "LLM 返回的 function call ID。"
            "Chat Completions 路径从 tool_calls[].id 填充；"
            "Responses API 路径从 function_call.call_id 填充。"
            "为空时回退到自然语言回填模式（FR-064-12 向后兼容）。"
        ),
    )


# ============================================================
# ToolFeedbackMessage 扩展（skills/models.py 第 178-187 行）
# ============================================================


class ToolFeedbackMessageExtension(BaseModel):
    """ToolFeedbackMessage 新增字段。

    位置: packages/skills/src/octoagent/skills/models.py::ToolFeedbackMessage

    由 SkillRunner._build_tool_feedback() 从 ToolCallSpec.tool_call_id 传递。
    在 LiteLLMSkillClient.generate() 回填时使用此 ID 关联 tool role message。
    """

    tool_call_id: str = Field(
        default="",
        description="对应 ToolCallSpec.tool_call_id，用于回填标准 tool role message",
    )


# ============================================================
# SkillExecutionContext 扩展（skills/models.py 第 138-154 行）
# ============================================================


class SkillExecutionContextExtension(BaseModel):
    """SkillExecutionContext 新增字段。

    位置: packages/skills/src/octoagent/skills/models.py::SkillExecutionContext
    """

    parent_task_id: str | None = Field(
        default=None,
        description="父任务 ID。Subagent 的 Child Task 通过此字段关联父 Task。",
    )


# ============================================================
# Task 扩展（core/models/task.py 第 28-46 行）
# ============================================================


class TaskExtension(BaseModel):
    """Task 新增字段。

    位置: packages/core/src/octoagent/core/models/task.py::Task

    DB Migration: ALTER TABLE tasks ADD COLUMN parent_task_id TEXT DEFAULT NULL
    索引: CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id)
    """

    parent_task_id: str | None = Field(
        default=None,
        description="父任务 ID（Subagent Child Task 用）。顶层 Task 为 None。",
    )


# ============================================================
# SkillManifest 扩展（skills/manifest.py）
# ============================================================


class SkillManifestExtension(BaseModel):
    """SkillManifest 新增字段。

    位置: packages/skills/src/octoagent/skills/manifest.py::SkillManifest

    这些字段也需要在 SkillManifestModel（skills/models.py 第 205-221 行）中同步添加。
    """

    # P2-A: 上下文压缩
    compaction_model_alias: str | None = Field(
        default=None,
        description=(
            "上下文压缩使用的 LLM model alias。"
            "None 表示使用默认 'compaction' alias（需在 LiteLLM Proxy 预配置）。"
        ),
    )
    compaction_threshold_ratio: float = Field(
        default=0.8,
        ge=0.1,
        le=1.0,
        description="触发压缩的 token 占比阈值（相对于模型上下文窗口）",
    )
    compaction_recent_turns: int = Field(
        default=8,
        ge=2,
        le=50,
        description="压缩时保留的最近对话轮次数",
    )

    # P1-A: Subagent 心跳
    heartbeat_interval_steps: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Subagent 心跳上报间隔（每 N 个 step 上报一次）",
    )

    # P1-A: Subagent 并发控制
    max_concurrent_subagents: int = Field(
        default=5,
        ge=1,
        le=20,
        description="单个 Worker 最大并发 Subagent 数",
    )


# ============================================================
# ToolBrokerProtocol 扩展（tooling/protocols.py 第 173-229 行）
# ============================================================


class ToolBrokerProtocolExtension:
    """ToolBrokerProtocol 新增方法签名。

    位置: packages/tooling/src/octoagent/tooling/protocols.py::ToolBrokerProtocol

    实现位置: packages/tooling/src/octoagent/tooling/broker.py::ToolBroker
    """

    # 新增方法:
    # async def get_tool_meta(self, tool_name: str) -> ToolMeta | None:
    #     """按名称查询工具元数据（含 SideEffectLevel）。
    #
    #     O(1) 复杂度，从内部 _registry 字典查找。
    #     返回 None 表示工具未注册。
    #
    #     Args:
    #         tool_name: 工具名称
    #
    #     Returns:
    #         ToolMeta 或 None
    #     """
    pass


# ============================================================
# SSEHub.broadcast() 扩展（gateway/services/sse_hub.py 第 45-63 行）
# ============================================================


class SSEHubBroadcastExtension:
    """SSEHub.broadcast() 签名变更。

    位置: apps/gateway/src/octoagent/gateway/services/sse_hub.py::SSEHub.broadcast

    原签名:
        async def broadcast(self, task_id: str, event: Event) -> None

    新签名:
        async def broadcast(
            self,
            task_id: str,
            event: Event,
            parent_task_id: str | None = None,  # 新增
        ) -> None

    行为变更:
        当 parent_task_id 不为 None 时，事件同时广播到 task_id 和 parent_task_id 的订阅者。
        用于 Subagent 生命周期事件冒泡到父 Task。
    """

    pass


# ============================================================
# EventType 枚举扩展（core/models/enums.py 第 71-185 行）
# ============================================================


class EventTypeExtension:
    """EventType 枚举新增成员。

    位置: packages/core/src/octoagent/core/models/enums.py::EventType

    新增:
        TOOL_BATCH_STARTED = "TOOL_BATCH_STARTED"    # 并行工具批次开始
        TOOL_BATCH_COMPLETED = "TOOL_BATCH_COMPLETED"  # 并行工具批次完成

    注: CONTEXT_COMPACTION_COMPLETED 已存在（第 79 行），无需新增。
    补充: CONTEXT_COMPACTION_FAILED = "CONTEXT_COMPACTION_FAILED"  # 压缩失败
    """

    pass
