# Spec 合规审查报告

**Feature**: 064-butler-dispatch-redesign (Phase 1: Butler 直接回答)
**审查日期**: 2026-03-19
**审查基线**: spec.md Phase 1 范围 + tasks.md FR 覆盖映射表
**分支**: `claude/festive-meitner`

---

## 逐条 FR 状态

以下基于 tasks.md 的 FR 覆盖映射表逐条检查。

| FR 编号 | 描述 | 状态 | 证据/说明 |
|---------|------|------|----------|
| 变更 A: 移除预路由 LLM 调用 | `_resolve_butler_decision()` 跳过 model decision | **已实现** | orchestrator.py L923-941: `_resolve_butler_decision()` 仅调用 `decide_butler_decision()` 规则决策，DIRECT_ANSWER 返回 `(None, {})`。未调用 `_resolve_model_butler_decision()`。L1207-1208: deprecated 注释已添加。diff 确认移除了对 `_resolve_model_butler_decision()` 的调用行。 |
| 变更 B: Butler 直接执行路径 | `_dispatch_butler_direct_execution()` 新增 | **已实现** | orchestrator.py L1004-1071: 方法已实现，包含 trivial 判定、ORCH_DECISION 事件写入、butler_metadata 构建（含 `butler_execution_mode=direct` + `butler_is_trivial`）、`ensure_task_running()`、`process_task_with_llm()` 复用 Event Sourcing 链路、使用 `self._llm_service` 真实 LLM、`tool_profile="standard"`。完整 docstring 和类型注解。 |
| 变更 C: 委派通过工具触发 | `delegate_to_worker` 内建工具 | **未实现（Phase 2 范畴）** | tasks.md 明确标注为 Phase 2 范畴，Phase 1 不含此变更。spec.md 4.1 节分阶段实施表确认 Phase 2 才实现。 |
| 变更 D: 保留规则快速路径 | `_is_trivial_direct_answer()` + 规则保留 | **已实现** | butler_behavior.py L1195-1248: `_is_trivial_direct_answer()` 完整实现，含 4 组正则模式（greeting/identity/ack/meta）、30 字符上限、保守匹配策略。orchestrator.py L923-941: `decide_butler_decision()` 规则决策保留，天气/位置等非 DIRECT_ANSWER 决策正常返回。 |
| 性能: 简单问题 1 次 LLM 调用 | 跳过预路由 + Butler Direct 路径 | **已实现** | orchestrator.py L937-940: DIRECT_ANSWER 返回 `(None, {})` 跳过预路由。L629-633: `dispatch()` 中 butler_decision=None 且 `_should_butler_direct_execute()=True` 时走 `_dispatch_butler_direct_execution()`，直接使用 `self._llm_service` 调用一次 LLM。测试 `test_dispatch_routes_to_butler_direct_execution` 验证了 `len(llm.calls) >= 1`。 |
| 可观测: Event 链完整 | Butler Direct Execution 生成完整 Event 链 | **已实现** | orchestrator.py L1029-1032: `_write_orch_decision_event()` 写入 ORCH_DECISION 事件。L1051-1061: `process_task_with_llm()` 复用现有 Event Sourcing 链路（MODEL_CALL_STARTED/COMPLETED + ARTIFACT_CREATED）。测试 `test_event_chain_completeness` 验证了 ORCH_DECISION、MODEL_CALL_STARTED、MODEL_CALL_COMPLETED、ARTIFACT_CREATED 事件存在。 |
| 可观测: `butler_execution_mode=direct` metadata | metadata 注入 + 传递验证 | **已实现** | orchestrator.py L1038: `butler_metadata["butler_execution_mode"] = "direct"`。L1057: 通过 `dispatch_metadata=butler_metadata` 传递给 `process_task_with_llm()`。测试 `test_event_metadata_contains_butler_execution_mode` 和 `test_trivial_metadata` 验证了 metadata 传递。 |
| 可观测: `butler_is_trivial` metadata | trivial 检测 + metadata 注入 | **已实现** | orchestrator.py L1023: `is_trivial = _is_trivial_direct_answer(request.user_text)`。L1039: `butler_metadata["butler_is_trivial"] = is_trivial`。测试 `test_trivial_metadata`（"你好" -> True）和 `test_standard_metadata`（"Python 的 GIL 是什么？" -> False）验证了区分。 |
| 兼容: 天气/位置路径不变 | 规则决策保留 + 回归测试 | **已实现** | orchestrator.py L939-940: 非 DIRECT_ANSWER 的规则决策正常返回。L582-609: `_is_freshness_butler_decision()` 分支在 Butler Direct Execution 之前，天气/位置查询优先走 freshness 路径。测试 `test_inline_butler_decision_still_works` 验证了 ASK_ONCE 决策走 inline butler decision 路径。 |
| 兼容: 现有 Worker 路径不变 | fallback 保留 + 回归测试 | **已实现** | orchestrator.py L635-667: `_should_butler_direct_execute()` 返回 False 时，后续代码仍走 freshness/Delegation Plane/Worker Dispatch。测试 `test_worker_dispatch_fallback_for_subtask` 验证了子任务不走 butler-direct 路径。 |
| 兼容: 前端/Telegram/API 无影响 | 回归测试 + 全套测试通过 | **部分实现** | SSE 事件流格式未变（复用 `process_task_with_llm()` 链路）。T013 标记已完成但未提供测试通过截图/日志。T017 端到端手动验证未完成（tasks.md 中未勾选）。 |
| 兼容: Event Store 向后兼容 | metadata 为可选字段 | **已实现** | orchestrator.py L1035-1040: `butler_execution_mode` 和 `butler_is_trivial` 作为 metadata 附加字段，不修改现有 Event 模型，向后兼容。 |
| 安全: Policy Gate 不变 | 本特性不修改 Policy Gate | **已实现** | diff 确认 Policy Gate 相关代码无变更。dispatch() 中 Policy Gate 检查（L546-568）在 Butler Direct Execution 分支之前执行。 |
| 安全: 轮次上限 10 | `max_iterations=10` 通过 dispatch_metadata 传递 | **未实现** | orchestrator.py 中无 `max_iterations` 字段。Grep 搜索确认该关键词在 orchestrator.py 中不存在。butler_metadata 中未包含 `max_iterations=10`。 |
| 术语规范 | docstring 和注释使用统一术语 | **已实现** | orchestrator.py: `_dispatch_butler_direct_execution()` 有完整 docstring，使用 "Butler Direct Execution"、"Butler Decision Preflight"、"Event Sourcing" 等 spec 统一术语。`_should_butler_direct_execute()` 有完整 docstring。butler_behavior.py: `_is_trivial_direct_answer()` 有完整 docstring + 模块级注释说明每组模式用途和误判影响。 |

---

## 总体合规率

**13/15** FR 已实现（**87%**）

- 1 个 FR 明确属于 Phase 2（变更 C），Phase 1 不要求实现
- 1 个 FR 未实现（轮次上限 10）
- 1 个 FR 部分实现（前端/Telegram/API 兼容性验证）

Phase 1 范围内的有效 FR 数为 14 个（排除变更 C），其中 12 个已实现，1 个部分实现，1 个未实现。

**Phase 1 有效合规率: 12/14 = 86%**

---

## 偏差清单

| FR 编号 | 状态 | 偏差描述 | 修复建议 |
|---------|------|---------|---------|
| 安全: 轮次上限 10 | 未实现 | tasks.md FR 覆盖映射表明确要求 "`max_iterations=10` 通过 dispatch_metadata 传递"，tasks.md T006 描述中也提到此项。但实际代码中 `butler_metadata` 未包含 `max_iterations` 字段。同时 `process_task_with_llm()` 是否消费该参数也未确认。 | 在 `_dispatch_butler_direct_execution()` 的 `butler_metadata` 中添加 `"max_iterations": 10`。同时确认 `process_task_with_llm()` 是否读取并执行该约束。如果 `process_task_with_llm()` 不支持迭代上限，Phase 1 可将该 metadata 作为声明性标记保留，留待 Phase 2 Butler Free Loop 完整实现时消费。 |
| 兼容: 前端/Telegram/API 无影响 | 部分实现 | T017（端到端手动验证）在 tasks.md 中未勾选。无法确认前端 SSE、Telegram 消息收发在实际运行环境中是否正常。 | 完成 T017 端到端手动验证：启动服务后发送 "你好"、"Hello 你是什么模型？"、"今天天气怎么样" 验证各路径正常工作。 |
| T012.1: test_freshness_path_not_affected | 部分缺失 | tasks.md T012 要求 3 个回归测试：(1) freshness 路径不受影响、(2) Worker dispatch fallback、(3) inline butler decision 仍工作。实际 test_butler_dispatch_redesign.py 中仅实现了 (2) 和 (3)，缺少 (1) `test_freshness_path_not_affected`。 | 在 `TestRegressionSafety` 类中新增 `test_freshness_path_not_affected` 测试，验证天气/位置查询仍走 `_dispatch_butler_owned_freshness()` 路径。 |

---

## 过度实现检测

| 位置 | 描述 | 风险评估 |
|------|------|---------|
| orchestrator.py diff: 移除 Subagent Result Queue（~80 行） | diff 显示移除了 `_subagent_result_queues`、`enqueue_subagent_result()`、`drain_subagent_results()` 等代码。这些代码标记为 "Feature 064 P1-B"，但不在当前 spec.md Phase 1 范围内。推测为之前一个版本的 Feature 064 实现残留，在本次 redesign 中被清理。 | INFO - 代码清理行为，不影响 Phase 1 功能。但需确认这些被移除的功能是否有其他 Feature 依赖。若 Subagent Lifecycle（如 Feature 055 区域）依赖这些方法，则为 CRITICAL 回归风险。 |
| orchestrator.py diff: 移除 Notification Service（~100 行） | 移除了 `_notification_service` 属性、`_notify_state_change()` 方法、以及所有调用点（Worker 完成通知、FAILED/REJECTED/WAITING_APPROVAL 状态通知）。标记为 "Feature 064 P2-B"。 | WARNING - 如果前一版本 Feature 064 的通知服务已有消费者（如 Telegram 通知、前端状态推送），则移除会导致功能回归。需确认 `notification_service` 参数是否被外部调用者传入。 |
| orchestrator.py diff: 移除 `agent_name` 从 `_write_worker_dispatched_event()` | 移除了 `agent_name` 参数及其在 `WorkerDispatchedPayload` 中的传递。同时移除了获取 target agent 显示名称的逻辑。 | WARNING - 如果前端泳道标题依赖 `WorkerDispatchedPayload.agent_name` 字段，移除可能导致 UI 显示退化。需确认 `WorkerDispatchedPayload` model 是否仍有 `agent_name` 字段定义。 |
| orchestrator.py diff: 移除 `asyncio` import | 移除了 `import asyncio`，因为 Subagent Result Queue 代码被清理后不再需要。 | INFO - 合理的 import 清理，无功能影响。 |
| orchestrator.py diff: 移除构造函数 `notification_service` 参数 | `__init__()` 不再接受 `notification_service` 参数。 | INFO（如果无外部调用者传入此参数）或 CRITICAL（如果有调用者传入会导致 TypeError）。需检查 `OrchestratorService` 的所有实例化点。 |

---

## 验证标准检查（spec.md 6.1-6.3）

### 6.1 功能验证

| 验证项 | Phase 1 可验证 | 状态 | 证据 |
|--------|--------------|------|------|
| 简单问题无 Worker 创建，单次 LLM 调用 | 是 | **通过** | 测试 `test_dispatch_routes_to_butler_direct_execution` + `test_trivial_metadata` + `test_standard_metadata` 均验证了 Butler Direct Execution 路径，dispatch_id 以 "butler-direct:" 开头（无 Worker）。 |
| 工具使用问题：Butler 直接调用工具，无 Worker | 否（Phase 2） | **不适用** | Phase 1 未实现 Butler Free Loop 多轮工具调用。 |
| 复杂任务通过 delegate_to_worker 委派 | 否（Phase 2） | **不适用** | Phase 1 未实现 `delegate_to_worker` 工具。 |
| 所有场景生成完整 Event 链 | 是 | **通过** | 测试 `test_event_chain_completeness` 验证了 ORCH_DECISION + MODEL_CALL_STARTED + MODEL_CALL_COMPLETED + ARTIFACT_CREATED 事件链。 |

### 6.2 性能验证

| 验证项 | Phase 1 可验证 | 状态 | 证据 |
|--------|--------------|------|------|
| 简单问题端到端延迟 < 15s | 需要手动验证 | **未验证** | T017 未完成。测试环境使用 mock LLM，无法反映真实延迟。 |
| 无 regression：复杂任务仍能正确委派 | 是 | **通过** | 测试 `test_worker_dispatch_fallback_for_subtask` + `test_inline_butler_decision_still_works` 验证了 fallback 路径。 |

### 6.3 兼容性验证

| 验证项 | Phase 1 可验证 | 状态 | 证据 |
|--------|--------------|------|------|
| 前端 SSE 事件流正常 | 需要手动验证 | **未验证** | T017 未完成。 |
| Telegram 消息收发正常 | 需要手动验证 | **未验证** | T017 未完成。 |
| Event Store 查询正常 | 是 | **通过** | `test_event_chain_completeness` 验证了 Event Store 写入和查询。 |
| 现有测试通过 | 是 | **已声明通过** | T013 + T016 在 tasks.md 中已勾选。 |

---

## 宪法合规性检查

| 宪法条款 | 状态 | 证据 |
|----------|------|------|
| Durability First | **合规** | Butler Direct Execution 通过 `process_task_with_llm()` 落盘（Event Store 写入）。Task 状态通过 `ensure_task_running()` + `get_task()` 持久化。 |
| Everything is an Event | **合规** | ORCH_DECISION 事件写入（L1029-1033）。`process_task_with_llm()` 生成 MODEL_CALL_STARTED/COMPLETED + ARTIFACT_CREATED 事件链。route_reason 区分 trivial/standard。 |
| Tools are Contracts | **合规** | 未引入新工具（Phase 1 不含 `delegate_to_worker`），现有工具 schema 不变。 |
| Side-effect Must be Two-Phase | **不适用** | Phase 1 无不可逆操作。Butler 直接回答为文本生成，无副作用。 |
| Least Privilege by Default | **合规** | Butler Direct Execution 使用 `tool_profile="standard"`（标准工具集），与 spec 一致。 |
| Degrade Gracefully | **合规** | `_should_butler_direct_execute()` 返回 False 时，fallback 到 Delegation Plane -> Worker Dispatch。LLMService 不支持 `supports_single_loop_executor` 时自动降级。 |
| User-in-Control | **合规** | Policy Gate 检查在 Butler Direct Execution 之前执行（L546-568）。高风险动作仍受 Policy Gate 管控。 |
| Observability is a Feature | **合规** | `butler_execution_mode=direct` + `butler_is_trivial` metadata 可查询。ORCH_DECISION route_reason 区分 trivial/standard。Event 链与 Worker 路径一致。 |

---

## 问题分级汇总

- **CRITICAL**: 0 个
- **WARNING**: 3 个
  - W1: `max_iterations=10` 轮次上限未在 butler_metadata 中实现（tasks.md FR 映射表明确要求）
  - W2: T017 端到端手动验证未完成
  - W3: T012.1 `test_freshness_path_not_affected` 测试缺失
- **INFO**: 3 个
  - I1: diff 中移除了前一版本 Feature 064 的 Subagent Result Queue 代码（需确认无外部依赖）
  - I2: diff 中移除了 Notification Service 及所有状态变更通知调用（需确认无消费者）
  - I3: diff 中移除了 `WorkerDispatchedPayload.agent_name` 字段传递（需确认前端无依赖）

---

## 总结

Phase 1 的核心变更（变更 A/B/D + 规则快速路径）均已正确实现，设计意图与 spec 一致：

1. **预路由 LLM 调用已移除** -- `_resolve_butler_decision()` 不再调用 `_resolve_model_butler_decision()`，简单问题从 2-3 次 LLM 降至 1 次
2. **Butler Direct Execution 路径已建立** -- 通过 `_dispatch_butler_direct_execution()` 使用真实 LLM 直接回答，复用 Event Sourcing 链路
3. **规则快速路径已保留** -- 天气/位置等非 DIRECT_ANSWER 决策正常走 inline/freshness 路径
4. **Event 链完整性已保证** -- metadata 含 `butler_execution_mode` + `butler_is_trivial`，与 Worker 路径事件粒度一致
5. **Fallback 路径完好** -- 子任务、spawned 请求、不支持 single_loop_executor 的 LLM 仍走旧路径

主要关注点是 `max_iterations=10` 轮次上限的缺失（spec CRITICAL-1 决议要求 Phase 1 实现），以及 diff 中附带的代码清理（移除 Notification Service / Subagent Queue / agent_name）需确认不产生回归。
