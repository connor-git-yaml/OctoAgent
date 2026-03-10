# Research Synthesis - Feature 037

## 综合判断

最优解不是继续给 metadata 打补丁，也不是直接重做 orchestrator，而是把“运行态控制上下文”提升成正式对象。

## 最终方案

- 新增 `RuntimeControlContext`
- 让 `DelegationPlane -> DispatchEnvelope -> WorkerRuntime -> TaskService -> AgentContextService` 共用这一对象
- 让 `AgentContextService` 用 `ContextResolveRequest` 驱动 resolve
- 让 response writeback 和 request snapshot 也沿用同一条 lineage

## 不做的事

- 不新建 runtime 专用 store
- 不在本次 Feature 内重做 control plane session projection
- 不移除旧 metadata 兼容字段
