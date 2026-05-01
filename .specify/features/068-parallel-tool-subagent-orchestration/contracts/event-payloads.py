"""Feature 064 新增事件 Payload Schema。

定义 TOOL_BATCH_STARTED / TOOL_BATCH_COMPLETED 和
CONTEXT_COMPACTION_COMPLETED 事件的 payload 契约。

⚠️ Status: 已退役（F087 followup 清理，2026-05-01）。Feature 064 整体
（含 SubagentExecutor / 事件 payload schema）已被 ``task_runner`` 路径替代；
本文件保留为历史契约证据。完整退役说明见 ../spec.md 顶部 banner。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ============================================================
# P0-A: 并行工具调用批次事件
# ============================================================


class ToolBatchStartedPayload(BaseModel):
    """TOOL_BATCH_STARTED 事件 payload。

    在 SkillRunner 并行分桶执行开始时发射。
    仅当 batch_size > 1 时才发射此事件（单工具不生成 BATCH 事件）。
    """

    batch_id: str = Field(description="批次唯一标识（ULID）")
    tool_names: list[str] = Field(description="本批次包含的工具名列表")
    execution_mode: str = Field(
        description="执行模式: 'parallel'（NONE 桶）/ 'serial'（REVERSIBLE 桶）/ 'gated_serial'（IRREVERSIBLE 桶）"
    )
    batch_size: int = Field(description="本批次工具数量", ge=1)
    agent_runtime_id: str = Field(default="", description="执行 Agent 的 runtime ID")
    skill_id: str = Field(default="", description="所属 Skill ID")

    # 分桶统计
    bucket_none_count: int = Field(default=0, description="NONE（并行）桶工具数", ge=0)
    bucket_reversible_count: int = Field(default=0, description="REVERSIBLE（串行）桶工具数", ge=0)
    bucket_irreversible_count: int = Field(default=0, description="IRREVERSIBLE（审批）桶工具数", ge=0)


class ToolBatchCompletedPayload(BaseModel):
    """TOOL_BATCH_COMPLETED 事件 payload。

    在 SkillRunner 并行分桶执行全部完成后发射。
    """

    batch_id: str = Field(description="批次唯一标识（与 STARTED 事件对应）")
    duration_ms: int = Field(description="批次总执行耗时（毫秒）", ge=0)
    success_count: int = Field(description="成功执行的工具数", ge=0)
    error_count: int = Field(description="失败的工具数", ge=0)
    total_count: int = Field(description="工具总数", ge=1)
    agent_runtime_id: str = Field(default="", description="执行 Agent 的 runtime ID")
    skill_id: str = Field(default="", description="所属 Skill ID")


# ============================================================
# P2-A: 上下文压缩事件
# ============================================================


class ContextCompactionCompletedPayload(BaseModel):
    """CONTEXT_COMPACTION_COMPLETED 事件 payload。

    EventType 已在 enums.py 中定义但未实现。本 Feature 实现该事件。
    """

    before_tokens: int = Field(description="压缩前估算 token 数", ge=0)
    after_tokens: int = Field(description="压缩后估算 token 数", ge=0)
    strategy_used: list[str] = Field(
        description="使用的压缩策略列表（按执行顺序）: 'truncate_large_output' / 'summarize_early_turns' / 'drop_oldest_summary'"
    )
    turns_before: int = Field(description="压缩前对话轮次数", ge=0)
    turns_after: int = Field(description="压缩后对话轮次数", ge=0)
    compaction_model_alias: str | None = Field(
        default=None, description="生成摘要使用的模型 alias（如果使用了 Level 2 策略）"
    )
    duration_ms: int = Field(description="压缩操作总耗时（毫秒）", ge=0)
    agent_runtime_id: str = Field(default="", description="执行 Agent 的 runtime ID")
    skill_id: str = Field(default="", description="所属 Skill ID")


class ContextCompactionFailedPayload(BaseModel):
    """CONTEXT_COMPACTION_FAILED 事件 payload（补充定义）。

    当压缩操作失败时发射。压缩失败后降级为简单截断（Constitution #6）。
    """

    error_message: str = Field(description="失败原因")
    fallback_strategy: str = Field(
        default="simple_truncation",
        description="降级策略: 'simple_truncation' / 'skip'",
    )
    before_tokens: int = Field(description="压缩前估算 token 数", ge=0)
    agent_runtime_id: str = Field(default="", description="执行 Agent 的 runtime ID")
    skill_id: str = Field(default="", description="所属 Skill ID")
