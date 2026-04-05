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
10. **Policy-Driven Access** - 工具访问控制统一走 `check_permission()`（PermissionPreset × SideEffectLevel 矩阵 + ApprovalManager 审批），工具层不得自行做路径/权限拦截

## 里程碑

- **M0（基础底座）**: Task/Event/Artifact + SSE 事件流 + 最小 Web UI
- **M1（最小智能闭环）**: LiteLLM + Pydantic Skill + Tool Contract + Policy Engine
- **M2（多渠道多 Worker）**: Telegram + Worker + A2A-Lite + JobRunner + Memory
- **M3（增强）**: Chat Import + Vault + ToolIndex + Skill Pipeline Engine
