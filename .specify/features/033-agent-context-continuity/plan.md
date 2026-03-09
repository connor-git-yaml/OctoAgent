# Implementation Plan: Feature 033 Agent Profile + Bootstrap + Context Continuity

## 1. 目标与交付姿态

033 不是单纯补一个“记忆搜索 API”，而是把主 Agent 的真实上下文链补齐。实现完成后，OctoAgent 的主聊天、automation、delegation 和 worker 至少要共享同一套 canonical context semantics：

1. `project/workspace` 提供作用域和默认绑定
2. `AgentProfile` 提供 persona / model / tool / policy / memory hints
3. `OwnerProfile + BootstrapSession` 提供 owner/assistant 的最小身份与协作偏好
4. `SessionContextState` 提供 recent turns / rolling summary / artifact refs
5. `MemoryService` 提供 long-term retrieval / evidence
6. `ContextFrame` 将上面几层收敛为一次可执行、可恢复、可审计的输入快照

## 2. 设计决策

### 2.1 正式对象优先，不再靠隐式 markdown 当真相源

- 借鉴 OpenClaw 的 `BOOTSTRAP.md` / `AGENTS.md` 用户体验，但 canonical truth 不放在文本文件里
- `AgentProfile`、`OwnerProfile`、`BootstrapSession`、`ContextFrame` 放入正式 store
- 如需 workspace 可读文件，只作为 export/materialized view，不作为主真相源

### 2.2 短期上下文与长期 Memory 分层

- **短期 continuity**：recent turns、rolling summary、recent artifact refs，放在 `SessionContextState`
- **长期 memory**：继续使用 `packages/memory` 的 SoR / Fragments / Vault / MemU 路径
- context assembler 同时读取两层，但只有“值得沉淀的长期事实”才走 `WriteProposal`

### 2.3 运行时接线点必须前移到 Task/Session 入口

要避免假实现，context assembly 不能停留在 control plane 或 worker preflight：

- `TaskService.process_task_with_llm()` 之前必须拿到 resolved `ContextFrame`
- `LLMService.call()` 或其上游必须接收结构化 prompt/context envelope
- `DelegationPlaneService` / `AutomationSchedulerService` 创建 work/run 时也要挂上 `context_frame_id`

### 2.4 Profile 继承链要变成正式 contract

- `Project.default_agent_profile_id`
- `Session.selected_agent_profile_id`
- `AutomationJob.agent_profile_id`
- `Work.agent_profile_id`
- `EffectiveAgentConfigSnapshot`

任何 override 都必须明确记录来源和被覆盖字段，禁止再靠 `metadata` 临时拼。

### 2.5 控制面既要能看，也要能调试 provenance

Feature 026 的 control plane 不重做，但要增量发布：

- `agent_profiles`
- `owner_profile`
- `bootstrap_session`
- `context_sessions`
- `context_frames`

重点不是“再加一个表格”，而是让 operator 能看懂某次响应为什么会这样回答。

## 3. 模块分解

### Phase A - Domain & Durability

- `packages/core/src/octoagent/core/models/agent_context.py`
- `packages/core/src/octoagent/core/store/agent_profile_store.py`
- `packages/core/src/octoagent/core/store/context_frame_store.py`

职责：

- AgentProfile / OwnerProfile / BootstrapSession / SessionContextState / ContextFrame 模型
- SQLite durable store
- project default profile binding 与 revision/version

### Phase B - Bootstrap & Profile Runtime

- `packages/provider/src/octoagent/provider/dx/bootstrap_identity_service.py`
- `packages/provider/src/octoagent/provider/dx/profile_commands.py`
- `apps/gateway/src/octoagent/gateway/services/bootstrap_runtime.py`

职责：

- 首启 bootstrap session
- CLI / Web / chat surface 共用 bootstrap session
- owner/assistant basics 的 canonical 更新与导出

### Phase C - Context Assembly

- `apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `apps/gateway/src/octoagent/gateway/services/session_context.py`
- `packages/memory/src/...` 仅复用现有 read path，不重做 memory core

职责：

- recent turn projection / rolling summary
- memory retrieval plan
- budget / degrade handling
- 生成 ContextFrame

### Phase D - Runtime Wiring

- `apps/gateway/src/octoagent/gateway/services/task_service.py`
- `apps/gateway/src/octoagent/gateway/services/orchestrator.py`
- `apps/gateway/src/octoagent/gateway/services/delegation_plane.py`
- `apps/gateway/src/octoagent/gateway/services/automation_scheduler.py`
- `apps/gateway/src/octoagent/gateway/services/llm_service.py`

职责：

- 主 Agent 真正消费 context frame
- automation / work / pipeline / worker 继承 snapshot
- runtime audit events

### Phase E - Control Plane & Surfaces

- `apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `apps/gateway/src/octoagent/gateway/routes/control_plane.py`
- `frontend/src/pages/ControlPlane.tsx`

职责：

- profiles/bootstrap/context 资源
- provenance 展示
- bootstrap resume / profile switch / context refresh 动作

## 4. Backend / Frontend 边界

### Backend 负责

- canonical object
- effective context resolution
- durable recent summary
- memory retrieval + provenance
- runtime event emission
- action semantics / surface parity

### Frontend 负责

- 展示 current profile / bootstrap state / context provenance
- 提供 bootstrap resume / profile switch / context refresh 的 operator 入口
- 在 session/task 详情里解释 degraded reason，而不是拼 prompt

## 5. 测试策略

### 5.1 单元测试

- `test_agent_profile_store.py`
- `test_context_frame_store.py`
- `test_agent_context_service.py`
- `test_bootstrap_identity_service.py`

覆盖：

- 继承链解析
- budget slicing
- degrade path
- bootstrap step 状态机

### 5.2 集成测试

- `test_task_service_context_integration.py`
- `test_delegation_context_inheritance.py`
- `test_automation_context_snapshot.py`
- `test_control_plane_context_api.py`

覆盖：

- 主 Agent 真正消费 context frame
- work / automation / pipeline 持有 context refs
- control plane 可读 provenance

### 5.3 E2E

- 首聊 bootstrap -> 第二轮 continuity -> 重启恢复
- 双 project 隔离
- automation/delegation 继承

### 5.4 反假实现门禁

033 必须新增至少一组测试，直接断言：

1. 没有 context frame 时响应进入 degraded path
2. 有 context frame 时 LLM 输入包含 profile/bootstrap/recency/memory 摘要
3. 进程重启后仍能恢复 recent summary，而不是依赖进程内 `_histories`

## 6. 风险与回退

- 如果 bootstrap 设计成硬阻塞，会损伤首聊体验；因此选择 safe default + explicit follow-up
- 如果 recent summary 和 long-term memory 混为一谈，会把 020/027 的治理边界打乱
- 如果让 frontend 直接拼 context 展示逻辑，会和 backend 语义漂移；必须以后端资源为准

## 7. 与 Feature 031 的衔接

031 当前的 M3 release gate artifacts 需要同步补记一条 carry-forward gate：

- `GATE-M3-CONTEXT-CONTINUITY`

内容至少包括：

- 首聊 bootstrap 完成
- 多轮对话 continuity 成立
- 重启后 continuity 不丢
- 跨 project 不串 profile/memory
- context provenance 可见
