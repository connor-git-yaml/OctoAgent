# OctoAgent 技术架构深度分析

> 基于源码逐行分析，截至 2026-03 主线。

---

## 1. 系统概览

### 1.1 整体架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Channels (Web / Telegram)                     │
│                                                                      │
│  Web UI (React+Vite)          Telegram (aiogram)                     │
│       │                            │                                 │
│  POST /api/chat/send          aiogram FSM webhook                    │
│  POST /api/message                 │                                 │
│       │                            │                                 │
└───────┼────────────────────────────┼─────────────────────────────────┘
        │                            │
        ▼                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     OctoGateway (FastAPI + Uvicorn)                   │
│                                                                      │
│  routes/chat.py ─────┐                                               │
│  routes/message.py ──┼──▶ TaskService.create_task()                  │
│                      │         │                                     │
│                      │         ▼                                     │
│                      └──▶ TaskRunner.enqueue()                       │
│                                │                                     │
│                                ▼                                     │
│                     OrchestratorService.dispatch()                    │
│                         │         │         │                        │
│              ┌──────────┘         │         └──────────┐             │
│              ▼                    ▼                    ▼             │
│     Butler Direct          Butler Inline       DelegationPlane       │
│     Execution              Decision            .prepare_dispatch()   │
│         │                     │                       │              │
│         │                     │                       ▼              │
│         │                     │              SkillPipelineEngine     │
│         │                     │                       │              │
│         ▼                     ▼                       ▼              │
│     TaskService         InlineReply            DispatchEnvelope      │
│     .process_task        LLMService                   │              │
│     _with_llm()              │                        ▼              │
│         │                    │               LLMWorkerAdapter        │
│         │                    │                .handle()              │
│         ▼                    ▼                       │              │
│    AgentContext         TaskService                   ▼              │
│    Service             .process_task          WorkerRuntime.run()    │
│    .build_task          _with_llm()                  │              │
│    _context()               │                        ▼              │
│         │                   │               Backend.execute()        │
│         ▼                   ▼                  (inline/docker/       │
│    LLMService.call()   TaskService                  graph)           │
│         │              .process_task                  │              │
│         ▼              _with_llm()                   │              │
│    LiteLLMClient             │                       │              │
│    .complete()               ▼                       ▼              │
│         │              SSE broadcast           SSE broadcast         │
│         ▼                                                            │
│    LiteLLM Proxy ──▶ OpenAI / Anthropic / ...                       │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    Stores (SQLite WAL)                           │ │
│  │  TaskStore │ EventStore │ ArtifactStore │ WorkStore              │ │
│  │  AgentContextStore │ TaskJobStore │ SideEffectLedgerStore        │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    Memory (SQLite + LanceDB)                    │ │
│  │  MemoryService │ SqliteMemoryStore │ VectorBackend              │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心模块关系

| 模块 | 文件位置 | 职责 |
|------|----------|------|
| **TaskService** | `gateway/services/task_service.py` | 任务 CRUD、Event Sourcing、LLM 调用编排 |
| **OrchestratorService** | `gateway/services/orchestrator.py` | 请求封装、Policy Gate、路由决策、Worker 派发 |
| **DelegationPlaneService** | `gateway/services/delegation_plane.py` | Work 生命周期、Pipeline 执行、工具选择 |
| **WorkerRuntime** | `gateway/services/worker_runtime.py` | Worker 多步循环、Backend 选择、超时监控 |
| **TaskRunner** | `gateway/services/task_runner.py` | 后台任务调度、持久化恢复、取消管理 |
| **AgentContextService** | `gateway/services/agent_context.py` | System Prompt 装配、Memory Recall、Session 管理 |
| **CapabilityPackService** | `gateway/services/capability_pack.py` | 工具注册/发现、ToolIndex、Browser 模拟 |
| **MemoryService** | `packages/memory/service.py` | SoR 写入仲裁、向量检索、分区管理 |
| **LiteLLMClient** | `packages/provider/client.py` | LiteLLM Proxy 调用封装、成本计算 |
| **FallbackManager** | `packages/provider/fallback.py` | Primary→Fallback 降级链 |

---

## 2. 用户消息完整执行路径

以用户在 Web UI 发送一条消息为例，追踪完整调用链。

### 2.1 HTTP 请求入口

**路径 A：Chat API（主入口）**

```
POST /api/chat/send  →  routes/chat.py:router
```

`routes/chat.py:120` 处 `_resolve_chat_scope_snapshot()` 解析 project/workspace 上下文，构建 `NormalizedMessage`，调用 `TaskService.create_task()`。

**路径 B：Message API（Legacy 入口）**

```
POST /api/message  →  routes/message.py:receive_message()
```

`routes/message.py:39-105`：直接构建 `NormalizedMessage`，调用 `TaskService.create_task()`。

### 2.2 Task 创建

`task_service.py:129-243` — `TaskService.create_task(msg)`

1. **幂等检查** (`task_service.py:139-143`)：`event_store.check_idempotency_key(msg.idempotency_key)` — 重复请求直接返回已有 task_id
2. **生成 Task** (`task_service.py:146-172`)：`task_id = str(ULID())`，构建 `Task(status=TaskStatus.CREATED)`
3. **原子写入** (`task_service.py:217-233`)：`create_task_with_initial_events(conn, task_store, event_store, task, [event_1, event_2])` — 单事务写入 Task + TASK_CREATED + USER_MESSAGE 两个事件
4. **SSE 广播** (`task_service.py:236-241`)：通过 `sse_hub.broadcast(task_id, event)` 通知前端

### 2.3 任务入队

```
TaskRunner.enqueue(task_id, text)  →  task_runner.py:156-165
```

1. `task_job_store.create_job(task_id, user_text)` — 在 `task_jobs` 表创建持久记录
2. `_start_job(task_id)` → `_spawn_job()` → `asyncio.create_task(_run_job())`

### 2.4 Orchestrator Dispatch

`task_runner.py:502-575` — `_run_job()` 调用 `OrchestratorService.dispatch()`

`orchestrator.py:608-770` — `dispatch()` 完整流程：

```
dispatch()
├─ 1. Policy Gate 评估
│    └─ _policy_gate.evaluate(request)          [orchestrator.py:644]
│         └─ 非 HIGH_RISK → allow=True
│         └─ HIGH_RISK → 检查 approval_id       [orchestrator.py:229-297]
│
├─ 2. Worker Lens 标准化
│    └─ _normalize_requested_worker_lens()       [orchestrator.py:772-830]
│         └─ 解析 delegation_target_profile_id → 标准化 worker_type
│
├─ 3. Owner Self Worker 检测
│    └─ _resolve_owner_self_worker_execution_choice()  [orchestrator.py:1006-1041]
│         └─ 会话 owner 绑定了自定义 Worker profile → 走 owner self execution
│
├─ 4. Single Loop Butler 准备
│    └─ _prepare_single_loop_request()           [orchestrator.py:832-910]
│         └─ 条件：LLMService.supports_single_loop_executor == True
│         └─ 条件：worker_capability in {"", "llm_generation"}
│         └─ 条件：非子任务、非 spawned
│         └─ 条件：无自定义非 singleton Worker profile
│         └─ 解析工具集：resolve_profile_first_tools() → DynamicToolSelection
│
├─ 5. 路由决策
│    └─ _resolve_routing_decision()              [orchestrator.py:1050-1081]
│         └─ decide_agent_routing(user_text, runtime_hints)  [agent_decision.py:1120-1139]
│              └─ Pipeline trigger_hint 匹配 → DELEGATE_GRAPH
│              └─ 其余 → DIRECT_ANSWER → return None
│
├─ 6. 分支执行
│    ├─ DELEGATE_GRAPH → _dispatch_delegate_graph()
│    ├─ 有 delegated_request → Delegation Plane 路径
│    ├─ routing_decision != None → _dispatch_inline_decision()
│    ├─ _should_direct_execute() → _dispatch_direct_execution()   [orchestrator.py:1144-1252]
│    └─ else → DelegationPlane.prepare_dispatch() → _dispatch_envelope()
│
└─ 7. Worker 派发
     └─ _dispatch_envelope()                     [orchestrator.py:2072-2172]
          └─ worker = self._workers[envelope.worker_capability]
          └─ worker.handle(envelope)
               └─ WorkerRuntime.run()
```

### 2.5 决策分支详解

**分支 A：Butler Direct Execution（最常见路径）**

条件（`orchestrator.py:1432-1441`）：
- `LLMService.supports_single_loop_executor == True`
- 满足 `_is_routing_decision_eligible()`（非子任务、非 spawned、无自定义 Worker）

执行流程 (`orchestrator.py:1144-1252`)：
1. 判断是否 trivial（`_is_trivial_direct_answer()`）
2. 解析工具集 `_resolve_single_loop_tool_selection()`
3. `TaskService.ensure_task_running()` — 状态转 RUNNING
4. `TaskService.process_task_with_llm()` — 调用主 LLM

**分支 B：Butler Inline Decision**

条件：`decide_agent_routing()` 返回非 None 的 `AgentDecision`（目前仅 Pipeline trigger_hint 匹配）

执行流程 (`orchestrator.py:1083-1142`)：
1. 使用 `_InlineReplyLLMService(decision.reply_prompt)` 代替真实 LLM
2. 调用 `process_task_with_llm()` 写入确定性回复

**分支 C：Delegation Plane → Worker**

条件：不满足 Butler 条件（自定义 Worker、subagent 等）

执行流程：
1. `DelegationPlane.prepare_dispatch()` (`delegation_plane.py:105-416`)
2. 创建 `Work` 记录 + 运行 `SkillPipelineEngine`
3. 构建 `DispatchEnvelope`
4. `_dispatch_envelope()` → `worker.handle()` → `WorkerRuntime.run()`

### 2.6 LLM 调用核心流程

`task_service.py:468-750` — `process_task_with_llm()`

Pipeline 节点化执行（支持 checkpoint 恢复）：

```
process_task_with_llm()
├─ Node: state_running
│    └─ _prepare_task_for_processing()       — CREATED → RUNNING 状态转移
│    └─ _write_checkpoint(node_id="state_running")
│
├─ _build_task_context()                     — 装配完整上下文
│    └─ AgentContextService.build_task_context()
│         ├─ 解析 Project/Workspace 绑定
│         ├─ 解析 AgentProfile → BehaviorPack
│         ├─ Bootstrap 检测
│         ├─ Memory Recall（向量检索）
│         ├─ Session 回放（历史对话）
│         └─ System Prompt 组装
│
├─ Node: model_call_started
│    └─ 存储 request snapshot artifact
│    └─ 写入 MODEL_CALL_STARTED 事件
│    └─ _write_checkpoint(node_id="model_call_started")
│
├─ Node: response_persisted
│    └─ _call_llm_service()                  — 实际 LLM 调用
│         └─ llm_service.call(messages, model_alias, ...)
│              └─ LLMService 内部判断有无 tool_selection
│                   ├─ 有 → _try_call_with_tools() → SkillRunner 多轮循环
│                   └─ 无 → FallbackManager.call_with_fallback()
│    └─ 存储 response artifact
│    └─ 写入 MODEL_CALL_COMPLETED 事件
│    └─ 写入 ARTIFACT_CREATED 事件
│    └─ record_response_context() — 更新 Session 状态 + 触发记忆提取
│
└─ Node: task_succeeded
     └─ 写入 STATE_TRANSITION: RUNNING → SUCCEEDED
     └─ _write_checkpoint(node_id="task_succeeded")
```

### 2.7 SSE 响应返回

每个关键事件都通过 `sse_hub.broadcast(task_id, event)` 推送到前端。前端订阅 `GET /stream/task/{task_id}` 获取实时更新。

---

## 3. 编排层详解

### 3.1 OrchestratorService 初始化

`orchestrator.py:380-438`

```python
class OrchestratorService:
    def __init__(self, store_group, sse_hub, llm_service, ...):
        self._policy_gate = OrchestratorPolicyGate(approval_manager)
        self._router = SingleWorkerRouter()
        self._delegation_plane = delegation_plane
        self._workers = {"llm_generation": LLMWorkerAdapter(...)}
```

核心组件：
- `_policy_gate`: 高风险 Gate（检查审批状态）
- `_router`: 单 Worker 路由器（backup，通常不使用）
- `_delegation_plane`: 统一委派平面
- `_workers`: Worker 注册表（默认只有 `LLMWorkerAdapter`）

### 3.2 OrchestratorPolicyGate

`orchestrator.py:220-297`

评估逻辑：
1. `risk_level != HIGH` → 直接放行
2. `HIGH` → 检查 `approval_id` 是否存在
3. 查询 `ApprovalManager.get_approval()` 验证审批状态
4. 支持 `ALLOW_ONCE`（消费一次性令牌）和 `ALLOW_ALWAYS`

### 3.3 Butler Decision 机制

`agent_decision.py:1120-1139` — `decide_agent_routing()`

```python
def decide_agent_routing(user_text, *, runtime_hints=None, pipeline_items=None):
    # Pipeline trigger_hint 规则匹配
    pipeline_match = _match_pipeline_trigger(normalized, pipeline_items)
    if pipeline_match is not None:
        return pipeline_match  # → DELEGATE_GRAPH
    return AgentDecision()     # → DIRECT_ANSWER → None
```

**关键设计**：天气/位置等不再做专属分支，统一由 Agent Direct Execution + web.search 处理（`agent_decision.py:1126`）。

### 3.4 Worker 路由和委派

四种执行路径的判定顺序（`orchestrator.py:608-770`）：

1. **Owner Self Worker** (`orchestrator.py:664-670`)：会话 owner 直接绑定了 Worker profile → 跳过 Butler，走 owner 自执行
2. **Single Loop Butler** (`orchestrator.py:671`)：LLMService 支持 + 无自定义 Worker → Butler 直接执行
3. **Inline Decision** (`orchestrator.py:701-706`)：Pipeline trigger → 确定性回复
4. **Direct Execution** (`orchestrator.py:711-715`)：满足 Butler 条件 → Butler 直接执行（带工具）
5. **Delegation Plane** (`orchestrator.py:717-770`)：其余请求 → Worker 委派

### 3.5 DelegationPlane 工作流

`delegation_plane.py:105-416` — `prepare_dispatch()`

```
prepare_dispatch(request)
├─ _resolve_project_context()         — 查询 Project/Workspace 绑定
├─ _resolve_task_context_refs()       — 继承 agent profile
├─ 创建 Work 记录
│    └─ work_store.save_work(work)
│    └─ emit WORK_CREATED 事件
├─ 运行 SkillPipelineEngine
│    └─ pipeline_engine.start_run(definition, initial_state)
│    └─ Pipeline 可能 PAUSED（需要审批）或 SUCCEEDED
├─ 同步 Work 状态
│    └─ work_store.save_work(updated_work)
│    └─ emit WORK_STATUS_CHANGED 事件
│    └─ emit TOOL_INDEX_SELECTED 事件
└─ 构建 DispatchEnvelope
     └─ 包含 runtime_context, tool_selection, metadata
```

### 3.6 WorkerRuntime 执行循环

`worker_runtime.py:427-726` — `WorkerRuntime.run()`

```
run(envelope, worker_id)
├─ _resolve_tool_profile()            — 确定工具权限级别 (minimal/standard/privileged)
├─ 创建 WorkerSession
├─ _check_profile_gate()              — privileged 需要显式审批
├─ _select_backend()                  — 选择执行后端
│    └─ GRAPH_AGENT → GraphRuntimeBackend
│    └─ docker_mode=disabled → InlineRuntimeBackend
│    └─ docker 可用 → DockerRuntimeBackend
│    └─ docker_mode=required 但不可用 → 抛异常
├─ 执行循环 (max_steps=200)
│    for step in range(1, max_steps+1):
│        ├─ 检查 cancel_signal
│        ├─ _await_backend_execute()
│        │    └─ backend.execute() → TaskService.process_task_with_llm()
│        │    └─ 同时监控超时 (max_exec=7200s) 和取消信号
│        ├─ 检查 task 状态
│        │    └─ SUCCEEDED → 返回成功
│        │    └─ CANCELLED → 返回取消
│        │    └─ FAILED → 返回失败
│        └─ 继续下一步
└─ 异常处理
     ├─ WorkerRuntimeCancelled → mark_cancelled
     ├─ WorkerRuntimeTimeoutError → mark_failed
     └─ WorkerBudgetExhaustedError → max_steps_exhausted
```

---

## 4. Context 管理详解

### 4.1 System Prompt 组装流程

`agent_context.py:505-535` — `AgentContextService`

调用链：`TaskService.process_task_with_llm()` → `_build_task_context()` → `AgentContextService.build_task_context()`

装配步骤：

1. **解析 Project/Workspace** — 从 `scope_id` 或 `control_metadata` 提取
2. **解析 AgentProfile** — 默认 profile 或自定义 profile
3. **构建 Runtime Facts** (`agent_context.py:194-244`) — 当前时间、时区、locale、weekday
4. **解析 BehaviorPack** — 行为文件系统加载（详见 4.2）
5. **Bootstrap 检测** — 检查 onboarding 状态
6. **Session 回放** — 从 `AgentSession.turns` 重建对话历史
7. **Memory Recall** — 向量检索相关记忆（详见 6.2）
8. **Budget 规划** — `ContextBudgetPlanner.plan()` 分配 token 预算
9. **Context Compaction** — 超限时压缩历史（rolling summary）
10. **最终消息列表** — `[system_prompt, ...history, user_message]`

### 4.2 BehaviorPack 解析和注入

`agent_decision.py:86-177` — `resolve_behavior_pack()`

BehaviorPack 缓存键 = `(profile_id, project_slug, project_root, load_profile, workspace_root)`

加载优先级：
1. **文件系统 BehaviorPack** (`_resolve_filesystem_behavior_pack()`) — 从 `behavior/` 目录读取
2. **AgentProfile metadata** — `agent_profile.metadata["behavior_pack"]`
3. **默认模板** — `build_default_behavior_pack_files()` 从包内资源加载

行为文件体系（`behavior_workspace.py:41-66`）：

| Scope | 文件 | Token 预算 |
|-------|------|-----------|
| system_shared | AGENTS.md, USER.md, TOOLS.md, BOOTSTRAP.md | 3200/1800/3200/2200 |
| project_shared | PROJECT.md, KNOWLEDGE.md | 2400/2200 |
| agent_private | IDENTITY.md, SOUL.md, HEARTBEAT.md | 1600/1600/1600 |

BehaviorLoadProfile 差异化加载（`behavior_workspace.py:111-127`）：

| Profile | 加载文件 | 场景 |
|---------|---------|------|
| FULL | 全部 9 个 | Butler 主 Agent |
| WORKER | AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE | Worker |
| MINIMAL | AGENTS, TOOLS, IDENTITY, USER | Subagent |

行为文件按 Layer 组织注入 system prompt（`agent_decision.py:194-237`）：

```
ROLE → COMMUNICATION → SOLVING → TOOL_BOUNDARY → MEMORY_POLICY → BOOTSTRAP
```

### 4.3 RuntimeHintBundle 构建

`agent_decision.py` — `build_runtime_hint_bundle()`

提供给路由决策的运行时上下文提示，包含：
- `user_text` — 用户输入
- `can_delegate_research` — 是否有 DelegationPlane
- `recent_clarification_category` — 最近的澄清分类
- `recent_worker_lane_*` — 最近使用的 Worker 通道信息

---

## 5. Skill/Tool 系统详解

### 5.1 ToolBroker 注册和发现

`packages/tooling/broker.py` — `ToolBroker`

ToolBroker 是工具注册表，管理所有可用工具的元信息和处理器。

注册流程：
```
@tool_contract(name="web.search", side_effect=SideEffectLevel.NONE, profile=ToolProfile.STANDARD)
async def web_search(query: str) -> str: ...

broker.register("web.search", handler, meta)
```

### 5.2 ToolContract 反射机制

`packages/tooling/schema.py` — `reflect_tool_schema()`

从 Python 函数签名自动生成 JSON Schema：
- 参数类型注解 → schema properties
- `@tool_contract` 装饰器 → 工具元信息（side_effect, profile, fail_mode）
- Pydantic BaseModel 参数 → 嵌套 schema

### 5.3 ToolIndex 选择逻辑

`packages/tooling/tool_index.py` — `ToolIndex`

两种 backend：
- `InMemoryToolIndexBackend` — 内存全量匹配（MVP）
- `LanceDBToolIndexBackend` — 向量语义检索

选择策略（`capability_pack.py` — `resolve_profile_first_tools()`）：

```
profile_first 策略:
1. 根据 worker_type 查找 Worker Profile
2. 获取 profile 绑定的 tool_groups
3. 展开 tool_groups → 具体工具列表
4. 如果 profile 有 selected_tools → 直接使用
5. 否则 → ToolIndex 语义检索 → 合并 profile 默认工具
```

`tool_index` 策略（后备）：
```
1. ToolIndex.search(query, limit=12)
2. 按 score 排序返回 top-N
```

### 5.4 Policy Engine 双维度审批

工具执行时的 Policy 模型：`PolicyAction × ApprovalDecision`

- **PolicyAction**: `ALLOW` / `DENY` / `REQUIRE_APPROVAL`
- **ApprovalDecision**: `ALLOW_ONCE` / `ALLOW_ALWAYS` / `DENY`

执行链路：
```
SkillRunner.execute_tool()
├─ ToolBroker.execute()
│    ├─ BeforeHook 链
│    │    ├─ ApprovalOverrideHook — Policy Gate 审批
│    │    └─ PresetBeforeHook — 预设参数注入
│    ├─ handler(call) — 实际执行
│    └─ AfterHook 链
│         └─ EventGenerationHook — 写入 TOOL_CALL 事件
└─ LargeOutputHandler — 大输出截断
```

### 5.5 SkillRunner 多轮循环

在 `LLMService._try_call_with_tools()` 中：

```
SkillRunner 循环:
while True:
    1. 构建 messages（system + history + user + tool_results）
    2. LLM.complete(messages, tools=tool_schemas)
    3. 如果响应包含 tool_calls:
         for each tool_call:
           result = ToolBroker.execute(tool_call)
           append tool_result to messages
         continue
    4. 如果响应是纯文本 → break
    5. 超出 max_iterations → break
```

---

## 6. Memory 系统详解

### 6.1 SoR 模型（propose → validate → commit）

`packages/memory/service.py:66-80` — `MemoryService`

三步写入协议：

```python
# 1. Propose — 创建 WriteProposal
proposal = await memory_service.propose_write(
    scope_id="project:default",
    partition=MemoryPartition.WORK,
    action=WriteAction.ADD,
    subject_key="用户/偏好/语言",
    content="用户偏好中文沟通",
    rationale="用户明确表述",
    confidence=0.9,
    evidence_refs=[EvidenceRef(...)],
)

# 2. Validate — 校验版本冲突
validation = await memory_service.validate_proposal(proposal.proposal_id)

# 3. Commit — 写入 SoR 记录
result = await memory_service.commit_proposal(proposal.proposal_id)
```

`propose_write()` (`service.py:96-132`)：
- 创建 `WriteProposal`（proposal_id, scope_id, partition, action, subject_key, content, confidence）
- `_store.save_proposal(proposal)` → SQLite 持久化

`validate_proposal()` (`service.py:134-150`)：
- `ADD` → 检查是否已存在同 scope+key 的 SoR
- `UPDATE` → 检查 expected_version 是否匹配
- 敏感分区（`SENSITIVE_PARTITIONS`）需要额外权限

### 6.2 Memory Recall 流程

`agent_context.py` — `AgentContextService.build_task_context()` 中的 Memory Recall 步骤

```
Memory Recall:
1. 确定 prefetch_mode:
   ├─ agent_led_hint_first — Butler 模式：LLM 先看 hint 再按需检索
   ├─ hint_first — Worker 模式：直接基于 query 预检索
   └─ detailed_prefetch — 详细预加载
2. 构建 MemoryRecallHookOptions:
   ├─ post_filter_mode: KEYWORD_OVERLAP
   ├─ rerank_mode: HEURISTIC
   └─ subject_hint: 从用户文本提取
3. 执行向量检索:
   └─ MemoryService.recall(scope_ids, query, hook_options)
        └─ Backend.search() → LanceDB 或 SQLite FTS
        └─ post_filter → keyword overlap 过滤低相关结果
        └─ rerank → heuristic 排序
4. 注入 system prompt:
   └─ "以下是可能相关的历史记忆: ..."
```

### 6.3 Session Memory Extraction

`session_memory_extractor.py:121-148` — `SessionMemoryExtractor`

触发时机：`AgentContextService.record_response_context()` 末尾 fire-and-forget

提取流程：
1. 从 `AgentSession.turns` 获取新增 turns
2. 构建 LLM 提取 prompt（`_EXTRACTION_SYSTEM_PROMPT`，`session_memory_extractor.py:44-78`）
3. LLM 返回 JSON 数组：`[{type, subject_key, content, confidence, action, partition, ...}]`
4. 按类型分派：
   - `fact` → `MemoryPartition.WORK/PERSONAL/CORE`
   - `solution` → `MemoryPartition.WORK` + `SOLUTION` 标记
   - `entity` → 实体关系记忆
   - `tom` → Theory of Mind 推理
5. 通过 `propose_write() → validate_proposal() → commit_proposal()` 写入 SoR

---

## 7. LLM 调用链详解

### 7.1 LiteLLM Proxy 集成

`packages/provider/client.py:68-200` — `LiteLLMClient`

```python
class LiteLLMClient:
    def __init__(self, proxy_base_url, proxy_api_key, timeout_s, ...):
        self._proxy_base_url = proxy_base_url.rstrip("/")
        # 流式模型别名集合
        self._stream_model_aliases = set(stream_model_aliases or ())
        # Responses API 别名集合
        self._responses_model_aliases = set(responses_model_aliases or ())
```

调用方式：

```python
# 标准调用
response = await litellm.acompletion(
    model=model_alias,      # e.g. "main" → Proxy 内部路由到具体模型
    messages=messages,
    api_base=self._proxy_base_url,
    api_key=self._proxy_api_key,
    timeout=self._timeout_s,
)

# 流式调用（stream_model_aliases 匹配时）
response = await litellm.acompletion(..., stream=True)
chunks = await _collect_stream_response(response)
```

结果封装为 `ModelCallResult`：
- `content` — 模型回复文本
- `model_alias` / `model_name` — 别名和实际模型
- `provider` — 供应商
- `duration_ms` — 耗时
- `token_usage` — TokenUsage(prompt_tokens, completion_tokens, total_tokens)
- `cost_usd` — CostTracker 计算的美元成本
- `is_fallback` / `fallback_reason` — 降级标记

认证刷新（`client.py:117-163`）：
- 调用失败时检查 `_is_auth_error(e)` → 401/403
- 调用 `_auth_refresh_callback()` 刷新凭证
- 重试一次

### 7.2 FallbackManager 降级

`packages/provider/fallback.py:16-113` — `FallbackManager`

```
降级链: LiteLLMClient (Primary) → EchoMessageAdapter (Fallback)
```

策略：Lazy Probe — 每次调用先尝试 Primary，失败则切换 Fallback。

```python
async def call_with_fallback(self, messages, model_alias):
    try:
        return await self._primary.complete(messages, model_alias)   # 正常路径
    except Exception as primary_error:
        if self._fallback is None:
            raise ProviderError(...)
        result = await self._fallback.complete(messages, model_alias)
        result = result.model_copy(update={"is_fallback": True, ...})
        return result  # 降级路径
```

### 7.3 Token 计费和成本统计

`packages/provider/cost.py` — `CostTracker`

每次 LLM 调用后：
1. 从 `litellm.completion_cost()` 获取成本（如果可用）
2. 封装到 `ModelCallResult.cost_usd`
3. 通过 `MODEL_CALL_COMPLETED` 事件写入 EventStore
4. 前端可通过 Event Store 查询统计

---

## 8. 特殊处理和已知问题

### 8.1 硬编码的特殊逻辑

1. **Secret 脱敏**（`task_service.py:99-106`）：`_SECRET_PATTERN` 匹配 `sk-xxx`, `Bearer xxx`, `api_key=xxx` 等模式，在错误信息中替换为 `[REDACTED]`

2. **Trivial 直答判定**（`agent_decision.py` — `_is_trivial_direct_answer()`）：简短问候等直接回复，不走工具调用

3. **Single Loop 资格判定**（`orchestrator.py:912-929`）：硬编码 `worker_type in {"", "general", "research", "dev", "ops"}` 四种内置 Worker 类型

4. **Worker Profile 路由白名单**：`singleton:` 前缀的 profile_id 走 Butler Single Loop，其余走 Delegation Plane

5. **响应截断**（`task_service.py:466`）：`RESPONSE_SUMMARY_MAX_BYTES = 8192`（8KB），超限截断为 summary + Artifact 引用

6. **Session Transcript 限制**（`agent_context.py:128`）：`_SESSION_TRANSCRIPT_LIMIT = 20` 轮对话

7. **Worker 步数上限**（`worker_runtime.py:77`）：`max_steps = 200`，执行超时 `max_execution_timeout_seconds = 7200`（2 小时）

8. **Docker 可用性检测**（`worker_runtime.py:373-395`）：`shutil.which("docker")` + 可选 `docker info` 检查

9. **行为文件总大小告警**（`behavior_workspace.py:98`）：`_BEHAVIOR_SIZE_WARNING_THRESHOLD = 15000` 字符

10. **Pipeline trigger_hint 匹配**（`agent_decision.py:1134-1137`）：Pipeline 定义中的 `trigger_hint` 关键词做正则匹配

### 8.2 已知的架构问题和技术债

1. **TaskService._task_locks 全局共享**：`task_service.py:117` — `_task_locks: dict[str, asyncio.Lock] = {}` 是类变量，所有 TaskService 实例共享，多实例部署时无法提供分布式锁

2. **BehaviorPack 缓存是进程级全局**：`agent_decision.py:45` — `_behavior_pack_cache` 是模块级字典，无 TTL 过期机制，需要手动调用 `invalidate_behavior_pack_cache()` 清除

3. **DockerRuntimeBackend 继承 InlineRuntimeBackend**：`worker_runtime.py:222-225` — Docker backend 仅改了 `name`，执行路径完全复用 Inline，实际 Docker 隔离尚未实现

4. **_InlineReplyLLMService 伪 LLM**：`orchestrator.py:114-152` — 确定性回复通过伪 LLMService 注入 TaskService 链路，cost 为 0，但会产生 MODEL_CALL_COMPLETED 事件

5. **单进程 TaskRunner**：`task_runner.py:59` — `_running_jobs` 是内存字典，不支持多进程水平扩展

6. **Subagent 结果队列内存态**：`orchestrator.py:438` — `_subagent_result_queues` 是内存字典，进程重启后丢失

7. **CostTracker 依赖 litellm 内置定价**：成本计算依赖 `litellm.completion_cost()`，自定义模型或私有部署可能返回 `cost_unavailable=True`

8. **Memory Recall 无 async 批量查询**：每个 scope 独立查询，多 scope 时串行执行

9. **Worker 取消信号依赖 0.1s 轮询**（`worker_runtime.py:807`）：`asyncio.wait_for(asyncio.shield(backend_task), timeout=0.1)` — 最坏情况下取消响应延迟 100ms

10. **AgentContextService._shared_llm_service 类变量注入**（`agent_context.py:509`）：通过 `set_llm_service()` 类方法注入，隐式全局状态，测试时需要注意隔离

## 9. 四系统横向对比

> 完整的对照系统分析见 `docs/design/` 下的对应文档。

### 架构定位

| 维度 | OctoAgent | Claude Code | OpenClaw | Agent Zero |
|------|-----------|-------------|----------|-----------|
| **定位** | 个人 AI OS | CLI 开发工具 | 多渠道 AI 服务 | 自主 Agent 框架 |
| **语言** | Python 3.12 | TypeScript (Bun) | TypeScript (Node) | Python 3.12 |
| **编排** | Orchestrator + Worker 委派 | 单 Agent 循环 + 子 Agent | Channel → Agent 直连 | 递归嵌套 monologue |
| **状态** | Event Sourcing + SQLite WAL | Zustand + 磁盘 | JSON + SQLite | 全内存 |
| **模型** | LiteLLM Proxy（多厂商） | Anthropic SDK 直连 | Anthropic SDK 直连 | LiteLLM（多厂商） |

### 核心机制对比

| 机制 | OctoAgent | Claude Code | OpenClaw | Agent Zero |
|------|-----------|-------------|----------|-----------|
| **权限** | PolicyEngine 双维度 | 四层决策 + 自动分类器 | Tool Profile + /approve | 无审批 |
| **Context 压缩** | Rolling Summary | 三级渐进（Micro/Auto/Memory） | Compaction（保留 40%） | 三级渐进（65%/合并/丢弃） |
| **Memory** | SoR 三步 + 向量检索 | MEMORY.md + auto memory | MEMORY.md + sqlite-vec | FAISS 向量 + 分区 |
| **工具执行** | ToolBroker + Policy Hook | StreamingToolExecutor 并发 | Pi Agent ReAct 循环 | LLM JSON → DirtyJson |
| **MCP** | 基础 stdio | 成熟多传输（5 种） | Per-session 生命周期 | 无 |
| **多 Agent** | Delegation Plane + Worker | AgentTool 子 Agent | Sub-agent + ACP | Subordinate 递归 |
| **行为系统** | 四层 BehaviorScope | CLAUDE.md 三级加载 | Bootstrap 文件注入 | Extension + 模板 |

### OctoAgent 相对各系统的借鉴方向

**从 Claude Code 借鉴**：
- AutoCompact 简单阈值公式（`contextWindow - 13K`），替代复杂的 budget planner
- StreamingToolExecutor 并发执行模式
- 七种 Hook 点（pre/post_tool_call, session_start 等）
- CLAUDE.md 嵌套发现机制

**从 OpenClaw 借鉴**：
- Heartbeat 定时自省（主动维护 Memory）
- 成熟的 MCP 生命周期管理
- Block Chunking 流式输出

**从 Agent Zero 借鉴**：
- Extension 系统（19+ 扩展点，优先级排序）
- Utility Model 概念（低成本模型处理辅助任务）
- 配置继承机制（Agent 层级配置合并）
