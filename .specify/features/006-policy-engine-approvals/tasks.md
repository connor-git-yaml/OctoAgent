# Tasks: Feature 006 — Policy Engine + Approvals + Chat UI

**Input**: `.specify/features/006-policy-engine-approvals/` (spec.md, plan.md, data-model.md, contracts/policy-api.md)
**Prerequisites**: plan.md (required), spec.md (required), data-model.md, contracts/policy-api.md
**Branch**: `feat/006-policy-engine-approvals`
**Date**: 2026-03-02

**Tests**: spec.md 明确要求各 FR 有对应测试，因此每个 User Story Phase 包含测试任务。采用 Tests FIRST 策略（写测试 -> 确认失败 -> 实现）。

**Organization**: 任务按 User Story 分组，支持独立实现和测试。Setup/Foundational 阶段不绑定 Story，提供共享基础设施。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件、无依赖）
- **[USN]**: 所属 User Story（US1, US2, ... US9）
- 每个任务包含目标文件的精确路径

---

## Phase 1: Setup（项目脚手架）

**Purpose**: 创建 `packages/policy/` 包结构，初始化所有目录和 `__init__.py`。

- [x] T001 创建 `packages/policy/` 目录结构和 `__init__.py`: `packages/policy/__init__.py`, `packages/policy/evaluators/__init__.py`
- [x] T002 [P] 创建测试目录结构: `tests/unit/policy/__init__.py`, `tests/integration/__init__.py`, `tests/contract/__init__.py`
- [x] T003 [P] 创建前端组件目录: `frontend/src/components/ApprovalPanel/`, `frontend/src/components/ChatUI/`, `frontend/src/hooks/`

---

## Phase 2: Foundational — 数据模型与枚举

**Purpose**: 建立所有 Pydantic 模型、枚举定义和 Task 状态机扩展。全部后续 Phase 依赖此阶段。

**CRITICAL**: 此阶段完成前，任何 User Story 任务不可开始。

### 测试优先

- [x] T004 [P] 编写数据模型单元测试 in `tests/unit/policy/test_models.py` — 验证 PolicyAction 严格度排序、PolicyDecision 必填字段、ApprovalRecord 状态约束、PolicyProfile 默认值、Event Payload 模型序列化。覆盖 FR-005, FR-008, FR-013, FR-026。
- [x] T005 [P] 编写 TaskStatus 状态转换测试 in `tests/unit/policy/test_models.py` (task section) — 验证 3 条新转换规则: RUNNING->WAITING_APPROVAL, WAITING_APPROVAL->RUNNING, WAITING_APPROVAL->REJECTED。验证非法转换被拒绝。覆盖 FR-013。

### 实现

- [x] T006 [P] 实现 PolicyAction, ApprovalDecision, ApprovalStatus 枚举 in `packages/policy/models.py`。覆盖 FR-005, FR-008。
- [x] T007 [P] 扩展 EventType 枚举: 新增 POLICY_DECISION, APPROVAL_REQUESTED, APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_EXPIRED in `packages/core/models/enums.py`。覆盖 FR-026。
- [x] T008 [P] 扩展 TaskStatus: 激活 WAITING_APPROVAL + validate_transition() 新增 3 条规则 in `packages/core/models/enums.py` + `packages/core/models/task.py`。覆盖 FR-013。
- [x] T009 实现 PolicyDecision, PolicyStep 模型 in `packages/policy/models.py`（依赖 T006）。覆盖 FR-001, FR-002, FR-005。
- [x] T010 实现 PolicyProfile 模型 + DEFAULT/STRICT/PERMISSIVE 预置 Profile in `packages/policy/models.py`（依赖 T006）。覆盖 FR-005, FR-027。
- [x] T011 实现 ApprovalRequest, ApprovalRecord, ApprovalResolveRequest, ApprovalListItem 模型 in `packages/policy/models.py`（依赖 T006）。覆盖 FR-007, FR-008, FR-009, FR-018, FR-019。
- [x] T012 实现事件 Payload 模型: PolicyDecisionEventPayload, ApprovalRequestedEventPayload, ApprovalResolvedEventPayload, ApprovalExpiredEventPayload in `packages/policy/models.py`（依赖 T006）。覆盖 FR-006, FR-010, FR-012, FR-026。
- [x] T013 实现 PendingApproval 运行时模型 + SSEApprovalEvent 模型 in `packages/policy/models.py`（依赖 T011）。覆盖 FR-007, FR-022。
- [x] T014 实现 REST API 响应模型: ApprovalsListResponse, ApprovalResolveResponse, ChatSendRequest, ChatSendResponse in `packages/policy/models.py`（依赖 T011）。覆盖 FR-018, FR-019, FR-023。

**Checkpoint**: 全部数据模型就绪，T004/T005 测试全部通过。

---

## Phase 3: User Story 3 — Policy Pipeline 多层策略过滤与决策追溯 (Priority: P1)

**Goal**: 实现策略评估管道核心逻辑——纯函数 Pipeline + 2 层 Evaluator，每层决策附带来源标签，遵循"只收紧不放松"原则。

**Independent Test**: 构造不同 ToolProfile/SideEffectLevel 组合，调用 evaluate_pipeline()，验证决策和 label 正确。

**Why US3 先行**: US3 是 US1/US2 的共同前置依赖。Pipeline 是策略评估的核心引擎，US1（触发审批）和 US2（直接放行）都需要 Pipeline 产生决策。

### 测试优先

- [x] T015 [P] [US3] 编写 Pipeline 纯函数测试 in `tests/unit/policy/test_pipeline.py` — 测试: 空 steps 返回 allow, 单层 deny 短路, 多层"只收紧不放松", label 链完整性, 异常处理。覆盖 FR-001, FR-002, FR-003。
- [x] T016 [P] [US3] 编写 ProfileFilter 测试 in `tests/unit/policy/test_profile_filter.py` — 测试: standard 下 privileged 工具被拒, minimal 下 standard 工具被拒, 同级别工具放行, 防御性警告(EC-7)。覆盖 FR-004。
- [x] T017 [P] [US3] 编写 GlobalRule 测试 in `tests/unit/policy/test_global_rule.py` — 测试: none->allow, reversible->allow, irreversible->ask, strict profile 下 reversible->ask, label 格式验证。覆盖 FR-005。

### 实现

- [x] T018 [P] [US3] 实现 Layer 1: ProfileFilter evaluator in `packages/policy/evaluators/profile_filter.py` — profile_filter() 纯函数 + EC-7 防御性警告（依赖 T006, T009）。覆盖 FR-004。
- [x] T019 [P] [US3] 实现 Layer 2: GlobalRule evaluator in `packages/policy/evaluators/global_rule.py` — global_rule() 纯函数，基于 SideEffectLevel + PolicyProfile 映射（依赖 T006, T009, T010）。覆盖 FR-005。
- [x] T020 [US3] 实现 evaluate_pipeline() 纯函数 in `packages/policy/pipeline.py` — 逐层评估 + deny 短路 + "只收紧不放松" + trace 链（依赖 T009, T018, T019）。覆盖 FR-001, FR-002, FR-003。
- [x] T021 [US3] 导出 Pipeline 公共 API in `packages/policy/__init__.py` — 导出 evaluate_pipeline, profile_filter, global_rule, 以及所有模型。

**Checkpoint**: Pipeline 纯函数可独立测试，T015-T017 全部通过。US3 核心价值已交付。

---

## Phase 4: User Story 4 — Two-Phase Approval 竞态安全保障 (Priority: P1)

**Goal**: 实现 ApprovalManager 完整生命周期——幂等注册、异步等待、原子消费、超时自动 deny、宽限期、启动恢复。

**Independent Test**: 模拟并发注册/消费/重启场景，验证幂等性、原子性和持久化恢复。

### 测试优先

- [x] T022 [P] [US4] 编写 ApprovalManager 单元测试 in `tests/unit/policy/test_approval_manager.py` — 测试: 幂等注册(FR-007), allow-once 原子消费(FR-008), allow-always 白名单(FR-008), 宽限期访问(FR-009), 超时自动 deny(FR-010), recover_from_store(FR-011), 并发解决竞态(EC-2)。覆盖 FR-007~FR-011。

### 实现

- [x] T023 [US4] 实现 ApprovalManager 核心: __init__ + register() + resolve() in `packages/policy/approval_manager.py` — 幂等注册 + Event Store 双写 + SSE 推送 + asyncio.Event 通知 + 超时定时器（依赖 T006, T011, T012, T013）。覆盖 FR-007, FR-008, FR-011。
- [x] T024 [US4] 实现 ApprovalManager: wait_for_decision() + consume_allow_once() in `packages/policy/approval_manager.py` — asyncio.Event.wait() + wait_for 超时 + 原子消费（依赖 T023）。覆盖 FR-007, FR-008, FR-009。
- [x] T025 [US4] 实现 ApprovalManager: 超时处理 + 宽限期清理 in `packages/policy/approval_manager.py` — call_later 超时回调 + APPROVAL_EXPIRED 事件 + 宽限期后清理（依赖 T023）。覆盖 FR-009, FR-010。
- [x] T026 [US4] 实现 ApprovalManager: recover_from_store() + allow-always 白名单 in `packages/policy/approval_manager.py` — Event Store 扫描 + 状态重建 + 过期标记 + 内存白名单管理（依赖 T023）。覆盖 FR-011, EC-1, EC-5。

**Checkpoint**: ApprovalManager 可独立测试，T022 全部通过。审批竞态安全保障已验证。

---

## Phase 5: User Story 1 + 2 — 策略门禁集成（不可逆触发审批 + 安全操作直接执行）(Priority: P1)

**Goal**: 实现 PolicyCheckHook（BeforeHook 适配器）和 PolicyEngine 门面类，完成 Policy Pipeline + ApprovalManager 与 Feature 004 ToolBroker 的集成。US1（irreversible -> ask -> 审批）和 US2（none/reversible -> allow -> 直接执行）在此阶段共同验证。

**Independent Test**: 注册 PolicyCheckHook 到 mock ToolBroker，分别调用 irreversible/reversible/none 工具，验证: irreversible 进入审批等待、reversible/none 直接放行。

### 测试优先

- [x] T027 [P] [US1] 编写 PolicyCheckHook 单元测试 in `tests/unit/policy/test_policy_check_hook.py` — 测试: allow 决策映射为 proceed=True, deny 映射为 proceed=False, ask 映射为注册+等待+proceed, evaluator 异常时 fail_mode=closed 拒绝(EC-3), 参数脱敏(FR-028)。覆盖 FR-015, FR-016, FR-028。
- [x] T028 [P] [US1] 编写 PolicyCheckpoint Protocol 契约测试 in `tests/contract/test_policy_checkpoint_contract.py` — 验证 PolicyCheckHook 满足 Feature 004 BeforeHook Protocol: name 属性, priority 属性, fail_mode 属性, before_execute() 签名。覆盖 FR-015。
- [x] T029 [P] [US2] 编写安全操作直接执行测试 in `tests/unit/policy/test_policy_check_hook.py` (safe section) — 测试: none 工具 -> allow -> proceed=True 无审批, reversible 工具 -> allow -> proceed=True 无审批。覆盖 FR-005(allow 路径)。

### 实现

- [x] T030 [US1] 实现 PolicyCheckHook in `packages/policy/policy_check_hook.py` — before_execute(): Pipeline 评估 + POLICY_DECISION 事件写入 + action 映射(allow/deny/ask) + ask 时 register() + wait_for_decision() + consume_allow_once() + 异常 fail-closed（依赖 T020, T023, T024）。覆盖 FR-006, FR-015, FR-016。
- [x] T031 [US1] 实现参数脱敏: 工具参数摘要生成 + 敏感值掩码 in `packages/policy/policy_check_hook.py` — 复用 Feature 004 ToolBroker Sanitizer 机制（依赖 T030）。覆盖 FR-028。
- [x] T032 [US1] 实现 PolicyEngine 门面类 in `packages/policy/policy_engine.py` — 组合 Pipeline + ApprovalManager + PolicyCheckHook, hook 属性, approval_manager 属性, startup() 启动恢复, update_profile() 配置变更（依赖 T020, T023, T030）。覆盖 FR-017, FR-027。
- [x] T033 [US1] 实现 FR-017: PolicyEngine 注册校验 — irreversible 工具无 PolicyCheckpoint hook 时强制拒绝 in `packages/policy/policy_engine.py`（依赖 T032）。覆盖 FR-017。

**Checkpoint**: PolicyCheckHook + PolicyEngine 可独立测试。T027-T029 全部通过。US1 + US2 核心策略门禁已验证。

---

## Phase 6: User Story 5 + 9 — Approvals REST API + SSE 推送 (Priority: P1/P3)

**Goal**: 实现后端 Approvals REST API（GET/POST）和 SSE 审批事件推送，为前端面板提供数据源。US5（面板展示和操作）依赖此 API，US9（实时更新）依赖 SSE 推送。

**Independent Test**: 通过 httpx TestClient 调用 GET /api/approvals 和 POST /api/approve/{id}，验证响应格式正确。

### 测试优先

- [x] T034 [P] [US5] 编写 Approvals REST API 集成测试 in `tests/integration/test_approval_api.py` — 测试: GET /api/approvals 返回正确列表, POST /api/approve/{id} 成功/404/409 响应, remaining_seconds 计算正确, 空列表返回。覆盖 FR-018, FR-019。
- [x] T035 [P] [US9] 编写 SSE 事件推送测试 in `tests/integration/test_sse_events.py` — 测试: approval:requested 事件推送, approval:resolved 事件推送, approval:expired 事件推送, 心跳间隔。覆盖 FR-022。

### 实现

- [x] T036 [US5] 实现 GET /api/approvals 路由 in `apps/gateway/routes/approvals.py` — 查询 ApprovalManager pending 列表, 计算 remaining_seconds, 返回 ApprovalsListResponse（依赖 T014, T023）。覆盖 FR-018。
- [x] T037 [US5] 实现 POST /api/approve/{approval_id} 路由 in `apps/gateway/routes/approvals.py` — 接收 ApprovalResolveRequest, 调用 ApprovalManager.resolve(), 返回 ApprovalResolveResponse, 错误处理 404/409/422（依赖 T014, T023）。覆盖 FR-019。
- [x] T038 [US9] 实现 SSE 审批事件集成 in `apps/gateway/sse/approval_events.py` — 监听 ApprovalManager 事件, 推送 approval:requested/resolved/expired SSE 事件, 心跳 15s, Last-Event-ID 断点续传（依赖 T013, T023）。覆盖 FR-022。
- [x] T039 [US5] 注册 approvals 路由到 Gateway in `apps/gateway/routes/__init__.py` 或 `apps/gateway/main.py` — 挂载 /api/approvals 和 /api/approve 路由（依赖 T036, T037）。

**Checkpoint**: REST API + SSE 可通过 TestClient 验证。T034-T035 全部通过。

---

## Phase 7: User Story 6 — Chat API + SSE 流式输出 (Priority: P2)

**Goal**: 实现基础 Chat 后端 API 和 SSE 流式输出，为前端 Chat UI 提供数据源。

**Independent Test**: 通过 API 发送消息，验证返回 task_id 和 stream_url，SSE 流返回 message:chunk 事件。

### 实现

- [x] T040 [US6] 实现 POST /api/chat/send 路由 in `apps/gateway/routes/chat.py` — 接收 ChatSendRequest, 创建/复用 Task, 返回 ChatSendResponse 含 stream_url（依赖 T014, T008）。覆盖 FR-023。
- [x] T041 [US6] 实现 GET /stream/task/{task_id} SSE 流 in `apps/gateway/routes/chat.py` 或 `apps/gateway/sse/task_stream.py` — message:chunk/message:complete/approval:requested/task:status_changed 事件类型, sse-starlette 实现, 心跳 15s（依赖 T038, T040）。覆盖 FR-024。
- [x] T042 [US6] 注册 chat 路由和 SSE stream 到 Gateway in `apps/gateway/routes/__init__.py` 或 `apps/gateway/main.py`（依赖 T040, T041）。

**Checkpoint**: Chat API 可通过 TestClient 验证。消息发送和 SSE 流式输出正常。

---

## Phase 8: User Story 5 前端 — Approvals 面板 (Priority: P1)

**Goal**: 实现 Web 审批面板组件，展示待审批列表，提供三按钮决策操作。

**Independent Test**: 创建审批请求后打开面板，验证展示正确且按钮可操作。

### 实现

- [x] T043 [P] [US5] 实现 useApprovals hook in `frontend/src/hooks/useApprovals.ts` — SSE EventSource 订阅 approval:* 事件 + 30s 轮询兜底 + 状态管理 + 自动重连（依赖 T036, T038）。覆盖 FR-022。
- [x] T044 [P] [US5] 实现 ApprovalCard 组件 in `frontend/src/components/ApprovalPanel/ApprovalCard.tsx` — 展示工具名称, 参数摘要(脱敏), 风险说明, 剩余倒计时 + 三按钮(Allow Once / Always Allow / Deny)。覆盖 FR-020, FR-021, FR-028。
- [x] T045 [US5] 实现 ApprovalPanel 组件 in `frontend/src/components/ApprovalPanel/ApprovalPanel.tsx` — 组合 useApprovals + ApprovalCard 列表渲染 + 空状态提示 + 加载状态（依赖 T043, T044）。覆盖 FR-020, FR-021。
- [x] T046 [US5] 实现 ApprovalPanel index 导出 in `frontend/src/components/ApprovalPanel/index.ts`（依赖 T045）。

**Checkpoint**: Approvals 面板可在浏览器中展示和操作审批请求。

---

## Phase 9: User Story 6 前端 — Chat UI (Priority: P2)

**Goal**: 实现基础 Chat UI 组件，包含消息输入和 SSE 流式输出。

**Independent Test**: 输入消息发送，验证流式回复逐块显示，审批触发时展示提示。

### 实现

- [x] T047 [P] [US6] 实现 useChatStream hook in `frontend/src/hooks/useChatStream.ts` — SSE EventSource 订阅 message:chunk/message:complete + 流式内容拼接 + 审批通知检测 + 重连逻辑（依赖 T041）。覆盖 FR-024。
- [x] T048 [P] [US6] 实现 MessageBubble 组件 in `frontend/src/components/ChatUI/MessageBubble.tsx` — 消息气泡(用户/Agent 区分) + 流式渲染动画 + 审批提示样式。覆盖 FR-023。
- [x] T049 [US6] 实现 ChatUI 主组件 in `frontend/src/components/ChatUI/ChatUI.tsx` — 消息输入框 + 发送按钮 + 消息列表 + useChatStream 集成 + 审批提示引导(FR-025)（依赖 T047, T048）。覆盖 FR-023, FR-024, FR-025。
- [x] T050 [US6] 实现 ChatUI index 导出 in `frontend/src/components/ChatUI/index.ts`（依赖 T049）。

**Checkpoint**: Chat UI 可在浏览器中发送消息并查看流式回复。

---

## Phase 10: User Story 7 + 8 — 审批事件全链路 + 策略配置可审计 (Priority: P2)

**Goal**: 验证事件全链路完整性，实现策略配置变更审计。

**Independent Test**: 触发完整审批流程后查询 Event Store，验证事件链完整。修改策略配置后验证变更事件记录。

### 测试优先

- [x] T051 [P] [US7] 编写事件全链路集成测试 in `tests/integration/test_approval_flow.py` — 测试: allow 路径事件链(POLICY_DECISION), ask->approve 事件链(POLICY_DECISION + APPROVAL_REQUESTED + APPROVAL_APPROVED), ask->deny 事件链, 超时过期事件链(APPROVAL_EXPIRED), 事件字段完整性。覆盖 FR-006, FR-012。
- [x] T052 [P] [US8] 编写策略配置变更事件测试 in `tests/integration/test_approval_flow.py` (config section) — 测试: update_profile() 后 Event Store 包含变更事件, 变更前后配置差异记录。覆盖 FR-027。

### 实现

- [x] T053 [US7] 补全 PolicyCheckHook 事件写入: 确保 allow/deny/ask 三种路径都写入 POLICY_DECISION 事件 in `packages/policy/policy_check_hook.py`（依赖 T030）。覆盖 FR-006。
- [x] T054 [US7] 补全 ApprovalManager 事件写入: 确保 register/resolve/expire 三种动作都写入对应审批事件 in `packages/policy/approval_manager.py`（依赖 T023）。覆盖 FR-012。
- [x] T055 [US8] 实现 PolicyEngine.update_profile() 配置变更事件: 写入 POLICY_CONFIG_CHANGED 事件(含变更前后差异) in `packages/policy/policy_engine.py`（依赖 T032）。覆盖 FR-027。

**Checkpoint**: T051-T052 全部通过。事件全链路和配置变更审计已验证。

---

## Phase 11: 集成测试与端到端验证

**Purpose**: 跨 User Story 的集成验证，确保完整审批流程端到端正确。

### 测试

- [x] T056 编写完整审批流程端到端测试 in `tests/integration/test_approval_flow.py` (e2e section) — 测试: Worker 调用 irreversible 工具 -> PolicyCheckHook 拦截 -> Pipeline 评估 ask -> ApprovalManager 注册 -> SSE 推送 -> REST API 审批 -> 工具恢复执行 -> 事件链完整。覆盖 FR-001~FR-019 集成。
- [x] T057 编写 Task 状态机流转集成测试 in `tests/integration/test_approval_flow.py` (state machine section) — 测试: RUNNING->WAITING_APPROVAL->RUNNING(approve), RUNNING->WAITING_APPROVAL->REJECTED(deny), RUNNING->WAITING_APPROVAL->REJECTED(timeout), Task 超时暂停验证(FR-014)。覆盖 FR-013, FR-014。
- [x] T058 编写启动恢复集成测试 in `tests/integration/test_approval_flow.py` (recovery section) — 测试: 注册审批 -> 模拟重启(清除内存状态) -> recover_from_store() -> 验证 pending 审批恢复 -> 可继续审批。覆盖 FR-011, SC-006。

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: 文档、清理、验证、性能等跨 Story 优化。

- [x] T059 [P] 更新 `packages/policy/__init__.py` 公共 API 导出: 确保所有模型、Pipeline、ApprovalManager、PolicyEngine、PolicyCheckHook 正确导出。
- [x] T060 [P] 代码审查与清理: 检查所有文件的类型注解完整性、docstring 一致性、import 整洁度。
- [x] T061 [P] 运行 quickstart.md 验证: 按照 `.specify/features/006-policy-engine-approvals/quickstart.md` 的步骤手动验证完整流程。
- [x] T062 [P] 性能验证: SC-003(注册到可见<3s), SC-004(决策到恢复<2s), SC-005(超时5s内deny), SC-008(SSE首字节<1s)。
- [x] T063 检查 Feature 004 Mock 兼容性: 验证所有 mock 实现与 Feature 004 锁定契约一致，contract test 通过。

---

## FR Coverage Matrix

> 28 条 FR 全部覆盖，每条至少一个对应 Task。

| FR | 描述 | Task ID(s) | Phase |
|----|------|------------|-------|
| FR-001 | 多层策略管道 | T015, T020 | P3 |
| FR-002 | 决策来源标签 | T009, T015, T020 | P2, P3 |
| FR-003 | 只收紧不放松 | T015, T020 | P3 |
| FR-004 | Profile 过滤 | T016, T018 | P3 |
| FR-005 | 三种决策结果 | T004, T006, T017, T019, T029 | P2, P3, P5 |
| FR-006 | 策略决策事件 | T012, T030, T051, T053 | P2, P5, P10 |
| FR-007 | 幂等注册 | T011, T022, T023 | P2, P4 |
| FR-008 | 三种审批决策 | T006, T011, T022, T023, T024 | P2, P4 |
| FR-009 | 宽限期 | T011, T022, T024, T025 | P2, P4 |
| FR-010 | 超时自动 deny | T012, T022, T025 | P2, P4 |
| FR-011 | 持久化与恢复 | T022, T023, T026, T058 | P4, P11 |
| FR-012 | 审批工作流状态 | T012, T051, T054 | P2, P10 |
| FR-013 | WAITING_APPROVAL 状态 | T004, T005, T008, T057 | P2, P11 |
| FR-014 | Task 超时暂停 | T057 | P11 |
| FR-015 | PolicyCheckpoint Protocol | T027, T028, T030 | P5 |
| FR-016 | hook 内部审批 | T027, T030 | P5 |
| FR-017 | irreversible 无 hook 拒绝 | T032, T033 | P5 |
| FR-018 | GET /api/approvals | T014, T034, T036 | P2, P6 |
| FR-019 | POST /api/approve | T014, T034, T037 | P2, P6 |
| FR-020 | Approvals 面板 | T044, T045 | P8 |
| FR-021 | 三按钮操作 | T044, T045 | P8 |
| FR-022 | SSE 实时更新 | T013, T035, T038, T043 | P2, P6, P8 |
| FR-023 | Chat UI 输入 | T014, T040, T048, T049 | P2, P7, P9 |
| FR-024 | SSE 流式输出 | T041, T047, T049 | P7, P9 |
| FR-025 | 审批提示 | T049 | P9 |
| FR-026 | EventType 扩展 | T004, T007, T012 | P2 |
| FR-027 | 配置变更事件 | T010, T052, T055 | P2, P10 |
| FR-028 | 参数脱敏 | T027, T031, T044 | P5, P8 |

**覆盖率**: 28/28 = 100%

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
  |
  v
Phase 2 (Foundational: 数据模型) -- BLOCKS ALL --
  |
  +---> Phase 3 (US3: Pipeline) --+
  |                                |
  |                                v
  |                          Phase 4 (US4: ApprovalManager) --+
  |                                                            |
  |                                                            v
  |                                                      Phase 5 (US1+US2: PolicyCheckHook)
  |                                                            |
  |                            +-------------------------------+------+
  |                            |                               |      |
  |                            v                               v      v
  |                      Phase 6 (US5+US9: REST API)     Phase 7 (US6: Chat API)
  |                            |                               |
  |                            v                               v
  |                      Phase 8 (US5: 前端 Approvals)   Phase 9 (US6: 前端 Chat)
  |                            |                               |
  |                            +--------->+<-------------------+
  |                                       |
  |                                       v
  +------------------------------>  Phase 10 (US7+US8: 事件审计)
                                          |
                                          v
                                    Phase 11 (集成测试)
                                          |
                                          v
                                    Phase 12 (Polish)
```

### User Story Dependencies

| User Story | 依赖的 Story | 说明 |
|------------|-------------|------|
| US3 (Pipeline) | 无 (仅依赖 Phase 2) | 纯函数，可独立测试 |
| US4 (ApprovalManager) | US3 (间接，共享模型) | Pipeline 决策驱动审批注册 |
| US1 (irreversible 审批) | US3 + US4 | PolicyCheckHook 组合 Pipeline + ApprovalManager |
| US2 (safe 直接执行) | US3 | Pipeline allow 路径 |
| US5 (Approvals 面板) | US4 + US1 | REST API 依赖 ApprovalManager |
| US6 (Chat UI) | Phase 2 (弱依赖) | Chat API 可独立于 Policy Engine |
| US7 (事件审计) | US1 + US4 | 验证事件链需要完整流程 |
| US8 (配置变更) | US1 | PolicyEngine.update_profile() |
| US9 (实时更新) | US5 | SSE 推送依赖审批基础设施 |

### Story 内部并行机会

| Phase | 可并行任务组 | 说明 |
|-------|------------|------|
| Phase 2 | T004+T005 (测试), T006+T007+T008 (枚举), T009+T010+T011+T012 (模型) | 不同文件，无交叉依赖 |
| Phase 3 | T015+T016+T017 (测试), T018+T019 (evaluators) | 两个 evaluator 完全独立 |
| Phase 4 | T022 (测试) 可与 Phase 3 实现并行 | 测试仅依赖模型 |
| Phase 5 | T027+T028+T029 (测试) | 三个测试文件独立 |
| Phase 6 | T034+T035 (测试) | 两个集成测试独立 |
| Phase 8 | T043+T044 (hooks+card) | 不同文件 |
| Phase 9 | T047+T048 (hooks+bubble) | 不同文件 |
| Phase 10 | T051+T052 (测试) | 不同测试维度 |

---

## Implementation Strategy

### Recommended: MVP First (US3 + US4 + US1/US2)

1. Complete Phase 1: Setup (30 min)
2. Complete Phase 2: Foundational (3-4 hours)
3. Complete Phase 3: US3 Pipeline (2-3 hours)
4. Complete Phase 4: US4 ApprovalManager (3-4 hours)
5. Complete Phase 5: US1+US2 PolicyCheckHook (3-4 hours)
6. **STOP and VALIDATE**: Pipeline + ApprovalManager + PolicyCheckHook 端到端工作
7. 此时 MVP 核心价值（策略门禁 + 审批流）已交付，可独立于 UI 通过 API 验证

### Incremental Delivery (after MVP)

8. Phase 6: REST API (2-3 hours) -> 可通过 curl/httpx 验证
9. Phase 7: Chat API (1-2 hours) -> Chat 后端就绪
10. Phase 8+9: 前端 (3-4 hours each) -> Web UI 可用
11. Phase 10: 事件审计 (2 hours) -> 可审计性就绪
12. Phase 11: 集成测试 (2-3 hours) -> 端到端验证
13. Phase 12: Polish (1-2 hours) -> 交付就绪

### Estimated Total

- **MVP (Phase 1-5)**: ~14 hours
- **Full Feature (Phase 1-12)**: ~30 hours
- **可并行率**: ~45%（标记 [P] 的任务）

---

## Notes

- [P] 标记 = 不同文件、无依赖，可并行执行
- [USN] 标记 = 映射到 spec.md 中的 User Story
- Feature 004 代码尚未实现，Phase 5 中使用 Mock 实现 ToolMeta/BeforeHook 等类型
- 前端任务（Phase 8-9）可与后端 Phase 10 并行开发
- 每个 Checkpoint 处可暂停并独立验证该 Story 的功能
- 避免跨 Phase 修改同一文件导致冲突
