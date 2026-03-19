# Feature 064 任务分解

> **Feature**: 并行工具调用 + Subagent 编排增强
> **Spec Version**: 2026-03-19 Draft
> **Plan Version**: v1.0
> **生成时间**: 2026-03-19
> **总计**: 22 个 Task（P0: 8 / P1: 9 / P2: 5）

---

## 任务总览

| 编号 | 标题 | 优先级 | 复杂度 | 依赖 | 并行组 |
|------|------|--------|--------|------|--------|
| T-064-01 | ToolBrokerProtocol 新增 `get_tool_meta()` | P0 | S | — | A |
| T-064-02 | EventType 新增 TOOL_BATCH 事件类型 | P0 | S | — | A |
| T-064-03 | ToolCallSpec / ToolFeedbackMessage 新增 `tool_call_id` 字段 | P0 | S | — | A |
| T-064-04 | SkillRunner 提取 `_execute_single_tool()` 方法 | P0 | M | T-064-01 | — |
| T-064-05 | SkillRunner 实现并行分桶执行逻辑 | P0 | L | T-064-01, T-064-02, T-064-03, T-064-04 | — |
| T-064-06 | SkillRunner 并行分桶单元测试 | P0 | M | T-064-05 | B |
| T-064-07 | LiteLLMSkillClient 重写工具结果回填格式 | P0 | L | T-064-03 | — |
| T-064-08 | LiteLLMSkillClient 回填格式测试 | P0 | M | T-064-07 | — |
| T-064-09 | Task 模型新增 `parent_task_id` + DB Migration | P1 | M | — | C |
| T-064-10 | SkillExecutionContext / SkillManifest 扩展 Subagent 相关字段 | P1 | S | — | C |
| T-064-11 | TaskStore CRUD 适配 + `list_child_tasks()` | P1 | S | T-064-09 | — |
| T-064-12 | SubagentExecutor 类实现（独立执行循环核心） | P1 | L | T-064-09, T-064-10, T-064-11 | — |
| T-064-13 | 重写 `spawn_subagent()` — 含 Child Task + A2A + SkillRunner | P1 | L | T-064-12 | — |
| T-064-14 | 扩展 `kill_subagent()` — 含 Task 终态 + A2A CANCEL | P1 | M | T-064-12 | — |
| T-064-15 | Subagent 独立执行循环单元测试 + 集成测试 | P1 | M | T-064-13, T-064-14 | — |
| T-064-16 | SSEHub `broadcast()` 双路广播扩展 | P1 | S | T-064-09 | D |
| T-064-17 | Orchestrator Subagent 结果接收 + SubagentResultQueue | P1 | M | T-064-12, T-064-16 | — |
| T-064-18 | ContextCompactor 类实现（三级压缩策略） | P2 | L | — | E |
| T-064-19 | LiteLLMSkillClient 集成上下文压缩 | P2 | M | T-064-18 | — |
| T-064-20 | 上下文压缩测试 | P2 | M | T-064-19 | — |
| T-064-21 | NotificationService + NotificationChannelProtocol 实现 | P2 | M | — | E |
| T-064-22 | Telegram 审批通知 + 通知去重 + Orchestrator 集成 | P2 | M | T-064-21 | — |

---

## P0: 工具执行层修复

### T-064-01: ToolBrokerProtocol 新增 `get_tool_meta()`

- **FR**: FR-064-01 前置（C-02）
- **复杂度**: S
- **依赖**: 无
- **并行组**: A（与 T-064-02、T-064-03 可并行）
- **涉及文件**:
  - `octoagent/packages/tooling/src/octoagent/tooling/protocols.py` — Protocol 新增 `get_tool_meta(tool_name: str) -> ToolMeta | None` 方法
  - `octoagent/packages/tooling/src/octoagent/tooling/broker.py` — ToolBroker 实现：`self._registry.get(name)[0]`
- **验收标准**:
  1. `ToolBrokerProtocol` 新增 `get_tool_meta()` 抽象方法，签名含类型注解和 docstring
  2. `ToolBroker.get_tool_meta()` 实现 O(1) 复杂度查找，未注册工具返回 `None`
  3. 单元测试覆盖：已注册工具返回 ToolMeta / 未注册工具返回 None / 返回值含 `side_effect_level`

---

### T-064-02: EventType 新增 TOOL_BATCH 事件类型

- **FR**: FR-064-05
- **复杂度**: S
- **依赖**: 无
- **并行组**: A（与 T-064-01、T-064-03 可并行）
- **涉及文件**:
  - `octoagent/packages/core/src/octoagent/core/models/enums.py` — 新增 `TOOL_BATCH_STARTED` / `TOOL_BATCH_COMPLETED` 枚举值
- **验收标准**:
  1. `EventType` 枚举新增 `TOOL_BATCH_STARTED = "TOOL_BATCH_STARTED"` 和 `TOOL_BATCH_COMPLETED = "TOOL_BATCH_COMPLETED"`
  2. 同步新增 `CONTEXT_COMPACTION_FAILED = "CONTEXT_COMPACTION_FAILED"`（P2-A 预留）
  3. 现有枚举成员无变化，通过现有测试

---

### T-064-03: ToolCallSpec / ToolFeedbackMessage 新增 `tool_call_id` 字段

- **FR**: FR-064-11, FR-064-12, C-04
- **复杂度**: S
- **依赖**: 无
- **并行组**: A（与 T-064-01、T-064-02 可并行）
- **涉及文件**:
  - `octoagent/packages/skills/src/octoagent/skills/models.py` — `ToolCallSpec` 新增 `tool_call_id: str = ""`，`ToolFeedbackMessage` 新增 `tool_call_id: str = ""`
- **验收标准**:
  1. `ToolCallSpec.tool_call_id` 默认为空字符串，不影响现有构造方式（FR-064-12 向后兼容）
  2. `ToolFeedbackMessage.tool_call_id` 默认为空字符串
  3. 字段包含 Field description 说明来源和用途
  4. 现有 ToolCallSpec / ToolFeedbackMessage 的序列化/反序列化测试通过

---

### T-064-04: SkillRunner 提取 `_execute_single_tool()` 方法

- **FR**: FR-064-01~08 前置
- **复杂度**: M
- **依赖**: T-064-01
- **并行组**: 无
- **涉及文件**:
  - `octoagent/packages/skills/src/octoagent/skills/runner.py` — 从 `_execute_tool_calls()` 循环体中提取单工具执行逻辑为独立方法 `_execute_single_tool()`
- **验收标准**:
  1. `_execute_single_tool(manifest, execution_context, call) -> ToolFeedbackMessage` 封装完整的单工具执行流程：before hook → ToolBroker.execute → ask bridge → build feedback → after hook
  2. `_execute_single_tool()` 内部将 `ToolCallSpec.tool_call_id` 传递到 `ToolFeedbackMessage.tool_call_id`
  3. 提取后 `_execute_tool_calls()` 暂时仍为串行 for 循环调用 `_execute_single_tool()`（行为不变）
  4. 所有现有 SkillRunner 测试通过（回归验证）

---

### T-064-05: SkillRunner 实现并行分桶执行逻辑

- **FR**: FR-064-01, FR-064-02, FR-064-03, FR-064-04, FR-064-05, FR-064-06, FR-064-07, FR-064-08
- **复杂度**: L
- **依赖**: T-064-01, T-064-02, T-064-03, T-064-04
- **并行组**: 无
- **涉及文件**:
  - `octoagent/packages/skills/src/octoagent/skills/runner.py` — 重写 `_execute_tool_calls()`，实现分桶并行逻辑；新增 `_emit_tool_batch_started()` / `_emit_tool_batch_completed()`
- **验收标准**:
  1. `_execute_tool_calls()` 按 `SideEffectLevel` 将 tool_calls 分为三桶：NONE（并行）/ REVERSIBLE（串行）/ IRREVERSIBLE（审批串行）
  2. NONE 桶使用 `asyncio.gather(*coros, return_exceptions=True)` 并行执行
  3. 执行顺序固定：NONE 并行 → REVERSIBLE 串行 → IRREVERSIBLE 逐个审批
  4. 未知工具（`get_tool_meta` 返回 None）视为 IRREVERSIBLE（最高风险）
  5. 并行组中单个工具失败不影响其他已完成工具的结果收集（FR-064-07）
  6. `len(tool_calls) > 1` 时发射 `TOOL_BATCH_STARTED` / `TOOL_BATCH_COMPLETED` 事件（FR-064-05、FR-064-06）
  7. 单个 tool_call 不生成 BATCH 事件（向后兼容 NFR-064-02）
  8. 结果列表按原始 `tool_calls` 顺序返回
  9. 并行执行中 task_seq 无竞争（asyncio.Lock 或预分配 seq 范围，参考 W-10）

---

### T-064-06: SkillRunner 并行分桶单元测试

- **FR**: FR-064-01~08 验证
- **复杂度**: M
- **依赖**: T-064-05
- **并行组**: B（与 T-064-07 可并行开发）
- **涉及文件**:
  - `octoagent/packages/skills/tests/test_runner_parallel.py` — 新建测试文件
- **验收标准**:
  1. 测试用例：3 个 NONE 工具并行执行 → 总耗时接近最慢单个（通过 mock sleep 验证）
  2. 测试用例：混合 NONE + REVERSIBLE + IRREVERSIBLE → 执行顺序正确
  3. 测试用例：3 个并行中 1 个失败 → 2 个成功结果 + 1 个错误反馈
  4. 测试用例：单个 tool_call → 不生成 BATCH 事件
  5. 测试用例：BATCH 事件 payload 含 batch_id、tool_names、execution_mode
  6. 测试用例：结果顺序与输入 tool_calls 顺序一致
  7. 回归测试：现有单工具场景行为不变

---

### T-064-07: LiteLLMSkillClient 重写工具结果回填格式

- **FR**: FR-064-09, FR-064-10, FR-064-11, FR-064-12
- **复杂度**: L
- **依赖**: T-064-03
- **并行组**: 无（但可与 T-064-06 并行开发）
- **涉及文件**:
  - `octoagent/packages/skills/src/octoagent/skills/litellm_client.py` — 重写 step > 1 feedback 回填逻辑（~第 517-538 行）；重写 assistant message 追加逻辑（~第 569-571 行）；`ToolCallSpec` 构造传入 `tool_call_id`（~第 576 行）
- **验收标准**:
  1. **Chat Completions 路径**：返回 tool_calls 时追加标准 assistant message（含 `tool_calls` 数组）；工具结果以 `{"role": "tool", "tool_call_id": "xxx", "content": "..."}` 回填（FR-064-09）
  2. **Responses API 路径**：追加 `function_call` items；结果以 `{"type": "function_call_output", "call_id": "xxx", "output": "..."}` 回填（FR-064-10）
  3. **向后兼容**：`tool_call_id` 为空时回退到当前自然语言拼接模式（FR-064-12）
  4. **Responses API call_id 校验**：call_id 非空且格式验证通过才使用标准回填，否则回退（W-04）
  5. `ToolCallSpec` 构造时从 `tc["id"]` 填充 `tool_call_id`
  6. 错误结果（`is_error=True`）同样通过标准 tool role message 回填（FR-064-09 场景 3）

---

### T-064-08: LiteLLMSkillClient 回填格式测试

- **FR**: FR-064-09~12 验证, NFR-064-08
- **复杂度**: M
- **依赖**: T-064-07
- **并行组**: 无
- **涉及文件**:
  - `octoagent/packages/skills/tests/test_litellm_client_backfill.py` — 新建测试文件
- **验收标准**:
  1. 测试用例：Chat Completions 路径 — tool_calls + tool results 使用标准格式
  2. 测试用例：Responses API 路径 — function_call + function_call_output 使用标准格式
  3. 测试用例：向后兼容 — tool_call_id 为空时回退自然语言
  4. 测试用例：多轮工具调用 — 对话历史中格式一致
  5. 测试用例：错误结果 — is_error=True 仍使用标准 tool role message
  6. 集成回归测试：现有 Skill 调用场景（含单工具、多轮）行为不退化

---

## P1: Subagent 编排

### T-064-09: Task 模型新增 `parent_task_id` + DB Migration

- **FR**: FR-064-13 前置（C-01）
- **复杂度**: M
- **依赖**: 无（P1 阶段入口）
- **并行组**: C（与 T-064-10 可并行）
- **涉及文件**:
  - `octoagent/packages/core/src/octoagent/core/models/task.py` — `Task` 新增 `parent_task_id: str | None = Field(default=None)`
  - `octoagent/packages/core/src/octoagent/core/store/task_store.py`（或等效文件）— DB Migration：`ALTER TABLE tasks ADD COLUMN parent_task_id TEXT DEFAULT NULL` + 索引 `idx_tasks_parent_task_id`
- **验收标准**:
  1. `Task.parent_task_id` 字段可选，默认 None，不影响现有 Task 创建
  2. SQLite migration 使用 `ALTER TABLE ... ADD COLUMN`（不锁表）
  3. 新增索引 `idx_tasks_parent_task_id` 用于子任务列表查询
  4. `save_task()` / `get_task()` 正确持久化/读取新字段
  5. 已有 Task（parent_task_id=None）查询不受影响

---

### T-064-10: SkillExecutionContext / SkillManifest 扩展 Subagent 相关字段

- **FR**: FR-064-13, FR-064-17, FR-064-16, C-05
- **复杂度**: S
- **依赖**: 无
- **并行组**: C（与 T-064-09 可并行）
- **涉及文件**:
  - `octoagent/packages/skills/src/octoagent/skills/models.py` — `SkillExecutionContext` 新增 `parent_task_id: str | None = None`
  - `octoagent/packages/skills/src/octoagent/skills/manifest.py` — `SkillManifest` 新增 `heartbeat_interval_steps: int = 5`、`max_concurrent_subagents: int = 5`
- **验收标准**:
  1. `SkillExecutionContext.parent_task_id` 默认 None，不影响现有构造
  2. `SkillManifest.heartbeat_interval_steps` 默认 5，`max_concurrent_subagents` 默认 5
  3. 字段含 `Field(ge=..., le=...)` 验证和 description
  4. 如 `SkillManifestModel`（skills/models.py 中的副本类）存在，同步添加相同字段

---

### T-064-11: TaskStore CRUD 适配 + `list_child_tasks()`

- **FR**: FR-064-13
- **复杂度**: S
- **依赖**: T-064-09
- **并行组**: 无
- **涉及文件**:
  - `octoagent/packages/core/src/octoagent/core/store/task_store.py`（或等效文件）— 新增 `list_child_tasks(parent_task_id: str) -> list[Task]`
- **验收标准**:
  1. `list_child_tasks()` 按 `parent_task_id` 查询，返回子 Task 列表（按 `created_at` 排序）
  2. 无子 Task 时返回空列表
  3. 单元测试：创建带 parent_task_id 的 Task → list_child_tasks 返回正确

---

### T-064-12: SubagentExecutor 类实现（独立执行循环核心）

- **FR**: FR-064-15, FR-064-16, FR-064-17, FR-064-18, FR-064-20
- **复杂度**: L
- **依赖**: T-064-09, T-064-10, T-064-11
- **并行组**: 无
- **涉及文件**:
  - `octoagent/apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` — 新增 `SubagentExecutor` 类
- **验收标准**:
  1. `SubagentExecutor.__init__()` 接收 child_task、skill_runner、manifest、execution_context、a2a_conversation_id 等参数
  2. `start()` 创建独立 `asyncio.Task`（name=`subagent-{child_task_id}`）运行 `_run_loop()`
  3. `_run_loop()` 执行 SkillRunner.run()，不阻塞父 Worker 主循环（FR-064-15）
  4. 继承父 Worker 的 `permission_preset`（FR-064-16）
  5. 每 N 步（`heartbeat_interval_steps`）发射 `TASK_HEARTBEAT` 事件（FR-064-17）
  6. 正常完成 → 发送 A2A RESULT → Child Task 流转 SUCCEEDED
  7. `cancel()` 设置取消标志 + `asyncio.Task.cancel()` → 优雅停止 → Child Task 流转 CANCELLED（FR-064-18）
  8. 未捕获异常 → 发送 A2A ERROR → Child Task 流转 FAILED（FR-064-20）
  9. `finally` 块调用 `_cleanup()` 确保 Session 关闭、Runtime 归档
  10. Subagent 获得独立 UsageLimits（默认 max_steps=30，max_duration_seconds=1800）

---

### T-064-13: 重写 `spawn_subagent()` — 含 Child Task + A2A + SkillRunner

- **FR**: FR-064-13, FR-064-14, FR-064-15
- **复杂度**: L
- **依赖**: T-064-12
- **并行组**: 无
- **涉及文件**:
  - `octoagent/apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` — 重写 `spawn_subagent()` 函数签名和实现
- **验收标准**:
  1. 新签名接收 `parent_task_id`、`task_description`、`permission_preset`、`usage_limits`、`model_client`、`tool_broker`、`event_store`、`parent_manifest` 参数
  2. 创建 Child Task（`parent_task_id` 指向父 Task）并持久化
  3. 创建 A2AConversation（source=父 Worker URI，target=Subagent URI）并持久化
  4. 发送 A2A TASK 消息
  5. 创建独立 SkillRunner 实例（共享 ToolBroker，新建 LiteLLMSkillClient）
  6. SkillManifest 从父 Worker manifest 衍生（覆盖 skill_id、description 等，参考 C-05）
  7. 创建 SubagentExecutor 并调用 `start()`
  8. 返回 `tuple[AgentRuntime, AgentSession, SubagentExecutor]`
  9. Subagent URI 格式：`agent://workers/{parent_id}/subagents/{subagent_id}`

---

### T-064-14: 扩展 `kill_subagent()` — 含 Task 终态 + A2A CANCEL

- **FR**: FR-064-18, FR-064-20
- **复杂度**: M
- **依赖**: T-064-12
- **并行组**: 无（但可与 T-064-13 同时开发，二者改不同函数）
- **涉及文件**:
  - `octoagent/apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` — 扩展 `kill_subagent()` 函数
- **验收标准**:
  1. 发送 A2A CANCEL 消息到 Subagent
  2. 调用 SubagentExecutor.cancel() 优雅终止
  3. Child Task 状态流转到 CANCELLED（通过 STATE_TRANSITION 事件）
  4. Session 关闭 + Runtime 归档（保留原有逻辑）
  5. 重复调用幂等（已终止的 Subagent 不报错）

---

### T-064-15: Subagent 独立执行循环单元测试 + 集成测试

- **FR**: FR-064-13~20 验证
- **复杂度**: M
- **依赖**: T-064-13, T-064-14
- **并行组**: 无
- **涉及文件**:
  - `octoagent/apps/gateway/tests/test_subagent_executor.py` — 新建测试文件
- **验收标准**:
  1. 单元测试：spawn → execute → SUCCEEDED 全流程，Child Task 事件流完整
  2. 单元测试：spawn → execute → 异常 → FAILED，A2A ERROR 消息正确
  3. 单元测试：spawn → cancel → CANCELLED，优雅终止确认
  4. 单元测试：心跳上报 — 每 N 步有 TASK_HEARTBEAT 事件
  5. 单元测试：Subagent 继承父 Worker permission_preset
  6. 集成测试：Subagent 执行不阻塞父 Worker 主循环（并发验证）
  7. 集成测试：Subagent 异常退出后资源正确清理（无孤儿 asyncio.Task）

---

### T-064-16: SSEHub `broadcast()` 双路广播扩展

- **FR**: FR-064-23
- **复杂度**: S
- **依赖**: T-064-09（需要 parent_task_id 概念）
- **并行组**: D（可与 T-064-12~15 并行）
- **涉及文件**:
  - `octoagent/apps/gateway/src/octoagent/gateway/services/sse_hub.py` — `broadcast()` 新增 `parent_task_id: str | None = None` 参数
- **验收标准**:
  1. `broadcast(task_id, event, parent_task_id=None)` 签名新增可选参数
  2. `parent_task_id` 非 None 时，事件同时广播到 task_id 和 parent_task_id 的订阅者
  3. `parent_task_id` 为 None 时行为与改动前完全一致
  4. 单元测试：SSE 订阅父 Task 后能收到子 Task 的事件
  5. 不引入内部 task_id 映射（由调用方传入 parent_task_id，参考 W-05）

---

### T-064-17: Orchestrator Subagent 结果接收 + SubagentResultQueue

- **FR**: FR-064-21, FR-064-22, FR-064-24, FR-064-25
- **复杂度**: M
- **依赖**: T-064-12, T-064-16
- **并行组**: 无
- **涉及文件**:
  - `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` — 新增 `_subagent_result_queues: dict[str, asyncio.Queue]`，新增 `enqueue_subagent_result()` 方法，新增结果注入逻辑
  - `octoagent/apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` — SubagentExecutor 完成时调用 Orchestrator.enqueue_subagent_result()
- **验收标准**:
  1. Subagent 完成 → A2A RESULT 消息 → Orchestrator 接收（FR-064-21）
  2. Orchestrator 写入 `A2A_MESSAGE_RECEIVED` 事件到父 Task（事件冒泡，FR-064-22）
  3. SSEHub 双路广播通知父 Task 订阅者（FR-064-23 联动 T-064-16）
  4. 结果摘要放入 `SubagentResultQueue`（`asyncio.Queue`），key 为 parent_task_id
  5. 父 Worker SkillRunner 在 `generate()` 前检查 Queue，有结果时追加 user role message（FR-064-24）
  6. 注入消息含 Subagent 名称、child_task_id、状态、摘要（参考 contracts/subagent-executor.py 格式）
  7. 多 Subagent 结果按到达顺序注入，父 Worker 不阻塞等待（FR-064-25）

---

## P2: 长任务增强

### T-064-18: ContextCompactor 类实现（三级压缩策略）

- **FR**: FR-064-26, FR-064-27, FR-064-28, FR-064-29, FR-064-30, FR-064-31
- **复杂度**: L
- **依赖**: 无（P2 阶段入口）
- **并行组**: E（与 T-064-21 可并行）
- **涉及文件**:
  - `octoagent/packages/skills/src/octoagent/skills/compactor.py` — **新建**：`ContextCompactor` 类
  - `octoagent/packages/skills/src/octoagent/skills/manifest.py` — `SkillManifest` 新增 `compaction_model_alias`、`compaction_threshold_ratio`、`compaction_recent_turns`
- **验收标准**:
  1. `ContextCompactor.compact(history, max_tokens, threshold_ratio, compaction_model_alias) -> CompactionResult`
  2. token 估算方法可配置（默认字符数 / 4 近似，FR-064-26）
  3. 阈值可配置（默认 80%，FR-064-27）
  4. 三级压缩策略依序执行（FR-064-28）：
     - Level 1: 截断 > 2000 字符的 tool role message 为前 500 字符 + `...[truncated]`
     - Level 2: 保留最近 N=8 轮，早期轮次用 LLM 摘要替换
     - Level 3: 丢弃最老的摘要块
  5. 每级压缩后重新检测 token 数，满足阈值即停止
  6. system prompt（history[0] if role=system）和最近一轮 user/assistant 永不压缩（FR-064-31）
  7. 摘要生成使用 `compaction_model_alias`（可配置，FR-064-30），通过独立 httpx 调用
  8. 发射 `CONTEXT_COMPACTION_COMPLETED` 事件，payload 含 before_tokens/after_tokens/strategy_used（FR-064-29）
  9. 压缩失败时降级为简单截断 + 发射 `CONTEXT_COMPACTION_FAILED` 事件（Constitution #6）

---

### T-064-19: LiteLLMSkillClient 集成上下文压缩

- **FR**: FR-064-26, FR-064-27
- **复杂度**: M
- **依赖**: T-064-18
- **并行组**: 无
- **涉及文件**:
  - `octoagent/packages/skills/src/octoagent/skills/litellm_client.py` — `generate()` 调用前插入 `ContextCompactor.compact()` 检查
- **验收标准**:
  1. `generate()` 在构建 LLM 请求前调用 `ContextCompactor.compact()`
  2. 压缩 token 消耗不计入 `UsageTracker`（基础设施开销）
  3. 两条 API 路径（Chat Completions / Responses API）共享压缩逻辑
  4. 压缩后对话历史可正常传递给 LLM（无格式错误）
  5. `compaction_threshold_ratio` 设为 1.0 时永不触发（回滚方案）

---

### T-064-20: 上下文压缩测试

- **FR**: FR-064-26~31 验证, NFR-064-04
- **复杂度**: M
- **依赖**: T-064-19
- **并行组**: 无
- **涉及文件**:
  - `octoagent/packages/skills/tests/test_compactor.py` — 新建测试文件
- **验收标准**:
  1. 测试用例：token 未达阈值 → 不压缩
  2. 测试用例：Level 1 截断大工具输出后满足阈值 → 停止
  3. 测试用例：Level 1 不够 → Level 2 LLM 摘要替换
  4. 测试用例：system prompt 永不被压缩
  5. 测试用例：最近一轮 user/assistant 永不被压缩
  6. 测试用例：压缩失败降级为简单截断
  7. 测试用例：CONTEXT_COMPACTION_COMPLETED 事件 payload 正确
  8. 集成测试：压缩后对话可正常继续推理（无 API 400 错误，SC-064-04）

---

### T-064-21: NotificationService + NotificationChannelProtocol 实现

- **FR**: FR-064-32, FR-064-35, FR-064-36
- **复杂度**: M
- **依赖**: 无
- **并行组**: E（与 T-064-18 可并行）
- **涉及文件**:
  - `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py` — **新建**：`NotificationChannelProtocol`（Protocol 类）+ `NotificationService`（路由分发 + 去重）+ `SSENotificationChannel` 实现
- **验收标准**:
  1. `NotificationChannelProtocol` 定义 `async notify(task_id, event_type, payload) -> bool` 接口
  2. `NotificationService` 支持注册多个 channel，按事件类型分发
  3. 通知去重：同一 Task 同一终态只通知一次（基于 event_id 幂等，FR-064-36）
  4. channel 不可用时降级记录日志，不影响 Task 执行（Constitution #6）
  5. `SSENotificationChannel` 实现基于已有 SSEHub

---

### T-064-22: Telegram 审批通知 + 通知去重 + Orchestrator 集成

- **FR**: FR-064-32, FR-064-33, FR-064-34
- **复杂度**: M
- **依赖**: T-064-21
- **并行组**: 无
- **涉及文件**:
  - `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py` — 新增 `TelegramNotificationChannel` 实现
  - `octoagent/plugins/channels/telegram/` — 新增 callback query handler（inline keyboard 审批）
  - `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` — 状态变更时调用 NotificationService
- **验收标准**:
  1. Task 终态（SUCCEEDED/FAILED/CANCELLED）时推送通知到已配置渠道（FR-064-32）
  2. WAITING_APPROVAL 时 Telegram 发送审批消息含 inline keyboard（批准/拒绝按钮，FR-064-33）
  3. TASK_HEARTBEAT 事件通过 Web SSE 实时展示进度（FR-064-34）
  4. Telegram 通知使用中文，包含 Task 标题 + 状态 + 耗时
  5. Telegram 通知延迟 < 5 秒（NFR-064-05）
  6. 重复事件不导致重复通知（通知去重验证）
  7. Telegram 不可用时降级（仅记录日志）

---

## 并行组说明

| 并行组 | 包含 Task | 说明 |
|--------|-----------|------|
| **A** | T-064-01, T-064-02, T-064-03 | P0 基础设施准备，三者互不依赖，可同时开发 |
| **B** | T-064-06, T-064-07 | 并行测试 + 回填重写可同时进行（分别改 runner 和 litellm_client） |
| **C** | T-064-09, T-064-10 | P1 基础设施准备，Task 模型和 Skill 模型扩展互不依赖 |
| **D** | T-064-16 | SSEHub 扩展可独立于 SubagentExecutor 开发，仅需 parent_task_id 概念 |
| **E** | T-064-18, T-064-21 | P2 的两个子系统（压缩 / 通知）互不依赖，可同时开发 |

---

## 关键里程碑

### MS-1: P0 工具执行层稳定（预估 3-4 天）

- **达成标志**: T-064-01 ~ T-064-08 全部完成
- **可交付物**:
  - SkillRunner 支持并行分桶执行，READ_ONLY 工具并行加速
  - LiteLLMSkillClient 使用标准 tool role message 回填
  - 所有现有 Skill 零修改通过回归测试
- **验证**: 3 个 READ_ONLY 工具并行执行总耗时 < 最慢单个 x 1.5（SC-064-01）

### MS-2: P1 Subagent 编排闭环（预估 5-6 天）

- **达成标志**: T-064-09 ~ T-064-17 全部完成
- **可交付物**:
  - Subagent 拥有独立执行循环（Child Task + SkillRunner + A2A 通信）
  - 父 Worker 自动接收 Subagent 结果并恢复处理
  - SSE 订阅者可同时查看父子 Task 事件
- **验证**: Subagent spawn → execute → result 全流程 < 60 秒（SC-064-03）

### MS-3: P2 长任务增强（预估 4-5 天）

- **达成标志**: T-064-18 ~ T-064-22 全部完成
- **可交付物**:
  - 对话上下文自动压缩，长任务不再触及上下文窗口限制
  - Task 终态和审批请求通过 Telegram/Web 即时推送
- **验证**: 长对话压缩后无 API 400 错误（SC-064-04）；通知延迟 < 5 秒（SC-064-05）

---

## 依赖关系图

```
P0 基础设施（并行组 A）
  T-064-01 ─┐
  T-064-02 ─┼──→ T-064-04 ──→ T-064-05 ──→ T-064-06 ──────────────→ [MS-1]
  T-064-03 ─┘         │                           ↑
                       └──────→ T-064-07 ──→ T-064-08 ──────────────→ [MS-1]

P1 基础设施（并行组 C）
  T-064-09 ─┬──→ T-064-11 ─┐
  T-064-10 ─┘               ├──→ T-064-12 ──→ T-064-13 ─┐
                             │                T-064-14 ─┤──→ T-064-15 → [MS-2]
  T-064-09 ────→ T-064-16 ──┘                            │
                             └────────────→ T-064-17 ────┘──────────→ [MS-2]

P2 压缩 + 通知（并行组 E）
  T-064-18 ──→ T-064-19 ──→ T-064-20 ──────────────────────────────→ [MS-3]
  T-064-21 ──→ T-064-22 ──────────────────────────────────────────→ [MS-3]
```

---

## FR 覆盖矩阵

| FR | Task |
|----|------|
| FR-064-01 | T-064-01, T-064-05 |
| FR-064-02 | T-064-05 |
| FR-064-03 | T-064-05 |
| FR-064-04 | T-064-05 |
| FR-064-05 | T-064-02, T-064-05 |
| FR-064-06 | T-064-05 |
| FR-064-07 | T-064-05 |
| FR-064-08 | T-064-05 |
| FR-064-09 | T-064-07 |
| FR-064-10 | T-064-07 |
| FR-064-11 | T-064-03, T-064-04, T-064-07 |
| FR-064-12 | T-064-03, T-064-07 |
| FR-064-13 | T-064-09, T-064-10, T-064-11, T-064-13 |
| FR-064-14 | T-064-13 |
| FR-064-15 | T-064-12, T-064-13 |
| FR-064-16 | T-064-12 |
| FR-064-17 | T-064-10, T-064-12 |
| FR-064-18 | T-064-12, T-064-14 |
| FR-064-19 | T-064-12, T-064-13 |
| FR-064-20 | T-064-12, T-064-14 |
| FR-064-21 | T-064-17 |
| FR-064-22 | T-064-17 |
| FR-064-23 | T-064-16 |
| FR-064-24 | T-064-17 |
| FR-064-25 | T-064-17 |
| FR-064-26 | T-064-18, T-064-19 |
| FR-064-27 | T-064-18, T-064-19 |
| FR-064-28 | T-064-18 |
| FR-064-29 | T-064-18 |
| FR-064-30 | T-064-18 |
| FR-064-31 | T-064-18 |
| FR-064-32 | T-064-21, T-064-22 |
| FR-064-33 | T-064-22 |
| FR-064-34 | T-064-22 |
| FR-064-35 | T-064-21 |
| FR-064-36 | T-064-21 |
