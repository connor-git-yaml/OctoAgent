# Butler / Worker 全 Agent Runtime 架构重构实施蓝图

> **文档类型**: Architecture Refactor Execution Plan  
> **状态**: Draft for Execution  
> **日期**: 2026-03-13  
> **上游事实源**: `docs/blueprint.md`、`docs/m3-feature-split.md`、`docs/m4-feature-split.md`、Feature `033 / 038 / 039 / 041`  
> **目标**: 把当前“能路由、能回答、但运行语义仍偏单主 Agent”的系统，升级为真正的 `Butler + Worker` 多 Agent 运行体系

---

## 1. 为什么需要这份实施蓝图

`blueprint` 和 `feature split` 已经完成了架构纠偏，但它们主要回答的是“目标应该是什么”，还没有把“怎么按正确顺序把它做出来”拆成可执行波次。

当前仓库的真实状态是：

- `Butler` 主聊天链已经具备 durable context continuity
- project-scoped recall 与 freshness routing 已可用
- `worker.review / apply`、`tool_profile`、runtime truth 已经存在
- 但系统仍未形成真正的 `ButlerSession -> A2AConversation -> WorkerSession` 主链
- `Worker` 仍未拥有与 `Butler` 对等的 `Session / Memory / Recall` 运行栈

因此，本次不是继续“补一点路由、补一点 bootstrap、补一点 UI”，而是按一套完整的实施顺序，把运行语义从“单主 Agent + worker adapter”升级成“多 Agent OS”。

---

## 2. 最终目标架构

### 2.1 运行对象

| 对象 | 作用 | 关键要求 |
|---|---|---|
| `Project` | 根隔离单位 | 提供 instructions、shared memory、secrets、knowledge、routing、默认 profiles |
| `AgentProfile` | Agent 静态模板 | persona、instruction overlays、tool/policy/auth/context budget |
| `WorkerProfile` | Worker 静态模板 | 角色、画像、能力、工具集合、权限集合、bootstrap |
| `AgentRuntime` | Agent 长期运行体 | Butler/Worker 的运行身份、绑定的 memory namespace、effective config |
| `ButlerSession` | 用户 ↔ Butler 主会话 | 当前阶段唯一 user-facing session |
| `WorkerSession` | Butler ↔ Worker 内部会话 | Worker 的 recency、tool/evidence summary、compaction、private continuity |
| `DirectWorkerSession` | 用户 ↔ Worker 直接会话 | 后续扩展能力，当前不默认开放 |
| `Work` | 执行/委派单元 | 任务图、budget、state、artifacts、children |
| `A2AConversation` | Butler ↔ Worker 消息往返 | `TASK / UPDATE / RESULT / ERROR` durable message chain |
| `MemoryNamespace` | 记忆命名空间 | `Project shared / Butler private / Worker private` |
| `RecallFrame` | 单轮召回快照 | recency、memory hits、artifact evidence、provenance |

### 2.2 主链

当前阶段必须收敛为：

1. `User -> ButlerSession`
2. `Butler` 基于自己的 `AgentRuntime + ButlerSession + ButlerMemory + ProjectMemory + RecallFrame` 判断是否需要委派
3. `Butler -> A2AConversation(TASK) -> WorkerSession`
4. `Worker` 基于自己的 `AgentRuntime + WorkerSession + WorkerMemory + ProjectMemory + RecallFrame` 执行
5. `Worker -> A2AConversation(UPDATE/RESULT) -> Butler`
6. `Butler` 综合结果后对用户发言

注意：

- 当前阶段 `Butler` 必须仍是唯一对用户负责的发言人
- `Worker` 默认不得直接读取完整用户主聊天历史
- `Worker` 默认只能消费 Butler 转交的上下文胶囊、被授权的 project shared memory 和自己的 private memory

---

## 3. 设计来源（Agent Zero / OpenClaw）

这次重构不是照抄，但确实要吸收两个仓库里已经被验证过的结构性经验。

### 3.1 来自 Agent Zero 的吸收点

参考：

- `_references/opensource/agent-zero/docs/guides/projects.md`
- `_references/opensource/agent-zero/docs/developer/extensions.md`

吸收点：

1. `Project` 必须是 instructions / memory / secrets / knowledge / git workspace / subagent settings 的根隔离单位。
2. agent-specific prompt/tool/extension override 必须建立在正式 profile/runtime 上，而不是散落在文案约定里。
3. 每个 agent 都应当有自己的行为层插点和上下文装配能力，而不是所有差异都堆在一个 root prompt 里。

不照抄的点：

1. 不走“文件夹即唯一真相”的配置模式；OctoAgent 仍以 durable store + projection 为主。
2. 不把 extension hook 当成运行时真相源；OctoAgent 仍坚持 event / task / artifact / control plane 为事实源。

### 3.2 来自 OpenClaw 的吸收点

参考：

- `_references/opensource/openclaw/src/config/sessions/session-key.ts`
- `_references/opensource/openclaw/src/channels/session-meta.ts`
- `_references/opensource/openclaw/src/config/sessions/types.ts`
- `_references/opensource/openclaw/src/shared/usage-types.ts`

吸收点：

1. `agentId + sessionKey` 是一级建模维度，session 不能只按 thread 或 channel 组织。
2. session meta、usage、compaction、origin、runtime options 应当是正式可查询对象，而不是进程内附属状态。
3. `Session` 需要承载 usage / latency / tool usage / compaction / runtime identity 等运行真相，而不是只存聊天文本。

不照抄的点：

1. 当前阶段不把 direct worker chat 作为默认产品面。
2. OctoAgent 仍坚持 `Project / Work / A2A / Memory Governance` 的 durable 主链，不回退到“session 文件就是全部真相”。

---

## 4. 硬约束

以下约束在整个重构过程中不得退让：

1. `Butler` 当前仍是唯一 user-facing speaker。
2. 每个 Agent 都必须拥有完整上下文栈：
   - persona
   - project context
   - session recency
   - memory namespace
   - recall frame
   - tool/policy/auth context
   - scratchpad
3. `WorkerSession` 不能退化为 loop/backoff/tool_profile 一类 runtime-only 结构。
4. A2A 必须是 message-native 主链，不再只是 envelope adapter。
5. `Memory` 与 `Recall` 必须继续分层：
   - Memory = 长期保留什么
   - Recall = 这次问题实际取回什么
6. 所有 migration 都必须允许系统在过渡期可运行；禁止一次性推翻所有旧对象再整体替换。

---

## 5. 实施策略总览

### 5.1 不新增平行产品面

这次重构继续复用当前 Feature 作为承载：

| Feature | 继续承担的重构责任 |
|---|---|
| `033` | `AgentRuntime / AgentSession / Context continuity` canonical contract |
| `038` | `MemoryNamespace / RecallFrame / Worker recall parity` |
| `039` | `A2AConversation / A2AMessage / WorkerSession / Butler-owned delegation chain` |
| `041` | freshness / latest / website query 的 Butler-owned end-to-end 主链 |
| `035 / 040` | UI 与 acceptance 收口，不负责替代运行语义 |

结论：

- 不另起平行 runtime feature 树
- 继续用 `033 / 038 / 039 / 041` 承接核心重构
- 但执行上按“波次”推进，而不是按历史 feature 完成口径推进

### 5.2 迁移原则

1. 先建新对象，再做双写，再切读，再删旧兼容层。
2. 所有阶段都必须保持 control plane 可解释。
3. 先完成 runtime 真相，再补 guided UI。
4. 先完成通用主链，再完成 freshness / research 这类高频案例。

---

## 6. 执行波次

## Wave 1：Agent Runtime Core

### 目标

把 `Butler` 和 `Worker` 升格为正式 `AgentRuntime`，并让 `Session` 真正绑定 `agent_id`，不再只绑定 `project/thread/work`。

### 范围

- 定义 `AgentRuntime`
- 定义 `AgentSession`
- 明确 `ButlerSession / WorkerSession / DirectWorkerSession`
- 补 `MemoryNamespace` / `RecallFrame` 主模型
- 更新 `ContextFrame` 与 `SessionContextState` 的关系

### 主要改动模块

- `packages/core`
  - models
  - stores
  - schema migration
- `apps/gateway`
  - `agent_context.py`
  - `task_service.py`
  - `control_plane.py`

### 退出门槛

1. `ButlerSession` 与 `WorkerSession` 成为一等持久对象。
2. 至少一条 Worker 路径不再复用 Butler session key。
3. control plane 能查询 `agent runtime -> session -> context/recall` 映射。

### 主要承载 Feature

- `033`
- `038`

---

## Wave 2：Message-Native A2A Plane

### 目标

把当前的 envelope roundtrip 升级为真实 `ButlerSession -> A2AConversation -> WorkerSession` 主链。

### 范围

- 定义 `A2AConversation`
- 定义 `A2AMessage`
- 支持 `TASK / UPDATE / RESULT / ERROR`
- 让 orchestrator 从“直调 worker adapter”切换到“先建 A2A conversation 再调度”
- 让 `WorkerSession` 和 `A2AConversation` 建立持久绑定

### 主要改动模块

- `packages/core`
  - A2A models / event payloads / stores
- `apps/gateway`
  - `orchestrator.py`
  - `delegation_plane.py`
  - `task_runner.py`
  - `control_plane.py`

### 退出门槛

1. 至少一条真实链路可以回放 `TASK -> RESULT`。
2. event chain 不再只看到 `WORKER_DISPATCHED`，而能看到 A2A message 真相。
3. 用户表面仍然只有 Butler 对外发言。

### 主要承载 Feature

- `039`

---

## Wave 3：Worker Private Memory & Recall Parity

### 目标

让 Worker 拥有自己的 private memory、private recall 和 continuity 主链，而不是复用 Butler 召回结果。

### 范围

- 定义 `Project shared / Butler private / Worker private` 三类 namespace
- recall contract 升级为 `namespace + agent + session` 感知
- Worker compaction / recall / memory flush 独立落盘
- 继续禁止 Worker 直接读取完整主会话

### 主要改动模块

- `packages/memory`
  - resolver
  - recall
  - provenance
- `apps/gateway`
  - `agent_context.py`
  - `task_service.py`
  - `worker runtime`

### 退出门槛

1. 至少一条 `Butler -> Worker` 链证明 Worker 使用自己的 namespace。
2. recall provenance 能区分“这是 Butler 的 recall”还是“这是 Worker 的 recall”。
3. MemU / indexing / retrieval audit 可以按 `namespace + agent + session` 查询。

### 主要承载 Feature

- `033`
- `038`

---

## Wave 4：Butler-Owned Freshness / Research 主链

### 目标

把天气、官网、最新资料、实时事实类问题从“路由正确”升级为“Butler 拥有问题并通过 A2A 委派给 Research Worker”。

### 范围

- freshness query -> Butler delegation policy
- Research Worker 使用独立 `WorkerSession + WorkerMemory + RecallFrame`
- `web.search / web.fetch / browser.*` 继续受治理，但执行主体变成 Worker
- Butler 汇总 Worker 结果后再对外答复

### 主要改动模块

- `apps/gateway`
  - `capability_pack.py`
  - `delegation_plane.py`
  - `orchestrator.py`
  - `llm_service.py`
- `frontend`
  - work / advanced 运行真相展示

### 退出门槛

1. 天气/官网/最新资料至少一条真实链路可回放 `Butler -> Research -> Butler`。
2. acceptance matrix 证明的不再只是 route/tool profile，而是完整 A2A conversation。
3. 系统不会再把“自己不直接上网”误说成“系统整体不会查实时信息”。

### 主要承载 Feature

- `041`
- `039`
- `038`

---

## Wave 5：Control Plane / Guided Surface / Acceptance 收口

### 目标

让新 runtime 真相真正可被 operator 和普通用户看到，并把 release gate 切到新的架构标准。

### 范围

- control plane 查询：
  - `AgentRuntime`
  - `AgentSession`
  - `A2AConversation`
  - `MemoryNamespace`
  - `RecallFrame`
- workbench / advanced 可视化：
  - Butler 当前委派给了谁
  - Worker 的 session / tool profile / degraded reason
  - recall provenance
- acceptance matrix 重写为新标准

### 主要改动模块

- `apps/gateway`
  - `control_plane.py`
- `frontend`
  - `Chat`
  - `Work`
  - `Advanced`

### 退出门槛

1. operator 可以直接查看一条完整 `Butler -> Worker` 会话。
2. `040` 的 release gate 不再替代 039/041 的运行语义验证。
3. 新的 acceptance 能证明“系统是多 Agent OS”，而不只是“系统能答对天气”。

### 主要承载 Feature

- `035`
- `040`
- `033 / 038 / 039 / 041` 的最终验收

---

## 7. 推荐执行顺序

推荐严格按以下顺序推进：

1. **Wave 1**
   先把 `AgentRuntime / AgentSession / MemoryNamespace / RecallFrame` 立起来。
2. **Wave 2**
   再把 A2A 变成一等主链。
3. **Wave 3**
   然后补 Worker 的 private memory / recall parity。
4. **Wave 4**
   再把 freshness / research 这类真实高频问题迁到新主链。
5. **Wave 5**
   最后再收 UI、control plane 和 release gate。

禁止倒序的原因：

- 没有 `WorkerSession`，就谈不上真正的 Worker continuity
- 没有 `A2AConversation`，就谈不上 Butler-owned delegation
- 没有 namespaced recall，就谈不上每个 Agent 是独立运行体

---

## 8. 与现有代码的接口映射

### 8.1 保留并演进

这些模块不应推翻重写，而应成为新架构的承载点：

- `apps/gateway/services/agent_context.py`
- `apps/gateway/services/orchestrator.py`
- `apps/gateway/services/delegation_plane.py`
- `apps/gateway/services/task_service.py`
- `apps/gateway/services/control_plane.py`
- `packages/memory/src/octoagent/memory/service.py`
- `packages/core/src/octoagent/core/models/*`

### 8.2 明确要降级的旧语义

以下语义必须在后续迁移中被明确降级为兼容层：

1. “Worker 只是 `worker_type + tool_profile + loop state`”
2. “A2A 只是 `DispatchEnvelope` 归一化”
3. “Worker 可以默认看到主聊天完整历史”
4. “freshness query route 命中就等于 runtime ready”

---

## 9. 风险控制

### 9.1 主要风险

1. **对象增多导致投影复杂度上升**
   - 应对：先建最小投影，只保证 runtime truth 和 audit 可读

2. **双写阶段出现 session / work / A2A 真相漂移**
   - 应对：每一波都必须补 integration test 和 projection truth test

3. **在 UI 还没准备好前，operator 无法理解新对象**
   - 应对：Wave 5 前至少保证 `Advanced` 能看到最小 runtime truth

4. **freshness 用例先跑通，但 Worker private recall 仍未隔离**
   - 应对：禁止跳过 Wave 3 直接宣称 041 fully passed

### 9.2 禁止的捷径

1. 不能把 `WorkerSession` 简化成一个 JSON metadata blob。
2. 不能把 `A2AConversation` 简化成“多记几条 event type”但没有正式对象。
3. 不能把 Worker private memory 伪装成 `project memory + tag filter`。
4. 不能用 UI 文案替代 runtime truth。

---

## 10. 完成定义（Definition of Done）

只有满足以下条件，才能宣称这轮架构重构完成：

1. `ButlerSession / WorkerSession / A2AConversation / MemoryNamespace / RecallFrame` 都是正式 durable 对象。
2. 至少一条真实 weather/latest/research query 能回放：
   - `User -> Butler`
   - `Butler -> A2AConversation(TASK) -> Research Worker`
   - `Research Worker -> A2AConversation(RESULT) -> Butler`
   - `Butler -> User`
3. `Worker` 使用自己的 session/private memory/recall，而不是复用 Butler 的上下文副产物。
4. control plane / workbench 可以直接解释“谁在做事、基于什么记忆、为什么能/不能回答”。
5. `033 / 038 / 039 / 041` 的 spec、tasks、verification、acceptance 全部以这套标准重新闭环。

---

## 11. 下一步建议

执行上建议从 **Wave 1 / Feature 033-038 联动** 开始：

1. 先冻结 `AgentRuntime / AgentSession / MemoryNamespace / RecallFrame` 的 schema
2. 明确 `ButlerSession / WorkerSession / DirectWorkerSession` 的最小字段
3. 设计 `A2AConversation / A2AMessage` 的 durable model
4. 再进入代码改造

如果继续往下做，实现层的第一张正式 spec，建议就是：

- **主题**：`Agent Runtime Core + Session Namespace Canonicalization`
- **承载 Feature**：`033 + 038`
- **目标**：先把每个 Agent 的运行体和私有上下文骨架立起来，再进入 A2A 主链改造
