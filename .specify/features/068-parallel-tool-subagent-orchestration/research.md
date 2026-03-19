# Feature 064 — 技术方案研究笔记（现有代码分析）

> **生成时间**: 2026-03-19
> **分析基准**: spec.md + clarify-report.md + 源码逐行比对

---

## 1. SkillRunner 当前执行流程分析

**文件**: `octoagent/packages/skills/src/octoagent/skills/runner.py`

### 1.1 主循环结构

```
run() → while tracker.check_limits(limits) is None:
  1. generate() → SkillOutputEnvelope（含 tool_calls）
  2. 重复签名检测（_tool_signature hash 比对）
  3. _execute_tool_calls() → list[ToolFeedbackMessage]
  4. _check_stop_hooks()
  5. complete / skip_remaining_tools 判定
```

### 1.2 `_execute_tool_calls()` 当前实现（第 360-416 行）

**串行 for 循环**：`for call in tool_calls:` 逐个执行。

关键步骤：
1. 白名单校验：`resolve_effective_tool_allowlist()` 检查 tool_name
2. `before_tool_execute` hook
3. 构建 `ExecutionContext`（从 `SkillExecutionContext` 转换，含 `permission_preset`）
4. `self._tool_broker.execute(call.tool_name, call.arguments, tool_context)` → `ToolResult`
5. Feature 061 ask 桥接：`_handle_ask_bridge(tool_result, call, tool_context)`
6. `_build_tool_feedback()` 构建 `ToolFeedbackMessage`
7. `after_tool_execute` hook
8. `skip_remaining_tools` 提前退出

**P0-A 改动点**：
- 需要在步骤 1 之后、步骤 2 之前插入分桶逻辑
- 需要访问 `ToolBroker._registry` 查询每个工具的 `SideEffectLevel`
- 当前 `ToolBrokerProtocol` 没有按名称查询 `ToolMeta` 的接口（**C-02**）

### 1.3 事件发射方法

- `_emit_event()` 使用 `event_store.get_next_task_seq()` 生成递增序号
- 所有事件写入使用 `append_event_committed()` 优先（优先带提交），回退到 `append_event()`
- `get_next_task_seq()` 是 async 方法——并行场景下需确认原子性（**W-10**）

---

## 2. LiteLLMSkillClient 当前回填机制分析

**文件**: `octoagent/packages/skills/src/octoagent/skills/litellm_client.py`

### 2.1 对话历史管理

- `self._histories: dict[str, list[dict[str, Any]]]`，key = `"{task_id}:{trace_id}"`
- `step == 1` 时通过 `_build_initial_history()` 初始化
- 后续 step 通过追加 `history` 列表维护上下文

### 2.2 工具结果回填（第 517-538 行）

当前实现（**P0-B 需要修复的核心问题**）：
```python
if step > 1 and feedback:
    results = []
    for fb in feedback:
        if fb.is_error:
            results.append(f"- {fb.tool_name}: ERROR: {fb.error}")
        else:
            results.append(f"- {fb.tool_name}: {fb.output}")
    history.append({
        "role": "user",
        "content": "Tool execution results:\n" + "\n".join(results) + ...
    })
```

**问题**：
1. 使用 `user` role 自然语言拼接，而非标准 `tool` role message
2. 注释（第 518-519 行）明确提到 Responses API 的 `call_id` 脱节问题
3. 工具调用的 assistant message 追加也是自然语言（第 569-571 行）：
   ```python
   tc_summary = ", ".join(f"{tc['tool_name']}({tc['arguments']})" for tc in tool_calls)
   history.append({"role": "assistant", "content": f"[Calling tools: {tc_summary}]"})
   ```

### 2.3 Chat Completions 路径 (`_call_proxy`)

- 流式接收 SSE，按 `index` 合并 `tool_call` 片段
- 返回 `tool_calls` 格式：`[{"id": str, "tool_name": str, "arguments": dict}]`
- **已包含 `id` 字段**（第 475 行 `tc["id"]`），可直接作为 `tool_call_id`

### 2.4 Responses API 路径 (`_call_proxy_responses`)

- 从 `response.output_item.added` 和 `response.output_item.done` 中提取 `function_call`
- 返回 `tool_calls` 格式：`[{"id": str, "tool_name": str, "arguments": dict}]`
- `id` 来自 `item.get("call_id") or item.get("id")`
- **call_id 一致性问题**：当前已在注释中标注（**W-04**）

### 2.5 `ToolCallSpec` 到 `tool_calls` 的转换（第 576 行）

当前 `ToolCallSpec` 构造丢失了 `id` 字段：
```python
ToolCallSpec(tool_name=tc["tool_name"], arguments=tc["arguments"])
```
需要扩展为：
```python
ToolCallSpec(tool_name=tc["tool_name"], arguments=tc["arguments"], tool_call_id=tc["id"])
```

---

## 3. 数据模型现状

### 3.1 ToolCallSpec（`skills/models.py` 第 157-161 行）

```python
class ToolCallSpec(BaseModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
```
- **缺少 `tool_call_id` 字段**（FR-064-11 需新增）

### 3.2 ToolFeedbackMessage（`skills/models.py` 第 178-187 行）

```python
class ToolFeedbackMessage(BaseModel):
    tool_name: str
    is_error: bool
    output: str
    error: str | None
    duration_ms: int
    artifact_ref: str | None
    parts: list[dict[str, Any]]
```
- **缺少 `tool_call_id` 字段**（**C-04** 遗漏，回填需要）

### 3.3 SkillExecutionContext（`skills/models.py` 第 138-154 行）

```python
class SkillExecutionContext(BaseModel):
    task_id: str
    trace_id: str
    caller: str = "worker"
    agent_runtime_id: str = ""
    agent_session_id: str = ""
    work_id: str = ""
    permission_preset: str = "normal"
    conversation_messages: list[dict[str, str]]
    metadata: dict[str, Any]
    usage_limits: UsageLimits
```
- **缺少 `parent_task_id` 字段**（FR-064-13 需新增，**C-01**）

### 3.4 Task 模型（`core/models/task.py` 第 28-46 行）

```python
class Task(BaseModel):
    task_id: str
    created_at: datetime
    updated_at: datetime
    status: TaskStatus
    title: str
    thread_id: str
    scope_id: str
    requester: RequesterInfo
    risk_level: RiskLevel
    pointers: TaskPointers
    trace_id: str
```
- **缺少 `parent_task_id` 字段**（**C-01**，P1-A 全部 FR 的前提）

### 3.5 EventType 枚举（`core/models/enums.py`）

已定义 70+ 事件类型，包括：
- `CONTEXT_COMPACTION_COMPLETED`（已定义但未实现）
- `TASK_HEARTBEAT`（Feature 011 Watchdog 使用）
- `A2A_MESSAGE_SENT` / `A2A_MESSAGE_RECEIVED`（Feature 008 使用）

**需新增**：
- `TOOL_BATCH_STARTED`
- `TOOL_BATCH_COMPLETED`

---

## 4. ToolBroker 与 SideEffectLevel 分析

### 4.1 ToolBrokerProtocol（`tooling/protocols.py` 第 173-229 行）

当前接口：
- `register(tool_meta, handler)` → None
- `try_register(tool_meta, handler)` → RegisterToolResult
- `discover(profile?, group?)` → list[ToolMeta]
- `execute(tool_name, args, context)` → ToolResult

**缺少**：按名称查询单个工具元数据的接口（**C-02**）

### 4.2 ToolBroker 内部实现（`tooling/broker.py`）

- `_registry: dict[str, tuple[ToolMeta, Callable]]`——键为 tool_name
- `execute()` 内通过 `self._registry.get(tool_name)` 获取 `(meta, handler)`
- `meta.side_effect_level` 即可获得 `SideEffectLevel`

**解决方案**：在 `ToolBrokerProtocol` 新增 `get_tool_meta(tool_name) -> ToolMeta | None`，
实现层直接从 `_registry` 字典查找，O(1) 复杂度。

### 4.3 SideEffectLevel（`tooling/models.py` 第 24-32 行）

```python
class SideEffectLevel(StrEnum):
    NONE = "none"           # 纯读取
    REVERSIBLE = "reversible"   # 可回滚
    IRREVERSIBLE = "irreversible"  # 不可逆
```

与 spec 中 `READ_ONLY / WRITE / DESTRUCTIVE` 的映射：
- `READ_ONLY` ↔ `NONE`
- `WRITE` ↔ `REVERSIBLE`
- `DESTRUCTIVE` ↔ `IRREVERSIBLE`

### 4.4 事件序号原子性（W-10）

`get_next_task_seq()` 在 `EventStore` 实现中使用 SQLite `SELECT MAX(task_seq) + 1`。
在 asyncio 单线程模型下，只要 `SELECT` 和 `INSERT` 之间没有 `await`，就不会有竞争。
但并行 `asyncio.gather()` 中多个 coroutine 交替执行时，`get_next_task_seq()` 内部有 DB 查询（含 `await`），
可能导致两个 coroutine 拿到相同的 `task_seq`。

**解决方案**：为并行批次预分配一段 task_seq 范围，或使用 `asyncio.Lock` 保护。

---

## 5. Subagent 生命周期现状

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py`

### 5.1 spawn_subagent()

当前功能：
- 查找 parent Worker 的 `AgentRuntime`
- 创建 `AgentRuntime`（metadata 标记 `is_subagent=True, parent_worker_runtime_id`）
- 创建 `AgentSession`（kind=`SUBAGENT_INTERNAL`）
- **不创建 Task**（**C-01**）
- **不创建 SkillRunner**（**C-05**）
- **不创建 A2AConversation**

### 5.2 kill_subagent()

- 关闭 Session（`CLOSED`）、归档 Runtime（`ARCHIVED`）
- 不清理 Task 状态（因为当前没有 Child Task）

### 5.3 list_active_subagents()

- 通过 parent_worker_runtime_id 查找关联 Session → 获取 Runtime

### 5.4 P1-A 需要扩展

- `spawn_subagent()` 需要：创建 Child Task + A2AConversation + 独立 SkillRunner 实例
- `kill_subagent()` 需要：流转 Child Task 到终态 + 发送 A2A 消息
- 新增 `SubagentExecutor` 类管理独立执行循环

---

## 6. SSE Hub 分析

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/sse_hub.py`

### 6.1 当前接口

```python
class SSEHub:
    _subscribers: dict[str, set[asyncio.Queue]]  # task_id -> queues

    async def subscribe(task_id) -> asyncio.Queue
    async def unsubscribe(task_id, queue)
    async def broadcast(task_id, event)  # put_nowait to all queues
```

### 6.2 P1-B 双路广播

- 需要在 `broadcast()` 中支持同时向 `child_task_id` 和 `parent_task_id` 广播
- 方案选择：
  - **方案 A**：在调用方分两次调用 `broadcast()`——简单但散落在各处
  - **方案 B**：`broadcast()` 新增 `parent_task_id` 可选参数——集中在 SSEHub 内部
  - **推荐方案 B**：修改 `broadcast()` 签名为 `broadcast(task_id, event, parent_task_id=None)`

---

## 7. A2A 协议模型分析

**文件**: `octoagent/packages/protocol/src/octoagent/protocol/models.py`

### 7.1 已有消息类型

6 种完整定义：`TASK / UPDATE / CANCEL / RESULT / ERROR / HEARTBEAT`

每种都有对应的 Payload 模型：
- `A2ATaskPayload`：user_text, metadata, resume_*
- `A2AUpdatePayload`：state, summary, requested_input
- `A2ACancelPayload`：reason
- `A2AResultPayload`：state, worker_id, summary, artifacts, retryable
- `A2AErrorPayload`：state, error_type, error_message, retryable
- `A2AHeartbeatPayload`：state, worker_id, loop_step, max_steps, summary

### 7.2 P1-A/B 复用策略

- Subagent spawn → 发送 `A2AMessageType.TASK` 消息
- Subagent 进度 → 发送 `A2AMessageType.HEARTBEAT` 消息
- Subagent 完成 → 发送 `A2AMessageType.RESULT` 消息
- Subagent 取消 → 发送 `A2AMessageType.CANCEL` 消息
- Subagent 异常 → 发送 `A2AMessageType.ERROR` 消息
- Subagent 需要输入 → 发送 `A2AMessageType.UPDATE`（state=input-required）

**不需要新增消息类型**（NG5），但 `A2AErrorPayload` 需补充 `error_category` 和 `recovery_hint`（**Constitution #13**）。

### 7.3 A2AMessage URI 格式

- 当前 `from_agent` / `to_agent` 要求 `agent://` 前缀
- Subagent URI 建议：`agent://workers/{parent_id}/subagents/{subagent_id}`

---

## 8. Orchestrator 关键集成点

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py`

### 8.1 Orchestrator 当前职责

1. 请求封装与高风险 gate
2. 单 worker 路由与派发
3. 控制平面事件写入（ORCH_DECISION / WORKER_DISPATCHED / WORKER_RETURNED）
4. Worker 结果回传与失败分类

### 8.2 P1-B 集成点

Subagent 结果注入需要在 Orchestrator 层：
- 监听 A2A RESULT 消息（或 Child Task 终态事件）
- 写入 `A2A_MESSAGE_RECEIVED` 事件到父 Task
- 将结果摘要注入父 Worker 的对话历史

**核心架构决策（C-07）**：
- 父 Worker 不暂停主循环（异步模式）
- 结果通过 `asyncio.Queue` 注入（event-driven）
- 多结果并发通过 `asyncio.Lock` 序列化

---

## 9. 现有模型变更清单

| 模型/类 | 文件 | 需新增字段 | 原因 |
|---------|------|-----------|------|
| `ToolCallSpec` | skills/models.py | `tool_call_id: str = ""` | FR-064-11 |
| `ToolFeedbackMessage` | skills/models.py | `tool_call_id: str = ""` | C-04，回填关联 |
| `SkillExecutionContext` | skills/models.py | `parent_task_id: str \| None = None` | FR-064-13 |
| `SkillManifest` | skills/manifest.py | `compaction_model_alias: str \| None`, `heartbeat_interval_steps: int = 5` | FR-064-30, FR-064-17 |
| `Task` | core/models/task.py | `parent_task_id: str \| None = None` | C-01 |
| `EventType` | core/models/enums.py | `TOOL_BATCH_STARTED`, `TOOL_BATCH_COMPLETED` | FR-064-05 |
| `ToolBrokerProtocol` | tooling/protocols.py | `get_tool_meta(tool_name) -> ToolMeta \| None` | C-02 |

---

## 10. 实现风险评估

| 风险项 | 等级 | 解决方案 |
|--------|------|---------|
| asyncio.gather 中 task_seq 竞争 | 中 | 预分配 seq 范围或 asyncio.Lock |
| Responses API call_id 脱节 | 高 | 条件启用：call_id 验证通过才使用标准回填 |
| 父 Worker 对话历史并发写入 | 中 | asyncio.Lock 保护注入操作 |
| Subagent 孤儿检测 | 低 | 复用 Watchdog 扫描逻辑 + parent_task_id 索引 |
| 上下文压缩丢失关键信息 | 中 | 保护 system prompt + 最近 N 轮 + 压缩事件审计 |
