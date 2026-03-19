# Feature 064 P0 Spec Review

> **审查时间**: 2026-03-19
> **审查范围**: FR-064-01 ~ FR-064-12（P0-A 并行工具调用 + P0-B 工具结果回填格式）
> **审查方法**: 逐条对照 spec.md 验收标准，检查实现代码和测试覆盖

---

## P0-A: 并行工具调用

| FR | 结果 | 说明 |
|----|------|------|
| FR-064-01 | **PASS** | `runner.py` `_execute_tool_calls()` 第 380~393 行：按 `SideEffectLevel` 分桶为 `bucket_none`（NONE）/ `bucket_reversible`（REVERSIBLE）/ `bucket_irreversible`（IRREVERSIBLE）。未注册工具（`get_tool_meta` 返回 None）默认视为 `IRREVERSIBLE`（第 387 行）。单元测试 `test_mixed_buckets_execution_order` 和 `test_unknown_tool_treated_as_irreversible` 覆盖。 |
| FR-064-02 | **PASS** | `runner.py` 第 417~436 行：NONE 桶使用 `asyncio.gather(*coros, return_exceptions=True)` 并行执行。结果通过 `results_map` + `call_keys` 按原始 `tool_calls` 顺序返回。`test_parallel_none_tools_concurrent_execution` 验证 3 个 100ms 工具并行总耗时 < 250ms。 |
| FR-064-03 | **PASS** | `runner.py` 第 438~444 行：REVERSIBLE 桶使用 `for call in bucket_reversible` 串行执行。`test_mixed_buckets_execution_order` 验证 REVERSIBLE 在 NONE 之后、IRREVERSIBLE 之前执行。 |
| FR-064-04 | **PARTIAL** | `runner.py` 第 446~452 行：IRREVERSIBLE 桶串行执行，但代码中 **未显式触发 WAITING_APPROVAL 流程**。审批机制仍由 `_execute_single_tool` 内部的 ToolBroker hook chain（PresetBeforeHook）间接驱动。spec 要求「执行前触发 WAITING_APPROVAL 流程（复用 Feature 061 PresetBeforeHook）」——实际确实复用了 Feature 061 的 hook chain（`_execute_single_tool` → `ToolBroker.execute` → `PresetBeforeHook`），但 runner 层面无额外显式审批调用。从验收标准「DESTRUCTIVE 工具触发审批，审批通过后执行」角度看，行为通过 hook chain 间接满足，可接受。 |
| FR-064-05 | **PASS** | `enums.py` 第 179~180 行新增 `TOOL_BATCH_STARTED` 和 `TOOL_BATCH_COMPLETED`。`runner.py` 第 844~896 行实现 `_emit_tool_batch_started()` / `_emit_tool_batch_completed()`。payload 包含 `batch_id`、`tool_names`、`execution_mode`（parallel）、`batch_size`、`bucket_none_count`、`bucket_reversible_count`、`bucket_irreversible_count`。COMPLETED payload 包含 `duration_ms`、`success_count`、`error_count`、`total_count`。`test_batch_event_payload` 全面验证。 |
| FR-064-06 | **PASS** | `_execute_single_tool()` 内部调用 `self._tool_broker.execute()`，ToolBroker 在执行过程中独立发射 `TOOL_CALL_STARTED` / `TOOL_CALL_COMPLETED` 事件（broker.py 第 288~434 行）。这些事件在 BATCH 事件时间范围之内。事件流结构：BATCH_STARTED → [TOOL_STARTED/COMPLETED × N] → BATCH_COMPLETED。 |
| FR-064-07 | **PASS** | `runner.py` 第 424~436 行：`asyncio.gather(*coros, return_exceptions=True)` 使用 `return_exceptions=True`，单个工具异常作为 Exception 对象返回，不影响其他工具。异常被包装为 `ToolFeedbackMessage(is_error=True)`。`test_parallel_partial_failure` 验证 3 个并行中 1 个失败 → 2 个成功 + 1 个错误反馈，BATCH_COMPLETED payload 中 `error_count=1, success_count=2`。 |
| FR-064-08 | **PASS** | `runner.py` 执行顺序固定：第 417 行先执行 `bucket_none`（并行）→ 第 439 行再执行 `bucket_reversible`（串行）→ 第 447 行最后执行 `bucket_irreversible`（串行）。`test_mixed_buckets_execution_order` 验证：NONE(tool_a) < REVERSIBLE(tool_b) < IRREVERSIBLE(tool_c)。 |

---

## P0-B: 修复工具结果回填格式

| FR | 结果 | 说明 |
|----|------|------|
| FR-064-09 | **PASS** | `litellm_client.py` 第 517~535 行：当 `has_standard_ids=True` 且 `not use_responses_api` 时，feedback 以 `{"role": "tool", "tool_call_id": "xxx", "content": "..."}` 标准格式回填。第 606~622 行：assistant message 携带标准 `tool_calls` 数组（含 `id`、`type: "function"`、`function.name`、`function.arguments`）。错误结果（`is_error=True`）同样通过标准 tool role message 回填（第 527 行 `f"ERROR: {fb.error}"`）。`TestChatCompletionsBackfill` 测试类覆盖。 |
| FR-064-10 | **PASS** | `litellm_client.py` 第 536~551 行：当 `has_standard_ids=True` 且 `use_responses_api` 时，feedback 以 `{"type": "function_call_output", "call_id": "xxx", "output": "..."}` 格式回填。第 623~631 行：assistant output 追加 `function_call` items（含 `type`、`call_id`、`name`、`arguments`）。`TestResponsesAPIBackfill` 测试类覆盖。 |
| FR-064-11 | **PASS** | `models.py` 第 162~170 行：`ToolCallSpec.tool_call_id: str = Field(default="", description=...)`。描述说明了 Chat Completions 从 `tool_calls[].id` 填充、Responses API 从 `function_call.call_id` 填充。`litellm_client.py` 第 644~646 行构造 ToolCallSpec 时传入 `tool_call_id=tc.get("id", "")`。`runner.py` `_execute_single_tool()` 第 512 行将 `call.tool_call_id` 传递到 `_build_tool_feedback()`。`ToolFeedbackMessage.tool_call_id`（第 197~199 行）同步扩展。`test_tool_call_id_propagation` 验证端到端传递。 |
| FR-064-12 | **PASS** | `ToolCallSpec.tool_call_id` 默认为空字符串（第 162 行 `default=""`），不影响现有构造方式。`litellm_client.py` 第 518 行 `has_standard_ids = any(fb.tool_call_id for fb in feedback)` — 当所有 `tool_call_id` 为空时走第 552~571 行的自然语言回退路径（`"role": "user", "content": "Tool execution results:\n..."`）。第 632~637 行 assistant message 同样回退到自然语言摘要。`TestBackwardCompatibility` 测试类覆盖。 |

---

## 补充发现

### 已新增 P2-A 预留 EventType
`enums.py` 第 183 行新增 `CONTEXT_COMPACTION_FAILED = "CONTEXT_COMPACTION_FAILED"`，满足 T-064-02 验收标准 2。

### ToolBrokerProtocol 扩展
`protocols.py` 第 221~233 行新增 `get_tool_meta()` 抽象方法，含完整类型注解和 docstring。`broker.py` 第 177~189 行实现 O(1) 查找。满足 T-064-01 验收标准。

### 测试覆盖
- `test_runner_parallel.py`: 8 个测试用例，覆盖 FR-064-01 ~ FR-064-08 全部场景
- `test_litellm_client_backfill.py`: 10 个测试用例，覆盖 FR-064-09 ~ FR-064-12 全部场景

---

## 总结

| 统计 | 数量 |
|------|------|
| PASS | 11 |
| PARTIAL | 1 |
| FAIL | 0 |

**FR-064-04 PARTIAL 说明**：IRREVERSIBLE 工具的审批流程通过 ToolBroker hook chain（PresetBeforeHook）间接触发，runner 层面无额外显式审批代码。从功能行为看审批确实会被触发（取决于 hook 注册），但 runner 自身不保证审批 — 如果未注册 PresetBeforeHook，IRREVERSIBLE 工具会直接执行而不经审批。建议后续在 runner 层面添加对 IRREVERSIBLE 桶的显式审批保障。

**整体结论**：P0 实现质量良好，12 个 FR 中 11 个完全通过、1 个部分通过（行为可接受但缺少 runner 层面的显式审批保障）。代码结构清晰，测试覆盖充分。
