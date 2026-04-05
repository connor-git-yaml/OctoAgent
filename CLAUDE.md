<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. -->
<!-- Source: .agent-config/templates/claude.header.md + .agent-config/shared.md -->
<!-- Regenerate: ./repo-scripts/sync-agent-config.sh -->

# OctoAgent（内部代号：ATM - Advanced Token Monster）

## 项目概述

**OctoAgent** 是一个个人智能操作系统（Personal AI OS），目标是构建一套可长期运行、可观测、可恢复、可审批的 Agent 系统。

- **Owner**: Connor Lu
- **阶段**: v0.1（MVP 实现）
- **蓝图文档**: `docs/blueprint.md`（工程蓝图，所有设计决策的权威来源）

## 核心架构（全层 Free Loop + Skill Pipeline）

```
Channels (Telegram/Web) -> OctoGateway -> OctoKernel -> Workers -> LiteLLM Proxy
```

- **Orchestrator**：路由与监督层，永远 Free Loop（目标理解、Worker 派发、全局监督）
- **Workers**：自治智能体层，永远 Free Loop（自主决策，按需调用 Skill Pipeline）
- **Skill Pipeline / Graph**：Subagent 的确定性编排工具（DAG/FSM + checkpoint），非独立执行模式
- **Pydantic Skills**：强类型执行层（Input/Output contract）
- **LiteLLM Proxy**：模型网关/治理层（alias 路由 + fallback + 成本统计）

## 当前主线实现状态（截至 2026-03）

- **Behavior / Context**：主线已完成四层 `BehaviorWorkspaceScope`（`system_shared / agent_private / project_shared / project_agent`）、`project_path_manifest` 与 `storage_boundary_hints`；行为文件、事实记忆、敏感信息、项目工作材料已明确分层，`MEMORY.md` 不再作为事实仓库。
- **Memory**：主线已完成 Feature 066，支持 `memory.browse`、增强筛选的 `memory.search`、`SOLUTION` 记忆、`ARCHIVED` 状态、SoR `propose -> validate -> commit` 编辑，以及敏感分区额外授权。
- **Graph / Pipeline**：主线已完成 Feature 069，支持 `PIPELINE.md`、三级 `PipelineRegistry`、`GraphPipelineTool`、`ButlerDecisionMode.DELEGATE_GRAPH` 和对应 REST API。
- **Worker 路由**：最近主线已修复前端轮询重复建 session，以及非 `singleton:*` 自定义 Worker profile 被 Butler single-loop 拦截的问题；自定义 Worker 应走 Delegation Plane，保留自己的 persona/context。
- **运行验证**：用户体验与真实运行验证默认针对 `~/.octoagent` 托管实例，而不是源码目录直接启动。

## 技术栈

- **语言**: Python 3.12+
- **包管理**: uv
- **Web/API**: FastAPI + Uvicorn + SSE
- **数据库**: SQLite WAL
- **Agent 框架**: Pydantic + Pydantic AI
- **模型网关**: LiteLLM Proxy
- **执行隔离**: Docker
- **可观测**: Logfire（OTel 原生）+ structlog + Event Store 查询
- **调度**: APScheduler（MVP）
- **渠道**: Telegram (aiogram) + Web

## 目标 Repo 结构

```
octoagent/
  pyproject.toml / uv.lock
  apps/
    gateway/          # OctoGateway（渠道适配 + 消息标准化 + SSE 转发）
    kernel/           # OctoKernel（Orchestrator + Policy + Memory）
    workers/          # 自治智能体（ops/research/dev，Free Loop + Skill Pipeline）
  packages/
    core/             # Domain Models + Event Store + Artifact Store
    protocol/         # A2A-Lite envelope + NormalizedMessage
    plugins/          # 插件加载器 + Manifest + 能力图
    tooling/          # 工具 schema 反射 + Tool Broker
    memory/           # SoR/Fragments/Vault + 写入仲裁
    provider/         # LiteLLM client wrapper + 成本模型
    observability/    # Logfire + structlog + Event Store metrics
  plugins/
    channels/         # telegram/ web/ wechat_import/
    tools/            # filesystem/ docker/ ssh/ web/
  frontend/             # React + Vite Web UI（M0 起步）
  data/               # sqlite/ artifacts/ vault/（.gitignore）
```

## Constitution（不可违反的硬规则）

1. **Durability First** - 任何长任务必须落盘，进程重启后任务状态不消失
2. **Everything is an Event** - 模型调用、工具调用、状态迁移都必须生成事件记录
3. **Tools are Contracts** - 工具 schema 必须与代码签名一致（单一事实源）
4. **Side-effect Must be Two-Phase** - 不可逆操作必须 Plan -> Gate -> Execute
5. **Least Privilege by Default** - secrets 按 project/scope 分区，不进 LLM 上下文
6. **Degrade Gracefully** - 任一插件/依赖不可用时，系统不得整体不可用
7. **User-in-Control** - 高风险动作必须可审批，任务必须可取消
8. **Observability is a Feature** - 每个任务必须可查看状态、步骤、消耗、失败原因
9. **Agent Autonomy** - 禁止用硬编码关键词/规则替代 LLM 决策（如天气检测、请求分类、位置提取）；系统层只负责提供完整工具集和上下文，由 LLM 自主选择工具和决策路径
10. **Policy-Driven Access** - 工具访问控制统一走 `check_permission()`（PermissionPreset × SideEffectLevel 矩阵 + ApprovalManager 审批），工具层不得自行做路径/权限拦截

## 里程碑

- **M0（基础底座）**: Task/Event/Artifact + SSE 事件流 + 最小 Web UI
- **M1（最小智能闭环）**: LiteLLM + Pydantic Skill + Tool Contract + Policy Engine
- **M2（多渠道多 Worker）**: Telegram + Worker + A2A-Lite + JobRunner + Memory
- **M3（增强）**: Chat Import + Vault + ToolIndex + Skill Pipeline Engine

## 开发规范

### 语言与风格

- 所有对话、注释、commit message、文档使用**中文**
- 代码标识符（变量名、函数名、类型名）使用**英文**
- 英文技术术语保持原文（API、SSE、Docker、Pydantic 等）

### Spec-Driven 开发

- 使用 Spec Driver 工作流：constitution -> spec -> implement -> verify
- 每个模块实现前先写 spec，spec 通过 review 后再编码
- Blueprint (`docs/blueprint.md`) 是所有 spec 的上游依据
- Spec Driver 运行时策略以 `driver-config.yaml` 为准（或 `.specify/driver-config.yaml`）
- 正式 Feature 制品根目录统一为 `.specify/features/<feature-id>-<feature-slug>/`，包括 `spec.md`、`plan.md`、`tasks.md`、`research/`、`contracts/`、`verification/`
- 不再新增、保留或依赖顶层 `specs/` 目录；发现历史遗留引用时，应直接改到 `.specify/features/...` 的正式路径
- 新增或迁移 Feature 文档时，必须先检查 canonical 位置是否正确；不要额外创建顶层占位目录，也不要用整目录软链代替规范位置

### Blueprint 同步规则

- `docs/blueprint.md` 是架构设计的权威文档。**任何影响架构的代码改动完成后，必须同步更新 Blueprint 中的相关描述**
- 需要同步的改动包括：删除/新增模块或类、权限/安全模型变更、工具系统变更、数据模型字段增删、目录结构变更、里程碑完成状态变更
- 更新时确保：术语一致（代码中删了的概念不能在 Blueprint 中继续描述为"当前状态"）、示例代码/YAML 与实际字段匹配、已完成的问题标记为 ✅
- 不需要同步的改动：纯 bug fix、测试修复、日志调整、注释修改等不影响架构描述的变更

### 代码规范

- 类型注解：所有公共函数必须有完整类型注解
- 数据模型：使用 Pydantic BaseModel
- 异步优先：IO 操作使用 async/await
- 测试：每个模块需有 unit test，关键路径需有 integration test
- 架构整洁优先：任何改动都要检查是否引入坏味道（职责漂移、临时分支、重复状态、命名失真、兼容层叠加、概念泄漏）；如果会把代码越改越脏，应停下来先调整结构，再继续实现
- 开发和重构时，不要把“最小改动”当作默认目标；应先从长期演进视角判断更合理的整体架构、模块边界与数据流，再在可控范围内向正确方向收敛
- 避免为了短期交付继续堆叠临时 patch、兼容层或例外分支；如果现有结构已经明显不合理，优先选择能降低后续复杂度的长期方案
- 当发现某次需求会把原有清晰边界打穿时，优先回到职责分层、数据流和对象模型上修正根因，而不是在调用链末端补救
- 去掉功能时直接删除所有相关代码（函数、类型、CSS、JSX），不要注释掉或保留"待后续启用"的死代码；需要时从 git 历史恢复

### Web UI / UX 规范

- Web 端页面默认面向**普通非技术用户**设计，优先降低理解成本和操作门槛，而不是优先展示系统内部实现细节
- 页面首要回答用户最关心的问题：**当前发生了什么、这对我有什么影响、我下一步该做什么**
- 主界面避免直接暴露 debug / 开发 / 运维术语与原始技术字段，例如：内部 ID、scope/backend 原始标识、调试状态码、索引细节、flush/replay、原始异常栈等
- 如确实需要保留技术信息，必须放到 **Advanced / 管理台 / 诊断区 / 折叠区**，不得占据普通用户主路径和首屏核心信息
- 状态、报错、提醒文案必须使用用户语言：先解释影响，再给出明确动作；不要只抛出底层状态名或实现名词
- 配置引导应坚持**最小必要原则**：优先告诉用户“最少需要配置什么”以及“去哪里配置”，不要把部署拓扑、底层组件关系或实现细节当作必读前置知识
- 任何新增或重构的 Web 页面都应检查是否存在“只有开发者能看懂”的区块；若存在，默认应改写、降级展示或迁移到高级入口

### Git 规范

- Remote: `origin` -> `https://github.com/connor-git-yaml/OctoAgent.git`
- 主分支: **`master`**（不是 main）
- 分支策略：`master`（稳定）+ `dev`（开发）+ `feat/*`（功能分支）
- Commit 格式：`<type>(<scope>): <description>`
  - type: feat / fix / refactor / docs / test / chore
  - scope: core / gateway / kernel / worker / memory / tooling / ...
- **禁止 force push**：绝对不允许使用 `--force`、`--force-with-lease` 或任何形式的强制推送。已推送的 commit 不得 amend/rebase 后再推送。遇到推送冲突时，必须 `git fetch` + `git rebase` 处理冲突，review 冲突解决结果后再正常推送。违反此规则曾导致线上 commit 丢失。

## 关键设计决策记录

| 决策          | 选择                                  | 理由                                                                                                                           |
| ------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 结构化存储    | SQLite WAL                            | Task/Event/Artifact 元信息，单用户足够                                                                                         |
| 语义检索      | 向量数据库（LanceDB）                 | 嵌入式零运维 + 版本化存储 + 混合检索 + Python 3.12 兼容 + async 原生                                                           |
| 编排模型      | 全层 Free Loop + Skill Pipeline       | Orchestrator/Workers 永远 Free Loop 保持灵活性；Skill Pipeline（pydantic-graph）作为 Worker 的确定性编排工具按需调用           |
| 模型网关      | LiteLLM Proxy                         | 统一 alias 路由，业务代码不写死厂商型号                                                                                        |
| 执行隔离      | Docker 默认                           | Agent Zero 验证过的方案，安全边界清晰                                                                                          |
| 事件溯源      | 最小 Event Sourcing                   | append-only events + tasks projection，先保证崩溃不丢                                                                          |
| 门禁策略      | Safe by default + PermissionPreset 可配 | PermissionPreset（MINIMAL/NORMAL/FULL）× SideEffectLevel 矩阵 + ApprovalManager 审批                                          |
| A2A 兼容      | 内部超集 + A2AStateMapper 双向映射    | 内部保留 WAITING_APPROVAL/PAUSED 等治理状态，对外映射为标准 A2A TaskState                                                      |
| Task 终态     | SUCCEEDED/FAILED/CANCELLED/REJECTED   | REJECTED 区分策略拒绝与运行时失败                                                                                              |
| Artifact 模型 | A2A parts 超集 + version/hash/size    | 多 Part 结构对齐 A2A，保留版本化与完整性校验，支持流式追加                                                                     |
| Telegram      | aiogram                               | 原生 async + 内置 FSM（审批流）+ 与 FastAPI 共享 event loop                                                                    |
| Web UI        | React + Vite                          | 从 M0 起一步到位，避免迁移债务；SSE 原生 EventSource 对接 Gateway                                                              |
| 可观测        | Logfire + structlog + Event Store     | Pydantic 团队出品，自动 instrument Pydantic AI/FastAPI；structlog 结构化日志；Event Store 已有 metrics 数据源，无需 Prometheus |

## 设计文档索引

| 文档 | 路径 | 说明 |
|------|------|------|
| 工程蓝图 | `docs/blueprint.md` | 所有设计决策的权威来源 |
| OctoAgent 架构分析 | `docs/design/octoagent-architecture.md` | 主线代码深度逆向分析 + 四系统横向对比 |
| Claude Code 架构分析 | `docs/design/claude-code-architecture.md` | Anthropic 官方 CLI 源码分析 |
| OpenClaw 架构分析 | `docs/design/openclaw-architecture.md` | 多渠道 AI 服务架构分析 |
| Agent Zero 架构分析 | `docs/design/agent-zero-architecture.md` | 自主 Agent 框架架构分析 |
| LLM Provider 配置 | `docs/design/llm-provider-config-architecture.md` | LLM Provider 配置架构设计 |

## 项目级 Skills（Codex + Claude 通用）

- `milestone-blueprint-split-sync`  
  - 路径：`skills/milestone-blueprint-split-sync/SKILL.md`  
  - 用途：把“blueprint 需求提取 -> 里程碑 Feature 并行拆解 -> 调研复核 -> 回写 blueprint -> 一致性校验”固化为可复用流程。  
  - 触发示例：`使用 [$milestone-blueprint-split-sync](skills/milestone-blueprint-split-sync/SKILL.md) 从 M2/M3 开始拆解并回写 blueprint。`

