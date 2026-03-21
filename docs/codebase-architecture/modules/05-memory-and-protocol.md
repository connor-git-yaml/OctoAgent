# Memory / Protocol 模块

这一层对应当前代码中的：

- [`octoagent/packages/memory`](../../../octoagent/packages/memory)
- [`octoagent/packages/protocol`](../../../octoagent/packages/protocol)

它们共同承担的是两个方向的“长期结构化能力”：

1. 系统如何记住、治理、检索长期信息
2. 系统如何把内部运行对象映射成 A2A-Lite 消息与状态

## 1. Memory 模块不是“向量搜索工具”

当前 `packages/memory` 远比“搜一下 embedding”复杂。  
它已经具备：

- proposal -> validate -> commit 写入治理
- SoR / Fragment / Vault 分层
- recall / rerank / hook / degraded fallback
- maintenance / replay / compaction flush
- vault 授权与审计

## 2. `MemoryService`: 记忆治理与检索中枢

位置：[`service.py`](../../../octoagent/packages/memory/src/octoagent/memory/service.py)

### 2.1 `propose_write()`

职责：

- 把一次写入意图先落成 `WriteProposal`
- 记录 scope、partition、action、evidence、confidence 等信息

这意味着记忆写入不是直接写 SoR，而是先进入治理队列。

### 2.2 `validate_proposal()`

实现逻辑：

1. 读取 proposal
2. 根据 action 检查当前 SoR 状态和版本约束
3. 校验证据引用
4. 产出 `ProposalValidation`
5. 将 proposal 标记为 validated 或 rejected

这里的重点是：**写入正确性先被显式验证，再进入 commit。**

### 2.3 `commit_memory()`

这是记忆写入治理的核心函数。

实现逻辑：

1. 确认 proposal 已通过验证
2. 构建 fragment
3. 根据 action 分别执行 add / update / delete / merge
4. 在需要时写 vault
5. 回写 committed proposal 元数据

它把“记忆是一条审计链”真正落成了结构。

### 2.4 `_commit_add()` / `_commit_update()` / `_commit_delete()`

职责：

- 对当前 SoR 做显式版本化变更
- 保留 fragment 历史

### 2.5 `search_memory()`

职责：

- 面向查询和 recall 做检索
- 在后端可用和退化场景间切换

### 2.6 `recall_memory()`

这是当前 memory read path 的核心。

它会：

1. 构建 recall search options
2. 拉取候选结果
3. 应用 recall hooks
4. 做 focus term、subject match、temporal decay、MMR dedup、rerank 等处理
5. 产出 recall hits 与 trace

这表明记忆召回不是单纯的“top-k 搜索”，而是正式的排序与治理过程。

### 2.7 `_apply_recall_hooks()` / `_rerank_recall_candidates()`

职责：

- 对 recall 过程施加额外控制与排序
- 支持后续 retrieval profile 和不同 recall mode 的演进

### 2.8 `run_memory_maintenance()`

职责：

- 处理 flush、replay 等维护命令
- 维护 backend 健康与回放同步

### 2.9 Vault 相关函数

例如：

- `create_vault_access_request()`
- `resolve_vault_access_request()`
- `record_vault_retrieval_audit()`

这说明敏感记忆并不是普通 SoR 的同一条访问路径，而是带授权与审计链的单独机制。

## 3. 当前 Memory 的架构现实

### 3.1 写入和检索都不是“旁路功能”

Memory 已经深度接入：

- `TaskService`
- 控制面 Memory Console
- retrieval platform
- vault 授权与 operator 行为

### 3.2 degraded fallback 是正式设计的一部分

`MemoryService` 内部维护 backend 状态、失败原因、回放积压和 fallback 行为。  
这说明记忆系统已经按“可退化但不整体不可用”的原则在设计。

## 4. Protocol 模块：内部模型与 A2A-Lite 之间的适配层

当前协议层对应 [`octoagent/packages/protocol`](../../../octoagent/packages/protocol)。

它的重点不是 transport，而是：

- 把 core 的对象变成 A2A-Lite 消息
- 把 A2A 消息还原成 dispatch/runtime 对象
- 保持状态与 artifact 的语义映射

## 5. `A2AStateMapper`

位置：[`mappers.py`](../../../octoagent/packages/protocol/src/octoagent/protocol/mappers.py)

### `to_a2a()`

职责：

- 把内部 `TaskStatus` 映射成标准 A2A 状态

例如：

- `CREATED` / `QUEUED` -> `SUBMITTED`
- `RUNNING` -> `WORKING`
- `WAITING_INPUT` / `WAITING_APPROVAL` -> `INPUT_REQUIRED`

### `from_a2a()`

职责：

- 把外部 A2A 状态映射回内部 `TaskStatus`

这里最重要的不是映射表本身，而是：  
**OctoAgent 当前保留了比标准 A2A 更细的治理状态，然后通过 mapper 对外兼容。**

## 6. `A2AArtifactMapper`

位置：同 [`mappers.py`](../../../octoagent/packages/protocol/src/octoagent/protocol/mappers.py)

### `to_a2a()`

职责：

- 将内部 `Artifact` 及其 parts 映射成 A2A artifact
- 保留 version/hash/size 等元数据

### `from_a2a()`

职责：

- 将外部 A2A artifact 还原成内部 artifact

### `_to_a2a_part()` / `_from_a2a_part()`

职责：

- 在 text/file/json/image 等 part 类型之间做双向适配

这说明当前 artifact 模型不是平面字符串，而是多 part 结构。

## 7. `adapters.py`: 消息级适配入口

位置：[`adapters.py`](../../../octoagent/packages/protocol/src/octoagent/protocol/adapters.py)

### 7.1 `build_task_message()`

职责：

- 从 `DispatchEnvelope` 构造 A2A TASK message
- 带上 trace、route reason、tool profile、model alias 等元信息

### 7.2 `dispatch_envelope_from_task_message()`

职责：

- 把收到的 A2A TASK message 还原成 `DispatchEnvelope`

这保证了 A2A 不只是日志结构，而是可以真实回到内部执行入口。

### 7.3 `build_result_message()`

职责：

- 从 `WorkerResult` 和 artifact 列表构造 RESULT message

### 7.4 `build_update_message()`

职责：

- 构造中间状态更新消息

### 7.5 `build_error_message()` / `build_cancel_message()` / `build_heartbeat_message()`

职责：

- 覆盖 worker 生命周期中常见的终态或中间态消息

这使 protocol 层成为 A2A durable conversation 的基础。

## 8. 这两个模块如何接到主链上

### 8.1 Memory 接到 Task Runtime

当前连接点主要在：

- `TaskService._build_memory_recall_plan()`
- `TaskService._record_delayed_recall_once()`
- 控制面 Memory Console

### 8.2 Protocol 接到 Orchestrator / Worker

当前连接点主要在：

- `OrchestratorService._prepare_a2a_dispatch()`
- `OrchestratorService._persist_a2a_terminal_message()`
- A2A conversation / message audit 相关持久化逻辑

## 9. 当前维护的关键判断

如果你要改 memory，不要只盯向量检索：

- 写入治理
- recall 排序
- vault 授权
- degraded fallback

如果你要改 protocol，不要只盯消息字段：

- state 映射
- artifact part 映射
- dispatch/runtime 对象的双向适配

当前这两层都已经是系统主链的一部分，而不是附加组件。
