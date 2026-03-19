# Tasks: Feature 064 — Butler Dispatch Redesign (Phase 1)

**Input**: `.specify/features/064-butler-dispatch-redesign/` (spec.md, plan.md, data-model.md, contracts/, checklists/)
**Scope**: 仅 Phase 1（Butler 直接回答路径），Phase 2-4 作为后续 TODO 记录
**Branch**: `claude/festive-meitner`

---

## 范围说明

本任务分解仅覆盖 **Phase 1: Butler 直接回答**（变更点 1A-1F）。

Phase 1 的核心目标：跳过 Butler Decision Preflight（预路由 LLM 调用），让 Butler 用主 LLM 直接回答用户请求，简单问题从 2-3 次 LLM 调用降至 1 次。

**涉及文件**（仅 2 个源文件 + 2 个测试文件）：
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` — 主变更
- `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` — 新增函数
- `octoagent/apps/gateway/tests/test_butler_dispatch_redesign.py` — 新增测试
- `octoagent/apps/gateway/tests/test_butler_behavior.py` — 扩展测试

**CRITICAL-1 决议**：Phase 1 仅设轮次上限 10，不做 per-request token budget 和无进展检测。

---

## Phase 1: Setup

**Purpose**: 无独立 Setup 阶段——本特性无需新建项目结构或引入新依赖，所有变更在现有文件中进行。

---

## Phase 2: Foundational — butler_behavior.py 新增 trivial 检测

**Purpose**: 新增 `_is_trivial_direct_answer()` 函数，为 Orchestrator 提供 Rule-based Fast Path 判断能力。此函数是 Phase 3 实现任务的前置依赖。

- [x] T001 [P] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` 中新增 `_is_trivial_direct_answer()` 函数。包含 4 组正则模式列表（`_TRIVIAL_GREETING_PATTERNS`、`_TRIVIAL_IDENTITY_PATTERNS`、`_TRIVIAL_ACK_PATTERNS`、`_TRIVIAL_META_PATTERNS`）和主函数体。覆盖：纯问候、身份询问、致谢/确认、简单元问题。长度上限 30 字符。详见 plan.md 变更 1F 的完整代码。需在文件顶部补充 `import re`（如不存在）。

**Checkpoint**: `_is_trivial_direct_answer()` 函数可独立调用，返回 bool，无外部依赖。

---

## Phase 3: US1 — Butler Direct Execution 核心路径 (Priority: P1)

**Goal**: 跳过 Butler Decision Preflight，Butler 用主 LLM 直接回答所有非规则命中的请求。简单问题端到端延迟从 30s+ 降至 4-15s，LLM 调用次数从 2-3 降至 1。

**Independent Test**: 发送 "Hello 你是什么模型？"，验证无 Worker 创建、无 `_resolve_model_butler_decision()` 调用、仅 1 次 LLM 调用、Event 链完整。

### Tests for US1

> **NOTE: 先写测试，确认失败，再实现**

- [x] T002 [P] [US1] 在 `octoagent/apps/gateway/tests/test_butler_behavior.py` 中扩展测试：新增 `_is_trivial_direct_answer()` 的单元测试类。正向用例（"你好"、"hello"、"Hi!"、"你是谁"、"你是什么模型？"、"谢谢"、"好的"、"ok"、"你能做什么"、"帮助"），反向用例（"帮我写一段代码"、"今天天气怎么样"、"你好，帮我查一下..."、空字符串、超长文本 >30 字符、"你好啊我想问你一个关于编程的问题"）。

- [x] T003 [P] [US1] 在 `octoagent/apps/gateway/tests/test_butler_dispatch_redesign.py` 中新建测试文件。包含以下测试用例（均使用 mock LLMService + mock TaskService）：
  1. `test_resolve_butler_decision_skips_model_decision` — 验证 `_resolve_butler_decision()` 不再调用 `_resolve_model_butler_decision()`
  2. `test_should_butler_direct_execute_eligible` — 验证普通请求返回 True
  3. `test_should_butler_direct_execute_ineligible_subtask` — 验证子任务（有 parent_task_id）返回 False
  4. `test_dispatch_routes_to_butler_direct_execution` — 验证 dispatch() 在 butler_decision=None 时路由到 `_dispatch_butler_direct_execution`
  5. `test_butler_direct_execution_trivial_metadata` — 验证 trivial 请求的 metadata 包含 `butler_execution_mode=direct` 和 `butler_is_trivial=True`
  6. `test_butler_direct_execution_standard_metadata` — 验证非 trivial 请求的 metadata 包含 `butler_execution_mode=direct` 和 `butler_is_trivial=False`

### Implementation for US1

- [x] T004 [US1] **变更 1A**: 修改 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 中的 `_resolve_butler_decision()` 方法（行 913-945 附近）。移除对 `_resolve_model_butler_decision()` 的调用，移除对 `_annotate_compatibility_fallback_decision()` 的调用，移除对 `_build_precomputed_recall_plan_metadata()` 的调用。保留 `decide_butler_decision()` 规则决策调用。DIRECT_ANSWER 结果返回 `(None, {})`，非 DIRECT_ANSWER 规则决策（天气/位置）正常返回。

- [x] T005 [US1] **变更 1C**: 在 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 中新增 `_should_butler_direct_execute()` 方法。检查条件：(1) LLMService 支持 tool calling（`supports_single_loop_executor`），(2) 请求满足 `_is_butler_decision_eligible()` 条件（非子任务、非 spawned 等）。

- [x] T006 [US1] **变更 1D**: 在 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 中新增 `_dispatch_butler_direct_execution()` 方法。逻辑包含：(1) 调用 `_is_trivial_direct_answer()` 判定 trivial 标志，(2) 写入 `_write_orch_decision_event(route_reason="butler_direct_execution:trivial|standard")`，(3) 构建 `butler_metadata`（含 `butler_execution_mode=direct`、`butler_is_trivial`），(4) 通过 `TaskService.ensure_task_running()` 确保任务运行中，(5) 调用 `TaskService.process_task_with_llm()` 复用现有 Event Sourcing 链路（传入 `self._llm_service`、`model_alias`、`tool_profile="standard"`），(6) 构建并返回 `WorkerResult`。详见 plan.md 变更 1D 的完整伪代码。

- [x] T007 [US1] **变更 1B**: 修改 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 中的 `dispatch()` 方法（行 610-631 附近）。在 `_dispatch_inline_butler_decision` 分支之后、freshness check 之前，新增 Butler Direct Execution 分支：当 `_should_butler_direct_execute(request)` 为 True 时，调用 `_dispatch_butler_direct_execution()` 并 early return。保留现有 Delegation Plane -> Worker Dispatch 路径作为 fallback。

- [x] T008 [US1] **变更 1E**: 在 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 的 `_resolve_model_butler_decision()` 方法（行 1131 附近）上方添加 deprecated 注释：`# @deprecated Phase 1 (Feature 064): Butler Decision Preflight 已被 Butler Direct Execution 替代。保留函数体供回滚使用。Phase 4 清理时移除。`

- [x] T009 [US1] **变更 1F**: 在 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 的导入区域（行 85-95 附近）新增 `from .butler_behavior import _is_trivial_direct_answer`。

**Checkpoint**: 简单问题（"你好"、"你是谁"）通过 Butler Direct Execution 路径处理，无 Worker 创建，LLM 调用次数 = 1。天气/位置查询仍走 freshness 路径。现有 Delegation Plane 路径仍可用作 fallback。

---

## Phase 4: US2 — Event 链完整性与可观测性 (Priority: P1)

**Goal**: 验证 Butler Direct Execution 路径生成的 Event 链与 Worker 路径一致，确保可观测性不降级。

**Independent Test**: 发送请求后查询 Event Store，验证 MODEL_CALL_STARTED -> MODEL_CALL_COMPLETED -> ARTIFACT_CREATED 事件链完整，且 metadata 中包含 `butler_execution_mode=direct`。

### Tests for US2

- [x] T010 [US2] 在 `octoagent/apps/gateway/tests/test_butler_dispatch_redesign.py` 中新增 Event 链验证测试：`test_butler_direct_execution_event_chain` — mock LLM 返回文本回复后，验证 Event Store 中按序存在 MODEL_CALL_STARTED、MODEL_CALL_COMPLETED、ARTIFACT_CREATED 事件。验证事件 payload 中包含 `butler_execution_mode` 字段。

### Implementation for US2

- [x] T011 [US2] 在 `_dispatch_butler_direct_execution()` 中确认 `dispatch_metadata` 的 `butler_execution_mode` 和 `butler_is_trivial` 字段被正确传递给 `process_task_with_llm()`，并最终写入 Event payload。如果 `process_task_with_llm()` 的 `dispatch_metadata` 参数未被透传到 Event payload，需追加传递逻辑。文件：`octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py`。

**Checkpoint**: Event Store 中 Butler Direct Execution 的事件链与 Worker 路径一致。`butler_execution_mode=direct` 可查询。

---

## Phase 5: US3 — Regression 安全网 (Priority: P1)

**Goal**: 确保 Phase 1 变更不破坏现有路径（天气/位置快速路径、Worker 派发、前端 SSE）。

**Independent Test**: 运行现有测试套件，全部通过。

### Tests for US3

- [x] T012 [P] [US3] 在 `octoagent/apps/gateway/tests/test_butler_dispatch_redesign.py` 中新增回归测试：
  1. `test_freshness_path_not_affected` — 天气/位置查询仍走 `_dispatch_butler_owned_freshness()`，不进入 Butler Direct Execution
  2. `test_worker_dispatch_fallback` — 当 `_should_butler_direct_execute()` 返回 False 时（如子任务），仍走 Delegation Plane -> Worker Dispatch
  3. `test_inline_butler_decision_still_works` — 当 `butler_decision is not None`（非 DIRECT_ANSWER 规则决策）时，仍走 `_dispatch_inline_butler_decision()`

### Implementation for US3

> 无额外实现——Phase 3 的实现已包含所有 fallback 路径保留。此阶段仅通过测试验证。

- [x] T013 [US3] 运行完整测试套件 `cd octoagent && uv run pytest apps/gateway/tests/ -v`，确认所有现有测试通过，无回归。

**Checkpoint**: 所有现有测试通过，天气/位置/Worker 路径行为不变。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 代码质量、文档更新、最终验证

- [x] T014 [P] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 中为所有新增方法（`_should_butler_direct_execute`、`_dispatch_butler_direct_execution`）添加完整的 docstring 和类型注解，确保符合项目代码规范。

- [x] T015 [P] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` 中为 `_is_trivial_direct_answer()` 的模块级正则列表添加内联注释说明每组模式的用途和误判影响。

- [x] T016 运行 `cd octoagent && uv run pytest apps/gateway/tests/test_butler_dispatch_redesign.py apps/gateway/tests/test_butler_behavior.py -v` 确认所有新增测试通过。

- [ ] T017 端到端手动验证：启动完整服务后，通过 Web UI 或 API 发送以下请求并验证结果：(1) "你好" — 单次 LLM 调用，无 Worker，<15s；(2) "Hello 你是什么模型？" — 同上；(3) "今天天气怎么样" — 走 freshness 路径（如已配置天气工具）。

---

## 后续 TODO（Phase 2-4，不在本次分解范围内）

### Phase 2: delegate_to_worker 工具（LLM 自主委派）
- 新增 `delegate_to_worker` 内建工具定义（见 `contracts/delegate-to-worker.md`）
- 升级 Butler Execution Loop 支持多轮工具循环
- 新增 `_handle_delegate_to_worker()` 方法
- 工具定义注册到 CapabilityPackService

### Phase 3: Failover 候选链
- LLM 调用失败时自动降级到 cheap 模型
- 明确与 LiteLLM Proxy 的分工（alias 级别降级 vs provider fallback）
- 生成 failover 事件

### Phase 4: 清理
- 删除 `_resolve_model_butler_decision()`
- 删除 `_InlineReplyLLMService` 类
- 删除 `_dispatch_inline_butler_decision()`
- 删除 `_annotate_compatibility_fallback_decision()`
- 删除 `_build_precomputed_recall_plan_metadata()`
- Delegation Plane 降级为仅 delegate_to_worker 触发

---

## FR 覆盖映射表

| FR (来自 spec.md) | Task ID | 说明 |
|---|---|---|
| 变更 A: 移除预路由 LLM 调用 | T004, T008 | `_resolve_butler_decision()` 跳过 model decision + deprecated 标注 |
| 变更 B: Butler 直接执行路径 | T006 | `_dispatch_butler_direct_execution()` 新增 |
| 变更 C: 委派通过工具触发 | (Phase 2) | Phase 1 不含 delegate_to_worker 工具 |
| 变更 D: 保留规则快速路径 | T001, T004, T005, T007 | `_is_trivial_direct_answer()` + 规则决策保留 + dispatch 路由 |
| 性能: 简单问题 1 次 LLM 调用 | T004, T006, T007 | 跳过预路由 + Butler Direct 路径 |
| 可观测: Event 链完整 | T010, T011 | Event 链验证 + metadata 传递 |
| 可观测: `butler_execution_mode=direct` metadata | T006, T011 | metadata 注入 + 传递验证 |
| 可观测: `butler_is_trivial` metadata | T001, T006, T011 | trivial 检测 + metadata 注入 |
| 兼容: 天气/位置路径不变 | T004, T012 | 规则决策保留 + 回归测试 |
| 兼容: 现有 Worker 路径不变 | T007, T012 | fallback 保留 + 回归测试 |
| 兼容: 前端/Telegram/API 无影响 | T012, T013 | 回归测试 + 全套测试通过 |
| 兼容: Event Store 向后兼容 | T011 | metadata 为可选字段 |
| 安全: Policy Gate 不变 | (无变更) | 本特性不修改 Policy Gate |
| 安全: 轮次上限 10 | T006 | `max_iterations=10` 通过 dispatch_metadata 传递 |
| 术语规范 | T014, T015 | docstring 和注释使用统一术语 |

**覆盖率**: Phase 1 范围内的所有 FR 均已覆盖（变更 C / delegate_to_worker 属于 Phase 2）。

---

## Dependencies & Execution Order

### Phase 依赖

```
Phase 2 (Foundational: T001)  ← 无前置
Phase 3 (US1: T002-T009)      ← 依赖 Phase 2 (T001 提供 _is_trivial_direct_answer)
Phase 4 (US2: T010-T011)      ← 依赖 Phase 3 (T006 提供 _dispatch_butler_direct_execution)
Phase 5 (US3: T012-T013)      ← 依赖 Phase 3 (T004-T009 完成核心变更)
Phase 6 (Polish: T014-T017)   ← 依赖 Phase 3-5 全部完成
```

### User Story 间依赖

- **US1 (核心路径)**: 阻塞性前置——US2、US3 均依赖 US1 的实现
- **US2 (Event 链)**: 依赖 US1 的 `_dispatch_butler_direct_execution()` 实现
- **US3 (Regression)**: 依赖 US1 的核心变更完成，但不依赖 US2

### US1 内部执行顺序

```
T002, T003 (测试先行，可并行)
  ↓
T004 (变更 1A: _resolve_butler_decision 跳过 model decision)
  ↓
T005 (变更 1C: _should_butler_direct_execute)
  ↓
T006 (变更 1D: _dispatch_butler_direct_execution — 依赖 T001, T005)
  ↓
T007 (变更 1B: dispatch() 新增分支 — 依赖 T005, T006)
  ↓
T008, T009 (变更 1E, 1F — 可并行，与 T004-T007 无代码依赖但建议在核心实现后执行)
```

### 并行机会

| 可并行组 | Tasks | 理由 |
|---------|-------|------|
| 测试先行 | T002 + T003 | 不同测试文件，无依赖 |
| Foundational + 测试 | T001 + T002 + T003 | 不同文件，可同时启动 |
| 收尾标注 | T008 + T009 | 同文件但不同位置（deprecated 注释 vs 导入），可并行 |
| Polish | T014 + T015 | 不同文件 |
| US2 + US3 测试 | T010 + T012 | 同文件但不同测试类，可并行 |

### 推荐实现策略

**MVP First（单人顺序执行）**:

1. T001 → T002 + T003（并行）→ 确认测试失败
2. T004 → T005 → T006 → T007（核心路径顺序实现）
3. T008 + T009（并行收尾）
4. 运行 T002 + T003 → 确认测试通过
5. T010 → T011（Event 链验证）
6. T012 → T013（回归验证）
7. T014 + T015（并行 Polish）→ T016 → T017

预计总任务量：17 个任务，其中 6 个可并行。核心实现集中在 4 个任务（T004-T007）。

---

## Notes

- [P] 标记表示该任务可与同阶段其他 [P] 任务并行执行（不同文件、无依赖）
- [USN] 标记表示该任务所属 User Story
- 所有文件路径基于项目根目录 `octoagent/`
- Phase 1 的回滚方式：恢复 `_resolve_butler_decision()` 中对 `_resolve_model_butler_decision()` 的调用
- 轮次上限 10 通过 `dispatch_metadata["max_iterations"]` 传递，具体是否被 `process_task_with_llm()` 消费需在 T006 实现时确认
