<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. -->
<!-- Source: .agent-config/templates/agents.header.md + .agent-config/shared.md -->
<!-- Regenerate: ./repo-scripts/sync-agent-config.sh -->

# OctoAgent（内部代号：ATM - Advanced Token Monster）

## Codex 对齐说明

- 本文件由共享源生成，与 `CLAUDE.md` 保持同源。
- 请修改 `.agent-config/shared.md` 或模板文件，然后执行同步脚本。
- 如需本地私有补充，请创建 `AGENTS.local.md`（默认不纳入版本管理）。

## 项目概述

**OctoAgent** — 个人智能操作系统（Personal AI OS）：可长期运行、可观测、可恢复、可审批的 Agent 系统。

- **Owner**: Connor Lu | **阶段**: v0.1 MVP | **蓝图**: `docs/blueprint.md`

## 核心架构

```
Channels (Telegram/Web) -> OctoGateway -> OctoKernel -> Workers -> LiteLLM Proxy
```

- **Orchestrator**：路由与监督层，Free Loop（目标理解、Worker 派发、全局监督）
- **Workers**：自治智能体层，Free Loop（自主决策，按需调用 Skill Pipeline）
- **Skill Pipeline**：确定性编排工具（pydantic-graph DAG/FSM + checkpoint）
- **LiteLLM Proxy**：模型网关（alias 路由 + fallback + 成本统计）

## 技术栈

Python 3.12+ / uv / FastAPI+SSE / SQLite WAL / Pydantic AI / LiteLLM / Docker / Logfire+structlog / APScheduler / Telegram(aiogram)+Web(React+Vite)

## Constitution（不可违反）

1. **Durability First** — 长任务必须落盘，进程重启不丢状态
2. **Everything is an Event** — 模型/工具调用、状态迁移都生成事件
3. **Tools are Contracts** — 工具 schema 与代码签名一致（单一事实源）
4. **Side-effect Two-Phase** — 不可逆操作必须 Plan → Gate → Execute
5. **Least Privilege** — secrets 按 scope 分区，不进 LLM 上下文
6. **Degrade Gracefully** — 任一依赖不可用时系统不整体崩溃
7. **User-in-Control** — 高风险可审批，任务可取消
8. **Observability** — 每个任务可查状态、步骤、消耗、失败原因

## 里程碑

M0: Task/Event/Artifact+SSE+Web UI | M1: LLM+Skill+Policy | M2: Telegram+Worker+A2A+Memory | M3: ChatImport+Vault+Pipeline

## 开发规范

- **语言**：对话/注释/commit 用中文；代码标识符用英文；技术术语保持原文
- **Spec-Driven**：constitution → spec → implement → verify；Blueprint 是上游依据；Feature 制品在 `.specify/features/<id>-<slug>/`
- **代码**：公共函数完整类型注解 / Pydantic BaseModel / async 优先 / 模块需 unit test
- **架构整洁优先**：先从长期演进视角判断合理架构，不以"最小改动"为默认目标；不堆叠临时 patch 和兼容层；删除功能时直接删代码，不注释保留
- **Web UI**：面向非技术用户，不暴露内部术语（详见 `.agent-config/refs/ux-guidelines.md`）
- **Git**：主分支 `master`；Commit `<type>(<scope>): <desc>`（type: feat/fix/refactor/docs/test/chore）

## 按需参考

| 内容 | 路径 |
|------|------|
| Repo 目录结构 | `.agent-config/refs/repo-structure.md` |
| 关键设计决策 (ADR) | `.agent-config/refs/design-decisions.md` |
| Web UI/UX 详细规范 | `.agent-config/refs/ux-guidelines.md` |
| 工程蓝图（权威） | `docs/blueprint.md` |
| Spec Driver 配置 | `.specify/driver-config.yaml` |

