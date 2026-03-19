# Feature 065: Graph Agent 感知与编排入口 — 技术规划

**日期**: 2026-03-19
**状态**: draft
**前置依赖**: spec.md / research/tech-research.md / research.md / checklists/

---

## 0. CRITICAL 与 FAIL 问题解决方案

本章节优先回应 clarify-report 中的 4 个 CRITICAL 和 requirements.md 中的 1 个 FAIL。

### 0.1 CRITICAL #1：Pipeline 节点嵌入 Subagent 调用的行为未定义

**决策**：v0.1 中 `delegation` 类型节点**不允许 spawn Subagent**。

**理由**：
- Subagent 的 Free Loop 运行时间不确定（可能数小时），与 Pipeline 的确定性流转语义冲突
- Pipeline 的 `_drive()` 循环是同步阻塞式的，无法优雅地等待 Subagent 的异步生命周期
- 两者的错误恢复语义不同：Pipeline 有 checkpoint 恢复，Subagent 有 event history 注入恢复

**实现方案**：
- `delegation` 节点类型保留在 `PipelineNodeType` 枚举中，但 v0.1 的 PIPELINE.md 解析器遇到 `type: delegation` 时返回验证错误："delegation 节点类型在 v0.1 中不支持，请使用 Subagent 工具替代不确定性子任务"
- 后续版本可通过 `delegation` 节点实现 Pipeline → Subagent 桥接，设计为 Pipeline 进入 PAUSED 状态等待 Subagent 完成

**spec 约束补充**：
> 约束 5（新增）：v0.1 不支持 `delegation` 类型节点。PIPELINE.md 中使用 delegation 类型将触发解析错误。Pipeline 与 Subagent 是平行能力，由 LLM 根据任务性质选择，不互相嵌套。

### 0.2 CRITICAL #4：SkillPipelineEngine 与 GraphRuntimeBackend 执行路径断裂

**决策**：选择**方案 C（变体）**——Feature 065 的 Pipeline 执行不经过 WorkerRuntime 路由层。

**方案细节**：
1. `graph_pipeline` 工具**直接**持有独立的 `SkillPipelineEngine` 实例（与 DelegationPlane 内部的 Engine 分离）
2. `graph_pipeline(action="start")` 创建 Child Task + Work 后，直接调用 Engine 的 `start_run()`
3. Work 对象的 `target_kind` 使用现有的 `DelegationTargetKind.GRAPH_AGENT`，但**不进入 WorkerRuntime 分发**——因为 Pipeline 由工具层内联执行
4. `GraphRuntimeBackend` 保持不动（不修改、不删除），标记为 `# NOTE: 保留供后续 pydantic_graph 原生图场景使用，Feature 065 不依赖`

**为什么不选方案 A/B**：
- 方案 A（修改路由到 SkillPipelineEngine）：WorkerRuntime 的 `run()` 方法设计为统一的 LLM 执行循环，侵入性过大
- 方案 B（新增 PIPELINE target kind）：增加枚举值的风险收益比不高，GRAPH_AGENT 已语义足够

**handler 命名空间隔离**：
- DelegationPlane 的内部 Engine 实例注册 `route.resolve`/`bootstrap.prepare`/`tool_index.select`/`gate.review`/`finalize` 5 个 handler
- Feature 065 的独立 Engine 实例注册用户 Pipeline 的 handler（如 `terminal.exec`/`approval_gate` 等通用 handler）
- 两个 Engine 实例完全独立，handler_id 无冲突

### 0.3 CRITICAL #6：SKILL.md 与 PIPELINE.md 的交叉引用关系

**决策**：v0.1 中 PIPELINE.md 和 SKILL.md **互不引用**，是完全独立的注册表。

**具体规则**：
1. **Skill 不引用 Pipeline**：SKILL.md 中不存在"关联 Pipeline"字段。Skill 加载不触发 Pipeline 注册。
2. **Pipeline 节点不引用 Skill 名**：`handler_id` 引用的是 `PipelineNodeHandler` 注册表中的处理器 ID，不是 Skill 名。Pipeline 节点类型中的 `skill` 指的是节点由一个 Skill Handler 处理，但 handler 注册由代码完成，不由 SKILL.md 驱动。
3. **独立注册表**：`PipelineRegistry` 和 `SkillDiscovery` 是两个独立的服务实例，各自扫描各自的目录。

**spec 约束补充**：
> 约束 6（新增）：v0.1 中 PIPELINE.md 和 SKILL.md 互不引用。Pipeline 节点的 handler_id 引用代码注册的 PipelineNodeHandler，不引用 SKILL.md 名称。两个注册表（PipelineRegistry / SkillDiscovery）完全独立。

### 0.4 CRITICAL #9：节点失败后的回退/回滚策略

**决策**：v0.1 采用**"节点失败 = Pipeline 失败，不支持自动回退"**策略。

**具体行为**：
1. 节点 handler 返回 `status=FAILED` → Pipeline run 进入 `FAILED` 终态
2. Pipeline FAILED 时，metadata 中必须包含：
   - `failure_category`：`tool` / `gate` / `timeout` / `handler_missing` / `validation` / `unknown`
   - `failed_node_id`：失败节点 ID
   - `recovery_hint`：人类可读的恢复建议文本
3. 人工可通过 `graph_pipeline(action="retry", run_id=...)` 重试当前失败节点（暴露 Engine 已有的 `retry_current_node()` 方法）
4. **不支持自动回退到前一个节点**——原因：回退会导致已执行节点的 side-effect 不一致
5. **幂等性要求**：side-effect 类节点的 handler 实现**必须**利用 `side_effect_cursor` 保证幂等。Checkpoint 恢复时重新执行当前节点，handler 通过 cursor 判断是否需要跳过已完成的 side-effect

**spec 补充**：
- FR-065-03 工具 schema 新增 `action="retry"` → 调用 `SkillPipelineEngine.retry_current_node()`
- FR-065-05 AC-07 补充：Pipeline FAILED 时 metadata 包含 `failure_category` + `failed_node_id` + `recovery_hint`

### 0.5 FAIL：graph_pipeline 工具副作用等级未声明

**解决方案**：为 `graph_pipeline` 工具各 action 声明副作用等级。

| action | SideEffectLevel | 理由 |
|--------|----------------|------|
| `list` | `none` | 纯读取，从内存缓存返回 Pipeline 列表 |
| `start` | `irreversible` | 启动 Pipeline 可能执行不可逆操作（部署、发消息等） |
| `status` | `none` | 纯读取，查询 run 状态 |
| `resume` | `irreversible` | 恢复 Pipeline 继续执行不可逆节点 |
| `cancel` | `reversible` | 取消 run，但已执行的节点 side-effect 不可撤销 |
| `retry` | `irreversible` | 重试节点可能重复执行 side-effect（依赖幂等保证） |

**实现**：在 `@tool_contract` 装饰器中声明 `side_effect_level=SideEffectLevel.IRREVERSIBLE`（取最高级别），并在工具描述中注明各 action 的实际副作用等级。

---

## 1. 架构概览

### 1.1 核心组件关系

```
                    ┌──────────────────────────────────────────┐
                    │              LLM Agent Layer              │
                    │  (Butler / Worker / Subagent)            │
                    └──────┬───────────────┬───────────────────┘
                           │               │
               System Prompt 注入     Tool Call
            (Available Pipelines)         │
                                          ▼
                    ┌─────────────────────────────────────────┐
                    │          GraphPipelineTool               │
                    │  (graph_pipeline action=list/start/...)  │
                    └──────┬──────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
      PipelineRegistry   TaskService   SkillPipelineEngine
      (discover/cache)   (create       (start_run/resume/
                          child task)   cancel/retry)
              │                         │
              ▼                         ▼
       PIPELINE.md                PipelineCheckpoint
       文件系统                    + Events (SSE)
```

### 1.2 执行路径

**路径 A：Worker/Subagent 通过工具调用 Pipeline**

```
Worker LLM → graph_pipeline(action="start", pipeline_id="deploy-staging", params={branch: "main"})
  → GraphPipelineTool.execute()
    → PipelineRegistry.get(pipeline_id) → PipelineManifest
    → TaskService.create_child_task()
    → SkillPipelineEngine.start_run(definition, task_id, work_id, initial_state)
      → _drive() 逐节点执行
        → 节点完成 → Checkpoint + Event
        → 遇到 gate 节点 → WAITING_APPROVAL → 返回
    → 返回 run_id + 当前状态
```

**路径 B：Butler 直接委派 Pipeline（DELEGATE_GRAPH）**

```
Butler LLM → ButlerDecision(mode=DELEGATE_GRAPH, pipeline_id="deploy-staging", pipeline_params={...})
  → Butler 路由逻辑
    → GraphPipelineTool.execute(action="start", pipeline_id=..., params=...)
    → 同路径 A 后续流程
```

---

## 2. 实现阶段划分

### Phase 1：核心基础（PIPELINE.md 解析 + PipelineRegistry + 通用 Handler）

**目标**：Pipeline 定义可被发现、解析、验证、缓存。

**任务**：

| # | 任务 | 改动文件 | FR 覆盖 |
|---|------|----------|---------|
| 1.1 | 定义 `PipelineManifest` / `PipelineSource` 数据模型 | 新建 `pipeline_models.py` | FR-065-02 AC-02 |
| 1.2 | 实现 PIPELINE.md 解析器（复用 `split_frontmatter`/`parse_frontmatter`） | 新建 `pipeline_registry.py` | FR-065-01 AC-01~06 |
| 1.3 | 实现 DAG 验证（entry_node 存在 + next 引用有效 + 环检测 + 孤立节点检查） | 同上 | FR-065-01 AC-03 |
| 1.4 | 实现 PipelineRegistry 三级目录扫描 + 缓存 + refresh | 同上 | FR-065-02 AC-01~05 |
| 1.5 | 创建内置 `echo-test` Pipeline 定义 | 新建 `pipelines/echo-test/PIPELINE.md` | 测试用 |
| 1.6 | 实现通用 Pipeline handler：`terminal.exec` / `approval_gate` | 新建 `pipeline_handlers.py` | FR-065-05 AC-04 |
| 1.7 | 单元测试：解析器 + 验证器 + Registry | 新建测试文件 | — |

**验收门**：
- PIPELINE.md 格式文件可被正确解析为 `SkillPipelineDefinition`
- 三级目录扫描 + 优先级覆盖生效
- DAG 验证拒绝环路、孤立节点、缺失引用
- 单文件解析失败不影响其他 Pipeline

### Phase 2：LLM 工具层（GraphPipelineTool + 执行集成）

**目标**：LLM 可通过 tool call 发现、启动、监控、管理 Pipeline。

**任务**：

| # | 任务 | 改动文件 | FR 覆盖 |
|---|------|----------|---------|
| 2.1 | 实现 `GraphPipelineTool`（6 个 action：list/start/status/resume/cancel/retry） | 新建 `pipeline_tool.py` | FR-065-03 |
| 2.2 | 创建独立 SkillPipelineEngine 实例 + 注册通用 handler | `pipeline_tool.py` | FR-065-05 AC-03~04 |
| 2.3 | `start` action：创建 Child Task + Work → 启动 Engine | `pipeline_tool.py` | FR-065-05 AC-01~03 |
| 2.4 | `resume` action：WAITING_INPUT 和 WAITING_APPROVAL 两条路径 | `pipeline_tool.py` | FR-065-06 AC-03~04 |
| 2.5 | `cancel` action：调用 Engine.cancel_run + Task 终态同步 | `pipeline_tool.py` | FR-065-03 AC-05 |
| 2.6 | `retry` action：调用 Engine.retry_current_node | `pipeline_tool.py` | CRITICAL #9 |
| 2.7 | `start` 非阻塞包装：asyncio.create_task 后台执行 + 立即返回 run_id | `pipeline_tool.py` | clarify INFO #14 |
| 2.8 | 并发 run 计数 + 上限检查（默认 10） | `pipeline_tool.py` | NFR-065-01 |
| 2.9 | 输入参数验证（pipeline_id 存在 + params 符合 input_schema） | `pipeline_tool.py` | FR-065-03 AC-06 |
| 2.10 | 工具注册到 CapabilityPack ToolBroker + `@tool_contract` 装饰 | `capability_pack.py` | FR-065-03 AC-08, FAIL |
| 2.11 | Engine handler 缺失改为 FAILED 终态 | `pipeline.py` | CRITICAL #9 |
| 2.12 | Pipeline FAILED metadata 补充 failure_category + recovery_hint | `pipeline.py` / `pipeline_tool.py` | CRITICAL #9 |
| 2.13 | Pipeline run 启动时快照 definition（存入 run.metadata） | `pipeline_tool.py` | 风险 R-NEW-1 |
| 2.14 | 单元测试 + 集成测试 | 新建测试文件 | — |

**验收门**：
- LLM 调用 `graph_pipeline(action="list")` 返回可用 Pipeline 列表
- LLM 调用 `graph_pipeline(action="start")` 成功创建 Child Task + Work + Pipeline run
- Pipeline 运行过程中每个节点生成 Checkpoint + Event
- Pipeline 遇到 gate 节点暂停 → resume 后继续
- cancel / retry 正常工作
- 并发上限生效

### Phase 3：Butler 路由感知 + System Prompt 注入

**目标**：Butler 可直接选择 DELEGATE_GRAPH；所有 Agent 层感知 Pipeline 存在。

**任务**：

| # | 任务 | 改动文件 | FR 覆盖 |
|---|------|----------|---------|
| 3.1 | ButlerDecisionMode 新增 DELEGATE_GRAPH | `behavior.py` | FR-065-04 AC-01 |
| 3.2 | ButlerDecision 新增 pipeline_id / pipeline_params | `behavior.py` | FR-065-04 AC-02/05 |
| 3.3 | Butler prompt 注入 Pipeline 列表 + trigger_hint | `butler_behavior.py` | FR-065-04 AC-03, FR-065-07 AC-05 |
| 3.4 | Butler DELEGATE_GRAPH 路由分支实现 | `butler_behavior.py` | FR-065-04 AC-04 |
| 3.5 | DELEGATE_GRAPH fallback 到 DELEGATE_DEV/OPS | `butler_behavior.py` | FR-065-04 AC-06 |
| 3.6 | Worker/Subagent system prompt 注入 Pipeline 列表 + 语义区分指引 | `llm_service.py` | FR-065-07 AC-01~04 |
| 3.7 | 单元测试 | 新建测试文件 | — |

**验收门**：
- Butler 可解析 `delegate_graph` decision mode
- Butler system prompt 包含 Pipeline 列表
- Worker system prompt 包含 Pipeline 列表 + 语义区分指引
- DELEGATE_GRAPH 失败时正确 fallback

### Phase 4：管理 API + HITL 渠道集成

**目标**：REST API 可查询 Pipeline 定义和运行实例；审批渠道集成。

**任务**：

| # | 任务 | 改动文件 | FR 覆盖 |
|---|------|----------|---------|
| 4.1 | 实现 Pipeline 管理 API（5 个端点） | 新建 `routes/pipelines.py` | FR-065-09 |
| 4.2 | API 响应模型定义（PipelineItemResponse / PipelineRunResponse 等） | 同上 | FR-065-09 AC-06 |
| 4.3 | 挂载路由到 FastAPI app | `app.py` | — |
| 4.4 | HITL：Task 状态同步（Pipeline WAITING_APPROVAL → Task WAITING_APPROVAL） | `pipeline_tool.py` | FR-065-06 AC-01~02 |
| 4.5 | HITL：渠道审批桥接（Web/Telegram 审批 → graph_pipeline resume） | `pipeline_tool.py` | FR-065-06 AC-05 |
| 4.6 | HITL：拒绝审批 → CANCELLED | `pipeline_tool.py` | FR-065-06 AC-06 |
| 4.7 | 集成测试 | 新建测试文件 | — |

**验收门**：
- 所有 5 个 API 端点可用
- Pipeline WAITING_APPROVAL 时 Task 状态同步
- Web UI 审批按钮可触发 Pipeline resume

---

## 3. 关键设计决策

### 3.1 Pipeline 执行模式：后台异步

`graph_pipeline(action="start")` 采用**非阻塞模式**：

```python
async def _handle_start(self, pipeline_id: str, params: dict) -> str:
    manifest = self._registry.get(pipeline_id)
    # ... 验证 + 创建 Task + Work ...

    # 后台执行 Pipeline，立即返回 run_id
    asyncio.create_task(self._execute_pipeline_run(
        definition=manifest.definition,
        task_id=child_task.task_id,
        work_id=work.work_id,
        initial_state=params,
        run_id=run_id,
    ))

    return f"Pipeline '{pipeline_id}' started. run_id={run_id}. Use graph_pipeline(action='status', run_id='{run_id}') to check progress."
```

**理由**：
- Pipeline 可能包含长时间运行的节点（如部署操作 30 分钟），阻塞 LLM tool call 会导致 Worker Free Loop 停滞
- 与 Subagent spawn 模式对齐（spawn 后立即返回，后续查询状态）
- LLM 可通过 `status` action 轮询进度

### 3.2 独立 Engine 实例

Feature 065 创建**独立的 SkillPipelineEngine 实例**，与 DelegationPlane 内部 Engine 分离：

- 独立 handler 注册表：用户 Pipeline 的 handler（`terminal.exec` / `approval_gate` 等）不与内部 delegation handler 冲突
- 独立生命周期：Engine 由 `GraphPipelineTool` 管理，不依赖 DelegationPlane 初始化
- 共享 StoreGroup：两个 Engine 共享底层存储（pipeline_run / checkpoint 表），通过 run_id 隔离

### 3.3 Pipeline Definition 启动快照

Pipeline run 启动时，将完整 `SkillPipelineDefinition` 序列化存入 `run.metadata["definition_snapshot"]`：

- **理由**：运行期间 PIPELINE.md 可能被修改或删除，快照保证 run 使用一致的 definition
- **恢复时**：从 `metadata["definition_snapshot"]` 反序列化 definition，而非重新从 PipelineRegistry 获取

### 3.4 并发控制层

并发 run 计数由 `GraphPipelineTool` 维护（非 Engine 层），因为：
- Engine 层不感知"系统级"限制，它只执行单个 run
- 工具层是所有外部启动的唯一入口，统一在此检查
- 进程重启后，从数据库查询 `status=RUNNING` 的 run 数量重建计数

### 3.5 Butler DELEGATE_GRAPH 路由优先级

Butler 同时看到 Pipeline trigger_hint 和 Worker 类型时的决策规则：

1. 如果用户请求**精确匹配**某个 Pipeline 的 trigger_hint（如"部署到 staging"）→ 优先 `DELEGATE_GRAPH`
2. 如果用户请求**模糊匹配**（如"帮我处理一下部署的事情"）→ 由 LLM 自主判断，trigger_hint 作为上下文
3. **fallback 规则**：`DELEGATE_GRAPH` 中指定的 pipeline_id 不存在或参数不合法时，按 Pipeline tags 就近匹配 Worker 类型：
   - tags 包含 `deploy` / `ci-cd` / `ops` → fallback 到 `DELEGATE_OPS`
   - tags 包含 `dev` / `code` / `build` → fallback 到 `DELEGATE_DEV`
   - 其他 → fallback 到 `DELEGATE_RESEARCH`

### 3.6 Pipeline vs Subagent 语义区分（System Prompt 注入）

注入到所有 Agent 层的指引文本：

```
## Pipelines vs Subagents

Use **Pipeline** (graph_pipeline tool) when:
- The task follows a known, repeatable sequence of steps
- Steps need checkpoint/recovery guarantees (e.g., deploy, data migration)
- Steps include approval gates or human review points
- Deterministic execution is preferred over LLM reasoning

Use **Subagent** (subagents tool) when:
- The task requires exploration, reasoning, or multi-turn interaction
- The approach is not predetermined and needs LLM judgment
- The task involves creative work (writing, analysis, research)
- Flexibility is more important than determinism
```

---

## 4. 通用 Pipeline Handler 设计

### 4.1 内置 Handler 清单

| handler_id | 用途 | 实现位置 |
|-----------|------|----------|
| `terminal.exec` | 在终端执行命令 | `pipeline_handlers.py` |
| `approval_gate` | 审批门禁，触发 WAITING_APPROVAL | `pipeline_handlers.py` |
| `input_gate` | 用户输入门禁，触发 WAITING_INPUT | `pipeline_handlers.py` |
| `transform.passthrough` | 透传（测试/调试用） | `pipeline_handlers.py` |

### 4.2 Handler 注册时机

`GraphPipelineTool.__init__()` 中创建 Engine 实例后立即注册所有内置 handler：

```python
self._engine = SkillPipelineEngine(store_group=store_group, event_recorder=event_recorder)
self._engine.register_handler("terminal.exec", _terminal_exec_handler)
self._engine.register_handler("approval_gate", _approval_gate_handler)
self._engine.register_handler("input_gate", _input_gate_handler)
self._engine.register_handler("transform.passthrough", _passthrough_handler)
```

### 4.3 Handler 幂等性约定

所有 side-effect 类 handler 必须遵循：
- 接收 `run.state_snapshot` 中的 `side_effect_cursor` 字段
- 如果 cursor 表明操作已完成，跳过执行，返回 `PipelineNodeOutcome(status=RUNNING, summary="skipped (idempotent)")`
- 执行成功后，在 outcome 中设置新的 `side_effect_cursor`

---

## 5. 事件与可观测

### 5.1 事件流

| 时机 | 事件类型 | payload 关键字段 |
|------|---------|----------------|
| Pipeline 启动 | `PIPELINE_RUN_UPDATED` | status=RUNNING, pipeline_id, run_id, task_id |
| 节点开始执行 | `PIPELINE_RUN_UPDATED` | status=RUNNING, current_node_id |
| 节点完成 | `PIPELINE_CHECKPOINT_SAVED` | checkpoint_id, node_id, status |
| Pipeline 暂停 | `PIPELINE_RUN_UPDATED` | status=WAITING_APPROVAL/WAITING_INPUT |
| Pipeline 恢复 | `PIPELINE_RUN_UPDATED` | status=RUNNING |
| Pipeline 完成 | `PIPELINE_RUN_UPDATED` | status=SUCCEEDED/FAILED/CANCELLED |

### 5.2 事件 payload 脱敏

Pipeline 事件 payload 中的工具执行参数（如 terminal 命令内容）**不直接写入 Event payload**。如果需要记录，使用 Artifact 引用：
- Event payload 中放 `artifact_ref_id`
- 详细内容写入 Artifact Store

### 5.3 Pipeline 错误分类

Pipeline FAILED 时 metadata 包含：

```json
{
  "failure_category": "tool",  // tool / gate / timeout / handler_missing / validation / unknown
  "failed_node_id": "deploy",
  "recovery_hint": "节点 'deploy' 执行失败。可通过 graph_pipeline(action='retry', run_id='...') 重试当前节点。",
  "error_message": "Exit code 1: permission denied"
}
```

---

## 6. WARNING 问题处理方案

### 6.1 graph_pipeline 与 subagents 工具的 LLM 选择冲突

通过 system prompt 注入语义区分指引（见 3.6 节）。不在工具层增加 `recommended_over_subagent` 字段——由 LLM 自主判断。

### 6.2 节点级超时

`SkillPipelineNode` 模型**已有** `timeout_seconds` 字段，但 `SkillPipelineEngine._drive()` 未消费。Phase 2 中为 `_drive()` 增加节点超时逻辑：

```python
if node.timeout_seconds:
    try:
        outcome = await asyncio.wait_for(
            handler(run=current, node=node, state=dict(current.state_snapshot)),
            timeout=node.timeout_seconds,
        )
    except asyncio.TimeoutError:
        outcome = PipelineNodeOutcome(
            status=PipelineRunStatus.FAILED,
            summary=f"节点 {node.node_id} 执行超时 ({node.timeout_seconds}s)",
        )
```

同时在 PIPELINE.md frontmatter 的 nodes 定义中允许声明 `timeout_seconds`。

### 6.3 并发上限实施层

- **计数维护**：`GraphPipelineTool` 内部 `_active_run_count: int`
- **计数范围**：全局级别（单进程）
- **崩溃恢复**：进程重启后，查询 `work_store` 中 `status=RUNNING` 的 Pipeline run 数量重建计数
- **配额回收**：Pipeline run 进入终态时递减计数

### 6.4 Pipeline 版本兼容性

v0.1 仅支持 version 以 `1.` 开头的 PIPELINE.md。其他版本号在解析时返回错误：
```
"不支持的 Pipeline 版本 '{version}'。v0.1 仅支持 1.x.x 版本。"
```

### 6.5 REST API 分页

`GET /api/pipeline-runs` 支持分页参数：
- `page`：页码（默认 1）
- `page_size`：每页数量（默认 20，上限 100）
- `pipeline_id`：可选筛选
- `status`：可选筛选
- `task_id`：可选筛选

### 6.6 WAITING_INPUT 超时

PIPELINE.md 节点定义中的 `timeout_seconds` 也适用于 WAITING_INPUT 状态。超时后 Pipeline 转 FAILED，failure_category 为 `timeout`，recovery_hint 提示用户可 retry 当前节点并提供输入。

---

## 7. 测试策略

### 7.1 单元测试

| 模块 | 测试重点 |
|------|---------|
| PIPELINE.md 解析器 | frontmatter 解析、必填字段验证、DAG 环检测、孤立节点检测、delegation 类型拒绝 |
| PipelineRegistry | 三级目录扫描、优先级覆盖、缓存 + refresh、单文件失败隔离 |
| GraphPipelineTool | 6 个 action 的正常/异常路径、参数验证、并发上限、run_id 不存在 |
| PipelineManifest | 模型序列化/反序列化、字段默认值 |
| ButlerDecision 扩展 | DELEGATE_GRAPH 模式解析、pipeline_id 填充、fallback 逻辑 |

### 7.2 集成测试

| 场景 | 覆盖范围 |
|------|---------|
| 端到端 Pipeline 执行 | start → 节点逐步执行 → Checkpoint → SUCCEEDED |
| HITL 审批流 | start → gate 节点 → WAITING_APPROVAL → resume(approved=true) → 继续 → SUCCEEDED |
| HITL 拒绝流 | start → gate → WAITING_APPROVAL → resume(approved=false) → CANCELLED |
| 节点失败 + 重试 | start → 节点 FAILED → retry → 节点成功 → SUCCEEDED |
| 并发上限 | 启动 10 个 run → 第 11 个被拒绝 |
| 进程恢复 | start → 进程重启 → 从 Checkpoint 恢复 |
| REST API | 所有端点 + 分页 + 筛选 |

---

## 8. 风险缓解措施追踪

| 风险 | 缓解措施 | 实现阶段 |
|------|---------|---------|
| R-NEW-1：Pipeline 定义热更新冲突 | 启动时快照 definition 到 run.metadata | Phase 2 (2.13) |
| R-NEW-2：DAG 环路 | 解析器增加环检测（DFS） | Phase 1 (1.3) |
| R-NEW-3：Token 预算失控 | Pipeline run 的 token 消耗纳入 Feature 062 Loop Guard 预算 | Phase 2 |
| R-NEW-4：Checkpoint 恢复后上下文丢失 | handler 幂等性约定 + side_effect_cursor | Phase 2 (handler 设计) |

---

## 9. 交付物清单

### 9.1 代码交付

| 文件 | 类型 | 描述 |
|------|------|------|
| `octoagent/packages/skills/src/octoagent/skills/pipeline_models.py` | 新建 | PipelineManifest / PipelineSource 模型 |
| `octoagent/packages/skills/src/octoagent/skills/pipeline_registry.py` | 新建 | PipelineRegistry 文件系统扫描 + 缓存 |
| `octoagent/packages/skills/src/octoagent/skills/pipeline_tool.py` | 新建 | GraphPipelineTool LLM 工具 |
| `octoagent/packages/skills/src/octoagent/skills/pipeline_handlers.py` | 新建 | 通用 Pipeline handler 实现 |
| `octoagent/apps/gateway/src/octoagent/gateway/routes/pipelines.py` | 新建 | REST API 路由 |
| `pipelines/echo-test/PIPELINE.md` | 新建 | 内置测试用 Pipeline |
| `octoagent/packages/core/src/octoagent/core/models/behavior.py` | 修改 | DELEGATE_GRAPH + pipeline_id |
| `octoagent/packages/skills/src/octoagent/skills/pipeline.py` | 修改 | handler 缺失 → FAILED + 节点超时 |
| `octoagent/packages/skills/src/octoagent/skills/__init__.py` | 修改 | 导出新模块 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` | 修改 | 注册 graph_pipeline 工具 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py` | 修改 | System prompt Pipeline 注入 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` | 修改 | DELEGATE_GRAPH 路由 |
| 测试文件（若干） | 新建 | 单元 + 集成测试 |

### 9.2 不包含在交付中

- Pipeline 前端可视化（后续 Feature）
- GraphRuntimeBackend 修改/删除（保持不动）
- delegation 节点类型实现（v0.1 不支持）
- Pipeline 动态创建（LLM 不能构建图结构）
