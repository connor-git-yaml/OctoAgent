# Feature 064 技术规划

> **Feature**: 并行工具调用 + Subagent 编排增强
> **Spec Version**: 2026-03-19 Draft
> **Plan Version**: v1.0
> **优先级分组**: P0（阻断 → 先行）→ P1（核心增强）→ P2（体验优化）

---

## 1. 架构概述

### 1.1 改动全景

Feature 064 涉及 6 个子系统、36 条 FR 的实现。按依赖关系和优先级划分为三个阶段：

```
Phase 1 (P0): 工具执行层修复
  ├── P0-A: SkillRunner 并行分桶
  └── P0-B: LiteLLMSkillClient 回填格式修复

Phase 2 (P1): Subagent 编排
  ├── P1-A: Subagent 独立执行循环
  └── P1-B: Subagent Announce 机制

Phase 3 (P2): 长任务增强
  ├── P2-A: 上下文压缩
  └── P2-B: 后台执行与通知
```

### 1.2 核心改动文件

| 文件 | Phase | 改动类型 |
|------|-------|---------|
| `packages/skills/src/octoagent/skills/runner.py` | P0-A | 重写 `_execute_tool_calls()` |
| `packages/skills/src/octoagent/skills/litellm_client.py` | P0-B, P2-A | 重写回填逻辑 + 新增压缩模块 |
| `packages/skills/src/octoagent/skills/models.py` | P0-A/B | 扩展 `ToolCallSpec` / `ToolFeedbackMessage` / `SkillExecutionContext` |
| `packages/skills/src/octoagent/skills/manifest.py` | P1-A, P2-A | 扩展 `SkillManifest` |
| `packages/core/src/octoagent/core/models/enums.py` | P0-A | 新增 EventType |
| `packages/core/src/octoagent/core/models/task.py` | P1-A | 新增 `parent_task_id` |
| `packages/tooling/src/octoagent/tooling/protocols.py` | P0-A | 新增 `get_tool_meta()` |
| `packages/tooling/src/octoagent/tooling/broker.py` | P0-A | 实现 `get_tool_meta()` |
| `apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` | P1-A | 重大扩展 |
| `apps/gateway/src/octoagent/gateway/services/orchestrator.py` | P1-B | 扩展结果注入逻辑 |
| `apps/gateway/src/octoagent/gateway/services/sse_hub.py` | P1-B | 扩展双路广播 |

---

## 2. CRITICAL 问题解决方案（C-01 到 C-07）

### C-01: Task 模型缺少 `parent_task_id` 字段

**决策**: 在 `Task` 模型新增 `parent_task_id: str | None = None` 字段。

**实现**:
1. **模型层**: `packages/core/src/octoagent/core/models/task.py` → `Task` 类新增字段：
   ```python
   parent_task_id: str | None = Field(default=None, description="父任务 ID（Subagent Child Task 用）")
   ```
2. **DB Schema Migration**: `TaskStore` 的 SQLite 表新增 `parent_task_id TEXT DEFAULT NULL` 列。
   使用 `ALTER TABLE tasks ADD COLUMN parent_task_id TEXT DEFAULT NULL` 在线迁移（SQLite 支持 ADD COLUMN 不锁表）。
3. **索引**: 新增索引 `CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id)` 用于查询子任务列表。
4. **TaskStore CRUD 适配**: `save_task()` / `get_task()` 适配新字段；新增 `list_child_tasks(parent_task_id) -> list[Task]`。
5. **事件投影**: `STATE_TRANSITION` 事件投影逻辑不变（`parent_task_id` 是静态字段，仅在创建时设置）。

**影响范围**: P1-A 全部 FR 的前提。Phase 2 开始前必须完成。

---

### C-02: ToolBrokerProtocol 缺少按名称查询 SideEffectLevel 的接口

**决策**: 在 `ToolBrokerProtocol` 新增 `get_tool_meta(tool_name: str) -> ToolMeta | None` 方法。

**实现**:
1. **Protocol 扩展**: `packages/tooling/src/octoagent/tooling/protocols.py`
   ```python
   async def get_tool_meta(self, tool_name: str) -> ToolMeta | None:
       """按名称查询工具元数据（含 SideEffectLevel）。O(1) 复杂度。"""
       ...
   ```
2. **ToolBroker 实现**: `packages/tooling/src/octoagent/tooling/broker.py`
   ```python
   async def get_tool_meta(self, tool_name: str) -> ToolMeta | None:
       entry = self._registry.get(tool_name)
       return entry[0] if entry else None
   ```
3. **SkillRunner 调用方式**:
   ```python
   meta = await self._tool_broker.get_tool_meta(call.tool_name)
   level = meta.side_effect_level if meta else SideEffectLevel.IRREVERSIBLE  # 未知工具视为高风险
   ```

**影响范围**: P0-A 分桶逻辑前提。

---

### C-03: 并行工具调用与 Feature 061 审批流（ask 信号桥接）的交互

**决策**:
1. 分桶执行顺序固定：**NONE 并行 → REVERSIBLE 串行 → IRREVERSIBLE 逐个审批**
2. IRREVERSIBLE 桶中工具**逐个审批**（不批量）——每个工具独立触发 `_handle_ask_bridge()`
3. 审批等待期间 SkillRunner **yield 回主循环**：`_handle_ask_bridge()` 内部 `await` 审批结果，asyncio event loop 正常调度其他协程
4. 审批超时：由 `ApprovalBridgeProtocol.handle_ask()` 内部超时机制处理（已有），返回 "timeout" → 作为工具失败反馈给 LLM

**实现**:
- IRREVERSIBLE 桶的执行逻辑与当前串行模式一致（保留 `_handle_ask_bridge()` 原样）
- 审批期间 Task 状态由 ToolBroker 的 `PresetBeforeHook` 管理（已有流程），不在 SkillRunner 层额外管理

---

### C-04: ToolFeedbackMessage 缺少 `tool_call_id` + 回填格式重构协调

**决策**:
1. `ToolFeedbackMessage` 新增 `tool_call_id: str = ""` 字段
2. `ToolCallSpec` 新增 `tool_call_id: str = ""` 字段
3. `LiteLLMSkillClient.generate()` 在 step > 1 时按新逻辑回填

**实现（Chat Completions 路径）**:
1. 当 `generate()` 返回 `tool_calls` 时，追加标准 `assistant` message：
   ```python
   history.append({
       "role": "assistant",
       "content": content or None,
       "tool_calls": [
           {
               "id": tc["id"],
               "type": "function",
               "function": {
                   "name": _to_fn_name(tc["tool_name"]),
                   "arguments": json.dumps(tc["arguments"]),
               },
           }
           for tc in tool_calls
       ],
   })
   ```
2. 在 step > 1 回填 feedback 时，使用 `tool` role message：
   ```python
   for fb in feedback:
       if fb.tool_call_id:
           history.append({
               "role": "tool",
               "tool_call_id": fb.tool_call_id,
               "content": fb.output if not fb.is_error else f"ERROR: {fb.error}",
           })
       else:
           # 向后兼容：无 tool_call_id 时回退自然语言
           ...
   ```
3. `_build_tool_feedback()` 中将 `ToolCallSpec.tool_call_id` 传递到 `ToolFeedbackMessage.tool_call_id`

**实现（Responses API 路径）**:
1. 追加 `function_call` items 到 history
2. 回填使用 `function_call_output` type
3. **条件启用**：仅当 `call_id` 非空且验证通过时使用标准回填；否则回退自然语言（**W-04** 缓解）

**结果合并顺序保证**:
- SkillRunner `_execute_tool_calls()` 返回的 `list[ToolFeedbackMessage]` 按原始 `tool_calls` 顺序排列
- 并行组结果通过 `asyncio.gather()` 返回的列表顺序与输入一致
- 回填时按相同顺序追加到 history

---

### C-05: Subagent 独立 SkillRunner 的依赖来源

**决策**: Subagent SkillRunner 从父 Worker 衍生（derive）配置，不独立发现。

**实现**:
1. **SkillManifest**: 复制父 Worker 的 manifest，覆盖以下字段：
   - `skill_id` → `"subagent-{ulid}"`
   - `description` → Subagent 的 `task_description`
   - `permission_mode` → 继承父 Worker
   - `tools_allowed` → 继承父 Worker（Subagent 不额外限制，权限由 preset 控制）
2. **ToolBroker**: 共享父 Worker 的 `ToolBroker` 实例（工具注册表是进程全局的，共享安全）
3. **LiteLLMSkillClient**: 新建实例，使用 `child_task_id:trace_id` 作为对话历史 key（隔离对话上下文）
4. **UsageLimits**: Subagent 获得独立的 `UsageLimits`（默认值），不与父 Worker 共享配额。
   - 默认 `max_steps=30`，`max_duration_seconds=1800`（30 分钟）
   - 可通过 `subagents.spawn` 工具参数覆盖
5. **PermissionPreset**: 继承父 Worker 的 preset，但支持通过 spawn 参数指定更严格的 preset（不得高于父 Worker）

---

### C-06: 上下文压缩执行时机与机制

**决策**:
1. 压缩在 `generate()` 调用**前同步执行**（在构建请求之前先压缩历史）
2. 压缩 token 消耗**不计入** `UsageTracker`（作为基础设施开销）
3. compaction model 使用**独立的 httpx 调用**（不复用 LiteLLMSkillClient 实例）
4. 补充 `CONTEXT_COMPACTION_FAILED` 事件；压缩失败时**降级为简单截断**（Constitution #6）

**实现**:
1. 新增 `ContextCompactor` 类（`packages/skills/src/octoagent/skills/compactor.py`）：
   ```python
   class ContextCompactor:
       async def compact(
           self,
           history: list[dict],
           max_tokens: int,
           threshold_ratio: float = 0.8,
           compaction_model_alias: str | None = None,
       ) -> CompactionResult
   ```
2. 三级压缩策略：
   - **Level 1**: 截断 > 2000 字符的 tool role message 为前 500 字符 + "...[truncated]"
   - **Level 2**: 保留最近 N=8 轮（可配置），早期轮次用 LLM 生成摘要替换
   - **Level 3**: 丢弃最早的摘要块（保留 system prompt + 最近 8 轮）
3. system prompt（`history[0]` if role=system）和最近一轮 user/assistant **永不压缩**
4. `LiteLLMSkillClient.generate()` 在构建请求前调用 `ContextCompactor.compact()`

---

### C-07: Subagent 结果注入父 Worker 对话的机制

**决策**: 父 Worker **不暂停主循环**，采用 event-driven 异步注入模式。

**架构**:
```
Subagent 完成 → A2A RESULT → Orchestrator 接收
  ├── 1. 写入 A2A_MESSAGE_RECEIVED 事件到父 Task
  ├── 2. SSE Hub 广播到父 Task 订阅者
  └── 3. 将结果摘要放入 SubagentResultQueue
         → 父 Worker SkillRunner 在每个 step 开始前检查队列
         → 有结果时追加到 feedback 列表（作为特殊的系统消息注入对话）
```

**实现**:
1. **SubagentResultQueue**: 新增 `asyncio.Queue` per 父 Worker（挂载在 Orchestrator 上）
   ```python
   # Orchestrator 内部
   _subagent_result_queues: dict[str, asyncio.Queue]  # parent_task_id -> queue
   ```
2. **注入时机**: SkillRunner `run()` 主循环中，每个 step 开始前（`generate()` 调用前），通过 hook 或直接检查队列
3. **注入格式**: 作为 `user` role system message 注入：
   ```python
   {
       "role": "user",
       "content": f"[Subagent Result] Subagent '{subagent_name}' (task: {child_task_id}) completed:\n{summary}"
   }
   ```
4. **多结果序列化**: `asyncio.Queue` 天然 FIFO，按到达顺序逐个 `get_nowait()` 消费
5. **并发安全**: Queue 的 `put()` 和 `get_nowait()` 在 asyncio 单线程模型下天然安全，无需额外 Lock

---

## 3. 模块改动清单

### P0-A: 并行工具调用（8 FR）

| 文件 | 改动 | FR |
|------|------|-----|
| `tooling/protocols.py` | 新增 `get_tool_meta(tool_name) -> ToolMeta \| None` 方法 | FR-064-01 前置 |
| `tooling/broker.py` | 实现 `get_tool_meta()` → `self._registry.get(name)[0]` | FR-064-01 前置 |
| `core/models/enums.py` | 新增 `TOOL_BATCH_STARTED` / `TOOL_BATCH_COMPLETED` EventType | FR-064-05 |
| `skills/models.py` | `ToolCallSpec` 新增 `tool_call_id: str = ""` | FR-064-11 |
| `skills/models.py` | `ToolFeedbackMessage` 新增 `tool_call_id: str = ""` | C-04 |
| `skills/runner.py` | 重写 `_execute_tool_calls()` → 分桶并行逻辑 | FR-064-01~08 |
| `skills/runner.py` | 新增 `_emit_tool_batch_started()` / `_emit_tool_batch_completed()` | FR-064-05~06 |
| `skills/runner.py` | `_build_tool_feedback()` 传递 `tool_call_id` | FR-064-11 |

**`_execute_tool_calls()` 重写细节**:

```python
async def _execute_tool_calls(self, *, manifest, execution_context, tool_calls, skip_remaining_tools):
    # 1. 白名单校验（保持原逻辑）
    allowed = resolve_effective_tool_allowlist(...)
    for call in tool_calls:
        if allowed and call.tool_name not in allowed:
            raise SkillToolExecutionError(...)

    # 2. 分桶
    bucket_none = []      # SideEffectLevel.NONE → 并行
    bucket_reversible = [] # SideEffectLevel.REVERSIBLE → 串行
    bucket_irreversible = [] # SideEffectLevel.IRREVERSIBLE → 审批串行

    for call in tool_calls:
        meta = await self._tool_broker.get_tool_meta(call.tool_name)
        level = meta.side_effect_level if meta else SideEffectLevel.IRREVERSIBLE
        if level == SideEffectLevel.NONE:
            bucket_none.append(call)
        elif level == SideEffectLevel.REVERSIBLE:
            bucket_reversible.append(call)
        else:
            bucket_irreversible.append(call)

    results: list[ToolFeedbackMessage] = []
    batch_id = str(ULID()) if len(tool_calls) > 1 else None

    # 3. 发射 TOOL_BATCH_STARTED（仅 batch_size > 1）
    if batch_id:
        await self._emit_tool_batch_started(execution_context, batch_id, tool_calls)

    # 4a. 并行执行 NONE 桶（asyncio.gather + return_exceptions=True）
    if bucket_none:
        coros = [self._execute_single_tool(manifest, execution_context, call) for call in bucket_none]
        parallel_results = await asyncio.gather(*coros, return_exceptions=True)
        for call, result in zip(bucket_none, parallel_results):
            if isinstance(result, Exception):
                results.append(ToolFeedbackMessage(
                    tool_name=call.tool_name, tool_call_id=call.tool_call_id,
                    is_error=True, output="", error=str(result), duration_ms=0))
            else:
                results.append(result)

    # 4b. 串行执行 REVERSIBLE 桶
    for call in bucket_reversible:
        fb = await self._execute_single_tool(manifest, execution_context, call)
        results.append(fb)
        if skip_remaining_tools:
            break

    # 4c. 逐个审批执行 IRREVERSIBLE 桶
    for call in bucket_irreversible:
        fb = await self._execute_single_tool(manifest, execution_context, call)
        results.append(fb)
        if skip_remaining_tools:
            break

    # 5. 发射 TOOL_BATCH_COMPLETED
    if batch_id:
        await self._emit_tool_batch_completed(execution_context, batch_id, results)

    # 6. 按原始 tool_calls 顺序重排结果
    return self._reorder_results(tool_calls, results)
```

新增 `_execute_single_tool()` 方法：提取当前 `for call in tool_calls:` 循环体中的单工具执行逻辑。

---

### P0-B: 修复工具结果回填格式（4 FR）

| 文件 | 改动 | FR |
|------|------|-----|
| `skills/litellm_client.py` | 重写 step > 1 feedback 回填逻辑（第 517-538 行） | FR-064-09 |
| `skills/litellm_client.py` | 重写 assistant message 追加逻辑（第 569-571 行） | FR-064-09 |
| `skills/litellm_client.py` | Responses API 路径条件启用标准回填 | FR-064-10 |
| `skills/litellm_client.py` | `ToolCallSpec` 构造传入 `tool_call_id` | FR-064-11 |
| `skills/litellm_client.py` | 向后兼容：`tool_call_id` 为空时回退自然语言 | FR-064-12 |

**回填重写细节**:

```python
# generate() 中 step > 1 的 feedback 回填
if step > 1 and feedback:
    has_standard_ids = any(fb.tool_call_id for fb in feedback)

    if has_standard_ids and not use_responses_api:
        # Chat Completions 标准回填
        for fb in feedback:
            history.append({
                "role": "tool",
                "tool_call_id": fb.tool_call_id,
                "content": fb.output if not fb.is_error else f"ERROR: {fb.error}",
            })
    elif has_standard_ids and use_responses_api:
        # Responses API 标准回填（条件启用）
        for fb in feedback:
            history.append({
                "type": "function_call_output",
                "call_id": fb.tool_call_id,
                "output": fb.output if not fb.is_error else f"ERROR: {fb.error}",
            })
    else:
        # 向后兼容：自然语言回填（保持原逻辑）
        ...
```

**assistant message 追加重写**:

```python
# 返回 tool_calls 时追加 assistant message
if tool_calls:
    if not use_responses_api:
        # Chat Completions: 追加标准 assistant tool_calls
        history.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": _to_fn_name(tc["tool_name"]),
                        "arguments": json.dumps(tc["arguments"]),
                    },
                }
                for tc in tool_calls
            ],
        })
    else:
        # Responses API: 追加 function_call items
        for tc in tool_calls:
            history.append({
                "type": "function_call",
                "call_id": tc["id"],
                "name": _to_fn_name(tc["tool_name"]),
                "arguments": json.dumps(tc["arguments"]),
            })
```

---

### P1-A: Subagent 独立执行循环（8 FR）

| 文件 | 改动 | FR |
|------|------|-----|
| `core/models/task.py` | `Task` 新增 `parent_task_id` | FR-064-13 |
| `core/store/task_store.py` | 适配新字段 + 新增 `list_child_tasks()` | FR-064-13 |
| `skills/models.py` | `SkillExecutionContext` 新增 `parent_task_id` | FR-064-13 |
| `skills/manifest.py` | `SkillManifest` 新增 `heartbeat_interval_steps` | FR-064-17 |
| `gateway/services/subagent_lifecycle.py` | 重写 `spawn_subagent()` → 含 Task + A2A + SkillRunner | FR-064-13~15 |
| `gateway/services/subagent_lifecycle.py` | 新增 `SubagentExecutor` 类 | FR-064-15~20 |
| `gateway/services/subagent_lifecycle.py` | 扩展 `kill_subagent()` → 含 Task 终态 + A2A CANCEL | FR-064-18 |

**SubagentExecutor 核心设计**:

```python
class SubagentExecutor:
    """管理单个 Subagent 的独立执行循环。"""

    def __init__(self, *, child_task: Task, skill_runner: SkillRunner,
                 manifest: SkillManifest, execution_context: SkillExecutionContext,
                 a2a_conversation_id: str, parent_task_id: str,
                 event_store: EventStoreProtocol, store_group: StoreGroup):
        self._child_task = child_task
        self._runner = skill_runner
        self._manifest = manifest
        self._context = execution_context
        self._cancel_event = asyncio.Event()
        self._asyncio_task: asyncio.Task | None = None

    async def start(self):
        """启动独立 asyncio.Task 执行循环。"""
        self._asyncio_task = asyncio.create_task(self._run_loop())

    async def _run_loop(self):
        """独立执行循环，含心跳上报和优雅终止。"""
        try:
            result = await self._runner.run(
                manifest=self._manifest,
                execution_context=self._context,
                skill_input={},
                prompt=self._context.metadata.get("task_description", ""),
            )
            # 发送 A2A RESULT
            await self._send_result(result)
            # 流转 Child Task 到 SUCCEEDED
        except asyncio.CancelledError:
            # 发送 A2A CANCEL response
            # 流转 Child Task 到 CANCELLED
        except Exception as exc:
            # 发送 A2A ERROR
            # 流转 Child Task 到 FAILED
        finally:
            # 清理资源
            await self._cleanup()

    async def cancel(self):
        """优雅取消。"""
        self._cancel_event.set()
        if self._asyncio_task:
            self._asyncio_task.cancel()
```

---

### P1-B: Subagent Announce 机制（5 FR）

| 文件 | 改动 | FR |
|------|------|-----|
| `gateway/services/sse_hub.py` | `broadcast()` 新增 `parent_task_id` 参数 | FR-064-23 |
| `gateway/services/orchestrator.py` | 新增 Subagent 结果接收处理 | FR-064-22, 24 |
| `gateway/services/orchestrator.py` | 新增 `_subagent_result_queues` | FR-064-24 |
| `gateway/services/subagent_lifecycle.py` | 结果发送时写入 A2A_MESSAGE_RECEIVED | FR-064-22 |

---

### P2-A: 上下文压缩（6 FR）

| 文件 | 改动 | FR |
|------|------|-----|
| `skills/compactor.py` | **新建**：`ContextCompactor` 类 | FR-064-26~31 |
| `skills/litellm_client.py` | `generate()` 调用前插入压缩检查 | FR-064-26~27 |
| `skills/manifest.py` | 新增 `compaction_model_alias` / `compaction_threshold_ratio` / `compaction_recent_turns` | FR-064-27, 30 |
| `core/models/enums.py` | 确认 `CONTEXT_COMPACTION_COMPLETED` 已定义（已有） | FR-064-29 |

---

### P2-B: 后台执行与通知（5 FR）

| 文件 | 改动 | FR |
|------|------|-----|
| `gateway/services/notification.py` | **新建**：`NotificationService` + `NotificationChannelProtocol` | FR-064-35 |
| `gateway/services/notification.py` | SSE + Telegram channel 实现 | FR-064-32, 33 |
| `gateway/services/orchestrator.py` | 状态变更时调用 NotificationService | FR-064-32, 34 |
| `plugins/channels/telegram/` | inline keyboard 审批处理 | FR-064-33 |

---

## 4. 数据模型变更

### 4.1 模型扩展

| 模型 | 文件 | 新增字段 | 默认值 | 说明 |
|------|------|---------|--------|------|
| `ToolCallSpec` | skills/models.py | `tool_call_id: str` | `""` | LLM 返回的 function call ID |
| `ToolFeedbackMessage` | skills/models.py | `tool_call_id: str` | `""` | 回填关联 ID |
| `SkillExecutionContext` | skills/models.py | `parent_task_id: str \| None` | `None` | Child Task 关联 |
| `Task` | core/models/task.py | `parent_task_id: str \| None` | `None` | Subagent 父任务 |
| `SkillManifest` | skills/manifest.py | `compaction_model_alias: str \| None` | `None` | 压缩模型 alias |
| `SkillManifest` | skills/manifest.py | `compaction_threshold_ratio: float` | `0.8` | 压缩触发阈值 |
| `SkillManifest` | skills/manifest.py | `compaction_recent_turns: int` | `8` | 保留最近 N 轮 |
| `SkillManifest` | skills/manifest.py | `heartbeat_interval_steps: int` | `5` | Subagent 心跳间隔 |
| `SkillManifest` | skills/manifest.py | `max_concurrent_subagents: int` | `5` | 最大并发 Subagent 数 |

### 4.2 新增 EventType

| EventType | Payload 关键字段 | 说明 |
|-----------|-----------------|------|
| `TOOL_BATCH_STARTED` | batch_id, tool_names, execution_mode, batch_size | 并行批次开始 |
| `TOOL_BATCH_COMPLETED` | batch_id, duration_ms, success_count, error_count | 并行批次完成 |

注：`CONTEXT_COMPACTION_COMPLETED` 已在 EventType 枚举中定义（尚未实现）。

### 4.3 DB Migration

```sql
-- P1-A 前置：Task 表新增 parent_task_id
ALTER TABLE tasks ADD COLUMN parent_task_id TEXT DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id);
```

---

## 5. 接口契约变更

### 5.1 ToolBrokerProtocol 扩展

新增方法：
```python
async def get_tool_meta(self, tool_name: str) -> ToolMeta | None
```

### 5.2 SSEHub.broadcast() 扩展

签名变更：
```python
async def broadcast(
    self,
    task_id: str,
    event: Event,
    parent_task_id: str | None = None,  # 新增
) -> None
```

### 5.3 NotificationChannelProtocol（新增）

```python
class NotificationChannelProtocol(Protocol):
    async def notify(
        self,
        task_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> bool
```

### 5.4 事件 Payload Schema

详见 `contracts/` 目录中的具体定义。

---

## 6. 实现阶段划分

### Phase 1: P0 — 工具执行层修复（预估 3-4 天）

**Step 1.1: 基础设施准备（0.5 天）**
- [ ] `ToolBrokerProtocol` 新增 `get_tool_meta()`
- [ ] `ToolBroker` 实现 `get_tool_meta()`
- [ ] `EventType` 新增 `TOOL_BATCH_STARTED` / `TOOL_BATCH_COMPLETED`
- [ ] `ToolCallSpec` 新增 `tool_call_id`
- [ ] `ToolFeedbackMessage` 新增 `tool_call_id`

**Step 1.2: P0-A 并行分桶（1.5 天）**
- [ ] 提取 `_execute_single_tool()` 方法
- [ ] 实现分桶逻辑（分桶 → 并行执行 → 结果重排）
- [ ] 实现 `_emit_tool_batch_started()` / `_emit_tool_batch_completed()`
- [ ] 单元测试：分桶逻辑、并行执行、部分失败、单工具兼容

**Step 1.3: P0-B 回填格式修复（1.5 天）**
- [ ] `LiteLLMSkillClient` 重写 assistant message 追加逻辑
- [ ] `LiteLLMSkillClient` 重写 step > 1 feedback 回填逻辑
- [ ] Responses API 路径条件启用
- [ ] 向后兼容测试：无 tool_call_id 时回退自然语言
- [ ] 集成测试：Chat Completions / Responses API 两条路径

### Phase 2: P1 — Subagent 编排（预估 5-6 天）

**Step 2.0: 前置基础设施（1 天）**
- [ ] `Task` 模型新增 `parent_task_id`
- [ ] DB Migration
- [ ] `TaskStore` CRUD 适配 + `list_child_tasks()`
- [ ] `SkillExecutionContext` 新增 `parent_task_id`
- [ ] `SkillManifest` 新增 `heartbeat_interval_steps` / `max_concurrent_subagents`

**Step 2.1: P1-A Subagent 独立执行（2.5 天）**
- [ ] `SubagentExecutor` 类实现
- [ ] 重写 `spawn_subagent()` → 含 Task + A2A + SkillRunner
- [ ] 扩展 `kill_subagent()` → 含 Task 终态 + A2A CANCEL
- [ ] 心跳上报机制
- [ ] 异常退出清理
- [ ] 单元测试 + 集成测试

**Step 2.2: P1-B Subagent Announce（1.5 天）**
- [ ] SSEHub `broadcast()` 扩展双路广播
- [ ] Orchestrator 结果接收 + A2A_MESSAGE_RECEIVED 事件
- [ ] SubagentResultQueue 实现
- [ ] 结果注入父 Worker 对话
- [ ] 多 Subagent 并行测试

### Phase 3: P2 — 长任务增强（预估 4-5 天）

**Step 3.1: P2-A 上下文压缩（3 天）**
- [ ] `ContextCompactor` 类实现（三级压缩）
- [ ] token 估算逻辑
- [ ] LLM 摘要生成（独立 httpx 调用）
- [ ] `LiteLLMSkillClient` 集成压缩检查
- [ ] `CONTEXT_COMPACTION_COMPLETED` 事件发射
- [ ] 压缩失败降级处理
- [ ] 压缩测试

**Step 3.2: P2-B 后台通知（2 天）**
- [ ] `NotificationService` 实现
- [ ] SSE NotificationChannel 实现
- [ ] Telegram NotificationChannel 实现（含 inline keyboard）
- [ ] 通知去重（event_id 幂等）
- [ ] Orchestrator 集成

---

## 7. 测试策略

### 7.1 单元测试

| 模块 | 测试重点 |
|------|---------|
| 分桶逻辑 | 按 SideEffectLevel 正确分桶；空桶处理；单工具不生成 BATCH 事件 |
| 并行执行 | asyncio.gather 正确并行；return_exceptions=True 隔离失败；结果顺序正确 |
| 回填格式 | Chat Completions tool role message；Responses API function_call_output；向后兼容 |
| ToolCallSpec | tool_call_id 传递链路完整（LLM → ToolCallSpec → ToolFeedbackMessage → 回填） |
| SubagentExecutor | 正常完成 → SUCCEEDED；异常 → FAILED；取消 → CANCELLED |
| ContextCompactor | 三级压缩策略；system prompt 保护；最近 N 轮保护 |
| NotificationService | 通知去重；channel 降级 |

### 7.2 集成测试

| 场景 | 验证点 |
|------|--------|
| 3 个 NONE 工具并行 | 总耗时 < 最慢单个 x 1.5 |
| 混合 NONE + REVERSIBLE + IRREVERSIBLE | 执行顺序正确（READ → WRITE → DESTRUCTIVE） |
| 多轮工具调用循环 | 对话历史中 tool_calls 和 tool results 格式正确 |
| Subagent spawn → execute → result | 全生命周期事件流完整 |
| 父 Worker 收到 Subagent 结果 | 自动恢复处理 |
| 长对话压缩 | 压缩后对话可正常继续 |

### 7.3 回归测试

- 所有现有 Skill 和 SKILL.md 配置零修改验证（NFR-064-02）
- 单工具调用场景行为不变
- 现有 ToolBroker hook chain 不受影响

---

## 8. 回滚方案

### 8.1 P0-A 回滚

- **Feature Flag**: `ENABLE_PARALLEL_TOOL_CALLS=false`
- 关闭后 `_execute_tool_calls()` 回退到串行 for 循环
- TOOL_BATCH 事件不生成

### 8.2 P0-B 回滚

- **Feature Flag**: `ENABLE_STANDARD_TOOL_BACKFILL=false`
- 关闭后回退到自然语言回填
- `tool_call_id` 为空时自动回退（已内置）

### 8.3 P1 回滚

- `spawn_subagent()` 回退到 Feature 059 版本（仅创建 Runtime + Session，不含 Task 和 SkillRunner）
- `parent_task_id` 字段保留（DB 列不删除），但不再填充

### 8.4 P2-A 回滚

- `ContextCompactor` 不调用即可跳过
- `compaction_threshold_ratio` 设为 1.0 则永远不触发

### 8.5 P2-B 回滚

- `NotificationService` 不注册 channel 则不发通知
- SSE Hub 广播不受影响

---

## 附录 A: 澄清报告 WARNING 级别处理决策

| # | 问题 | 决策 |
|---|------|------|
| W-01 | asyncio.gather 异常处理 | 使用 `return_exceptions=True`，超时工具标记 `is_error=True` |
| W-02 | SideEffectLevel 术语统一 | 代码中统一使用枚举值 `NONE/REVERSIBLE/IRREVERSIBLE` |
| W-03 | 分桶顺序与 LLM 意图冲突 | 标记为 Known Limitation；不提供保守模式（NG3 已排除自动策略选择） |
| W-04 | Responses API call_id 一致性 | 条件启用：call_id 验证通过才使用标准回填 |
| W-05 | SSE Hub 双路广播内存 | 由调用方传入 `parent_task_id`，SSEHub 不维护映射 |
| W-06 | Subagent preset 降级 | 不强制降级，但 spawn 时可指定更严格的 preset |
| W-07 | 压缩保留最近 N 轮 | N 默认值 = 8，可通过 SkillManifest.compaction_recent_turns 配置 |
| W-08 | Notification Channel 注册 | 系统启动时注册，挂载到 Orchestrator |
| W-09 | Telegram 审批集成 | P2-B 实现，新增 callback query handler |
| W-10 | 并行 task_seq 竞争 | 并行组中每个工具的事件由 ToolBroker 内部生成（已有 Lock），SkillRunner 层的 BATCH 事件在并行执行前/后生成（无竞争） |
| W-11 | HEARTBEAT 与 Watchdog | Watchdog 按 task_id 隔离监听，Child Task 心跳不影响父 Task |
| W-12 | 工具输出截断边界 | Level 1 截断为简单截取（前 500 字符 + `...[truncated]`），不使用 LLM |

## 附录 B: INFO 级别处理决策

| # | 决策 |
|---|------|
| I-01 | TOOL_BATCH 事件通过 batch_id 关联；前端按需适配 |
| I-02 | 单 tool_call 不生成 TOOL_BATCH 事件（向后兼容） |
| I-03 | 并行并发度上限默认 10（asyncio.Semaphore），可通过 SkillManifest 配置 |
| I-04 | Subagent 获得独立 UsageLimits（默认值），不共享父 Worker 配额 |
| I-05 | compaction_model_alias 默认 None → 使用 `compaction` alias（需在 LiteLLM Proxy 预配置） |
| I-06 | Telegram 通知消息使用中文，包含 Task 标题 + 状态 + 耗时 |
| I-07 | 上下文压缩统一在 history 列表层面操作，两条 API 路径共享逻辑 |
| I-08 | 孤儿 Subagent 检测复用 Watchdog 扫描 + parent_task_id 索引 |
| I-09 | Subagent URI 格式：`agent://workers/{parent_id}/subagents/{subagent_id}` |
