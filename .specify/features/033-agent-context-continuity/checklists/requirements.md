# Requirements Checklist: Feature 033 Agent Profile + Bootstrap + Context Continuity

- [x] 明确问题不是 Memory Core 缺失，而是主 Agent 没有 consume profile/bootstrap/context/memory 的运行时主链
- [x] 明确要求新增正式 `AgentProfile` / `OwnerProfile` / `BootstrapSession` / `ContextFrame`
- [x] 明确 short-term continuity 与 long-term Memory 的分层边界
- [x] 明确运行时必须接入 `TaskService -> LLMService` 真链路
- [x] 明确不得绕过既有 `MemoryService` / `WriteProposal` / ToolBroker / Policy / Event 审计边界
- [x] 明确 control plane 需要展示 provenance 与 degraded reason
- [x] 明确 project/profile/memory/bootstrap 的隔离要求
- [x] 明确需要真实 wiring 的 unit/integration/e2e 测试矩阵
- [x] 明确与 Feature 031 release gates 的衔接
