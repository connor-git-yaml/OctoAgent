# Core / Persistence 模块

本模块对应当前代码里的 [`octoagent/packages/core`](../../../octoagent/packages/core)，它不是“辅助工具包”，而是整个系统的共享对象模型和 durable 持久化骨架。

如果只用一句话概括：**Gateway、Memory、Policy、Protocol、Frontend 所看到的大多数核心对象，最终都要回到 core 包定义的数据模型、行为工作区和 SQLite store。**

## 1. 模块职责

当前 `packages/core` 主要承担四类职责：

1. **领域模型**
   - `Task` / `Event` / `Artifact` / `Checkpoint` / `Work`
   - `OrchestratorRequest` / `DispatchEnvelope` / `WorkerResult`
   - 控制面文档模型，例如 `ControlPlaneDocument`、`SessionProjectionDocument`

2. **持久化存储**
   - SQLite store 的建表、读写、聚合
   - 跨 task/event/checkpoint 的事务边界

3. **行为工作区**
   - 行为文件目录骨架
   - 行为文件 overlay 解析
   - bootstrap / onboarding 生命周期

4. **共享枚举和协议中立对象**
   - 让 gateway / provider / policy / memory / frontend 共享同一套对象语义

## 2. 当前目录里最重要的几块

| 目录 / 文件 | 作用 |
| --- | --- |
| [`store/__init__.py`](../../../octoagent/packages/core/src/octoagent/core/store/__init__.py) | StoreGroup 工厂与共享连接聚合 |
| [`store/transaction.py`](../../../octoagent/packages/core/src/octoagent/core/store/transaction.py) | task/event/checkpoint 原子事务边界 |
| [`behavior_workspace.py`](../../../octoagent/packages/core/src/octoagent/core/behavior_workspace.py) | 行为工作区、bootstrap、文件解析与访问守卫 |
| `models/task.py` `models/event.py` `models/artifact.py` | durable task runtime 的最基础对象 |
| `models/orchestrator.py` | orchestrator / worker / dispatch 相关共享对象 |
| `models/control_plane.py` | 前后端共享的控制面文档结构 |
| `models/pipeline.py` | pipeline definition / run / checkpoint / replay 的共享模型 |

## 3. 核心类与核心函数

### 3.1 `StoreGroup`

位置：[`store/__init__.py`](../../../octoagent/packages/core/src/octoagent/core/store/__init__.py)

`StoreGroup` 是当前 SQLite 持久化层的根聚合器。它做的事情很简单，但非常关键：

- 共享一条 `aiosqlite.Connection`
- 把 task/event/artifact/checkpoint/work/project/a2a 等 store 绑定到同一连接上
- 让上层服务可以在同一事务上下文里操作多个 store

这就是为什么 `TaskService` 能在一次事务里同时写：

- task projection
- event
- checkpoint

而不会把这些动作拆散到多条连接里。

#### `create_store_group()`

核心逻辑：

1. 确保 artifacts 目录存在
2. 确保数据库目录存在
3. 打开 SQLite 连接
4. 调用 `init_db()` 初始化 schema
5. 返回绑定好所有 store 的 `StoreGroup`

这一步把“实例目录 -> 可运行的数据平面”真正落地。

### 3.2 事务辅助函数

位置：[`store/transaction.py`](../../../octoagent/packages/core/src/octoagent/core/store/transaction.py)

这是 core 模块最关键的一层之一。它把 Constitution 里的 `Durability First` 落成了几个明确的原子边界。

#### `create_task_with_initial_events()`

实现逻辑：

1. 先创建 task projection
2. 顺序写入初始事件列表，通常至少是 `TASK_CREATED` 和 `USER_MESSAGE`
3. 在同一事务里更新 `tasks.pointers.latest_event_id`
4. 成功则 commit，异常则 rollback

这个函数的价值在于：**任务本体和初始事件不会分裂落盘**。  
如果失败，不会出现“task 创建了但第一条用户消息没写进去”这类脏状态。

#### `append_event_and_update_task()`

实现逻辑：

1. 先写 event
2. 如果传了 `new_status`，顺便更新 task 的 status、updated_at 和 latest_event 指针
3. 可选使用 `expected_status` 做 CAS 风格并发保护
4. 成功 commit，失败 rollback

这个函数是 task status 投影与事件流保持一致的关键边界。

#### `append_event_only()`

实现逻辑：

1. 写 event
2. 只更新 `latest_event_id` 和 `updated_at`
3. 不变更 task status

适合写 `USER_MESSAGE`、辅助事件、非状态迁移动作。

#### `append_event_and_save_checkpoint()`

实现逻辑：

1. 写 event
2. 写 checkpoint snapshot
3. 在同一事务里同步更新 `latest_event_id` 和 `latest_checkpoint_id`

这让 pipeline / resume 相关状态具备可恢复性。

### 3.3 `behavior_workspace.py`

位置：[`behavior_workspace.py`](../../../octoagent/packages/core/src/octoagent/core/behavior_workspace.py)

这个文件已经远不只是“读几个 markdown 文件”。它当前承担了行为系统的四层工作区、bootstrap 生命周期和文件安全访问边界。

#### `BehaviorLoadProfile` 与 `get_profile_allowlist()`

当前定义了三类加载级别：

- `FULL`
- `WORKER`
- `MINIMAL`

`get_profile_allowlist()` 做的事情是：  
根据角色返回可加载的行为文件白名单，控制不同角色能看到哪些行为材料。

这为后续的 Butler / Worker / Subagent 差异化上下文加载提供了基础。

#### `load_onboarding_state()` / `save_onboarding_state()` / `mark_onboarding_completed()`

这组函数围绕 `.onboarding-state.json` 工作。

核心逻辑：

- `load_onboarding_state()` 读取状态文件，并在 `BOOTSTRAP.md` 被删除时自动推断 onboarding 已完成
- `save_onboarding_state()` 用临时文件 + rename 方式原子写入
- `mark_onboarding_completed()` 用统一入口写完成时间戳

这里的重点不是文件读写，而是：**bootstrap 生命周期已经被持久化建模，而不是临时前端状态。**

#### `ensure_filesystem_skeleton()`

职责：

- 确保 behavior 目录结构存在
- 为 system / agent / project / project_agent 等 scope 准备文件系统骨架

这是行为系统可用的最低前提。

#### `materialize_agent_behavior_files()` / `materialize_project_behavior_files()`

职责：

- 根据模板与 overlay 规则把行为文件实际落到磁盘
- 区分 system、agent、project、project-agent 四层来源
- 让运行时不是直接读模板，而是读 materialized 后的工作区

#### `resolve_behavior_workspace()`

这是当前行为系统最重要的解析入口。

实现逻辑可以概括成：

1. 根据 project root、agent profile、scope 等信息收集候选文件
2. 按 overlay 顺序叠加不同来源
3. 过滤 load profile 白名单
4. 产出 `BehaviorWorkspace`，里面包含文件来源、可见性、可编辑性、最终内容等信息

这让行为文件系统已经具备“逻辑层 -> 物理层 -> 最终工作区”的完整解析过程。

#### `validate_behavior_file_path()` / `read_behavior_file_content()`

这两者负责行为文件访问边界。

- `validate_behavior_file_path()` 做路径合法性和边界校验
- `read_behavior_file_content()` 在通过校验后读取内容

它们的存在说明 behavior 文件不是任意开放路径，而是受工作区边界控制的资源。

## 4. 核心共享模型

### 4.1 Task / Event / Artifact

`Task`、`Event`、`Artifact` 是当前 durable runtime 的底座。

- `Task` 保存状态投影和 pointers
- `Event` 保存 append-only 事件历史
- `Artifact` 保存大文本、文件、结构化输出

当前设计的关键点不在于模型字段多少，而在于：

- task 是 projection
- event 是历史
- artifact 是结果载体

这三者是分层而不是混在一起的。

### 4.2 Orchestrator 共享对象

在 [`models/orchestrator.py`](../../../octoagent/packages/core/src/octoagent/core/models/orchestrator.py) 里，最关键的是：

- `RuntimeControlContext`
- `OrchestratorRequest`
- `DispatchEnvelope`
- `WorkerResult`
- `WorkerSession`

这些对象让 gateway 的 orchestrator、delegation、worker runtime、protocol 适配可以共享统一语义，而不是在各层自己组 dict。

### 4.3 控制面文档模型

在 [`models/control_plane.py`](../../../octoagent/packages/core/src/octoagent/core/models/control_plane.py) 中，控制面资源文档被做成显式对象：

- `ConfigSchemaDocument`
- `ProjectSelectorDocument`
- `SessionProjectionDocument`
- `AgentProfilesDocument`
- `SetupGovernanceDocument`

这使得 frontend 不是直接读后端内部结构，而是消费一套控制面资源文档。

### 4.4 Pipeline 共享模型

[`models/pipeline.py`](../../../octoagent/packages/core/src/octoagent/core/models/pipeline.py) 提供了：

- `SkillPipelineDefinition`
- `SkillPipelineNode`
- `SkillPipelineRun`
- `PipelineCheckpoint`
- `PipelineReplayFrame`

它们把 deterministic pipeline 从“运行逻辑”提升成了“可持久化、可回放的对象模型”。

## 5. 这个模块与其他模块怎么连接

- `apps/gateway` 通过 `StoreGroup`、共享模型和行为工作区直接依赖它
- `packages/policy`、`packages/tooling`、`packages/protocol` 都依赖 core 里的模型和枚举
- `frontend` 虽然不直接 import Python 代码，但其 REST contract 对应的资源文档模型来自这里

可以把 `packages/core` 理解为：**当前整个系统的持久化骨架 + 共享语言层。**

## 6. 维护时最该先看的点

如果你要改 durable task runtime，优先看：

1. `store/transaction.py`
2. `models/task.py` / `models/event.py`
3. `models/orchestrator.py`

如果你要改行为和 bootstrap，优先看：

1. `behavior_workspace.py`
2. 相关控制面行为文件 action

如果你要改控制面资源 contract，优先看：

1. `models/control_plane.py`
2. Gateway 的 `ControlPlaneService`
