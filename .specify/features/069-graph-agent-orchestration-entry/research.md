# Feature 065: Graph Agent 感知与编排入口 — 现有代码分析

**日期**: 2026-03-19
**分析范围**: SkillPipelineEngine / DelegationPlane / CapabilityPack / WorkerRuntime / ButlerBehavior / SkillDiscovery / 领域模型

---

## 1. SkillPipelineEngine（确定性 Pipeline 执行器）

**文件**: `octoagent/packages/skills/src/octoagent/skills/pipeline.py`

### 1.1 公开 API

| 方法 | 签名 | 行为 |
|------|------|------|
| `start_run` | `(definition, task_id, work_id, initial_state?, run_id?) -> SkillPipelineRun` | 创建 run → 调用 `_drive()` 同步执行至完成或暂停 |
| `resume_run` | `(definition, run_id, state_patch?) -> SkillPipelineRun` | 加载 run → 合并 state_patch → `_drive()` 继续 |
| `retry_current_node` | `(definition, run_id) -> SkillPipelineRun` | 加载 run → 递增 retry_cursor → `_drive()` |
| `cancel_run` | `(run_id, reason?) -> SkillPipelineRun` | 标记 CANCELLED + 写入 checkpoint |
| `list_replay_frames` | `(run_id) -> list[PipelineReplayFrame]` | 从 checkpoint 构建回放帧 |

### 1.2 关键设计特征

- **同步阻塞 `_drive()`**：`start_run()` 和 `resume_run()` 内部调用 `_drive()` 后一直运行直到 Pipeline 完成或暂停。这意味着调用方协程会被阻塞。**Feature 065 的 `graph_pipeline(action="start")` 需要考虑是否改为后台 asyncio.Task 执行。**
- **Handler 注册表**：`_handlers: dict[str, PipelineNodeHandler]`，通过 `register_handler(handler_id, handler)` 注册。当前仅由 `DelegationPlaneService._register_pipeline_handlers()` 调用。
- **Handler 未找到时抛异常**：`_drive()` 中 `handler is None` 直接 `raise PipelineExecutionError`，不进入 FAILED 终态。**需要改为 FAILED + 事件而非异常。**
- **Checkpoint 原子写入**：每个节点执行后立即 `save_pipeline_checkpoint` + `conn.commit()`，满足 Durability First。
- **暂停语义**：支持 `WAITING_INPUT` / `WAITING_APPROVAL` / `PAUSED` 三种暂停状态，`_drive()` 返回当前 run 实例。`resume_run` 通过 `metadata.resume_next_node_id` 确定恢复节点。

### 1.3 依赖关系

- 依赖 `StoreGroup`（`work_store.save_pipeline_run` / `save_pipeline_checkpoint` / `get_pipeline_run` / `list_pipeline_checkpoints`）
- 依赖 `EventRecorder` 回调发射事件
- 不依赖 PipelineRegistry（当前 definition 由调用方直接传入）

### 1.4 Feature 065 改动点

| 改动 | 类型 | 说明 |
|------|------|------|
| handler 缺失从异常改为 FAILED 终态 | 修改 | `_drive()` 中 handler=None 时设置 run FAILED + 发射错误事件，而非 raise |
| 支持后台执行模式 | 新增 | 新增 `start_run_async()` 方法或在 `graph_pipeline` 工具层包装 asyncio.create_task |
| 并发 run 计数 | 新增 | 在 Engine 或工具层维护活跃 run 计数，支持上限检查 |

---

## 2. 领域模型（Pipeline 相关）

**文件**: `octoagent/packages/core/src/octoagent/core/models/pipeline.py`

### 2.1 已有模型

| 模型 | 用途 | Feature 065 变更 |
|------|------|----------------|
| `PipelineNodeType` | 节点类型枚举：skill/tool/transform/gate/delegation | 无变更 |
| `PipelineRunStatus` | run 状态枚举：created/running/waiting_input/waiting_approval/paused/succeeded/failed/cancelled | 无变更 |
| `SkillPipelineNode` | 节点定义（node_id/label/node_type/handler_id/next_node_id/retry_limit/timeout_seconds/metadata） | 无变更；**timeout_seconds 已存在**（clarify-report 中"节点级超时缺失"不准确——模型有该字段，Engine 未消费） |
| `SkillPipelineDefinition` | Pipeline 定义（pipeline_id/label/version/entry_node_id/nodes/metadata） | 无变更，PIPELINE.md 解析后复用此模型 |
| `SkillPipelineRun` | 运行时状态 | 无变更 |
| `PipelineCheckpoint` | 节点级检查点 | 无变更 |
| `PipelineReplayFrame` | 回放视图帧 | 无变更 |

### 2.2 需要新增的模型

| 模型 | 位置 | 用途 |
|------|------|------|
| `PipelineManifest` | `packages/skills/` 或 `packages/core/models/` | Pipeline 元数据摘要（id/description/version/tags/trigger_hint/input_schema/output_schema/source_path/source_level/definition） |
| `PipelineSource` | 同上 | 来源枚举：BUILTIN/USER/PROJECT（对齐 SkillSource） |

---

## 3. DelegationPlaneService（委派预处理）

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`

### 3.1 与 Pipeline Engine 的关系

- `DelegationPlaneService.__init__()` 内部创建 `SkillPipelineEngine` 实例并注册 5 个内部 handler：
  - `route.resolve` — 路由解析
  - `bootstrap.prepare` — Bootstrap 准备
  - `tool_index.select` — ToolIndex 选择
  - `gate.review` — 审批门禁
  - `finalize` — 终结
- 通过 `_build_definition()` 构建硬编码的 5 节点 delegation pipeline。
- **Engine 实例是 DelegationPlane 内部的**，与 Feature 065 用户 Pipeline 执行需要隔离。

### 3.2 Feature 065 影响分析

- DelegationPlane 的内部 pipeline（route → bootstrap → tool_index → gate → finalize）与用户定义的 PIPELINE.md 运行在**不同的 Engine 实例**上——Feature 065 需要独立的 SkillPipelineEngine 实例。
- DelegationPlane 已有 `pipeline_engine` property 暴露内部 Engine。Feature 065 的 `graph_pipeline` 工具**不应复用此 Engine**，避免 handler_id 命名空间冲突（CRITICAL #6 解决方案）。
- `DelegationPlaneService.delegate()` 中创建 Work + 启动 pipeline 的模式可作为 Feature 065 `graph_pipeline(action="start")` 的参考。

---

## 4. WorkerRuntime（后端路由）

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`

### 4.1 Backend 路由逻辑

`_select_backend()` 中：
- `target_kind == GRAPH_AGENT` → `self._graph_backend`（GraphRuntimeBackend）
- 否则根据 docker_mode 选择 inline/docker

### 4.2 GraphRuntimeBackend 分析

- 三节点硬编码管线（prepare → execute → finalize），execute 节点调用 `process_task_with_llm()`
- 本质是 inline backend 的 pydantic_graph 包装，不具备真正的用户自定义图能力
- **Feature 065 不依赖此 backend**。Pipeline 执行由独立的 SkillPipelineEngine 驱动，不经过 WorkerRuntime 路由。

### 4.3 CRITICAL #4 路由断裂解决方案

spec FR-065-05 AC-02 要求 `delegation_target_kind = GRAPH_AGENT`。但 WorkerRuntime 会将 GRAPH_AGENT 路由到 GraphRuntimeBackend。

**选择方案 C**：Feature 065 的 Pipeline 执行**不经过 WorkerRuntime**。`graph_pipeline` 工具直接调用独立的 SkillPipelineEngine。Work 对象的 `target_kind` 改用 `DelegationTargetKind.WORKER`（或新增 PIPELINE），Pipeline 运行由工具层控制，不进入 WorkerRuntime 分发流程。

---

## 5. ButlerDecisionMode 与 ButlerDecision

**文件**: `octoagent/packages/core/src/octoagent/core/models/behavior.py`

### 5.1 当前状态

```python
class ButlerDecisionMode(StrEnum):
    DIRECT_ANSWER = "direct_answer"
    ASK_ONCE = "ask_once"
    DELEGATE_RESEARCH = "delegate_research"
    DELEGATE_DEV = "delegate_dev"
    DELEGATE_OPS = "delegate_ops"
    BEST_EFFORT_ANSWER = "best_effort_answer"
```

```python
class ButlerDecision(BaseModel):
    mode: ButlerDecisionMode
    # ... 无 pipeline_id 字段
```

### 5.2 Feature 065 改动点

| 改动 | 说明 |
|------|------|
| 新增 `DELEGATE_GRAPH = "delegate_graph"` | ButlerDecisionMode 枚举扩展 |
| 新增 `pipeline_id: str = Field(default="")` | ButlerDecision 可选字段 |
| 新增 `pipeline_params: dict = Field(default_factory=dict)` | Butler 提取的 Pipeline 输入参数 |

### 5.3 Butler Behavior 改动

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py`

- `build_butler_behavior_prompt()` 中需注入 Pipeline 列表到 decision_modes 描述
- `_parse_butler_decision()` 需识别 `delegate_graph` mode 并填充 pipeline_id
- 需增加 DELEGATE_GRAPH → DelegationPlane 的路由分支

---

## 6. SkillDiscovery（参考模板）

**文件**: `octoagent/packages/skills/src/octoagent/skills/discovery.py`

### 6.1 设计模式

PipelineRegistry 应复用 SkillDiscovery 的以下模式：
- **三级目录扫描**：builtin → user → project，后覆盖前
- **YAML frontmatter + Markdown body** 解析：`split_frontmatter()` + `parse_frontmatter()` 工具函数可直接复用
- **内存缓存 + refresh()**：`_cache: dict[str, ...]` + `scan()` / `refresh()`
- **错误隔离**：单文件解析失败不影响其他文件
- **日志记录**：structlog 结构化日志

### 6.2 差异点

| 维度 | SkillDiscovery | PipelineRegistry |
|------|---------------|------------------|
| 文件名 | `SKILL.md` | `PIPELINE.md` |
| 必填字段 | name, description | name, description, entry_node, nodes |
| 输出模型 | `SkillMdEntry` | `PipelineManifest` |
| 额外验证 | 无 | entry_node 引用存在、next 引用存在、无孤立节点、DAG 环检测 |
| 与 Engine 关系 | 无 | 解析后生成 `SkillPipelineDefinition` |

---

## 7. CapabilityPack（工具注册）

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`

### 7.1 工具注册模式

现有工具通过 `tool_contract` 装饰器声明元数据，通过 `ToolBroker.register()` 注册。Feature 065 的 `graph_pipeline` 工具需遵循同一模式：

- `@tool_contract(side_effect_level=..., tool_profile=..., tool_group=...)` 装饰器
- `ToolBroker.register(name, handler, schema)` 注册
- Schema 由 `reflect_tool_schema()` 从函数签名自动生成

### 7.2 SkillsTool 注册参考

`SkillsTool` 通过 `execute()` 方法分发 action，注册时使用 `skills` 作为工具名。`GraphPipelineTool` 应采用相同模式：
- 工具名：`graph_pipeline`
- 入口方法：`execute(action, pipeline_id?, run_id?, params?, ...)`
- 注册到 CapabilityPack 的 ToolBroker

---

## 8. System Prompt 注入

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

### 8.1 现有 Skill 注入模式

L800 附近：
```python
lines = ["## Available Skills\n"]
for item in items:
    marker = " [loaded]" if item.name in loaded_names else ""
    lines.append(f"- **{item.name}**{marker}: {item.description}")
```

### 8.2 Feature 065 Pipeline 注入

在同一位置（或紧接其后）新增 `## Available Pipelines` 段落：
- 从 PipelineRegistry 获取 `list_items()`
- 格式：`- **{pipeline_id}**: {description} (trigger: {trigger_hint})`
- 空列表时不注入
- 同时注入 Pipeline vs Subagent 语义区分指引

---

## 9. REST API 路由（Skills 参考模板）

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/routes/skills.py`

### 9.1 现有端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 列出所有 Skill |
| GET | `/api/skills/{name}` | 获取单个 Skill 详情 |
| POST | `/api/skills` | 安装新 Skill |
| DELETE | `/api/skills/{name}` | 卸载 Skill |

### 9.2 Feature 065 Pipeline API

应在 `octoagent/apps/gateway/src/octoagent/gateway/routes/pipelines.py` 新建路由模块：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/pipelines` | 列出所有已注册 Pipeline |
| GET | `/api/pipelines/{pipeline_id}` | 获取单个 Pipeline 详情 |
| GET | `/api/pipeline-runs` | 列出 Pipeline run（支持筛选） |
| GET | `/api/pipeline-runs/{run_id}` | 获取单个 run 详情 |
| POST | `/api/pipelines/refresh` | 触发重新扫描 |

---

## 10. 文件改动总览

### 10.1 新建文件

| 文件 | 用途 |
|------|------|
| `octoagent/packages/skills/src/octoagent/skills/pipeline_registry.py` | PipelineRegistry（文件系统扫描 + 缓存） |
| `octoagent/packages/skills/src/octoagent/skills/pipeline_models.py` | PipelineManifest / PipelineSource 数据模型 |
| `octoagent/packages/skills/src/octoagent/skills/pipeline_tool.py` | GraphPipelineTool（LLM 工具实现） |
| `octoagent/apps/gateway/src/octoagent/gateway/routes/pipelines.py` | REST API 路由 |
| `pipelines/echo-test/PIPELINE.md` | 内置测试用 Pipeline |
| 测试文件（多个） | 对应模块的单元测试 |

### 10.2 修改文件

| 文件 | 改动 |
|------|------|
| `octoagent/packages/core/src/octoagent/core/models/behavior.py` | ButlerDecisionMode 新增 DELEGATE_GRAPH；ButlerDecision 新增 pipeline_id/pipeline_params |
| `octoagent/packages/skills/src/octoagent/skills/__init__.py` | 导出 PipelineRegistry / PipelineManifest / GraphPipelineTool |
| `octoagent/packages/skills/src/octoagent/skills/pipeline.py` | handler 缺失改为 FAILED 终态（而非 raise） |
| `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py` | system prompt 注入 Pipeline 列表 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` | 注册 graph_pipeline 工具到 ToolBroker |
| `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` | DELEGATE_GRAPH 路由分支 + Pipeline 列表注入到 Butler prompt |
| `octoagent/apps/gateway/src/octoagent/gateway/app.py`（或路由注册处） | 挂载 pipelines 路由 |
