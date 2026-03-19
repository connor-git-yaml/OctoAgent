# Feature 065: Graph Agent 感知与编排入口 — 技术调研报告

**日期**: 2026-03-19
**状态**: 完成

---

## 1. Pydantic AI Graph API 分析

### 1.1 核心概念模型

pydantic-graph 是一个**独立于 pydantic-ai 的异步图/状态机库**，核心由以下组件构成：

| 组件 | 职责 |
|------|------|
| `BaseNode[StateT, DepsT, RunEndT]` | 节点基类；`run()` 方法的返回类型注解决定出边 |
| `End[RunEndT]` | 终止标记，`run()` 返回 `End(data)` 表示图结束 |
| `Graph(nodes=[...])` | 图定义，接收节点类列表，自动验证边的合法性 |
| `GraphRunContext` | 运行时上下文，携带 `state` 和 `deps` |
| `GraphRun` | 有状态的异步迭代器，支持 `async for` 或手动 `next()` |
| `GraphRunResult` | 图运行最终结果（`output` + `state` + `persistence`） |

### 1.2 图的定义与边的声明

边不是显式声明的。pydantic-graph 从 `run()` 方法的返回类型注解自动推导：

```python
@dataclass
class MyNode(BaseNode[MyState]):
    async def run(self, ctx: GraphRunContext) -> NodeA | NodeB | End[str]:
        # 返回类型 Union 中的每个 BaseNode 子类 = 一条出边
        # End[str] = 可以终止图
```

这种方式使得图结构在**编译时/注册时**即可静态分析，`Graph.__init__` 中 `_validate_edges()` 会检查所有引用的节点是否已注册。

### 1.3 执行模型：`run` vs `iter` vs `iter_from_persistence`

三种执行入口，递进暴露控制力：

1. **`graph.run(start_node, state=, deps=)`** — 一次性执行到 `End`，无法中途干预
2. **`graph.iter(start_node, ...)`** — 返回 `GraphRun` 异步上下文管理器，可逐步 `next()` 推进，支持中途检查/修改 state
3. **`graph.iter_from_persistence(persistence, deps=)`** — 从持久化快照恢复，配合 `load_next()` 实现跨进程 HITL

关键能力：
- `GraphRun.next(node?)` 可手动传入要执行的下一个节点（覆盖图的自然流转）
- 每个节点执行后自动通过 persistence 做 snapshot
- 支持 `logfire_span` 自动埋点（`auto_instrument=True`）

### 1.4 Persistence（检查点持久化）

`BaseStatePersistence` 抽象定义了 5 个核心方法：

| 方法 | 职责 |
|------|------|
| `snapshot_node(state, next_node)` | 保存节点快照（含序列化的 node 实例） |
| `snapshot_end(state, end)` | 保存终止快照 |
| `record_run(snapshot_id)` | 上下文管理器：标记 running → success/error |
| `load_next()` | 取出下一个 `created` 状态的快照（用于恢复） |
| `load_all()` | 加载全部历史快照 |

内置实现：
- `SimpleStatePersistence`：纯内存
- `FileStatePersistence`：JSON 文件（单文件对应单次 run）

快照状态机：`created → pending → running → success/error`

### 1.5 HITL（Human-in-the-Loop）模式

pydantic-graph 的 HITL 通过 **persistence + iter** 实现：

1. 图运行到需要用户输入的节点，`GraphRun.next()` 返回该节点实例
2. 调用方检测到是"需要用户输入"类型的节点后，退出迭代循环，持久化当前状态
3. 用户提供输入后，通过 `iter_from_persistence` 恢复，**构造一个新的节点实例**（携带用户输入）传入 `next()`

关键洞察：pydantic-graph 没有内置"暂停/恢复"语义 — 它通过让调用方在 `iter` 循环中自行决定何时退出来实现 HITL。

### 1.6 Graph 如何被 Agent 调用

在 Pydantic AI 的多 Agent 文档中，Graph 被定义为第 4 级复杂度。Graph 和 Agent 的关系是：

- **Agent 可以作为 Graph 节点内的执行体** — 节点的 `run()` 方法中调用 `agent.run()`
- **Graph 不是 Agent 的工具** — Graph 是应用层编排，不通过 tool call 暴露给 LLM
- **Graph 是程序性控制流** — 由开发者代码驱动，不由 LLM 决策

这与 OctoAgent 的设计一致：Skill Pipeline（DAG/FSM + checkpoint）作为 Agent 的确定性编排工具。

---

## 2. OctoAgent 现有 Graph 基础设施盘点

### 2.1 GraphRuntimeBackend（worker_runtime.py）

**定位**：Worker Runtime 的第三个后端选项（与 inline/docker 并列）。

**当前实现**：
```
GraphPrepareNode → GraphExecuteNode → GraphFinalizeNode
```

三个硬编码节点组成的固定管线：
- `GraphPrepareNode`：emit step 事件，检查取消信号
- `GraphExecuteNode`：调用 `task_service.process_task_with_llm()`（与 inline backend 相同逻辑）
- `GraphFinalizeNode`：emit 完成事件，返回 `End("graph_runtime_succeeded")`

**问题诊断**：
1. **图结构是静态硬编码的** — 只有 prepare → execute → finalize，没有动态节点
2. **本质上只是 inline backend 的包装** — `GraphExecuteNode` 直接调用了 `process_task_with_llm`
3. **不支持用户自定义 Graph** — 无 registry、无 definition 输入
4. **不支持 HITL/暂停** — `graph.run()` 一次性执行到底，未使用 `iter` 模式
5. **不发射细粒度进度事件** — 只有 `emit_step` 粗粒度通知

**能做什么**：
- 证明 pydantic_graph 依赖可用
- 证明 GraphRuntimeState + GraphRuntimeDeps 模式可行
- 路由层（WorkerRuntime._select_backend）已支持 target_kind=GRAPH_AGENT 时选择 graph backend

### 2.2 SkillPipelineEngine（pipeline.py）

**定位**：确定性 Pipeline 执行器，用于 delegation 预处理（capability routing、tool selection）。

**关键能力**：
- `start_run` / `resume_run` / `retry_current_node` / `cancel_run` — 完整生命周期
- `PipelineNodeHandler` 协议 — 可插拔节点处理器
- Checkpoint 持久化 — 每个节点执行后保存 `PipelineCheckpoint`
- 暂停/恢复语义 — WAITING_INPUT, WAITING_APPROVAL, PAUSED 三种暂停状态
- 事件发射 — `PIPELINE_RUN_UPDATED` / `PIPELINE_CHECKPOINT_SAVED`

**与 pydantic_graph 的区别**：
- SkillPipelineEngine 是 OctoAgent 自研的确定性 Pipeline，**不依赖 pydantic_graph**
- 节点通过 `handler_id` 字符串关联处理器（松耦合），而非 pydantic_graph 的类型系统
- 支持 `SkillPipelineDefinition`（包含 `entry_node_id` + 节点列表 + 边）
- 支持 `state_patch`、`input_request`、`approval_request` 等 HITL 原语

### 2.3 DelegationTargetKind.GRAPH_AGENT

在 delegation 模型中已定义为一种目标类型（与 WORKER、SUBAGENT、ACP_RUNTIME、FALLBACK 并列）。

DelegationPlaneService 的 `_resolve_delegation_target_kind` 方法中：
- `WorkerType.DEV` → 自动路由到 `GRAPH_AGENT`
- 也支持请求方显式指定 `target_kind=graph_agent`

### 2.4 Butler 决策流

`ButlerDecisionMode` 当前有 6 个模式：
- DIRECT_ANSWER / ASK_ONCE / BEST_EFFORT_ANSWER — Butler 直接回复
- DELEGATE_RESEARCH / DELEGATE_DEV / DELEGATE_OPS — 委派给 Worker

**缺口**：没有 DELEGATE_GRAPH / RUN_PIPELINE 等模式。Butler 完全不知道 Graph 的存在。

### 2.5 LLM 工具感知现状

LLM 当前可用的编排相关工具：
- `skills`：list/load/unload SKILL.md
- CapabilityPack 内置工具：project.inspect、task.inspect、artifact.list、filesystem 等
- Feature 064 Subagent：通过 `SubagentLifecycleManager.spawn_subagent` 创建（由 CapabilityPack `_dispatch_delegation_work` 触发）

**Graph 相关工具**：**零**。LLM 无法：
1. 列出可用的 Graph/Pipeline 定义
2. 启动一个 Graph 执行
3. 查看正在运行的 Graph 状态
4. 暂停/恢复/取消 Graph 执行

---

## 3. 业界 Agent-Graph 集成模式对比

### 3.1 Pydantic AI 原生模式：Graph 作为应用层编排

**模式**：Graph 在 Agent 之上，作为应用层控制流。Agent 是 Graph 节点内的执行体。

```
应用代码 → Graph.run() → Node.run() 内部调用 agent.run()
```

**特征**：
- 开发者预定义图结构
- 图的流转由节点返回类型决定，不由 LLM 决策
- LLM 无需感知 Graph 的存在
- 适合确定性工作流（CI/CD、审批流、数据管线）

### 3.2 Agent Zero 的 Extension Pipeline

**模式**：生命周期钩子驱动的扩展管线。

```
Agent 主循环 → call_extensions("message_loop_start") → Agent 执行 → call_extensions("message_loop_end")
```

**特征**：
- Extension 是被动触发的钩子，不是主动编排
- Agent 对 Extension 的存在是不透明的
- 没有 DAG/FSM 语义，只有线性钩子链
- 不支持动态选择或跳过 Extension

### 3.3 Claude Agent SDK：Session/Query 模式

**模式**：Client-Server 交互模型。

```
用户代码 → ClaudeSDKClient.connect(prompt) → 流式接收 Message → 可发送 follow-up
```

**特征**：
- 无内建 Graph 概念，是纯粹的会话模型
- `query()` 是无状态的一次性查询
- `ClaudeSDKClient` 是有状态的双向会话
- 编排完全由调用方代码控制
- Agent SDK 本身不做工作流编排

### 3.4 LangGraph StateGraph 模式（业界参考）

**模式**：LLM 驱动的动态图执行。

```
StateGraph → add_node() → add_edge() → compile() → invoke()
```

**特征**：
- 节点可以是 Agent、Tool、函数
- 支持条件边（`add_conditional_edges`）
- LLM 通过 tool call 触发条件判断，间接影响图流转
- 内置 checkpoint 和 HITL（通过 interrupt_before/interrupt_after）
- Graph 定义是代码态，不是 LLM 动态创建的

### 3.5 对比总结

| 维度 | Pydantic Graph | OctoAgent Pipeline | LangGraph | Agent Zero |
|------|---------------|-------------------|-----------|------------|
| 图定义方式 | 类型注解 | SkillPipelineDefinition | add_node/add_edge | 文件系统钩子 |
| 边的决定者 | 节点返回类型 | handler outcome.next_node_id | 条件函数 | N/A（线性） |
| LLM 是否感知图 | 否 | 否 | 间接（via tool call） | 否 |
| HITL | iter + persistence | WAITING_INPUT/APPROVAL | interrupt_before/after | 无 |
| 持久化 | BaseStatePersistence | PipelineCheckpoint (SQLite) | Checkpointer | 无 |
| 动态创建图 | 不支持 | 支持（SkillPipelineDefinition 运行时构建） | 支持（compile 前） | 不支持 |

---

## 4. 推荐方案

### 方案 A：Graph-as-Tool — LLM 通过工具调用 Graph（推荐）

**核心思路**：类比 Subagent 模式（Feature 064），为 LLM 暴露 `graph_pipeline` 工具，使 LLM 可以发现、启动、监控 Graph Pipeline。

**工具设计**：

```
graph_pipeline(action, ...)
├── action=list        → 返回可用 Pipeline 定义列表
├── action=start       → 启动指定 Pipeline，返回 run_id
├── action=status      → 查询 Pipeline run 状态/进度
├── action=resume      → 恢复暂停的 Pipeline（提供 input/approval）
└── action=cancel      → 取消正在运行的 Pipeline
```

**流转链路**：

```
User 请求 → Butler 决策 → DELEGATE_DEV/OPS → Worker(LLM)
  → LLM 调用 graph_pipeline(action=list) 发现可用管线
  → LLM 调用 graph_pipeline(action=start, pipeline_id="deploy-staging", params={...})
  → SkillPipelineEngine.start_run() 执行
  → 如需审批 → Pipeline 暂停 → LLM 告知用户等待
  → 用户批准 → graph_pipeline(action=resume, run_id=..., approval={...})
  → Pipeline 恢复执行至完成
```

**优势**：
1. **最小侵入** — 复用现有 SkillPipelineEngine 和 PipelineCheckpoint 基础设施
2. **与 Subagent 工具对齐** — LLM 用统一的 tool call 模式操作不同类型的编排单元
3. **LLM 可智能选择** — LLM 根据任务性质决定是直接执行还是走 Pipeline
4. **支持 HITL** — Pipeline 的 WAITING_INPUT/APPROVAL 暂停语义天然映射到 resume action
5. **可观测** — Pipeline 事件流（PIPELINE_RUN_UPDATED/CHECKPOINT_SAVED）已接入 SSE

**劣势**：
1. 需要 Pipeline 定义的注册/发现机制（当前 Pipeline 仅在 DelegationPlane 内部使用）
2. LLM 对 Pipeline 的理解依赖 system prompt 引导
3. 不支持 LLM 动态构建图结构（只能选择预定义 Pipeline）

**实现要点**：
- 新建 `GraphPipelineTool`（类似 SkillsTool），注册到 CapabilityPack
- 新建 `PipelineRegistry` — 从文件系统（如 `pipelines/` 目录）发现 Pipeline 定义
- 扩展 SkillPipelineEngine 支持外部启动（当前仅由 DelegationPlane 内部调用）
- 在 Worker/Subagent 的 system prompt 中注入可用 Pipeline 列表
- Pipeline 定义格式复用 `SkillPipelineDefinition`

---

### 方案 B：Butler 路由感知 + 自动 Pipeline 选择

**核心思路**：在 Butler 决策层增加 Graph-aware 路由，Butler 可直接将请求路由到预定义的 Graph Pipeline，无需经过 Worker LLM 中转。

**扩展 ButlerDecisionMode**：

```python
class ButlerDecisionMode(StrEnum):
    ...
    RUN_PIPELINE = "run_pipeline"   # 新增：直接启动 Pipeline
```

**流转链路**：

```
User 请求 → Butler 决策 → RUN_PIPELINE(pipeline_id="deploy-staging")
  → DelegationPlane 识别 target_kind=GRAPH_AGENT
  → SkillPipelineEngine.start_run() 执行
  → 进度通过 SSE 推送到 Web UI
  → 如需审批 → 暂停 → 通知用户
  → 用户通过 Web UI/Telegram 审批
  → Pipeline 恢复
```

**优势**：
1. **用户体验更好** — 用户说"部署到 staging"，Butler 直接启动 Pipeline，无需 Worker 中转
2. **减少 token 消耗** — 跳过 Worker LLM 的推理步骤
3. **确定性更高** — Butler 按规则匹配 Pipeline，而非依赖 Worker LLM 的判断

**劣势**：
1. **Butler 决策逻辑复杂度增加** — 需要在 Butler 层维护 Pipeline 匹配规则
2. **灵活性降低** — 预定义匹配规则无法覆盖所有场景，边界 case 需要 fallback 到 Worker
3. **与当前架构不一致** — 当前 Butler 只负责路由到 Worker 类型，不直接启动执行
4. **需要额外的 Pipeline 触发条件描述** — 每个 Pipeline 需要附带"何时应该被选择"的元数据

**实现要点**：
- 扩展 `ButlerDecisionMode` 增加 `RUN_PIPELINE`
- 扩展 `ButlerDecision` 增加 `pipeline_id` 字段
- `butler_behavior.py` 中增加 Pipeline 匹配逻辑
- DelegationPlane 支持 Butler 直接传入 pipeline_id 启动
- Pipeline 定义需要增加 `trigger_patterns` 或 `description` 用于 Butler 匹配

---

### 方案对比与推荐

| 维度 | 方案 A: Graph-as-Tool | 方案 B: Butler 路由 |
|------|----------------------|-------------------|
| 侵入性 | 低（新增工具） | 中（改动 Butler 决策链） |
| 灵活性 | 高（LLM 自行判断） | 中（规则匹配 + fallback） |
| 用户体验 | 中（需 Worker 中转） | 高（直达） |
| Token 消耗 | 较高 | 较低 |
| 实现复杂度 | 中 | 高 |
| 与现有架构一致性 | 高（复用 Subagent 模式） | 中（需扩展 Butler 决策） |
| 渐进式演进 | 好（先 A 再叠加 B） | 差（需一次性改 Butler） |

**推荐**：**方案 A 优先，方案 B 作为后续优化叠加**。

理由：
1. 方案 A 的实现路径与 Feature 064 Subagent 高度对齐，架构风格一致
2. 方案 A 可以独立交付 MVP，不需要改动 Butler 决策链
3. 方案 B 可以在方案 A 稳定后作为性能/体验优化叠加 — Butler 检测到确定性 Pipeline 场景时，跳过 Worker 直接启动
4. 方案 A 的 `graph_pipeline` 工具可以同时服务于 Worker 和 Subagent，不限制使用场景

---

## 5. 风险与约束

### 5.1 Pipeline 定义的来源与注册

当前 `SkillPipelineDefinition` 仅在 `DelegationPlaneService._build_definition()` 中硬编码构建。要让 LLM 可发现 Pipeline，需要设计 Pipeline 注册机制：

- **方案**：复用 SKILL.md 文件系统驱动模式（Feature 057），引入 `PIPELINE.yaml` 或在 SKILL.md 中嵌入 pipeline section
- **风险**：Pipeline 的 handler_id 需要在运行时注册到 SkillPipelineEngine，纯文件系统定义不够
- **缓解**：Pipeline 节点 handler 可以是通用的（如 `llm_step`、`tool_call`、`approval_gate`），定义文件只描述节点拓扑和参数

### 5.2 pydantic_graph vs SkillPipelineEngine 的定位

当前存在两个 Pipeline 执行引擎，需要明确边界：

- **SkillPipelineEngine**：OctoAgent 自研，已深度集成到 delegation 链路，支持 HITL、checkpoint、重试
- **pydantic_graph (GraphRuntimeBackend)**：当前是包装层，本质调用 process_task_with_llm

**建议**：Feature 065 基于 SkillPipelineEngine 构建 `graph_pipeline` 工具。GraphRuntimeBackend 后续可用于需要 pydantic_graph 原生类型系统的高级场景（如复杂条件分支、GenAI 节点编排）。

### 5.3 并发 Pipeline 的资源管理

Worker 可能同时启动多个 Pipeline。需要：
- Pipeline 粒度的超时（复用 Feature 062 的 adaptive loop guard）
- Pipeline 粒度的 token/cost 预算
- 并发 Pipeline 数量限制

### 5.4 Graph 进度的前端可视化

Pipeline 的进度事件已通过 SSE 发送。但前端 Task Detail 页面（Feature 060/061）当前按"轮次流程图"展示，需要扩展支持 Pipeline 节点视图。

### 5.5 Graph 与 Subagent 的语义边界

两者可能混淆：
- **Subagent**：临时自治智能体，Free Loop，有 LLM 推理能力
- **Graph/Pipeline**：确定性编排流程，节点可调用 LLM 但不自主决策

需要在 system prompt 和工具描述中明确区分：
- 不确定任务（需要探索/推理） → 用 Subagent
- 确定性流程（已知步骤序列） → 用 Pipeline

### 5.6 安全约束

Pipeline 的 side-effect 节点（如 deploy、delete）必须经过 Policy Engine 门禁：
- 复用 Two-Phase（Plan → Gate → Execute）Constitution 规则
- Pipeline 定义中标记节点的 risk_level
- WAITING_APPROVAL 暂停状态已支持审批流

---

## 附录：关键文件索引

| 文件 | 路径 | 说明 |
|------|------|------|
| pydantic_graph 核心 | `_references/opensource/pydantic-ai/pydantic_graph/pydantic_graph/graph.py` | Graph/GraphRun/GraphRunResult |
| pydantic_graph 节点 | `_references/opensource/pydantic-ai/pydantic_graph/pydantic_graph/nodes.py` | BaseNode/End/Edge |
| pydantic_graph 持久化 | `_references/opensource/pydantic-ai/pydantic_graph/pydantic_graph/persistence/` | BaseStatePersistence/FileStatePersistence |
| GraphRuntimeBackend | `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py` | 现有 Graph 后端（L271-370） |
| SkillPipelineEngine | `octoagent/packages/skills/src/octoagent/skills/pipeline.py` | 确定性 Pipeline 执行器 |
| DelegationTargetKind | `octoagent/packages/core/src/octoagent/core/models/delegation.py` | GRAPH_AGENT 枚举定义 |
| DelegationPlaneService | `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py` | Delegation 路由 |
| ButlerDecisionMode | `octoagent/packages/core/src/octoagent/core/models/behavior.py` | Butler 决策模式 |
| SkillsTool | `octoagent/packages/skills/src/octoagent/skills/tools.py` | skills 工具实现（参考模式） |
| SubagentLifecycle | `octoagent/apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` | Subagent 生命周期（参考模式） |
| CapabilityPack | `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` | 工具注册与运行时绑定 |
