# Tech Research - Feature 037

## 当前代码问题

### 1. 运行态控制信息分散

- `DelegationPlaneService` 在 `Work.metadata.request_context` 中保存 trace/hop/model/tool_profile 等信息。
- `DispatchEnvelope.metadata` 继续保存 `work_id/pipeline_run_id/agent_profile_id/context_frame_id` 等字符串字段。
- `ExecutionRuntimeContext` 只知道 execution session，本身不理解 project/workspace/session/profile/frame lineage。

结果：

- 运行期需要在多个位置“拼回”完整上下文。
- 字段语义不统一，新增链路时容易漏传或传歪。

### 2. Context resolve 可能重新读取 live selector

- `AgentContextService._resolve_project_scope()` 在缺少显式 binding 时，会继续读取当前 selector。
- 对 deferred / queued / delegated task 来说，执行时的 selector 可能已经和派发时不同。

结果：

- project/workspace/profile/memory/session 可能在执行阶段漂移。

### 3. typed resolver contract 未真正进入主链

- `ContextResolveRequest / ContextResolveResult` 已存在，但主链还是手工拼 project/profile/bootstrap/session/memory。
- request snapshot 只体现 frame 基本信息，不体现 resolve lineage。

## 技术策略

### RuntimeControlContext

定义一个跨 service 传递的正式模型，冻结：

- task / trace / hop
- surface / scope / thread / session
- project / workspace
- work / pipeline
- agent_profile / context_frame

### Resolver 优先级

`runtime snapshot hints > task.scope binding > live selector > default project`

这样可以保证：

- 新路径具备确定性
- 旧路径继续兼容

### Writeback 优先级

`context_frame.session/project/workspace > live selector fallback`

这样 response summary 会回写到真正消费过的 session 上。
