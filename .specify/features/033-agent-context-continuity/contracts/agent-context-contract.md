# Contract: Agent Context Continuity

## 1. 目标

定义 OctoAgent 主 Agent / automation / work / pipeline / worker 共享的 canonical context assembly contract，禁止再由各 runtime 通过零散 `metadata` 拼 prompt。

## 2. Resolve 输入

```python
class ContextResolveRequest(BaseModel):
    request_id: str
    request_kind: Literal["chat", "automation", "work", "pipeline", "worker", "bootstrap"]
    surface: str
    project_id: str
    workspace_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    work_id: str | None = None
    pipeline_run_id: str | None = None
    automation_run_id: str | None = None
    worker_run_id: str | None = None
    agent_profile_id: str | None = None
    owner_overlay_id: str | None = None
    trigger_text: str | None = None
    thread_id: str | None = None
    requester_id: str | None = None
    requester_role: str = "owner"
    input_artifact_refs: list[str] = Field(default_factory=list)
    delegation_metadata: dict[str, Any] = Field(default_factory=dict)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
```

约束：

- `chat` 请求通常带 `session_id` 与 `trigger_text`，但 `thread_id` / `requester_id` 仅在对应 surface 存在时提供。
- `automation` / `work` / `pipeline` / `worker` 请求 MAY 没有聊天线程，也 MAY 没有直接的人类 requester。
- resolver MUST NOT 为了复用同一 contract 伪造聊天字段；没有的字段必须保持 `None`，并通过 `request_kind` + `runtime_metadata` 表达触发来源。
- 至少需要存在一种真实触发载荷：`trigger_text`、`input_artifact_refs`、`delegation_metadata` 或 `runtime_metadata` 之一。

## 3. Resolve 输出

```python
class ContextResolveResult(BaseModel):
    context_frame_id: str
    effective_agent_profile_id: str
    effective_owner_overlay_id: str | None = None
    owner_profile_revision: int | None = None
    bootstrap_session_id: str | None = None
    system_blocks: list[dict[str, Any]]
    recent_summary: str
    memory_hits: list[dict[str, Any]]
    degraded_reason: str = ""
    source_refs: list[dict[str, Any]]
```

## 4. 运行时约束

- `TaskService` MUST 在实际 LLM 调用前拿到 `ContextResolveResult`
- `LLMService` / `SkillRunner` MUST 消费 `system_blocks + recent_summary + memory_hits`
- `Work` / `AutomationRun` / `PipelineRun` MUST 持有 `context_frame_id` 或等价 snapshot ref
- scheduler / delegation / worker runtime MUST 直接调用同一 resolver，不得通过伪造 `user_text` / `thread_id` 冒充聊天请求
- 当 `memory_hits=[]` 且 `recent_summary=""` 时，不得静默表现为“正常完整上下文”；必须写入 degraded reason

## 5. 控制面资源

- `agent_profiles`
- `owner_profile`
- `owner_overlays`
- `bootstrap_session`
- `context_sessions`
- `context_frames`

这些资源 MUST 统一暴露 provenance、degraded reason、revision/version。
