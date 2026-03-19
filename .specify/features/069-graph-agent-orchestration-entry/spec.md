---
feature_id: "065"
title: "Graph Agent 感知与编排入口"
status: draft
priority: P1
research_mode: tech-only
---

# Feature 065: Graph Agent 感知与编排入口

## 概述

让 Butler/Worker/Subagent 的 LLM 层能**感知、发现、调用、监控** Graph Pipeline（确定性流程编排）。当前底层基础设施已就绪（`SkillPipelineEngine` + `PipelineCheckpoint` + `DelegationTargetKind.GRAPH_AGENT` + `PIPELINE_RUN_UPDATED`/`PIPELINE_CHECKPOINT_SAVED` 事件），但 LLM 层完全不可见——没有任何工具能列出、启动、查询或恢复 Pipeline。

采用技术调研推荐的**方案 A（Graph-as-Tool）**，与 Feature 064 Subagent 模式对齐：通过 `graph_pipeline` 工具将 Pipeline 能力暴露给 LLM，同时引入 `PIPELINE.md` 文件系统驱动的 Pipeline 注册表。

### 与现有基础设施的关系

| 组件 | 当前状态 | 本 Feature 变更 |
|------|----------|----------------|
| `SkillPipelineEngine` | 仅 DelegationPlane 内部调用 | 新增外部启动入口（供 `graph_pipeline` 工具调用） |
| `SkillPipelineDefinition` | 硬编码在 `DelegationPlane._build_definition()` | 可从 `PIPELINE.md` 文件解析构建 |
| `DelegationTargetKind.GRAPH_AGENT` | 已定义，WorkerRuntime 已路由 | 无变更，复用 |
| `PIPELINE_RUN_UPDATED` / `PIPELINE_CHECKPOINT_SAVED` | 已在 EventType 定义，Pipeline Engine 已发射 | 无变更，复用 |
| `ButlerDecisionMode` | 6 个模式，无 Graph 相关 | 新增 `DELEGATE_GRAPH` |
| `ButlerDecision` | 无 pipeline_id 字段 | 新增 `pipeline_id` 可选字段 |
| LLM 工具 | 无 Graph 相关工具 | 新增 `graph_pipeline` 工具 |
| System Prompt | 有 Skill 上下文注入 | 新增 Pipeline 列表注入 |

---

## 功能需求

### FR-065-01: PIPELINE.md 文件格式与解析

**描述**：定义 `PIPELINE.md` 文件格式，类似 SKILL.md 的文件系统驱动模式。每个 Pipeline 用一个 `PIPELINE.md` 文件描述其元数据、节点拓扑、输入/输出 schema 和触发条件。

**PIPELINE.md 格式**：

```yaml
---
name: deploy-staging
description: "将代码部署到 staging 环境。自动执行：拉取最新代码 -> 构建 -> 运行测试 -> 部署 -> 健康检查。"
version: 1.0.0
author: OctoAgent
tags:
  - deploy
  - staging
  - ci-cd
trigger_hint: "当用户要求部署到 staging、预发布环境、或测试环境时使用"
input_schema:
  branch:
    type: string
    description: "要部署的分支名"
    required: true
  skip_tests:
    type: boolean
    description: "是否跳过测试"
    default: false
output_schema:
  deploy_url:
    type: string
    description: "部署后的访问地址"
  build_id:
    type: string
    description: "构建 ID"
nodes:
  - id: pull-code
    label: "拉取最新代码"
    type: tool
    handler_id: terminal.exec
    next: build
  - id: build
    label: "构建项目"
    type: tool
    handler_id: terminal.exec
    next: run-tests
  - id: run-tests
    label: "运行测试"
    type: tool
    handler_id: terminal.exec
    next: deploy-gate
  - id: deploy-gate
    label: "部署审批"
    type: gate
    handler_id: approval_gate
    next: deploy
  - id: deploy
    label: "执行部署"
    type: tool
    handler_id: terminal.exec
    next: health-check
  - id: health-check
    label: "健康检查"
    type: tool
    handler_id: terminal.exec
entry_node: pull-code
---

# Deploy to Staging Pipeline

将代码部署到 staging 环境的标准流程。包含代码拉取、构建、测试、审批门禁和健康检查。

## 节点说明

### pull-code
从 Git 仓库拉取指定分支的最新代码。

### build
执行项目构建命令。

### run-tests
运行自动化测试套件，测试失败则 Pipeline 终止。

### deploy-gate
**需要人工审批**：确认是否继续部署到 staging。

### deploy
执行实际部署操作。

### health-check
验证部署后的服务健康状态。
```

**验收标准**：

- AC-01: PIPELINE.md frontmatter 必须包含 `name`（唯一标识）、`description`、`version`、`entry_node`、`nodes` 五个必填字段
- AC-02: `nodes` 列表中每个节点必须包含 `id`、`type`（对应 `PipelineNodeType` 枚举值：skill / tool / transform / gate / delegation）、`handler_id`；`next` 可选（无 next 表示终止节点）
- AC-03: 解析器必须验证 `entry_node` 指向已定义节点、所有 `next` 引用的节点存在、无孤立节点（除终止节点外）
- AC-04: 解析器将 PIPELINE.md 转换为 `SkillPipelineDefinition` 模型，复用现有领域模型
- AC-05: 解析失败（格式错误、引用缺失）时返回结构化错误信息，不影响其他 Pipeline 的加载
- AC-06: `input_schema` / `output_schema` / `trigger_hint` / `tags` / `author` 为可选字段，缺失时使用空默认值

---

### FR-065-02: Pipeline 注册表（PipelineRegistry）

**描述**：三级文件系统发现机制，与 Skill 系统（Feature 057）对齐。

**发现路径（优先级从高到低）**：

1. **项目级**：`{project_root}/pipelines/*/PIPELINE.md`
2. **用户级**：`~/.octoagent/pipelines/*/PIPELINE.md`
3. **内置级**：`pipelines/*/PIPELINE.md`（仓库根目录）

同名 Pipeline 按优先级覆盖（项目 > 用户 > 内置）。

**验收标准**：

- AC-01: `PipelineRegistry` 类提供 `discover_all() -> list[PipelineManifest]` 方法，扫描三级目录并返回已解析的 Pipeline 清单
- AC-02: `PipelineManifest` 数据类包含 `pipeline_id`、`description`、`version`、`tags`、`trigger_hint`、`input_schema`、`output_schema`、`source_path`、`source_level`（项目/用户/内置）、`definition: SkillPipelineDefinition`
- AC-03: 同名 Pipeline 按项目 > 用户 > 内置的优先级覆盖，低优先级的同名定义被忽略
- AC-04: 扫描结果缓存在内存中，提供 `refresh() -> list[PipelineManifest]` 方法手动刷新
- AC-05: 单个 PIPELINE.md 解析失败不影响其他 Pipeline 的发现，错误通过 structlog 记录

---

### FR-065-03: `graph_pipeline` LLM 工具

**描述**：新建 `GraphPipelineTool`，注册到 CapabilityPack，使 LLM 可以发现、启动、监控和管理 Pipeline。

**工具 schema**：

```
graph_pipeline(action, ...)
  action=list          -> 返回可用 Pipeline 列表（id, description, tags, input_schema）
  action=start         -> 启动指定 Pipeline，返回 run_id
    pipeline_id: str       （必填）
    params: dict           （可选，Pipeline 输入参数）
  action=status        -> 查询 Pipeline run 状态
    run_id: str            （必填）
  action=resume        -> 恢复暂停的 Pipeline（提供 input 或 approval）
    run_id: str            （必填）
    input_data: dict       （可选，WAITING_INPUT 时提供）
    approved: bool         （可选，WAITING_APPROVAL 时提供）
  action=cancel        -> 取消正在运行的 Pipeline
    run_id: str            （必填）
```

**验收标准**：

- AC-01: `graph_pipeline(action="list")` 返回 PipelineRegistry 中所有 Pipeline 的摘要信息（id、description、tags、input_schema、trigger_hint），格式适合 LLM 阅读
- AC-02: `graph_pipeline(action="start", pipeline_id="...", params={...})` 通过 SkillPipelineEngine 启动 Pipeline run，返回 `run_id`；启动时创建 Child Task + Work（`DelegationTargetKind.GRAPH_AGENT`）
- AC-03: `graph_pipeline(action="status", run_id="...")` 返回 Pipeline run 的当前状态（status、current_node_id、已完成节点列表、暂停原因），格式适合 LLM 阅读
- AC-04: `graph_pipeline(action="resume", run_id="...", ...)` 支持恢复 WAITING_INPUT（提供 input_data）和 WAITING_APPROVAL（提供 approved 决定）两种暂停状态
- AC-05: `graph_pipeline(action="cancel", run_id="...")` 取消正在运行或暂停的 Pipeline，状态转为 CANCELLED
- AC-06: 启动时验证 `pipeline_id` 存在于 PipelineRegistry、`params` 符合 `input_schema`（若定义了 schema）；验证失败返回结构化错误
- AC-07: 对不存在的 `run_id` 返回明确的"未找到"错误，不抛异常
- AC-08: 工具通过 ToolBroker 注册，schema 与实现签名一致（Constitution 原则 3：Tools are Contracts）

---

### FR-065-04: Butler 路由感知（DELEGATE_GRAPH 决策模式）

**描述**：扩展 `ButlerDecisionMode` 新增 `DELEGATE_GRAPH`，使 Butler 在决策时可选择直接委派 Graph Pipeline 执行，无需经过 Worker LLM 中转。

**验收标准**：

- AC-01: `ButlerDecisionMode` 新增 `DELEGATE_GRAPH = "delegate_graph"` 枚举值
- AC-02: `ButlerDecision` 模型新增可选字段 `pipeline_id: str | None = None`，当 mode 为 `DELEGATE_GRAPH` 时必须填充
- AC-03: Butler 的 system prompt 注入已注册 Pipeline 列表（id + description + trigger_hint），使 LLM 有足够上下文做路由决策
- AC-04: Butler 选择 `DELEGATE_GRAPH` 时，直接通过 DelegationPlane 创建 Child Task + Work + 启动 Pipeline，跳过 Worker LLM 中转
- AC-05: Butler 决策结果中可携带 `pipeline_id` 和 `params`（从用户请求提取的参数）
- AC-06: 当指定的 Pipeline 不存在或参数不合法时，Butler 应 fallback 到 `DELEGATE_DEV` / `DELEGATE_OPS`（按 Pipeline tags 就近匹配 Worker 类型），并在决策原因中说明 fallback 理由

---

### FR-065-05: 执行集成（Pipeline 启动 -> Task/Work 创建 -> Engine 执行）

**描述**：`graph_pipeline.start` 和 Butler `DELEGATE_GRAPH` 两条路径都需要创建 Child Task + Work，并通过 SkillPipelineEngine 执行。

**验收标准**：

- AC-01: Pipeline 启动时创建 Child Task（parent_task_id 指向调用者 Task），Task metadata 标记 `pipeline_id`
- AC-02: 创建 Work 对象，`delegation_target_kind = DelegationTargetKind.GRAPH_AGENT`，Work metadata 中包含 `pipeline_id` 和 `run_id`
- AC-03: SkillPipelineEngine 从 PipelineRegistry 获取对应的 `SkillPipelineDefinition` 并调用 `start_run()`
- AC-04: Pipeline 中的节点 handler 通过 `handler_id` 路由到已注册的 `PipelineNodeHandler` 实现
- AC-05: Pipeline 执行过程中，每个节点完成后写入 `PipelineCheckpoint`（Constitution 原则 1：Durability First）
- AC-06: Pipeline 执行过程中发射 `PIPELINE_RUN_UPDATED` 事件（Constitution 原则 2：Everything is an Event）
- AC-07: Pipeline 完成（SUCCEEDED / FAILED / CANCELLED）时更新 Child Task 和 Work 到终态
- AC-08: 进程重启后，可从最后一个成功的 `PipelineCheckpoint` 恢复未完成的 Pipeline run（确定性恢复）

---

### FR-065-06: HITL 集成（审批与用户输入）

**描述**：Pipeline 节点触发 WAITING_APPROVAL 或 WAITING_INPUT 时，利用现有 Task 治理状态和渠道审批机制。

**验收标准**：

- AC-01: Pipeline 中 `gate` 类型节点触发 `WAITING_APPROVAL` 时，Child Task 状态同步更新为 `WAITING_APPROVAL`
- AC-02: Pipeline 中节点触发 `WAITING_INPUT` 时，Child Task 状态同步更新为 `WAITING_INPUT`，并携带 `input_request` 描述所需输入字段
- AC-03: 用户通过 `graph_pipeline(action="resume", run_id=..., approved=true)` 批准后，Pipeline 从暂停节点继续执行
- AC-04: 用户通过 `graph_pipeline(action="resume", run_id=..., input_data={...})` 提供输入后，Pipeline 从暂停节点继续执行
- AC-05: 用户通过 Web UI 或 Telegram 渠道的审批按钮操作时，效果等同于调用 resume（复用现有渠道审批基础设施）
- AC-06: 用户拒绝审批（`approved=false`）时，Pipeline 状态转为 CANCELLED，Child Task 状态同步转为 CANCELLED
- AC-07: WAITING_APPROVAL / WAITING_INPUT 状态下的 Pipeline 不消耗 LLM token（Constitution 原则 7：User-in-Control）

---

### FR-065-07: System Prompt 注入

**描述**：将已注册 Pipeline 列表注入 Butler / Worker / Subagent 的 system prompt，使 LLM 了解可用的确定性流程编排能力以及 Pipeline 与 Subagent 的区别。

**验收标准**：

- AC-01: Worker / Subagent system prompt 中包含 `## Available Pipelines` 段落，列出所有已注册 Pipeline 的 id、description、trigger_hint
- AC-02: 注入内容包含 Pipeline 与 Subagent 的语义区分指引：
  - 不确定任务（需探索、推理、多轮交互）-> 用 Subagent
  - 确定性流程（已知步骤序列、需审批门禁、需 checkpoint）-> 用 Pipeline
- AC-03: 注入格式简洁，单个 Pipeline 摘要不超过 3 行（id + 一句话描述 + 触发提示），总体 token 占用可控
- AC-04: Pipeline 列表为空时不注入该段落（避免空状态噪音）
- AC-05: Butler system prompt 同样注入 Pipeline 列表（含 trigger_hint），用于支持 `DELEGATE_GRAPH` 决策

---

### FR-065-08: 事件与可观测

**描述**：Pipeline 执行全程可追踪，复用已有事件基础设施，确保符合 Constitution 原则 2（Everything is an Event）和原则 8（Observability is a Feature）。

**验收标准**：

- AC-01: Pipeline 启动时发射 `PIPELINE_RUN_UPDATED` 事件（status=RUNNING），payload 包含 `pipeline_id`、`run_id`、`task_id`
- AC-02: 每个节点完成后发射 `PIPELINE_CHECKPOINT_SAVED` 事件，payload 包含 `checkpoint_id`、`node_id`、`status`
- AC-03: Pipeline 状态变更（RUNNING -> WAITING_APPROVAL / WAITING_INPUT / PAUSED / SUCCEEDED / FAILED / CANCELLED）时发射 `PIPELINE_RUN_UPDATED` 事件
- AC-04: 所有 Pipeline 事件携带 `task_id`、`run_id`、`pipeline_id`、`current_node_id`，支持按 Pipeline 维度聚合查询
- AC-05: Pipeline 事件通过 SSE 推送到 Web UI（复用现有 SSE 事件流基础设施）
- AC-06: Pipeline 执行耗时、节点成功/失败计数可从 Event Store 聚合查询

---

### FR-065-09: Pipeline 管理 API

**描述**：提供 REST API 供前端和外部系统查询、管理 Pipeline 定义和运行实例。

**验收标准**：

- AC-01: `GET /api/pipelines` 返回所有已注册 Pipeline 列表（id、description、version、tags、source_level）
- AC-02: `GET /api/pipelines/{pipeline_id}` 返回单个 Pipeline 详情（含完整节点拓扑、input/output schema、trigger_hint）
- AC-03: `GET /api/pipeline-runs` 返回 Pipeline run 列表，支持按 `task_id`、`pipeline_id`、`status` 筛选
- AC-04: `GET /api/pipeline-runs/{run_id}` 返回单个 run 详情（状态、当前节点、checkpoint 历史、事件时间线）
- AC-05: `POST /api/pipelines/refresh` 触发 PipelineRegistry 重新扫描文件系统并返回更新后的列表
- AC-06: API 响应格式与现有 `/api/skills` 风格一致（JSON，统一 error 结构）

---

## 非功能需求

### NFR-065-01: 并发安全

- 同一 Pipeline 允许多个 run 并发执行，各 run 之间状态隔离（通过独立的 `run_id`、`SkillPipelineRun`、`PipelineCheckpoint` 保证）
- 系统级并发 Pipeline run 数量上限可配置（默认 10），超限时 `graph_pipeline(action="start")` 返回明确拒绝信息

### NFR-065-02: 性能

- `graph_pipeline(action="list")` 响应时间 < 100ms（从内存缓存读取）
- Pipeline 启动到第一个节点开始执行的延迟 < 500ms
- Checkpoint 写入不阻塞节点执行主路径（异步持久化，但保证 at-least-once 落盘）

### NFR-065-03: 降级

- PipelineRegistry 扫描失败时，`graph_pipeline(action="list")` 返回空列表 + 警告日志，不影响其他工具使用（Constitution 原则 6：Degrade Gracefully）
- SkillPipelineEngine 不可用时，`graph_pipeline(action="start")` 返回明确错误信息，不影响 Worker 的 Free Loop 执行
- PIPELINE.md 解析失败的单个 Pipeline 不影响其余 Pipeline 的可用性

### NFR-065-04: 安全

- Pipeline 中 `gate` 类型节点的 side-effect 操作必须经过 Policy Engine 门禁（Constitution 原则 4：Side-effect Must be Two-Phase）
- Pipeline 定义中 `handler_id` 引用的工具必须在 ToolBroker 已注册且通过权限检查
- PIPELINE.md 中不得包含 secrets（Constitution 原则 5：Least Privilege by Default）

---

## 约束与假设

### 约束

1. **基于 SkillPipelineEngine 构建**：不引入新的 Pipeline 执行引擎。`GraphRuntimeBackend`（pydantic_graph 包装层）保留但不扩展，后续可用于需要 pydantic_graph 原生类型系统的高级场景
2. **Pipeline 定义是静态的**：LLM 不能动态创建图结构，只能选择预定义的 Pipeline（参考 Constitution V.4 / NG4：不在 v0.x 阶段把所有工作流都图化）
3. **节点 handler 必须预注册**：PIPELINE.md 中引用的 `handler_id` 必须在 SkillPipelineEngine 的 handler 注册表中存在
4. **复用 Feature 062 Loop Guard**：Pipeline 整体超时和节点重试上限复用 Adaptive Loop Guard 机制

### 假设

1. Feature 057（Skill 系统重写）已交付，SKILL.md 文件系统驱动模式和 SkillDiscovery 可作为 PIPELINE.md / PipelineRegistry 的参考模板
2. Feature 064（Subagent）已交付，CapabilityPack 工具注册和 Child Task 创建模式可复用
3. SkillPipelineEngine 的 `start_run` / `resume_run` / `cancel_run` API 可直接对外暴露，无隐式内部依赖阻止外部调用
4. 内置通用 handler（如 `terminal.exec`、`approval_gate`）已存在或可快速实现

---

## 依赖关系

| 依赖 | 类型 | 说明 |
|------|------|------|
| Feature 057 (Skill 系统) | 设计参考 | PIPELINE.md 格式参照 SKILL.md；PipelineRegistry 参照 SkillDiscovery |
| Feature 064 (Subagent) | 实现参考 | graph_pipeline 工具注册模式参照 SubagentLifecycleManager |
| Feature 062 (Loop Guard) | 运行时依赖 | Pipeline 超时 / 资源限制复用 Adaptive Loop Guard |
| SkillPipelineEngine | 核心依赖 | Pipeline 执行引擎（start_run / resume_run / cancel_run） |
| DelegationPlane | 核心依赖 | Child Task + Work 创建 |
| ToolBroker | 核心依赖 | 工具注册与权限检查 |
| Policy Engine | 核心依赖 | Gate 节点审批门禁 |

---

## 术语表

| 术语 | 定义 |
|------|------|
| Pipeline | 确定性流程编排（DAG），由预定义节点和边组成，节点可调用工具、LLM 或审批门禁 |
| PIPELINE.md | Pipeline 定义文件，采用 YAML frontmatter + Markdown body 格式 |
| PipelineRegistry | Pipeline 注册表，从三级文件系统目录发现和缓存 Pipeline 定义 |
| PipelineManifest | Pipeline 元数据摘要（id、description、schema 等），供 LLM 工具和 REST API 消费 |
| Pipeline Run | 一次 Pipeline 执行实例，有独立的 run_id、状态和 checkpoint 链 |
| Graph-as-Tool | 本 Feature 的核心模式——将 Pipeline 能力封装为 LLM tool call |
| handler_id | Pipeline 节点处理器标识，路由到具体的 `PipelineNodeHandler` 实现 |
| Gate 节点 | Pipeline 中的审批门禁节点，触发 WAITING_APPROVAL 暂停等待人工决策 |
| trigger_hint | Pipeline 定义中的自然语言描述，告诉 LLM 何时应该选择该 Pipeline |

---

## 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| Pipeline handler_id 引用不存在的 handler | 中 | PipelineRegistry 注册时静态验证 handler_id 存在性；运行时 handler 缺失时 Pipeline 转 FAILED + 发射错误事件 |
| 并发 Pipeline run 导致资源竞争 | 中 | 并发上限配置 + 复用 Feature 062 Loop Guard 的资源预算 |
| Butler DELEGATE_GRAPH 误判 | 低 | trigger_hint 引导精准匹配 + Pipeline 不匹配时自动 fallback 到 Worker 路径 |
| PIPELINE.md 格式演进导致向后不兼容 | 低 | frontmatter 中包含 version 字段；解析器按 version 分支处理 |
| Pipeline 节点长时间阻塞导致资源泄漏 | 中 | 节点级 timeout_seconds + Pipeline 整体超时（Loop Guard） |
| SkillPipelineEngine 外部调用暴露内部不变量 | 中 | 对 Engine 的公开 API 增加参数校验层，确保外部调用与内部调用使用相同的前置条件检查 |
