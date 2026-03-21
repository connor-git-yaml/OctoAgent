# OctoAgent 当前代码架构总览

这组文档不是产品蓝图，也不是面向终端用户的使用指南，而是基于当前代码扫描结果整理的“实现级导览”。

如果你刚接手 OctoAgent，推荐阅读顺序是：

1. 先看本文，建立“当前真实模块分类”和“blueprint 目标态”的区别
2. 再看 [当前技术文档地图](./current-doc-map.md)，知道仓库里现有文档分别该怎么用
3. 然后按模块进入二级文档：
   - [Core / Persistence](./modules/01-core-domain-and-persistence.md)
   - [Gateway Runtime / Control Plane](./modules/02-gateway-runtime-and-control-plane.md)
   - [Provider / LLM Stack](./modules/03-provider-and-llm-stack.md)
   - [Tooling / Policy / Skill Runtime](./modules/04-tooling-policy-skill-runtime.md)
   - [Memory / Protocol](./modules/05-memory-and-protocol.md)
   - [Frontend Workbench](./modules/06-frontend-workbench.md)

## 1. 这份文档集解决什么问题

OctoAgent 当前同时存在两套“结构视角”：

- 一套来自 [blueprint](../blueprint.md)，描述的是目标态架构和长期分层
- 一套来自当前真实代码，描述的是现在已经落地、正在运行、正在被维护的模块骨架

如果不把这两层拆开，新维护者会误以为：

- `apps/kernel` 已经是独立模块
- `workers/*` 已经是独立源码树
- `packages/plugins` / `packages/observability` 已经像 blueprint 那样拆好

但当前真实实现并不是这样。当前主线实现的很多职责仍然收敛在 `apps/gateway/services/*`，并通过 `packages/core / provider / tooling / skills / policy / memory / protocol` 与 `frontend` 共同构成系统主链。

## 2. 当前真实实现的大模块分类

当前代码的主要模块面如下。

| 模块 | 当前目录 | 主要职责 | 代表入口 |
| --- | --- | --- | --- |
| Gateway Runtime | `octoagent/apps/gateway` | FastAPI 应用装配、任务运行时、控制面、Orchestrator、Delegation、Worker runtime | [`main.py`](../../octoagent/apps/gateway/src/octoagent/gateway/main.py) |
| Core Domain / Persistence | `octoagent/packages/core` | 领域模型、SQLite store、事务辅助、行为工作区、控制面共享文档模型 | [`store/__init__.py`](../../octoagent/packages/core/src/octoagent/core/store/__init__.py) |
| Provider / LLM Stack | `octoagent/packages/provider` | `octoagent.yaml` schema、alias 注册、LiteLLM Client、CLI/setup/doctor/runtime activation | [`config_schema.py`](../../octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py) |
| Tooling / Policy / Skills | `octoagent/packages/tooling` `packages/policy` `packages/skills` | tool contract、broker、审批与策略、Skill loop、deterministic pipeline | [`broker.py`](../../octoagent/packages/tooling/src/octoagent/tooling/broker.py) |
| Memory / Protocol | `octoagent/packages/memory` `packages/protocol` | 记忆治理、检索/维护、A2A-Lite 适配与状态映射 | [`service.py`](../../octoagent/packages/memory/src/octoagent/memory/service.py) |
| Frontend Workbench | `octoagent/frontend` | Web 工作台、控制面 snapshot 消费、设置/Agents/Memory/Work 等 UI surface | [`WorkbenchLayout.tsx`](../../octoagent/frontend/src/components/shell/WorkbenchLayout.tsx) |
| Docs / Specs / Skills | `docs/` `.specify/` `skills/` | 蓝图、里程碑拆解、feature 制品、运行提示词与 Skill | [当前技术文档地图](./current-doc-map.md) |

## 3. Blueprint 目标态和当前实现的关系

### 3.1 Blueprint 里的目标态

[blueprint](../blueprint.md) 中的目标分层大致是：

```text
Channels -> Gateway -> Kernel -> Workers -> Tools / Skills / Provider / Memory
```

并进一步拆成：

- `apps/gateway`
- `apps/kernel`
- `workers/*`
- `packages/core`
- `packages/protocol`
- `packages/plugins`
- `packages/tooling`
- `packages/memory`
- `packages/provider`
- `packages/observability`
- `frontend`

### 3.2 当前真实实现

当前代码已经实现了很多 blueprint 里的“逻辑角色”，但未必已经物理拆成对应目录。

最典型的差异是：

- `Kernel` 相关逻辑当前仍大量收敛在 `apps/gateway/services/orchestrator.py`、`task_runner.py`、`control_plane.py`
- `Workers` 相关运行时、执行后端、A2A 会话和 delegation plane 也仍主要在 `apps/gateway/services/*`
- `plugins` / `observability` 还没有作为独立 workspace package 拆出

因此，阅读当前代码时可以把 blueprint 当成“设计目标和边界说明”，但不要把它当成当前目录真相。当前目录真相应以本组文档和源码扫描为准。

## 4. 当前实现的主控制流

可以把当前系统先理解成一条围绕 `Task` 运转的 durable 主链：

```text
Web / Telegram 输入
  -> Gateway 路由
  -> TaskService 创建 Task + 初始事件
  -> TaskRunner 持久化调度
  -> OrchestratorService 决策
  -> DelegationPlane / WorkerRuntime / LLMService
  -> 事件 / Artifact / Checkpoint / Work 写回 SQLite + 文件存储
  -> Frontend 通过 Control Plane Snapshot + SSE 消费状态
```

这条主链有四个当前必须认清的中枢：

1. **`TaskService`**  
   任务创建、上下文构建、记忆召回、上下文压缩、LLM 调用、artifact 写入。

2. **`TaskRunner`**  
   durable 调度层，负责 queued/running 恢复、启动执行、挂起/取消、执行监控。

3. **`OrchestratorService` + `DelegationPlaneService` + `WorkerRuntime`**  
   路由决策、A2A/dispatch、work 生命周期、pipeline 和执行后端。

4. **`ControlPlaneService`**  
   当前 Web 工作台看到的大多数“控制面资源”和“控制面动作”都由它统一提供。

## 5. 当前实现的主数据面

### 5.1 SQLite + 文件存储仍是中心

当前主数据面主要由 `packages/core` 的 SQLite store 负责：

- `tasks`
- `events`
- `artifacts`
- `task_jobs`
- `checkpoints`
- `works`
- 以及 project / agent context / A2A 等附属表

文件类 artifact 则落到实例目录中的 artifact 存储位置。

### 5.2 `octoagent.yaml` 已经是配置事实源

当前 Provider、alias、runtime、memory、front-door、channels 等主配置已经收敛到 `octoagent.yaml`。  
`litellm-config.yaml` 是衍生文件，不是应该被人手工维护的主配置。

这一块的专题细节可继续看 [LLM Provider 配置到调用架构专题](../llm-provider-config-architecture.md)。

## 6. 当前模块阅读地图

### 6.1 如果你想理解 durable runtime

先读：

- [Gateway Runtime / Control Plane](./modules/02-gateway-runtime-and-control-plane.md)
- [Core / Persistence](./modules/01-core-domain-and-persistence.md)

### 6.2 如果你想理解模型与配置

先读：

- [Provider / LLM Stack](./modules/03-provider-and-llm-stack.md)
- [LLM Provider 配置专题](../llm-provider-config-architecture.md)

### 6.3 如果你想理解工具、审批、Skill

先读：

- [Tooling / Policy / Skill Runtime](./modules/04-tooling-policy-skill-runtime.md)

### 6.4 如果你想理解记忆与 A2A

先读：

- [Memory / Protocol](./modules/05-memory-and-protocol.md)

### 6.5 如果你想理解 Web 工作台

先读：

- [Frontend Workbench](./modules/06-frontend-workbench.md)

## 7. 当前文档集和现有 canonical 文档的关系

这组文档的定位是：

- 它解释 **当前真实代码是怎么组织的**
- 它不会替代 [blueprint](../blueprint.md) 的产品设计与目标架构角色
- 它不会替代 [octoagent/README.md](../../octoagent/README.md) 的用户上手说明
- 它不会替代专题深挖文档，例如 [LLM Provider 配置专题](../llm-provider-config-architecture.md)

如果你不确定某件事应该看哪份文档，继续看 [当前技术文档地图](./current-doc-map.md)。
