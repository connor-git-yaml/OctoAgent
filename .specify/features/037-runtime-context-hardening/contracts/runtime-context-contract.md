# Runtime Context Contract

## RuntimeControlContext

最小字段：

- `task_id`
- `trace_id`
- `contract_version`
- `surface`
- `scope_id`
- `thread_id`
- `session_id`
- `project_id`
- `workspace_id`
- `hop_count`
- `max_hops`
- `worker_capability`
- `route_reason`
- `model_alias`
- `tool_profile`
- `work_id`
- `parent_work_id`
- `pipeline_run_id`
- `agent_profile_id`
- `context_frame_id`
- `metadata`

## 传播链

1. `DelegationPlaneService.prepare_dispatch()` 冻结 `RuntimeControlContext`
2. 保存到 `Work.metadata.runtime_context`
3. 写入 `DispatchEnvelope.runtime_context`
4. 兼容透传到 `DispatchEnvelope.metadata.runtime_context_json`
5. `WorkerRuntime` 放入 `ExecutionRuntimeContext.runtime_context`
6. `TaskService.process_task_with_llm()` 继续传给 `AgentContextService`
7. `AgentContextService` 生成 `ContextResolveRequest` 和 request snapshot lineage

## 兼容策略

- 旧路径仍可只传 `dispatch_metadata`
- `TaskService` 会尝试从 `runtime_context_json` 回解析
- 缺失时回退到旧的 task/scope/selector 解析
