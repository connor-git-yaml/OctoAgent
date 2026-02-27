# OctoAgent（内部代号：ATM — Advanced Token Monster）

## 项目概述

**OctoAgent** 是一个个人智能操作系统（Personal AI OS），目标是构建一套可长期运行、可观测、可恢复、可审批的 Agent 系统。

- **Owner**: Connor Lu
- **阶段**: v0.1（MVP 实现）
- **蓝图文档**: `OctoAgent_Blueprint.md`（工程蓝图，所有设计决策的权威来源）

## 核心架构（三层 + 外层 Loop）

```
Channels (Telegram/Web) → OctoGateway → OctoKernel → Workers → LiteLLM Proxy
```

- **外层 Orchestrator Loop**：自由自治层，目标理解、记忆检索、模式选择（Free/Graph）
- **Agent Graph**：关键流程控制层（DAG/FSM + checkpoint）
- **Pydantic Skills**：强类型执行层（Input/Output contract）
- **LiteLLM Proxy**：模型网关/治理层（alias 路由 + fallback + 成本统计）

## 技术栈

- **语言**: Python 3.12+
- **包管理**: uv
- **Web/API**: FastAPI + Uvicorn + SSE
- **数据库**: SQLite WAL（MVP），Postgres（v0.2+ 可选）
- **Agent 框架**: Pydantic + Pydantic AI
- **模型网关**: LiteLLM Proxy
- **执行隔离**: Docker
- **可观测**: OpenTelemetry + Prometheus + 结构化日志
- **调度**: APScheduler（MVP）
- **渠道**: Telegram (aiogram) + Web

## 目标 Repo 结构

```
octoagent/
  pyproject.toml / uv.lock
  apps/
    gateway/          # OctoGateway（渠道适配 + 消息标准化 + SSE 转发）
    kernel/           # OctoKernel（Orchestrator + Graph + Skill + Policy + Memory）
    workers/          # 执行者（ops/research/dev）
  packages/
    core/             # Domain Models + Event Store + Artifact Store
    protocol/         # A2A-Lite envelope + NormalizedMessage
    plugins/          # 插件加载器 + Manifest + 能力图
    tooling/          # 工具 schema 反射 + Tool Broker
    memory/           # SoR/Fragments/Vault + 写入仲裁
    provider/         # LiteLLM client wrapper + 成本模型
    observability/    # OTel + 日志 + Metrics
  plugins/
    channels/         # telegram/ web/ wechat_import/
    tools/            # filesystem/ docker/ ssh/ web/
  data/               # sqlite/ artifacts/ vault/（.gitignore）
```

## Constitution（不可违反的硬规则）

1. **Durability First** — 任何长任务必须落盘，进程重启后任务状态不消失
2. **Everything is an Event** — 模型调用、工具调用、状态迁移都必须生成事件记录
3. **Tools are Contracts** — 工具 schema 必须与代码签名一致（单一事实源）
4. **Side-effect Must be Two-Phase** — 不可逆操作必须 Plan → Gate → Execute
5. **Least Privilege by Default** — secrets 按 project/scope 分区，不进 LLM 上下文
6. **Degrade Gracefully** — 任一插件/依赖不可用时，系统不得整体不可用
7. **User-in-Control** — 高风险动作必须可审批，任务必须可取消
8. **Observability is a Feature** — 每个任务必须可查看状态、步骤、消耗、失败原因

## 里程碑

- **M0（基础底座）**: Task/Event/Artifact + SSE 事件流 + 最小 Web UI
- **M1（最小智能闭环）**: LiteLLM + Pydantic Skill + Tool Contract + Policy Engine
- **M2（多渠道多 Worker）**: Telegram + Worker + A2A-Lite + JobRunner + Memory
- **M3（增强）**: Chat Import + Vault + ToolIndex + Graph Engine

## 开发规范

### 语言与风格
- 所有对话、注释、commit message、文档使用**中文**
- 代码标识符（变量名、函数名、类型名）使用**英文**
- 英文技术术语保持原文（API、SSE、Docker、Pydantic 等）

### Spec-Driven 开发
- 使用 Spec Driver 工作流：constitution → spec → implement → verify
- 每个模块实现前先写 spec，spec 通过 review 后再编码
- Blueprint (`OctoAgent_Blueprint.md`) 是所有 spec 的上游依据

### 代码规范
- 类型注解：所有公共函数必须有完整类型注解
- 数据模型：使用 Pydantic BaseModel
- 异步优先：IO 操作使用 async/await
- 测试：每个模块需有 unit test，关键路径需有 integration test

### Git 规范
- Remote: `origin` → `https://github.com/connor-git-yaml/OctoAgent.git`
- 主分支: **`master`**（不是 main）
- 分支策略：`master`（稳定）+ `dev`（开发）+ `feat/*`（功能分支）
- Commit 格式：`<type>(<scope>): <description>`
  - type: feat / fix / refactor / docs / test / chore
  - scope: core / gateway / kernel / worker / memory / tooling / ...

## 参考资料位置

- `_references/opensource/` — Agent Zero / AgentStudio / OpenClaw / Pydantic AI 源码
- `_references/openclaw-snapshot/` — 用户（Connor）的 OpenClaw 使用快照（sessions/cron/memory 等）
- `_research/` — 调研阶段文档（四轮 AI 对话报告 + 架构设计稿）

## 关键设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 结构化存储 | SQLite WAL | Task/Event/Artifact 元信息，单用户足够 |
| 语义检索 | 向量数据库（ChromaDB MVP） | 记忆/工具索引/知识库直接上 embedding，跳过 FTS 中间态 |
| 编排 | 自研轻量 Graph/FSM | 避免引入 Temporal 的运维成本，先实现 checkpoint+resume |
| 模型网关 | LiteLLM Proxy | 统一 alias 路由，业务代码不写死厂商型号 |
| 执行隔离 | Docker 默认 | Agent Zero 验证过的方案，安全边界清晰 |
| 事件溯源 | 最小 Event Sourcing | append-only events + tasks projection，先保证崩溃不丢 |
| 门禁策略 | Safe by default + Policy Profile 可配 | 平衡安全与智能化，减少低风险场景的用户打扰 |
| A2A 兼容 | 内部超集 + A2AStateMapper 双向映射 | 内部保留 WAITING_APPROVAL/PAUSED 等治理状态，对外映射为标准 A2A TaskState |
| Task 终态 | SUCCEEDED/FAILED/CANCELLED/REJECTED | REJECTED 区分策略拒绝与运行时失败 |
