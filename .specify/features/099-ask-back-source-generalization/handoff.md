# F099 Handoff → F100 Decision Loop Alignment + F101 Notification

**来源 Feature**: F099 Ask-Back Channel + Source Generalization
**目标 Feature**: F100 Decision Loop Alignment（H1）+ F101 Notification（接收 F3/F5 归档项）
**参考 baseline**: F099 完成后（5 commits + Verify + post-review fix）

---

## 三工具现状

| 工具名 | entrypoints | handler 文件 | 参数签名 |
|--------|-------------|--------------|---------|
| `worker.ask_back` | {"agent_runtime", "web"} | `gateway/services/builtin_tools/ask_back_tools.py` | `question: str, context: str = ""` |
| `worker.request_input` | {"agent_runtime", "web"} | 同上 | `prompt: str, expected_format: str = ""` |
| `worker.escalate_permission` | {"agent_runtime", "web"} | 同上 | `action: str, scope: str, reason: str` |

**状态路径**：
- ask_back / request_input → `execution_context.request_input(prompt, approval_required=False)` → RUNNING→WAITING_INPUT→RUNNING
- escalate_permission → `approval_gate.request_approval()` → `approval_gate.wait_for_decision(handle, timeout_seconds=300.0)` → 返回 "approved"/"rejected"

**重要约束**：
- `_approval_gate` 在 `ToolDeps` 中目前为 `None`（未接入生产），escalate_permission 总是降级返回 "rejected"
  - 接入点：`octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/_deps.py` 的 `ToolDeps._approval_gate`
  - 接入位置：`capability_pack.py` 的 `startup()` 或 `register_all()` 调用链

---

## source_runtime_kind 枚举（F099 新增）

**常量文件**：`packages/core/src/octoagent/core/models/source_kinds.py`

**caller 身份（source_runtime_kind 字段值）**：
```python
SOURCE_RUNTIME_KIND_MAIN = "main"
SOURCE_RUNTIME_KIND_WORKER = "worker"
SOURCE_RUNTIME_KIND_SUBAGENT = "subagent"
SOURCE_RUNTIME_KIND_AUTOMATION = "automation"        # F099 新增
SOURCE_RUNTIME_KIND_USER_CHANNEL = "user_channel"    # F099 新增

KNOWN_SOURCE_RUNTIME_KINDS: frozenset[str]  # 包含以上 5 个值
```

**控制事件来源（CONTROL_METADATA_SOURCE_* 字段值）**：
```python
CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_INIT = "subagent_delegation_init"       # F098
CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_BACKFILL = "subagent_delegation_session_backfill"  # F098
CONTROL_METADATA_SOURCE_ASK_BACK = "worker_ask_back"                               # F099
CONTROL_METADATA_SOURCE_REQUEST_INPUT = "worker_request_input"                     # F099
CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION = "worker_escalate_permission"         # F099
```

**注意**：这两套常量语义完全不同，禁止混用（source_kinds.py 顶层 docstring 已说明）。

---

## _resolve_a2a_source_role() 现在处理的 source 值范围

**文件**：`apps/gateway/src/octoagent/gateway/services/dispatch_service.py`

| source_runtime_kind 值 | → role | → session_kind | → agent_uri 格式 |
|------------------------|--------|----------------|-----------------|
| "main"（无 signal）| MAIN | MAIN_BOOTSTRAP | "main.agent" |
| "worker" | WORKER | WORKER_INTERNAL | "worker.{source_worker_capability}" |
| "subagent" | WORKER | WORKER_INTERNAL | "worker.{source_worker_capability}"（代码实测：dispatch_service.py:870 worker/subagent 同分支）|
| "automation"（F099 新增）| AUTOMATION | AUTOMATION_INTERNAL | "automation.{source_automation_id}" |
| "user_channel"（F099 新增）| USER_CHANNEL | USER_CHANNEL | "user.{source_channel_id}" |
| 未知值（FR-C4 降级）| MAIN | MAIN_BOOTSTRAP | "main.agent"（+ warning log）|

**注（Codex Final F9 修正）**：`"subagent"` 在 `_resolve_a2a_source_role` 中走 `"worker"/"subagent"` 同一分支（dispatch_service.py:870），映射为 `WORKER/WORKER_INTERNAL`，不是独立的 SUBAGENT 分支。若 F101+ 需要真正区分 subagent，需在此分支拆分。

---

## F100 接入点说明

### 1. RecallPlannerMode="auto" 启用

F091 为 `RecallPlannerMode.AUTO` 保留了 `raise NotImplementedError` 占位：

```python
# octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py
# 搜索 "RecallPlannerMode" 或 "auto" 实现位置
```

F100 主责：实现 `RecallPlannerMode.AUTO` 语义（按请求复杂度自适应 recall planner）。

### 2. ask_back WAITING_INPUT 与 recall planner 交互现状

当 Worker 调用 `worker.ask_back`：
1. `execution_context.request_input(prompt)` 触发 RUNNING → WAITING_INPUT
2. task_runner 轮询检测到 WAITING_INPUT，挂起当前 LLM 执行循环
3. 用户 `attach_input` → WAITING_INPUT → RUNNING，task_runner 恢复执行
4. `request_input()` 返回用户输入文本作为工具 tool_result

recall planner 在 WAITING_INPUT 期间**不运行**（task 未处于 RUNNING 状态）。F100 无需为 ask_back WAITING_INPUT 期间的 recall planner 行为做特殊处理。

### 3. single_loop_executor hack 移除

F091 保留了 `supports_single_loop_executor` 类属性（测试 fixture duck-type 依赖）。
F100 去掉 single_loop_executor 跳过 recall planner 的 hack 时，需确认：
- `supports_single_loop_executor = False` 的 mock 测试 fixture 不受影响
- ask_back / request_input 工具的 WAITING_INPUT 状态机路径不与 recall planner 冲突

---

## F099 推迟项（F101 主接收，Codex Final F3 归档）

| 项目 | 严重度 | 建议接收 | 说明 |
|------|--------|---------|------|
| **escalate_permission WAITING_APPROVAL 状态机路径**（Codex Final F3）| HIGH | **F101** | 需要 ExecutionConsole + TaskService 联合改造（task.status → WAITING_APPROVAL + approval_id 暴露 + 回 RUNNING）；超 F099 范围；AC-B4/AC-B5/FR-E4 标 PARTIAL/DEFERRED |
| `ApprovalGate` 生产接入（F2 已修复 DI，sse_push_fn=None 待绑定）| MEDIUM | F101 | octo_harness 已创建 ApprovalGate 并 bind，但 sse_push_fn=None（SSE 推送卡片无效）；F101 负责晚期绑定 sse_hub |
| 完整三条事件序列 e2e 验证（[E2E_DEFERRED] / AC-E1）| MEDIUM | F101 or 独立测试任务 | 需要 TaskRunner + ExecutionConsole 联合集成 |
| source_kinds.py `__all__` 定义 | LOW（style）| 任意清理 commit | 纯 style，不影响行为 |

---

## 枚举扩展位置（供 F100 参考）

如需进一步扩展 source 类型：

- **AgentRuntimeRole**: `packages/core/src/octoagent/core/models/agent_context.py`（AUTOMATION / USER_CHANNEL 已加）
- **AgentSessionKind**: 同文件（AUTOMATION_INTERNAL / USER_CHANNEL 已加）
- **SOURCE_RUNTIME_KIND_*** 常量: `packages/core/src/octoagent/core/models/source_kinds.py`
- **dispatch_service._resolve_a2a_source_role()**: `apps/gateway/src/octoagent/gateway/services/dispatch_service.py`

---

## 验证建议

F100 实施后建议验证：
1. F099 三工具在 ask_back WAITING_INPUT 期间，recall_planner_mode="auto" 不启动 recall
2. ask_back tool_result 在 Worker LLM 下一轮 context 中可见（compaction 不丢失）
3. escalate_permission + 生产 ApprovalGate 接入后 approval flow 端到端

---

v1.1 — F099 post-review fix，F100 + F101 接收（F3/F5/ApprovalGate 归档 F101）
