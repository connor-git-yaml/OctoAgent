---
feature_id: "072"
title: "Document Current Codebase Architecture"
created: "2026-03-21"
updated: "2026-03-21"
research_mode: "codebase-scan"
---

# Research

## 1. 调研范围

本次调研不是做外部竞品或在线资料搜索，而是对当前仓库做 codebase scan，目标是回答四个问题：

1. 当前真实落地的大模块到底有哪些
2. Blueprint 目标态与当前物理目录结构分别是什么关系
3. 仓库里已有技术文档各自承担什么角色
4. 每个核心模块里最重要的类和函数分别是谁

扫描基准：

- 仓库：`OctoAgent`
- 基线：已同步最新 `origin/master`
- 分支：`072-document-codebase-architecture`
- 扫描日期：2026-03-21

## 2. 当前真实模块结构

依据 `octoagent/pyproject.toml` 工作区成员、`octoagent/apps/`、`octoagent/packages/` 和 `octoagent/frontend/` 的当前代码，当前主线实现的核心模块实际是：

1. `octoagent/apps/gateway`
2. `octoagent/packages/core`
3. `octoagent/packages/provider`
4. `octoagent/packages/tooling`
5. `octoagent/packages/skills`
6. `octoagent/packages/policy`
7. `octoagent/packages/memory`
8. `octoagent/packages/protocol`
9. `octoagent/frontend`

与 blueprint 中“理想分层”的差异：

- `apps/kernel` 还没有作为独立物理模块存在，当前大量 orchestrator / control plane / runtime 协调逻辑仍收敛在 `apps/gateway/services/*`
- `workers/*` 还没有拆成独立顶层源码目录，当前 worker runtime、delegation、session 和 profile 仍由 gateway 侧运行时管理
- `packages/plugins`、`packages/observability` 尚未作为独立工作区成员出现，相关责任目前分别散落在 gateway / tooling / provider 等模块中

因此，新文档必须明确“当前实现骨架”和“blueprint 目标结构”是两个不同层次。

## 3. 已扫描的关键代码入口

### 3.1 Core / Persistence

- `octoagent/packages/core/src/octoagent/core/store/__init__.py`
- `octoagent/packages/core/src/octoagent/core/store/transaction.py`
- `octoagent/packages/core/src/octoagent/core/behavior_workspace.py`
- `octoagent/packages/core/src/octoagent/core/models/task.py`
- `octoagent/packages/core/src/octoagent/core/models/control_plane.py`
- `octoagent/packages/core/src/octoagent/core/models/orchestrator.py`
- `octoagent/packages/core/src/octoagent/core/models/pipeline.py`

关键发现：

- `StoreGroup` 是所有 SQLite store 的共享连接聚合器
- `create_task_with_initial_events()`、`append_event_and_update_task()`、`append_event_and_save_checkpoint()` 是 task/event/projection/checkpoint 原子边界
- `behavior_workspace.py` 已经承担了行为文件骨架、overlay 解析、bootstrap 生命周期和文件访问守卫

### 3.2 Gateway Runtime

- `octoagent/apps/gateway/src/octoagent/gateway/main.py`
- `services/control_plane.py`
- `services/task_service.py`
- `services/task_runner.py`
- `services/orchestrator.py`
- `services/delegation_plane.py`
- `services/worker_runtime.py`
- `services/llm_service.py`

关键发现：

- `main.py` 是当前真实应用装配入口
- `TaskService` 负责 durable task 主链
- `TaskRunner` 负责任务执行、恢复、超时和调度
- `OrchestratorService` 负责 Butler / A2A / delegation / worker dispatch
- `DelegationPlaneService` 已经承担 work + pipeline + target selection 平面
- `ControlPlaneService` 是当前最大的控制面聚合器，实际统一提供 snapshot 生产、action 分发、setup governance、agent/worker profile、memory console、import workbench 等子域

### 3.3 Provider / LLM Stack

- `octoagent/packages/provider/src/octoagent/provider/alias.py`
- `octoagent/packages/provider/src/octoagent/provider/client.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_wizard.py`

关键发现：

- `octoagent.yaml` 已经是主配置事实源
- `AliasRegistry` 当前负责“显式配置 alias + legacy alias fallback”的运行时解析
- `LiteLLMClient` 是统一 Proxy 调用封装，内建 auth / connection / responses API / cost 跟踪处理
- provider DX 层同时承担 schema、CLI、wizard、doctor、runtime activation 的职责

### 3.4 Tooling / Policy / Skills

- `octoagent/packages/tooling/src/octoagent/tooling/broker.py`
- `octoagent/packages/tooling/src/octoagent/tooling/schema.py`
- `octoagent/packages/skills/src/octoagent/skills/runner.py`
- `octoagent/packages/skills/src/octoagent/skills/pipeline.py`
- `octoagent/packages/policy/src/octoagent/policy/policy_engine.py`
- `octoagent/packages/policy/src/octoagent/policy/pipeline.py`
- `octoagent/packages/policy/src/octoagent/policy/approval_manager.py`

关键发现：

- `ToolBroker` 是工具注册/发现/执行中介
- `reflect_tool_schema()` 保持“代码签名 -> schema”单一事实源
- `SkillRunner` 是结构化 LLM + tool loop 执行器
- `SkillPipelineEngine` 是确定性 pipeline / checkpoint / replay 执行器
- `PolicyEngine` 将 evaluator pipeline、approval manager 和 hook 组合成门面

### 3.5 Memory / Protocol

- `octoagent/packages/memory/src/octoagent/memory/service.py`
- `octoagent/packages/protocol/src/octoagent/protocol/adapters.py`
- `octoagent/packages/protocol/src/octoagent/protocol/mappers.py`

关键发现：

- `MemoryService` 不只是 recall，还负责 proposal、validation、commit、vault 授权、maintenance 和 degraded fallback
- protocol 层的重点不是 transport，而是当前 core 模型和 A2A-Lite contract 的双向适配

### 3.6 Frontend

- `octoagent/frontend/src/components/shell/WorkbenchLayout.tsx`
- `octoagent/frontend/src/platform/queries/useWorkbenchData.ts`
- `octoagent/frontend/src/domains/settings/SettingsPage.tsx`
- `octoagent/frontend/src/domains/settings/shared.tsx`
- `octoagent/frontend/src/domains/agents/agentManagementData.ts`

关键发现：

- Web UI 当前采用“全局 snapshot + domain projection + 页面级 action orchestration”模式
- `useWorkbenchData()` 是控制面 snapshot 的抓取与 action 刷新中枢
- `SettingsPage` 是配置治理主入口，不只是字段表单
- `agentManagementData.ts` 承担从 control-plane snapshot 到 Agent 管理视图模型的纯函数推导

## 4. 当前技术文档盘点

扫描到的主要现有文档：

- `README.md`
- `octoagent/README.md`
- `docs/blueprint.md`
- `docs/agent-runtime-refactor-plan.md`
- `docs/llm-provider-config-architecture.md`
- `docs/m1-feature-split.md`
- `docs/m1.5-feature-split.md`
- `docs/m2-feature-split.md`
- `docs/m3-feature-split.md`
- `docs/m4-feature-split.md`
- `.specify/features/*`

初步判断：

- `docs/blueprint.md` 是目标架构和产品设计的最高级工程蓝图
- `octoagent/README.md` 是当前产品与配置使用说明
- `README.md` 是仓库入口和对外项目说明
- `docs/llm-provider-config-architecture.md` 是专题深挖，不是总代码地图
- milestone split 文档是历史/规划拆解，不是当前实现导览
- `.specify/features/*` 是 feature 级研发制品，不适合作为总代码架构索引

因此本次文档结构决策是：

1. 新增一份 codebase architecture 总览
2. 新增一份 current doc map
3. 按模块拆分 6 份实现级文档
4. 在根 README Documentation Map 中加入入口

## 5. 文档结构决策

新增文档目录：

```text
docs/codebase-architecture/
├── README.md
├── current-doc-map.md
└── modules/
    ├── 01-core-domain-and-persistence.md
    ├── 02-gateway-runtime-and-control-plane.md
    ├── 03-provider-and-llm-stack.md
    ├── 04-tooling-policy-skill-runtime.md
    ├── 05-memory-and-protocol.md
    └── 06-frontend-workbench.md
```

设计原则：

- 总览先给坐标系，不直接陷入单文件细节
- 模块文档只围绕“当前真实实现”展开
- 每份模块文档都要解释核心类和关键函数
- 不重复复制专题文档，而是建立引用关系

## 6. 风险与取舍

### 6.1 需要主动避免的误导

- 不能把 blueprint 中的 `apps/kernel`、`workers/*` 写成当前已存在目录
- 不能把 `control_plane.py` 简化成“UI 接口文件”，它实际上是当前控制面的主要聚合器
- 不能把 `LLMService` 写成 provider SDK 包装器，它还承担工具搜索、skill/tool promotion、structured context bridge 等运行时职责

### 6.2 文档边界

本次 feature 不做：

- 代码行为重构
- 新测试实现
- 在线竞品研究
- 重新整理所有历史里程碑文档

本次 feature 只做：

- 基于当前代码扫描的架构文档化
- 文档入口梳理
- feature 制品补全
