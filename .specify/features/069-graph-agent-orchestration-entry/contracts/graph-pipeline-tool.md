# 契约：graph_pipeline LLM 工具

**Feature**: 065
**模块**: `octoagent/packages/skills/src/octoagent/skills/pipeline_tool.py`

---

## 工具元数据

```python
@tool_contract(
    side_effect_level=SideEffectLevel.IRREVERSIBLE,  # 取各 action 的最高级别
    tool_profile=ToolProfile.FULL,
    tool_group="orchestration",
    tool_tier=ToolTier.CORE,
    description="发现、启动、监控和管理确定性 Pipeline 流程。",
)
```

## 工具签名

```python
async def graph_pipeline(
    *,
    action: str,              # 必填：list / start / status / resume / cancel / retry
    pipeline_id: str = "",    # start 时必填
    run_id: str = "",         # status / resume / cancel / retry 时必填
    params: dict = {},        # start 时可选（Pipeline 输入参数）
    input_data: dict = {},    # resume 时可选（WAITING_INPUT 时提供）
    approved: bool | None = None,  # resume 时可选（WAITING_APPROVAL 时提供）
    # --- 内部注入（不暴露给 LLM schema） ---
    task_id: str = "",        # 调用者 Task ID（由 CapabilityPack 注入）
    session_metadata: dict = {},  # 当前 session metadata
) -> str:
```

## Action 详细契约

### action="list"

**输入**: 无额外参数
**副作用等级**: none
**返回格式**:
```
Available Pipelines (3):

1. deploy-staging
   Description: 将代码部署到 staging 环境
   Tags: deploy, staging, ci-cd
   Trigger: 当用户要求部署到 staging、预发布环境时使用
   Input: branch (string, required), skip_tests (boolean, default=false)

2. ...

Use graph_pipeline(action="start", pipeline_id="<id>", params={...}) to start a pipeline.
```

**空列表时**:
```
No pipelines available. Pipeline definitions are loaded from PIPELINE.md files.
```

### action="start"

**输入**:
- `pipeline_id` (必填): Pipeline 名称
- `params` (可选): Pipeline 输入参数

**副作用等级**: irreversible
**前置验证**:
1. `pipeline_id` 存在于 PipelineRegistry → 否则返回 `"Error: pipeline not found: '{pipeline_id}'. Use graph_pipeline(action='list') to see available pipelines."`
2. `params` 符合 `input_schema`（required 字段必须提供，类型匹配） → 否则返回 `"Error: invalid params for pipeline '{pipeline_id}': missing required field 'branch'."`
3. 并发 run 数量未超限（默认 10） → 否则返回 `"Error: maximum concurrent pipeline runs reached (10). Cancel or wait for existing runs to complete."`

**成功返回**:
```
Pipeline 'deploy-staging' started successfully.
run_id: 01JARQF5X0000000000000000
task_id: 01JARQF5X1111111111111111

The pipeline is running in the background. Use graph_pipeline(action="status", run_id="01JARQF5X0000000000000000") to check progress.
```

**内部行为**:
1. 从 PipelineRegistry 获取 PipelineManifest
2. 创建 Child Task（parent_task_id = 调用者 task_id）
3. 创建 Work（target_kind=GRAPH_AGENT, pipeline_run_id=run_id）
4. 快照 definition 到 run.metadata["definition_snapshot"]
5. asyncio.create_task() 后台启动 Engine.start_run()
6. 立即返回 run_id

### action="status"

**输入**:
- `run_id` (必填): Pipeline run ID

**副作用等级**: none
**返回格式**:
```
Pipeline Run Status:
  run_id: 01JARQF5X0000000000000000
  pipeline: deploy-staging
  status: RUNNING
  current_node: build (构建项目)
  completed_nodes: pull-code ✓
  pending_nodes: run-tests, deploy-gate, deploy, health-check
  started_at: 2026-03-19T10:30:00Z
  elapsed: 2m 15s
```

**暂停状态时额外信息**:
```
  status: WAITING_APPROVAL
  current_node: deploy-gate (部署审批)
  waiting_for: Human approval required
  resume_command: graph_pipeline(action="resume", run_id="...", approved=true)
```

**run_id 不存在时**:
```
Error: pipeline run not found: '01JARQF5X0000000000000000'.
```

### action="resume"

**输入**:
- `run_id` (必填)
- `input_data` (可选): WAITING_INPUT 时提供的数据
- `approved` (可选): WAITING_APPROVAL 时的审批决定

**副作用等级**: irreversible
**前置验证**:
1. run 存在且状态为 WAITING_INPUT 或 WAITING_APPROVAL → 否则返回错误
2. WAITING_INPUT 时 `input_data` 不为空 → 否则提示需要提供 input_data
3. WAITING_APPROVAL 时 `approved` 不为 None → 否则提示需要提供 approved

**approved=false 时**:
- Pipeline 转 CANCELLED，Task 转 CANCELLED
- 返回 `"Pipeline run cancelled. The approval for node 'deploy-gate' was denied."`

**成功返回**:
```
Pipeline run resumed successfully.
run_id: 01JARQF5X0000000000000000
The pipeline continues from node 'deploy'. Use graph_pipeline(action="status", ...) to check progress.
```

### action="cancel"

**输入**:
- `run_id` (必填)

**副作用等级**: reversible
**前置验证**: run 存在且不在终态

**成功返回**:
```
Pipeline run cancelled.
run_id: 01JARQF5X0000000000000000
Note: Side effects from already completed nodes are not reverted.
```

### action="retry"

**输入**:
- `run_id` (必填)

**副作用等级**: irreversible
**前置验证**: run 存在且状态为 FAILED

**成功返回**:
```
Retrying current node 'deploy' for pipeline run 01JARQF5X0000000000000000.
The pipeline continues in the background. Use graph_pipeline(action="status", ...) to check progress.
```

---

## 错误响应格式

所有错误返回以 `"Error: "` 前缀开头，后跟人类可读的错误描述。不抛异常，不返回 stack trace。

```
Error: pipeline not found: 'non-existent'. Use graph_pipeline(action='list') to see available pipelines.
Error: invalid params for pipeline 'deploy-staging': missing required field 'branch'.
Error: pipeline run not found: '01JARQF5X0000000000000000'.
Error: cannot resume pipeline run '01JARQF5X0000000000000000': current status is RUNNING (expected WAITING_INPUT or WAITING_APPROVAL).
Error: cannot cancel pipeline run '01JARQF5X0000000000000000': already in terminal status SUCCEEDED.
Error: cannot retry pipeline run '01JARQF5X0000000000000000': current status is RUNNING (expected FAILED).
Error: maximum concurrent pipeline runs reached (10). Cancel or wait for existing runs to complete.
Error: unknown action 'invalid'. Supported actions: list, start, status, resume, cancel, retry.
```
