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
Channels (Telegram/Web) -> OctoGateway -> OctoKernel -> Workers -> ProviderRouter -> Provider HTTP
```

- **Orchestrator**：路由与监督层，永远 Free Loop（目标理解、Worker 派发、全局监督）
- **Workers**：自治智能体层，永远 Free Loop（自主决策，按需调用 Skill Pipeline）
- **Skill Pipeline / Graph**：Subagent 的确定性编排工具（DAG/FSM + checkpoint），非独立执行模式
- **Pydantic Skills**：强类型执行层（Input/Output contract）
- **ProviderRouter**：模型路由层（Feature 080/081 引入）—— alias 解析 + 凭证管理 + 直连 provider HTTP（OpenAI Chat / Responses / Anthropic Messages 三种 transport）；不再走 LiteLLM Proxy 子进程

## 技术栈

- **语言**: Python 3.12+
- **包管理**: uv
- **Web/API**: FastAPI + Uvicorn + SSE
- **数据库**: SQLite WAL
- **Agent 框架**: Pydantic + Pydantic AI
- **模型网关**: ProviderRouter 直连（Feature 080/081 替代 LiteLLM Proxy）
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
- **M1（最小智能闭环）** ✅: LiteLLM Proxy（已退役 Feature 081） + Pydantic Skill + Tool Contract + Policy
- **M2（多渠道多 Worker）** ✅: Telegram + Worker + A2A-Lite + JobRunner + Memory
- **M3（增强）** ✅: Chat Import + Vault + ToolIndex + Skill Pipeline Engine
- **M4（引导式工作台）** ✅: 30 Feature 全部完成（071b-D + 063-P3 推迟到 M5）
- **M5（文件工作台）** ⏳: 语音/多模态/Companion/通知中心

### 后续修复（M5 阶段）

- **Feature 081（LiteLLM 完全退役）** ✅: Provider 直连替代 LiteLLM Proxy；migrate-080 双对象迁移
- **Feature 082（Bootstrap & Profile Integrity）** ⚠️→F084 退役: 修复"用户首次引导从未真实跑过"
  根本性漏洞——OwnerProfile 默认值清理 + 状态机加严 + 完成路径接入 +
  USER.md 动态生成 + 迁移命令 + 多 root 收敛。
  **F084 Phase 4 整体退役**了 BootstrapSession 状态机 + UserMdRenderer +
  bootstrap_orchestrator + bootstrap_commands CLI（详见 F084 节）。
- **Feature 083（测试并发加速）** ✅（务实版本）: 修 thread shutdown hang（aiosqlite + asyncio
  executor）+ 修 fixture `os.environ` 污染 + `attach_input` 测试 race 加严等待。
  进程退出从 30+ 分钟 hang → ~20s（关键修复）。
  xdist 提速作为 opt-in（`pytest -n auto`，5.5x 提速但 task_runner 状态机测试有 race
  ~20% 失败率，治本超 F083 scope）。
  详见 `docs/codebase-architecture/testing-concurrency.md`
- **Feature 084（Context + Harness 全栈重构）** ✅（仿 Hermes Agent 模式）:
  替代 F082 的根本方案——
  - **Harness 层**：中央 ToolRegistry（数据驱动 entrypoints）+ ToolsetResolver +
    ThreatScanner（17+ pattern + invisible Unicode）+ SnapshotStore（冻结快照 +
    Live State 二分，保护 prefix cache）+ ApprovalGate（session allowlist + SSE）+
    DelegationManager（max_depth=2 / max_concurrent=3）
  - **Context 层**：USER.md 是 SoT，OwnerProfile 退化为派生只读视图；
    `user_profile.update/read/observe` 三工具 + Memory Candidates API（promote/
    discard/bulk_discard with atomic claim + skipped_ids）+ Web UI 红点 badge
  - **WriteResult 通用回显契约**：18+ 写工具 return type 强制 WriteResult 子类，
    注册期 fail-fast；保留 task_id / memory_id / run_id 等关联键不压扁
  - **退役**：BootstrapSession / BootstrapOrchestrator / UserMdRenderer /
    bootstrap_integrity / bootstrap_commands CLI（净删 ~2400 行 dead code）
  - **重装路径**：清 ~/.octoagent/data + behavior + octo update 重启
    （bootstrap 完成由 USER.md 实质填充判定，不依赖任何旧表 / 状态机）
  详见 `docs/codebase-architecture/harness-and-context.md`
- **Feature 087（Agent e2e Live Test Suite）** ✅: 替换旧 `test_acceptance_scenarios.py`
  5 域循环为 13 能力域 e2e_live 套件——
  - **OctoHarness 抽离**：`gateway/harness/octo_harness.py` 暴露 4 个 DI 钩子
    （`credential_store` / `secret_store` / `transport_factory` / `clock`）；
    内置 120s ProviderRouter timeout + 30s SIGALRM 单测 watchdog
  - **13 能力域**：smoke 5（#1 工具调用基础 / #2 USER.md 全链路 / #3 冻结快照 /
    #11 ThreatScanner block / #12 ApprovalGate SSE）+ full 8（Memory promote /
    Perplexity MCP / Skill / Graph Pipeline / delegate_task / max_depth /
    A2A / Routine cron）；smoke=集成层 + DI stub，full 中 4 域直调主路径绕开
    LLM 不确定性（GATE_P3_DEVIATION）
  - **Hermetic 隔离**：双 autouse fixture 重置 5 类凭证 env / 4 个 OCTOAGENT_*
    路径 env / 5 项 module 单例（清单见 `MODULE_SINGLETONS.md`）
  - **pre-commit hook**：`make install-hooks`（worktree-aware）→ commit 自动跑
    `pytest -m e2e_smoke` 180s portable watchdog（python3 SIGTERM→SIGKILL，
    不依赖 macOS `gtimeout`）；`SKIP_E2E=1` 紧急 bypass
  - **`octo e2e` CLI**：4 模式（smoke / full / `<id>` / `--list` / `--loop=N`）
  - **不变量**：≥ 3026 passed / 0 regression（P5 实测 3006 passed + 1 rerun，
    单测 race 是 F083 已知工程债）；smoke 5x 循环 4s/iter；SC-7 跑前后
    USER.md / auth-profiles.json / mcp-servers/ sha256 完全一致
  详见 `docs/codebase-architecture/e2e-testing.md`
