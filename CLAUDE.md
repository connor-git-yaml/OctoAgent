<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. -->
<!-- Source: .agent-config/templates/claude.header.md + .agent-config/shared.md -->
<!-- Regenerate: ./repo-scripts/sync-agent-config.sh -->

# OctoAgent（内部代号：ATM - Advanced Token Monster）

## 项目概述

**OctoAgent** 是一个个人智能操作系统（Personal AI OS），目标是构建一套可长期运行、可观测、可恢复、可审批的 Agent 系统。

- **Owner**: Connor Lu
- **阶段**: v0.1（MVP 实现）
- **蓝图文档**: `docs/blueprint.md`（工程蓝图索引，详细内容在 `docs/blueprint/` 子目录）

## 核心架构（全层 Free Loop + Skill Pipeline）

```
Channels (Telegram/Web) -> OctoGateway -> OctoKernel -> Workers -> LiteLLM Proxy
```

- **Orchestrator**：路由与监督层，永远 Free Loop（目标理解、Worker 派发、全局监督）
- **Workers**：自治智能体层，永远 Free Loop（自主决策，按需调用 Skill Pipeline）
- **Skill Pipeline / Graph**：Subagent 的确定性编排工具（DAG/FSM + checkpoint），非独立执行模式
- **Pydantic Skills**：强类型执行层（Input/Output contract）
- **LiteLLM Proxy**：模型网关/治理层（alias 路由 + fallback + 成本统计）

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

## Constitution（不可违反的硬规则）

1. **Durability First** - 任何长任务必须落盘，进程重启后任务状态不消失
2. **Everything is an Event** - 模型调用、工具调用、状态迁移都必须生成事件记录
3. **Tools are Contracts** - 工具 schema 必须与代码签名一致（单一事实源）
4. **Side-effect Must be Two-Phase** - 不可逆操作必须 Plan -> Gate -> Execute
5. **Least Privilege by Default** - secrets 按 project/scope 分区，不进 LLM 上下文
6. **Degrade Gracefully** - 任一插件/依赖不可用时，系统不得整体不可用
7. **User-in-Control** - 高风险动作必须可审批，任务必须可取消
8. **Observability is a Feature** - 每个任务必须可查看状态、步骤、消耗、失败原因
9. **Agent Autonomy** - 禁止用硬编码关键词/规则替代 LLM 决策；系统层只负责提供完整工具集和上下文，由 LLM 自主选择工具和决策路径
10. **Policy-Driven Access** - 工具访问控制统一走权限决策函数，工具层不得自行做路径/权限拦截；所有权限判断收敛到单一入口

## 里程碑

- **M0（基础底座）** ✅: Task/Event/Artifact + SSE 事件流 + 最小 Web UI
- **M1（最小智能闭环）** ✅: LiteLLM + Pydantic Skill + Tool Contract + Policy
- **M2（多渠道多 Worker）** ✅: Telegram + Worker + A2A-Lite + JobRunner + Memory
- **M3（增强）** ✅: Chat Import + Vault + ToolIndex + Skill Pipeline Engine
- **M4（引导式工作台）** 🔄: 用户 Ready 全链路（Feature 056/070/071b 进行中）
- **M5（文件工作台）** ⏳: 语音/多模态/Companion/通知中心

## 协作行为准则

### 沟通与输出

- **如实汇报，拒绝废话**：输出客观事实和核心判断，不要前置铺垫、过度谦虚或结果润色。不知道就说不知道，不允许猜测或编造
- **严格执行要求范围**：只做明确要求的事，不自行添加未要求的"优化"、附加功能或代码美化。原因：画蛇添足导致不必要的 review 成本和意外副作用

### 代码审查与验证

- **审查时优先找问题**：Review 的首要目标不是确认代码能跑，而是寻找潜在漏洞、异常分支和边界情况
- **先看再改**：修改任何代码前必须先完整读取目标文件，不允许凭上下文记忆盲写代码。原因：上下文记忆可能过时或不完整

### 任务执行

- **执行可分发，决策需集中**：具体操作可拆分子任务交由 Sub-agents，但核心逻辑判断和决策必须收敛在主节点
- **单次授权原则**：用户的某次操作授权仅在当次有效，不得自行扩大为永久或全局授权。原因：权限蔓延曾导致非预期的危险操作

### Prompt 与规则编写

- **禁令优于指令**：用"Do not..."比"Please do..."更有效。每条禁止事项必须附带原因，防止在缺乏语境时被绕过
- **规则按需加载**：不要一次性给出所有工具说明或上下文，根据当前任务节点按需提供对应信息

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
- 正式 Feature 制品根目录统一为 `.specify/features/<feature-id>-<feature-slug>/`
- 不再新增、保留或依赖顶层 `specs/` 目录

### Blueprint 同步规则

- `docs/blueprint.md` 是架构设计的权威索引文档，详细内容在 `docs/blueprint/` 子目录。**任何影响架构的代码改动完成后，必须同步更新相关描述**
- 需要同步的改动：删除/新增模块或类、权限/安全模型变更、工具系统变更、数据模型字段增删、目录结构变更、里程碑完成状态变更
- 不需要同步的改动：纯 bug fix、测试修复、日志调整、注释修改等

### 代码规范

- 类型注解：所有公共函数必须有完整类型注解
- 数据模型：使用 Pydantic BaseModel
- 异步优先：IO 操作使用 async/await
- 测试：每个模块需有 unit test，关键路径需有 integration test
- 架构整洁优先：任何改动都要检查是否引入坏味道（职责漂移、临时分支、重复状态、命名失真、兼容层叠加、概念泄漏）
- 不要把"最小改动"当作默认目标；先从长期演进视角判断更合理的整体架构
- 去掉功能时直接删除所有相关代码，不要注释掉或保留死代码；需要时从 git 历史恢复

### Web UI / UX 规范

- Web 端页面默认面向**普通非技术用户**设计
- 主界面避免直接暴露 debug / 开发 / 运维术语与原始技术字段
- 技术信息放到 **Advanced / 管理台 / 诊断区 / 折叠区**

### Git 规范

- Remote: `origin` -> `https://github.com/connor-git-yaml/OctoAgent.git`
- 主分支: **`master`**（不是 main）
- Commit 格式：`<type>(<scope>): <description>`（type: feat/fix/refactor/docs/test/chore）
- **禁止 force push**：绝对不允许使用 `--force`、`--force-with-lease` 或任何形式的强制推送。已推送的 commit 不得 amend/rebase 后再推送。遇到推送冲突时，必须 `git fetch` + `git rebase` 解决后正常推送。违反此规则曾导致线上 commit 丢失。

## 设计文档索引

| 文档 | 路径 | 说明 |
|------|------|------|
| 工程蓝图（索引） | `docs/blueprint.md` | 所有设计决策的权威来源 |
| 蓝图子文档 | `docs/blueprint/` | 核心设计 / 部署运维 / 里程碑 / 审计等 |
| 代码架构导览 | `docs/codebase-architecture/` | 6 个模块实现级文档 |
| 竞品架构分析 | `docs/design/` | Claude Code / Agent Zero / OpenClaw 等 |
| 里程碑拆解 | `docs/milestone/` | M1-M4 Feature 拆解 |

