# F099 Re-review（Post-Fix Adversarial Verification）

## 验证范围
- diff：7ff450c..bd7242a
- 验证时间：2026-05-12 01:41:39 CST
- 工作目录：/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F099-ask-back-source-generalization

## Finding 逐条验证

### F1 - is_caller_worker 替代 runtime_kind（CRITICAL）
状态：CLOSED

证据：
- `octoagent/apps/gateway/src/octoagent/gateway/services/execution_context.py:42`-`47` 新增 `is_caller_worker: bool = False`，默认 False。
- `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py:487`-`506` 在 WorkerRuntime 构造 `ExecutionRuntimeContext` 时显式设 `is_caller_worker=True`。
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1356` owner-self 路径仍设 `runtime_kind="worker"`，但 `orchestrator.py:1374`-`1388` 构造 `ExecutionRuntimeContext` 时未传 `is_caller_worker`，因此保持默认 False。
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1116`-`1125` 和 `orchestrator.py:1228`-`1237` 主 Agent inline/direct 路径传 `execution_context=None`，没有意外设置 True。
- `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/_spawn_inject.py:42`-`51` 无 context 或 `is_caller_worker=False` 时返回 `{}`；`_spawn_inject.py:53`-`64` 只有 True 时注入 `source_runtime_kind="worker"` 和可选 `source_worker_capability`。
- `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/_spawn_inject.py:66`-`70` 只把 `runtime_kind` 用于 debug log，未再用它判断 caller 身份。
- `octoagent/apps/gateway/tests/services/test_phase_c_source_injection.py:279`-`303` 覆盖 owner-self `runtime_kind="worker"` 但 `is_caller_worker=False` 不注入的回归。

残留风险：task resume / 无 live waiter 路径没有持久化该 caller 标记，见新风险 N-H1。

### F2 - ApprovalGate 接入
状态：CLOSED

证据：
- `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py:131`-`132` 先创建 CapabilityPackService，再进入 `_bootstrap_mcp()`；`octo_harness.py:694`-`711` 在 `capability_pack_service.startup()` 之前创建 `ApprovalGate` 并调用 `bind_approval_gate()`。
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py:209`-`211` 持有 `_approval_gate`；`capability_pack.py:266`-`274` 的 `bind_approval_gate()` 会同时更新已存在的 `_tool_deps`。
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py:1049`-`1066` 构造生产 `ToolDeps` 时传入 `_approval_gate=self._approval_gate`。
- `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/_deps.py:45`-`65` `ToolDeps` 字段包含 `_approval_gate`。
- `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:394`-`402` 的 None 降级分支仍存在，但正常 harness 启动路径已不应命中；`ask_back_tools.py:424`-`435` 会调用 `request_approval()` 和 `wait_for_decision()`。
- 全仓搜索 `ToolDeps(` 只有 `capability_pack.py:1049` 是生产构造点，其余为 tests / helpers。

残留风险：`octo_harness.py:700`-`704` 创建的 `ApprovalGate` 仍传 `sse_push_fn=None`，而 `approval_gate.py:223`-`245` 只有 `sse_push_fn` 非 None 才推送审批卡片；该项已在 `completion-report.md:129` 和 `handoff.md:110` 归档 F101。

### F3 - WAITING_APPROVAL 推迟归档
状态：CLOSED

证据：
- 代码仍未实现 WAITING_APPROVAL 状态机：`ask_back_tools.py:424`-`435` 只调用 `ApprovalGate.request_approval()` / `wait_for_decision()`，没有 task/session 状态切换。
- `completion-report.md:93`-`94` 将 AC-B4/AC-B5 标为 PARTIAL / PARTIAL/DEFERRED。
- `completion-report.md:100` 将 AC-E1 标为 PARTIAL/DEFERRED；`completion-report.md:103` 将 AC-G3 标为 PARTIAL。
- `completion-report.md:124`-`130` 明确把 `escalate_permission WAITING_APPROVAL` 状态机归档到 F101。
- `handoff.md:105`-`111` 将 F3 接收范围写给 F101，并说明需要 task.status -> WAITING_APPROVAL、approval_id 暴露、回 RUNNING。

残留风险：F101 未完成前，`worker.escalate_permission` 仍不满足 spec 中 AC-B4/AC-B5/FR-E4 的状态机要求；作为延期项记录充分。

### F4 - audit trace 结构化
状态：CLOSED

证据：
- `ask_back_tools.py:84`-`97` 在无 execution_context 时记录 warning，包含 `source` / `tool_name` / hint，并显式跳过 emit。
- `ask_back_tools.py:131`-`140` 在 EventStore append 失败时记录 `source`、`task_id`、`tool_name`、`error` 和 hint。
- `tests/services/test_ask_back_tools.py:492`-`527` 通过 `append_event_committed` side effect 覆盖 append 失败路径。
- `tests/services/test_ask_back_tools.py:530`-`548` 覆盖无 execution_context 的可观测降级路径。

残留风险：测试只断言 `mock_log.warning.called`，没有断言 warning 参数中确实包含 `task_id/tool_name/error`；字段级回归仍可能漏掉。

### F5 - RUNNING guard
状态：PARTIAL

证据：
- 三个 handler 都在入口读取真实 task store，而不是信任 context 字段：`ask_back_tools.py:183`-`187`、`ask_back_tools.py:271`-`275`、`ask_back_tools.py:365`-`369`。
- 非 RUNNING 时不会继续创建 waiter：`ask_back_tools.py:187`-`193` 返回 `""`；`ask_back_tools.py:275`-`281` 返回 `""`；`ask_back_tools.py:369`-`375` 返回 `"rejected"`。
- guard 内部异常会被吞掉并继续原流程：`ask_back_tools.py:194`-`195`、`ask_back_tools.py:282`-`283`、`ask_back_tools.py:376`-`377`。
- `tests/services/test_ask_back_tools.py:557`-`584` 覆盖 `worker.ask_back` 非 RUNNING 不调用 `request_input()`；`test_ask_back_tools.py:588`-`612` 覆盖 `worker.request_input` 非 RUNNING 不调用 `request_input()`。
- 搜索只找到上述两个非 RUNNING 测试，未找到 `worker.escalate_permission` 非 RUNNING guard 测试。

残留风险：ask_back/request_input guard 失败返回空字符串，LLM 无法区分“用户空回答”和“任务状态非法”；task_store / context 读取异常时 guard 会静默跳过，仍可能进入旧的 waiter 路径。

### F6/F7/F8 - 文档修复
状态：CLOSED

证据：
- `completion-report.md:86`-`104` 重建了 14 条 AC 列表：B1~B5、C1~C3、D1~D2、E1、G1~G4 均出现。
- `completion-report.md:93`-`94` 将 AC-B4/AC-B5 标为 PARTIAL / PARTIAL/DEFERRED。
- `completion-report.md:100` 将 AC-E1 标为 PARTIAL/DEFERRED。
- `completion-report.md:103` 将 AC-G3 标为 PARTIAL。
- `completion-report.md:104` 恢复 AC-G4 原意：三工具均有 `CONTROL_METADATA_UPDATED` 审计记录并关联 `task_id`，同时区分 happy path PASS 与 failure path PARTIAL。
- `completion-report.md:106` 汇总为 11/14 PASS、3/14 PARTIAL/DEFERRED。

残留风险：无影响闭环的残留；另见新 LOW 文档不一致 N-L1。

### F9 - subagent source 映射
状态：CLOSED

证据：
- `handoff.md:60`-`69` 已把 `"subagent"` 映射修正为 `WORKER / WORKER_INTERNAL / worker.{source_worker_capability}`，并注明当前代码中 `"worker"` / `"subagent"` 同分支。
- `octoagent/apps/gateway/src/octoagent/gateway/services/dispatch_service.py:864`-`881` 代码实际将 `source_runtime_kind in ("worker", "subagent")` 映射到 `AgentRuntimeRole.WORKER`、`AgentSessionKind.WORKER_INTERNAL`。

残留风险：如果 F101+ 需要真正区分 subagent，必须拆分 `dispatch_service.py:870` 分支；当前 handoff 已如实记录。

## 新风险（is_caller_worker 引入）

### N-H1 - is_caller_worker 未进入无 live waiter 的 input resume 持久化/重建路径
严重度：HIGH

证据：
- 全仓源码搜索 `is_caller_worker` 只找到 `execution_context.py:47` 字段、`worker_runtime.py:505` 设置点、`_spawn_inject.py:50` 和 `ask_back_tools.py:184/272/366` 消费点；未找到 task resume state、runtime_control、DispatchEnvelope 序列化字段。
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:586`-`597` live waiter 存在时直接投递，原内存 `ExecutionRuntimeContext` 可继续使用。
- `task_runner.py:599`-`622` 无 live waiter 时走恢复执行，只写入 `execution_session_id`、`human_input_artifact_id`、`input_request_id` 三个 resume 字段，没有 `is_caller_worker`、caller source 或原始 `dispatch_envelope`。
- `task_runner.py:673`-`686` 在 `dispatch_envelope is None` 时调用 `orchestrator.dispatch(...)`；`orchestrator.py:550`-`565` 重新构造 `OrchestratorRequest` 时 `runtime_context=None`，metadata 仅来自 latest user metadata。
- `worker_runtime.py:488`-`506` 只有重新进入 WorkerRuntime 构造 `ExecutionRuntimeContext` 时才会恢复 `is_caller_worker=True`；当前无 live waiter 的 input resume 路径没有持久化标记证明一定会恢复到该构造点。

影响：进程重启、live waiter 丢失或恢复路径触发后，worker caller 身份无法从持久化状态验证；后续若 resumed execution 再调用 `delegate_task` / `subagents.spawn`，`source_runtime_kind="worker"` 注入可能丢失，F1 的显式 caller 信号在 resume 场景不闭环。按本次 grounding rule，“字段在序列化路径中找不到处理”必须列为 HIGH。

建议：在 durable resume state / DispatchEnvelope / Work metadata 中持久化显式 caller-source 信号，或在恢复时从 Work/DispatchEnvelope 可验证地重建 `ExecutionRuntimeContext(is_caller_worker=True)`；补充无 live waiter 的 worker ask_back resume 回归测试。

### N-L1 - handoff 仍保留 ApprovalGate 未接入的陈旧说明
严重度：LOW

证据：
- `handoff.md:21`-`24` 仍写 `_approval_gate` 在 `ToolDeps` 中目前为 `None`、`escalate_permission` 总是降级返回 `"rejected"`。
- 这与 `capability_pack.py:1049`-`1066` 的生产注入、`octo_harness.py:694`-`711` 的 bind 路径相冲突；同一 handoff 后文 `handoff.md:110` 又正确写明 F2 已修复 DI、只剩 `sse_push_fn=None` 待 F101。

影响：不会破坏运行时代码，但会误导 F101 接手范围。

## 汇总
- 原 9 finding：8 CLOSED / 1 PARTIAL / 0 REGRESSED / 0 NOT_ADDRESSED
- 新发现：1 high / 0 medium / 1 low
- 测试验证：
  - 按指定命令运行：失败，pytest 未进入测试执行；尾部输出为 `PermissionError: [Errno 1] Operation not permitted`，来源为 `.venv/lib/python3.12/site-packages/pytest_rerunfailures.py` 初始化。
  - 诊断性复跑（禁用该插件）：`.venv/bin/python -m pytest -q -p no:rerunfailures apps/gateway/tests/services/test_phase_c_source_injection.py apps/gateway/tests/services/test_ask_back_tools.py` → `33 passed, 1 warning in 1.14s`。
- 最终判定：仍需修复。F3 推迟 F101 可接受；但 N-H1 违反本次 is_caller_worker resume grounding rule，F5 也只算部分闭环。修复 N-H1 并补 F5 明确错误返回/测试后再合入 master。
