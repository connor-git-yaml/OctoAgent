# Data Model: Feature 043 Module Connection Trust-Boundary Hardening

## 1. NormalizedMessage

### 现状

```python
NormalizedMessage.metadata: dict[str, str]
```

### 043 目标

```python
NormalizedMessage.metadata: dict[str, str]
NormalizedMessage.control_metadata: dict[str, Any]
```

语义：

- `metadata`
  - 渠道输入 hints
  - 默认不进入 orchestrator/delegation 控制路径
- `control_metadata`
  - trusted control envelope
  - 允许进入 task runner / orchestrator / delegation / runtime context

## 2. UserMessagePayload

### 043 目标

```python
UserMessagePayload(
  text_preview: str,
  text_length: int,
  text: str,
  attachment_count: int,
  metadata: dict[str, str],
  control_metadata: dict[str, Any],
)
```

语义：

- `metadata` 只保存输入 hints
- `control_metadata` 保存当前轮 trusted control 指令

## 3. Control Metadata Registry

### turn-scoped keys

这些键只对最近一轮 USER_MESSAGE 生效，后续轮次未再次提供则自动失效：

- `agent_profile_id`
- `requested_worker_profile_id`
- `requested_worker_profile_version`
- `effective_worker_snapshot_id`
- `tool_profile`
- `requested_worker_type`
- `target_kind`
- `project_id`
- `workspace_id`
- `approval_id`
- `approval_token`
- `delegation_pause`

### task-scoped keys

这些键允许在同一 task 内跨 follow-up 继承，用于 lineage / retry 追溯：

- `parent_task_id`
- `parent_work_id`
- `spawned_by`
- `child_title`
- `worker_plan_id`
- `retry_source_task_id`
- `retry_action_source`
- `retry_actor_id`

### clear semantics

- 当 `control_metadata[key] = null` 或空字符串时，表示显式清除该 key
- `turn` 级 key 清除后，本轮及后续轮不再从旧事件回溯恢复
- `task` 级 key 清除后，在当前 task 生命周期内视为已删除

## 4. Dispatch Metadata Contract

043 后以下模型的 canonical metadata 都使用 `dict[str, Any]`：

- `OrchestratorRequest.metadata`
- `DispatchEnvelope.metadata`
- `A2ATaskPayload.metadata`

兼容字段保留：

- `selected_tools_json`
- `runtime_context_json`

新增 typed canonical 字段示例：

```json
{
  "selected_tools": ["workers.review", "subagents.spawn"],
  "tool_selection": {
    "mounted_tools": ["workers.review", "subagents.spawn"],
    "blocked_tools": [],
    "warnings": []
  },
  "requested_worker_profile_version": 3
}
```

## 5. Snapshot Partial Degrade Model

### 顶层 snapshot 扩展字段

```json
{
  "status": "ready | partial",
  "degraded_sections": ["memory", "imports"],
  "resource_errors": {
    "memory": {
      "code": "RESOURCE_DEGRADED",
      "message": "memory backend unavailable"
    }
  }
}
```

### section fallback document

失败资源仍返回对应 `resource_type` 的 document，但带：

- `status = "degraded"`
- `degraded.is_degraded = true`
- `degraded.reasons = ["RESOURCE_DEGRADED"]`
- `degraded.unavailable_sections = [section_id]`
- `warnings = [...]`

## 6. Prompt Runtime Summary

043 不再输出原始 `dispatch_metadata={...}`。

改为：

```text
RuntimeContext: worker_capability=research
runtime_snapshot=session_id=..., project_id=..., workspace_id=...
control_metadata_summary={
  "agent_profile_id": "...",
  "requested_worker_type": "research",
  "target_kind": "subagent",
  "tool_profile": "standard"
}
```

不进入 prompt 的字段：

- `approval_token`
- `selected_tools_json`
- `runtime_context_json`
- 任意未通过 sanitizer 的原始输入 metadata
