# §11 冲突排查与合理性校验（Consistency & Conflict Checks）

> 本文件是 [blueprint.md](../blueprint.md) §11 的完整内容。

---

本节把"容易互相打架"的点提前检查并给出收敛方案。

### 11.1 事件溯源 vs 快速迭代

**冲突：** Event sourcing 看起来"重"，会拖慢 MVP。
**收敛：**
- MVP 只实现最小 event 表 + tasks projection 表，不做复杂 replay 工具；
- 先保证"崩溃不丢任务"，再逐步增强回放能力。

### 11.2 SQLite vs 可扩展并发

**冲突：** SQLite 并发能力有限。
**收敛：**

- 单用户场景使用 WAL + 单写多读即可；
- 单用户场景 SQLite WAL 足够，暂不引入额外数据库。

### 11.3 Free Loop 自由度 vs 安全门禁

**冲突：** Free Loop 容易越权执行高风险动作。
**收敛：**

- mode 不是安全边界；安全边界分两层纵深防御：
  1. **工具级**（不可绕过）：ToolBroker + Policy Engine — 无论 Worker 走 Free Loop 直接调 Tool 还是走 Skill Pipeline，所有工具调用都必须经过此链路。
  2. **任务级**：Orchestrator Supervisor + Watchdog — 预算阈值、超时、无进展检测，提供全局监督。
- 即使 Policy Engine 失效，Docker 隔离作为最后防线（§12.1 执行隔离）。

### 11.4 Tool RAG 动态注入 vs 可预测性

**冲突：** 动态注入工具会导致行为不稳定。
**收敛：**

- ToolIndex 的检索结果必须写事件（记录当时注入的工具集合与 schema 版本 hash）。
- 对关键 Skill Pipeline，若 `permission_mode=restrict`，工具集合固定在 `SkillSpec.tools_allowed` 里（§8.4.1）；默认 `inherit` 模式应继承当前 runtime 的 ambient mounted tool surface。
- 动态注入的工具应有来源验证；ToolIndex 检索失败时降级到固定基础工具集。

### 11.5 记忆自动写入 vs 记忆污染

**冲突：** 自动写记忆容易污染 SoR。
**收敛：**

- 禁止直接写 SoR；必须 WriteProposal + 仲裁。
- 仲裁默认严格：证据不足/冲突不明 → 不写（NONE）或进入待确认。
- 所有仲裁结果（包括 NONE）写入事件，便于分析仲裁质量。

### 11.6 多 Channel 实时接入 vs 导入一致性

**冲突：** 实时渠道与离线导入格式差异大。
**收敛：**

- 统一入口：NormalizedMessage + scope/thread 模型。
- 渠道差异只存在于 Adapter；内核只处理标准消息流。
- 离线导入幂等保证：基于 `msg_key = hash(sender + timestamp + normalized_text)` 去重（§8.7.5）。
- 时序交叉（历史导入 ts 早于已有实时消息）：以物理时间排序，导入消息按原始 ts 插入。

### 11.7 Policy Profile 可配 vs 安全门禁不可绕过

**冲突：** §8.6.2 允许用户通过 Policy Profile 调整门禁，包括"自动批准"和"静默执行"。但 Constitution §4 要求"不可逆操作必须二段式（Plan → Gate → Execute），绕过 Gate 视为严重缺陷"。如果 Policy Profile 将 irreversible 工具设为 `allow`，是否算"绕过 Gate"？
**收敛：**

- 明确区分 **Gate 的存在** 与 **Gate 的决策**。Policy Profile 改变的是 Gate 的决策结果（从 `ask` 变为 `allow`），但 Gate 链路本身（Plan → 策略评估 → Execute）仍然存在且执行，决策链条不可缩短。
- 即使 Policy Profile 设为 `allow`，仍必须：(1) 生成 Plan 事件；(2) Policy Engine 评估并记录决策事件（含引用的 Policy Profile）；(3) 才能 Execute。
- 安全底线：标记为 `policy_override_prohibited` 的动作（如 `delete_production_data`、`send_payment`）即使用户配了 `allow` 也强制 `ask`。

### 11.8 Free Loop 停止条件 vs 成本控制

**冲突：** Orchestrator/Workers "永远以 Free Loop 运行"（§8.3.1），每轮循环产生模型调用开销。若 Worker 陷入"推理但无进展"的循环，成本会快速累积。
**收敛：**

- Free Loop 必须内置**三道刹车**：
  1. **轮次上限**（max_iterations_per_task）：Worker 对单任务的推理轮次有硬上限，超过后进入 WAITING_INPUT 或 FAILED。
  2. **预算阈值**（来自 §8.9.2）：per-task 成本超限后自动降级（切换 cheap 模型）或暂停。
  3. **无进展检测**（Watchdog）：连续 N 轮没有产生工具调用或状态变更 → 自动暂停并通知用户。
- Watchdog 从"应该"（FR-EXEC-3）提升为 Free Loop 安全运行的**必要条件**。

### 11.9 A2A 状态映射信息损耗 vs 内部治理需求

**冲突：** §10.2.1 中 WAITING_APPROVAL → input-required、PAUSED → working 存在语义损耗。外部 SubAgent 看到 `working` 可能误以为任务正在执行，实际已暂停。
**收敛：**

- 接受这个语义压缩——A2A 协议本身就不区分这些状态。
- 在 A2A 消息的 `metadata` 中附加 `internal_status` 字段（可选），供知道 OctoAgent 扩展语义的客户端使用。
- 反向映射 `unknown → FAILED` 的降级必须写事件记录，因为可能掩盖外部 Agent 的真实状态。

### 11.10 Artifact 流式追加 vs Event Store 事件膨胀

**冲突：** Artifact 支持 `append: true` 的流式追加模式。若每次追加都生成 ARTIFACT_CREATED 事件，长时间流式产物（如 10 分钟实时日志）会产生大量事件，影响 SQLite 性能和事件流可读性。若不生成事件，又违反 Constitution §2（Everything is an Event）。
**收敛：**

- 流式 Artifact 采用**分层事件策略**：
  - 首次创建：生成 `ARTIFACT_CREATED` 事件（含 artifact_id、name、append=true）。
  - 中间追加：**不逐 chunk 写事件**，追加数据直接写入 Artifact Store（文件系统）。
  - 最终完成：生成 `ARTIFACT_COMPLETED` 事件（last_chunk=true，附带最终 hash + size）。
- 中间 chunk 的细粒度追踪通过 structlog + Logfire trace span 记录，不进入 Event Store。

### 11.11 REJECTED 终态 vs retry/resume 语义

**冲突：** REJECTED 作为终态（策略拒绝/能力不匹配），但 FR-TASK-1 声称"支持 retry / resume / cancel"。REJECTED 的任务是否允许 retry？策略没变则永远被拒绝。
**收敛：**

- REJECTED 是**不可重试的终态**，语义为"系统主动拒绝"：
  - Policy 拒绝 → REJECTED（用户需修改策略或任务描述后**新建任务**，不 retry 原任务）。
  - Worker 能力不匹配 → REJECTED（Orchestrator 可自动新建任务并 re-route 到另一 Worker，原任务保持 REJECTED）。
  - 运行时错误 → FAILED（支持 retry / resume）。
- Event 中记录 `rejection_reason: policy_denied | capability_mismatch | budget_exceeded | ...`，支持 UI 差异化展示。

### 11.12 双存储（SQLite + 向量数据库）一致性窗口

**冲突：** §7.3 设计了 SQLite（结构化）+ 向量数据库（embedding 语义检索）双存储。写入流程"commit 成功后异步更新向量索引"存在一致性窗口：SQLite 已 commit 但向量索引尚未更新，此时语义检索会漏掉最新数据。
**收敛：**

- 接受最终一致性（eventual consistency）——单用户场景下毫秒到秒级延迟可接受。
- 向量写入失败时记录事件并触发异步重试。
- 关键查询（如 SoR.current）走 SQLite metadata filter 优先，不依赖向量检索实时性。
- 提供手动 re-index 运维接口用于异常恢复。

### 11.13 Checkpoint 持久化 vs SQLite 事务边界

**冲突：** §8.3.4 要求 Skill Pipeline 每个节点结束后写 checkpoint，§8.2.2 要求"写事件与更新 projection 必须在同一事务内"。checkpoint、event、projection 三者是否必须同一事务？
**收敛：**

- 采用 **checkpoint + 事件原子写入**：节点完成后，在同一个 SQLite 事务中写入 (1) checkpoint (2) STATE_TRANSITION 事件 (3) 更新 tasks projection。
- 节点执行本身（模型调用、工具执行）在事务**之外**完成；只有结果元数据持久化在事务内，事务不会阻塞。
- 崩溃恢复时 checkpoint 和事件流始终一致——要么都写了，要么都没写。

### 11.14 上下文窗口管理 vs 任务完整性

**冲突：** 长任务的上下文可能超出模型 context window，截断会丢失关键信息，影响任务质量和连续性。
**收敛：**

- Orchestrator/Worker 层实现上下文管理策略：
  - checkpoint 保证任务状态完整性（不依赖 context window 保持状态）。
  - 对话历史在接近 context 上限时自动压缩（保留关键事实 + 最近 N 轮原文）。
  - 工具调用结果只回灌 summary + structured fields，全量输出走 Artifact（§10.3 已定义）。
- 压缩策略写事件记录（记录压缩前后 token 数），支持审计。

---
