"""WriteResult 通用写入回显契约（Feature 084 Phase 2 — FR-2.4/FR-2.7）。

所有 produces_write=True 工具的返回类型必须是 WriteResult 或其子类。
覆盖：config / mcp / delegation / filesystem / memory / behavior / canvas / user_profile。
不覆盖：browser.* / terminal.exec / tts.speak（执行类副作用，语义上不产生持久化写入物）。

Constitution C3 要求：工具 schema 与代码签名一致（单一事实源）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class WriteResult(BaseModel):
    """所有写入型工具（produces_write=True）的统一返回基类（Constitution C3）。

    status 语义：
    - "written"：同步写入成功
    - "skipped"：操作未执行（内容无变化等非错误跳过）
    - "rejected"：被 ThreatScanner 或权限拦截
    - "pending"：异步操作已提交（如 mcp.install 启动 npm/pip job）
    """

    status: Literal["written", "skipped", "rejected", "pending"]
    target: str
    """写入目标：文件绝对路径 / DB 表名 / 异步 job ID / 工具特定标识。"""

    bytes_written: int | None = None
    preview: str | None = None
    """写入内容前 200 字符摘要，status=written 时推荐填写。"""

    mtime_iso: str | None = None
    """写入后的 ISO 8601 mtime，仅文件类写入时填写。"""

    reason: str | None = None
    """status 非 written 时说明原因；pending 时说明异步 job 信息。"""

    @field_validator("preview", mode="before")
    @classmethod
    def truncate_preview(cls, v: str | None) -> str | None:
        """确保 preview 长度 ≤ 200 字符。"""
        if v is not None and len(v) > 200:
            return v[:200]
        return v

    @field_validator("reason", mode="after")
    @classmethod
    def reason_required_when_not_written(cls, v: str | None, info: Any) -> str | None:
        """status 非 written 时 reason 必填（不抛，仅收集 warning；实际 None 返回）。"""
        return v


# ---------------------------------------------------------------------------
# Config 工具子类
# ---------------------------------------------------------------------------

class ConfigAddProviderResult(WriteResult):
    """config.add_provider 写入结果。"""

    provider_id: str
    action: Literal["added", "updated"]
    hint: str = ""


class ConfigSetModelAliasResult(WriteResult):
    """config.set_model_alias 写入结果。"""

    alias: str
    provider: str
    model: str
    hint: str = ""


class ConfigSyncResult(WriteResult):
    """config.sync 写入结果。"""

    enabled_providers: list[str] = []
    enabled_aliases: list[str] = []


class SetupQuickConnectResult(WriteResult):
    """setup.quick_connect 写入结果。

    F19 修复：保留旧 tool 返回的全部 canonical envelope 字段（success / code /
    activation / resource_refs / review），调用方依赖这些字段做激活检查、
    resource refresh 与状态展示。
    """

    provider_id: str = ""
    alias: str = ""
    hint: str = ""
    # F19 修复：保留旧 tool envelope 字段（防压扁回归）
    success: bool = False                          # 旧 payload 的 "success" 字段
    code: str | None = None                        # error code（如 "INVALID_DRAFT_JSON"）
    review: dict[str, Any] | None = None           # 配置审视结果
    activation: dict[str, Any] | None = None       # 含 proxy_url / restart 等激活信息
    resource_refs: list[str] = Field(default_factory=list)  # 触发的 resource refresh 清单


# ---------------------------------------------------------------------------
# MCP 工具子类
# ---------------------------------------------------------------------------

class McpInstallResult(WriteResult):
    """mcp.install 写入结果。

    异步路径（npm/pip install）：status="pending" + task_id 非空，调用方通过 mcp.install_status 追踪。
    同步路径（本地配置写入完成）：status="written" + server_name 非空。
    防 F14 回归：task_id 在 pending 时必须非空，确保追踪链路不断裂。
    """

    server_name: str | None = None
    """本地注册时的 server 名称（local 模式）。"""

    task_id: str | None = None
    """status="pending" 时此字段必须非空（异步安装 job ID）。"""


class McpUninstallResult(WriteResult):
    """mcp.uninstall 写入结果。"""

    server_id: str | None = None


# ---------------------------------------------------------------------------
# Delegation / Work 工具子类
# ---------------------------------------------------------------------------

class ChildSpawnInfo(BaseModel):
    """subagents.spawn 单个子任务信息。"""

    task_id: str
    work_id: str = ""
    session_id: str = ""
    worker_type: str = ""
    objective: str = ""
    tool_profile: str = ""
    parent_task_id: str = ""
    parent_work_id: str = ""
    target_kind: str = ""
    title: str = ""
    thread_id: str = ""
    worker_plan_id: str = ""


class SubagentsSpawnResult(WriteResult):
    """subagents.spawn 写入结果。保留 requested / created / children 关联键（防 F4 压扁）。"""

    requested: int
    created: int
    children: list[ChildSpawnInfo] = []


class WorkSnapshot(BaseModel):
    """work 状态快照，供 kill/merge/delete 结果携带。"""

    work_id: str
    status: str
    title: str = ""


class SubagentsKillResult(WriteResult):
    """subagents.kill 写入结果。保留 task_id / work_id / runtime_cancelled（防 F4 压扁）。"""

    task_id: str
    work_id: str
    runtime_cancelled: bool
    work: WorkSnapshot | None = None


class SubagentsSteerResult(WriteResult):
    """subagents.steer 写入结果。保留所有关联键（防 F4 压扁）。"""

    session_id: str
    request_id: str
    artifact_id: str | None = None
    delivered_live: bool = False
    approval_id: str | None = None
    execution_session: Any | None = None


class WorkMergeResult(WriteResult):
    """work.merge 写入结果。"""

    child_work_ids: list[str] = []
    merged: WorkSnapshot | None = None


class WorkDeleteResult(WriteResult):
    """work.delete 写入结果。"""

    child_work_ids: list[str] = []
    deleted: WorkSnapshot | None = None


# ---------------------------------------------------------------------------
# Memory 工具子类
# ---------------------------------------------------------------------------

class MemoryWriteResult(WriteResult):
    """memory.write 写入结果。保留 memory_id / version / action / scope_id（防 F4 压扁）。"""

    memory_id: str
    version: int
    action: Literal["create", "update", "append"]
    scope_id: str


# ---------------------------------------------------------------------------
# Filesystem / Behavior / Canvas 工具子类
# ---------------------------------------------------------------------------

class FilesystemWriteTextResult(WriteResult):
    """filesystem.write_text 写入结果。"""

    workspace_root: str = ""
    path: str = ""
    """相对于 workspace_root 的文件路径。"""

    created_dirs: bool = False


class BehaviorWriteFileResult(WriteResult):
    """behavior.write_file 写入结果。"""

    file_id: str
    written: bool = True
    chars_written: int = 0
    budget_chars: int = 0
    proposal: bool = False
    """True 时表示返回的是 proposal（需用户确认），不是实际写入。"""

    onboarding_completed: bool = False


class CanvasWriteResult(WriteResult):
    """canvas.write 写入结果。保留 artifact_id / task_id（防 F4 压扁）。"""

    artifact_id: str
    task_id: str


# ---------------------------------------------------------------------------
# Graph Pipeline 工具子类（F15 修复：从执行类移入写入类）
# ---------------------------------------------------------------------------

class GraphPipelineResult(WriteResult):
    """graph_pipeline 写入结果（F15 修复）。

    graph_pipeline.start 会创建 Task / 保存 Work / commit SQLite 事务 / 启动后台 run，
    是真实持久化 state 写入，必须走 WriteResult 契约让调用方稳定追踪 run_id/task_id。

    status 语义：
    - "pending" + run_id + task_id：start 成功，后台 run 在执行
    - "written"：resume / cancel / retry / list / status 同步操作完成
    - "rejected" + reason：参数非法 / 状态机不允许
    """

    action: Literal["start", "resume", "cancel", "retry"]
    run_id: str | None = None
    """启动的 pipeline run ID；start 时返回，后续 action 必填。"""

    task_id: str | None = None
    """启动的 child task ID，用于追踪进度。"""

    detail: str = ""
    """完整文本内容（list / status 的人类可读输出），不受 preview 200 字符限制。"""


# ---------------------------------------------------------------------------
# User Profile 工具子类（Phase 2 新增）
# ---------------------------------------------------------------------------

class UserProfileUpdateResult(WriteResult):
    """user_profile.update 写入结果（Phase 2 新增）。"""

    blocked: bool = False
    pattern_id: str | None = None
    approval_requested: bool = False


class ObserveResult(WriteResult):
    """user_profile.observe 写入结果（写入 observation_candidates 队列）。

    与 spec FR-2.7 / contracts/tools-contract.md 对齐：
    - queued: 是否成功入队（语义比 status="written" 更直接，供 LLM/UI 判断）
    - candidate_id: 入队成功时的候选 ID（None 表示未入队）
    - dedup_hit: 是否命中 dedup（source_turn_id + fact_content_hash）
    """

    queued: bool = False
    candidate_id: str | None = None
    dedup_hit: bool = False


# ---------------------------------------------------------------------------
# delegate_task 工具子类（Feature 084 Phase 3 T045）
# ---------------------------------------------------------------------------


class DelegateTaskResult(WriteResult):
    """delegate_task 工具返回结果（FR-5 / T045）。

    async 模式：status="pending" + child_task_id，立即返回不等待子任务完成。
    sync 模式：等待子任务完成后返回 status="written"；超时返回 status="skipped" + reason。
    失败时：status="rejected" + reason（depth_exceeded / CAPACITY_EXCEEDED / blacklist_blocked）。

    child_task_id 保留供调用方追踪子任务状态（防 F14 追踪链路断裂模式）。
    """

    child_task_id: str | None = None
    """新创建的子任务 ID；派发失败时为 None。"""

    target_worker: str = ""
    """目标 Worker 名称（回显，方便调用方确认）。"""

    callback_mode: str = "async"
    """回调模式（async/sync）。"""


# ---------------------------------------------------------------------------
# 模块导出
# ---------------------------------------------------------------------------

__all__ = [
    "WriteResult",
    # Config
    "ConfigAddProviderResult",
    "ConfigSetModelAliasResult",
    "ConfigSyncResult",
    "SetupQuickConnectResult",
    # MCP
    "McpInstallResult",
    "McpUninstallResult",
    # Delegation / Work
    "ChildSpawnInfo",
    "SubagentsSpawnResult",
    "WorkSnapshot",
    "SubagentsKillResult",
    "SubagentsSteerResult",
    "WorkMergeResult",
    "WorkDeleteResult",
    # Memory
    "MemoryWriteResult",
    # Filesystem / Behavior / Canvas
    "FilesystemWriteTextResult",
    "BehaviorWriteFileResult",
    "CanvasWriteResult",
    # Pipeline
    "GraphPipelineResult",
    # User Profile
    "UserProfileUpdateResult",
    "ObserveResult",
    # Delegate Task（Feature 084 Phase 3）
    "DelegateTaskResult",
]
