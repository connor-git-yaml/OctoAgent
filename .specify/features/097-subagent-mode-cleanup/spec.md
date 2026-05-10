# F097 Subagent Mode Cleanup — Spec（v0.2 GATE_DESIGN 已拍板）

| 字段 | 值 |
|------|-----|
| Feature ID | F097 |
| 阶段 | M5 阶段 2（委托模式两路分离，**H3-A 临时 Subagent 显式建模**）|
| 主责设计哲学 | H3-A 临时 Subagent（spawn-and-die，共享调用方 Project/Memory/Context）|
| 前置依赖 | F092 DelegationPlane Unification + F094 Worker Memory Parity + F096 Worker Recall Audit |
| baseline | cc64f0c（origin/master，含 F096）|
| 分支 | feature/097-subagent-mode-cleanup |
| 状态 | **v0.2** — GATE_DESIGN 已通过（2026-05-10），Open Decisions OD-1/OD-2 + clarify C-1/C-2 + checklist #16 全部锁定 |
| 完成后开启 | F098 A2A Mode + Worker↔Worker |

---

## 0. GATE_DESIGN 决策已锁（plan 阶段权威 SoT）

**2026-05-10 GATE_DESIGN 用户拍板**（spec-driver-feature 编排器记录）：

| ID | 决策 | 锁定结果 | 影响 |
|----|------|---------|------|
| **OD-1** | Memory namespace 共享语义 | **α 共享引用** — Subagent 直接使用 caller 的 `AGENT_PRIVATE` namespace ID（不创建新的）| AC-F1/F2/F3 锁定为 α 语义；`_ensure_memory_namespaces` 在 Subagent 路径直接复用 caller namespace；F098 A2A 走自己 namespace 与之不冲突 |
| **OD-2** | `subagents.spawn` 是否写 SUBAGENT_SPAWNED | **保持 False**（F092 等价）| AC-EVENT-1 仅验证 `delegate_task` 路径写入；`subagents.spawn` 路径行为不变；F098 再考虑统一 |
| **C-1** | cleanup 查询路径 | **C 选项** — `SubagentDelegation` model 增 `child_agent_session_id` 字段，spawn 时记录，cleanup 直接定位 | AC-A1 字段列表追加 `child_agent_session_id: str`；AC-E1 cleanup 路径走此字段（避免 R5 多跳风险） |
| **C-2** | `agent_kind` 加 subagent 值兼容策略 | **B 选项** — plan 阶段必须 grep 所有 `agent_kind` 消费点确认无枚举硬校验后才直接引入；若发现限制做最小兼容扩展（无需 schema version bump） | plan.md 必须有专门 §"BEHAVIOR_PACK_LOADED 消费方实测"小节；AC-COMPAT-1 验证扩展 |
| **CL#16** | SubagentDelegation 持久化路径 | **child_task.metadata** — 写入 `child_task.metadata.subagent_delegation` 字段（JSON 序列化），零 migration，生命周期天然绑定子任务 | AC-A2 测试 round-trip 走 child_task.metadata；plan 阶段不需独立 SQL 表；`task store` 现有 metadata 路径已稳定 |

下文 §3.Gap-A / §3.Gap-F / §5 AC-* 已据此锁定。

---

## 1. 目标（Why）

OctoAgent 的 H3 设计哲学定义了**两种委托模式**：

- **H3-A 临时 Subagent**（F097 主责）：spawn-and-die，Subagent 是调用方的延伸，共享 Project / Memory / Context，完成后清理
- **H3-B A2A 真 P2P**（F098 主责）：长生命周期，Receiver 在自己独立的 Project / Memory 中工作

F097 的目标是让 H3-A 临时 Subagent 委托从"隐性的实现细节"变为"显式可观测的一等公民"：

1. **显式建模**：`SubagentDelegation` 作为独立数据类型，显式承载 Subagent 委托的生命周期（从 spawn 到 closed）
2. **身份正确**：Subagent 运行时获得 `kind=subagent` 的 `AgentProfile`，而非复用 Worker 的 profile——让 F096 audit chain 对 Subagent 路径正确标记 `agent_kind="subagent"`
3. **上下文完整传递**：`RuntimeHintBundle` 从 caller 显式拷贝到 Subagent，确保 Subagent 获得与 caller 等价的执行上下文（surface / tool hints / 失败约束等）
4. **Session 无 zombie**：Subagent 完成（succeeded/failed/cancelled）后，关联的 `SUBAGENT_INTERNAL` AgentSession 真正关闭（`status=CLOSED`），消除当前 zombie 积累风险
5. **内存共享语义明确**：H3-A "共享调用方 Memory" 从设计目标落实为代码中的明确路径选择（Memory 共享语义 α/β/γ，F097 spec 阶段列为 Open Decision）

**反命题**：F097 不动 A2A receiver context（F098）/ Worker→Worker 解禁（F098）/ dispatch_service 拆分 D7（F098）/ Ask-back（F099）/ Decision Loop Alignment（F100）。F097 也不动 F096 的 Phase E frontend UI（独立 Feature 或 F107 顺手清）。

---

## 2. 已通项（Baseline-Already-Passed）

以下 9 项在 tech-research 实测中确认 baseline（cc64f0c）已就位，**F097 不再重复实现这些能力**，也不将其列为 Acceptance Criteria：

| 编号 | 已通项描述 | 代码位置 |
|------|-----------|---------|
| BAP-1 | `AgentProfileKind` 含 `"subagent"` 值（F090 D2 引入）| `agent_context.py:16` |
| BAP-2 | `DelegationMode` 含 `"subagent"` 值（F090 D1 引入）| `orchestrator.py:39` |
| BAP-3 | `plane.spawn_child` 统一 spawn 入口（F092 已完成）| `delegation_plane.py:953` |
| BAP-4 | `DelegationTargetKind.SUBAGENT` enum 值 | `delegation.py:252` |
| BAP-5 | `AgentSessionKind.SUBAGENT_INTERNAL` enum 值 | `agent_context.py:133` |
| BAP-6 | `AgentSession.parent_worker_runtime_id` 字段 + SQLite index | `agent_context.py:326` + `sqlite_init.py:452` |
| BAP-7 | `list_subagent_sessions` store 方法（有实现，无 caller）| `agent_context_store.py:673` |
| BAP-8 | Subagent `scope_id` 继承父 task（project 共享已完成）| `capability_pack.py:1250` |
| BAP-9 | `TurnExecutorKind.SUBAGENT` 返回逻辑 | `session_service.py:694` |

**关键背景**：BAP-8 表明 Subagent 的 Project 共享通过 `scope_id` 传递**已经工作**——F097 的 Project 共享 AC 不需要新建路径，仅需验证现有路径正确。

---

## 3. 范围（What）

F097 实现 6 个 Gap（A-F），全部是 Baseline 之上的**新增能力**：

### Gap-A：SubagentDelegation 显式建模

新建 `SubagentDelegation` Pydantic model，作为 Subagent 委托的结构化载体：

**必须字段**：
- `delegation_id`：ULID 生成的唯一委托 ID
- `parent_task_id`：发起委托的父任务 ID
- `parent_work_id`：父任务对应的 work ID
- `child_task_id`：被委托的子任务 ID
- `caller_agent_runtime_id`：调用方 Agent 的 runtime ID（用于 Memory / Context 共享）
- `caller_project_id`：调用方 Project ID（用于 audit 和过滤）
- `caller_memory_namespace_ids`：共享的 Memory namespace 引用列表（具体内容由 Gap-F 决策决定）
- `spawned_by`：spawn 来源（工具名称，如 `delegate_task` / `subagents.spawn`）
- `target_kind`：固定为 `DelegationTargetKind.SUBAGENT`
- `created_at`：委托创建时间
- `closed_at`：委托关闭时间（`datetime | None`，closed_at 为 None 表示仍活跃）

**位置**：`packages/core/src/octoagent/core/models/` 新建文件或扩展现有 `delegation.py`

**F098 扩展点**：F098 的 A2A `WorkerDelegation` 将是独立模型（`target_kind=WORKER`），两者共同的字段（`delegation_id` / `parent_task_id` / `created_at` 等）可考虑提取 `BaseDelegation` 基类——**spec 阶段不强制，但 SubagentDelegation 设计应为此保留扩展空间**（字段命名遵循可派生惯例）。

### Gap-B：`_ensure_agent_session` 增加 SUBAGENT_INTERNAL 路径

`agent_context.py:2337-2345` 当前仅有 3 条路径（`DIRECT_WORKER` / `WORKER_INTERNAL` / `MAIN_BOOTSTRAP`）。当 `target_kind=subagent` 且有 `parent_agent_session_id` 时，应创建 `kind=SUBAGENT_INTERNAL` 的 AgentSession。

新路径判断条件：
- `agent_runtime.delegation_mode == "subagent"` 或 `target_kind="subagent"` 明确信号
- 有 `parent_agent_session_id`（Subagent 始终有父 session 上下文）

目标效果：Subagent 运行时不再落入 `WORKER_INTERNAL` 的 fallback 路径，有独立的 `SUBAGENT_INTERNAL` session 类型用于审计区分。

### Gap-C：Subagent 创建 `kind="subagent"` 的 ephemeral AgentProfile

**推荐方案（方案 A—轻量 ephemeral）**：spawn 时在 `_resolve_or_create_agent_profile` 路径中生成 `kind="subagent"` 的 `AgentProfile` 实例：
- `profile_id`：ULID 生成（不写入持久化 profile store）
- `kind`：`"subagent"`
- `scope`：`PROJECT`（与 caller 同 scope）
- 生命周期：与 `SubagentDelegation` 绑定（`closed_at` 时标记 profile 失效）

**不使用方案 B（复用 caller AgentProfile 改 kind）**：会破坏 caller 的 audit 链完整性，且无法区分 Subagent 自身的召回行为。

### Gap-D：RuntimeHintBundle 从 caller 显式拷贝到 child

当前 `_launch_child_task`（`capability_pack.py:1254`）创建的 `child_message.control_metadata` 不含 RuntimeHintBundle 字段。F097 需要在 Subagent spawn 时扩展拷贝：

**必须拷贝的字段**：
- `surface`（调用方所在 surface，如 web / telegram）
- `tool_universe` hints（工具集范围约束）
- `recent_worker_lane_*` 字段（最近 Worker 使用的 lane 信息）
- 最近失败限制（如 `recent_failure_budget`）

**拷贝时机**：仅在 `target_kind=SUBAGENT` 路径触发，不影响 Worker spawn。

### Gap-E：Subagent Session 完成后真清理

子任务进入终态（`succeeded` / `failed` / `cancelled`）时，触发以下清理序列：

1. 通过 `child_task_id` 反查关联的 `SUBAGENT_INTERNAL` AgentSession（使用 `task_id` 维度查询）
2. 调用 `agent_context_store.save_agent_session` 将 `status=CLOSED`，填充 `closed_at`
3. 同时关闭 `SubagentDelegation.closed_at`
4. **保留 RecallFrame 历史**：清 session 不清 RecallFrame——Subagent 审计记录必须持久保留（符合 H1 audit 优先原则，C2 Everything is an Event）
5. **清理触发点**：推荐在 task runner 终态回调处，而非 `worker_runtime.run()` 内部——task runner 对终态有明确感知，时机更准确

**幂等保证**：重复调用（如进程重启后重新触发清理）不报错，`status=CLOSED` 的幂等性由 store 层保证。

### Gap-F：Memory namespace 共享语义明确化（Open Decision）

H3-A "共享调用方 Memory" 的具体实现语义需要 spec 决策。**此决策是 F097 最关键的 spec 决策点，列为 Open Decision（见第 8 节），Phase F 实施前必须 GATE_DESIGN 用户拍板锁定选项**。

三个候选选项如下：

| 选项 | 语义描述 |
|------|---------|
| **α 共享引用** | Subagent 直接使用 caller 的 `AGENT_PRIVATE` namespace ID，不创建新 namespace |
| **β 拷贝 scope_ids** | Subagent 创建自己独立的 `AGENT_PRIVATE`，但 `memory_scope_ids` 拷贝 caller 的（读 scope 共享，写隔离）|
| **γ 读共享写隔离** | Subagent 读 caller namespace，写到独立临时 namespace，cleanup 时删除 |

AC-F1/F2/F3 随 GATE_DESIGN 拍板后锁定，Phase F 实施时才注入。

### Gap-G：BEHAVIOR_PACK_LOADED `agent_kind` 正确标记（Gap-C 自动副产品）

Gap-C 实施后，`make_behavior_pack_loaded_payload`（`agent_decision.py:352`）读取 `str(agent_profile.kind)`，将自动返回 `"subagent"`。

**此 Gap 无需独立实施代码**，但 F097 必须包含验证 AC（AC-G1）确认 Subagent 路径 emit 的 `BEHAVIOR_PACK_LOADED.agent_kind == "subagent"`。

---

## 4. 不在范围（Out of Scope）

| 主题 | 归属 Feature |
|------|-------------|
| A2A receiver 在自己 context 工作 | F098 |
| Worker→Worker 通信解禁（删除 `_enforce_child_target_kind_policy`）| F098 |
| `dispatch_service` 拆分（架构债 D7）| F098 |
| Ask-back channel（`worker.ask_back` / `worker.request_input` / `worker.escalate_permission`）| F099 |
| A2A source_type 泛化（`butler/user/worker/automation`）| F099 |
| Decision Loop Alignment / `RecallPlannerMode="auto"` | F100 |
| AC-F1 worker_capability 路径完整 audit chain（F096 H2 推迟项）| F098（等 delegate_task fixture 完备）|
| F096 Phase E frontend UI（agent 视角 recall audit Web UI）| 独立 Feature 或 F107 顺手清（backend 契约已稳定）|
| main direct 走 AGENT_PRIVATE namespace | F107 |
| WorkerProfile 与 AgentProfile 完全合并 | F107 |
| 3 条 F092 豁免路径（apply_worker_plan / work.split / spawn_from_profile）| 不动 |
| `BaseDelegation` 公共抽象基类（F097 仅保留扩展点）| F098 评估时决策 |

---

## 5. 验收标准（Acceptance Criteria）

### Gap-A：SubagentDelegation 建模

- [ ] **AC-A1**：`SubagentDelegation` Pydantic model 存在，含所有必须字段（`delegation_id` / `parent_task_id` / `parent_work_id` / `child_task_id` / **`child_agent_session_id`** / `caller_agent_runtime_id` / `caller_project_id` / `caller_memory_namespace_ids` / `spawned_by` / `target_kind` / `created_at` / `closed_at`）。**`child_agent_session_id` 由 GATE_DESIGN C-1 决策引入，cleanup 直接用此字段定位 SUBAGENT_INTERNAL session。**
- [ ] **AC-A2**：`target_kind` 字段默认值为 `DelegationTargetKind.SUBAGENT`；`closed_at` 默认 `None`；全部字段有类型注解；model 有单测覆盖（字段校验 + **写入 child_task.metadata.subagent_delegation 后 round-trip 反序列化**等价 — CL#16 决策）
- [ ] **AC-A3**：SubagentDelegation 持久化路径走 `child_task.metadata.subagent_delegation`（JSON 序列化），不引入独立 SQL 表，零 schema migration（CL#16 决策）

### Gap-B：SUBAGENT_INTERNAL session 路径

- [ ] **AC-B1**：`_ensure_agent_session` 新增第 4 路：当 `target_kind=subagent` 且有 `parent_agent_session_id` 时，创建 `kind=SUBAGENT_INTERNAL` 的 AgentSession，`parent_worker_runtime_id` 正确填充
- [ ] **AC-B2**：现有 `DIRECT_WORKER` / `WORKER_INTERNAL` / `MAIN_BOOTSTRAP` 三条路径的单测全部继续通过（0 regression）；新增单测覆盖 SUBAGENT_INTERNAL 路径

### Gap-C：ephemeral AgentProfile（kind=subagent）

- [ ] **AC-C1**：spawn Subagent 时创建 `AgentProfile(kind="subagent")`，`profile_id` 为 ULID 生成；该 profile 不写入持久化 profile store（`worker_profile` / `agent_profile` 表无新增行）
- [ ] **AC-C2**：ephemeral profile 的 `scope` 与 caller 同 project scope；`closed_at` 随 `SubagentDelegation.closed_at` 同步关闭

### Gap-D：RuntimeHintBundle 拷贝

- [ ] **AC-D1**：`_launch_child_task` 在 `target_kind=SUBAGENT` 时，`child_message.control_metadata` 包含从 caller 拷贝的 `surface` / `tool_universe` / `recent_worker_lane_*` 字段（字段值与 caller 原始值一致）
- [ ] **AC-D2**：Worker spawn 路径（`target_kind=WORKER`）的 `control_metadata` 不受影响（不增加 RuntimeHintBundle 字段）

### Gap-E：Session cleanup + 幂等

- [ ] **AC-E1**：子任务进入 `succeeded` / `failed` / `cancelled` 终态时，关联的 `SUBAGENT_INTERNAL` AgentSession `status` 变为 `CLOSED`，`closed_at` 填充为终态时间戳；`SubagentDelegation.closed_at` 同步更新
- [ ] **AC-E2**：清理操作幂等——对已 `CLOSED` 的 session 重复触发清理不报错，`closed_at` 保持首次设置值（不被覆盖）
- [ ] **AC-E3**：Subagent 产生的 `RecallFrame` 记录在 session 关闭后**仍然存在**（不删除），`list_recall_frames(agent_runtime_id=subagent_runtime_id)` 仍可返回数据

### Gap-F：Memory 共享语义（α 已锁定 — GATE_DESIGN 拍板）

> **OD-1 已锁定 α 共享引用**：Subagent 直接使用 caller 的 `AGENT_PRIVATE` namespace ID，不创建新 namespace row。

- [ ] **AC-F1**（α 语义）：Subagent 启动时，`_ensure_memory_namespaces` 路径**不为 Subagent 创建新的 AGENT_PRIVATE namespace**；Subagent 的 `agent_runtime` memory namespace 引用直接指向 caller 的 namespace ID 集合
- [ ] **AC-F2**：`SubagentDelegation.caller_memory_namespace_ids` 在 spawn 时填充（值来自 caller AgentRuntime 的实际 AGENT_PRIVATE namespace ID 集合）；该字段持久化到 `child_task.metadata.subagent_delegation` 中
- [ ] **AC-F3**：Memory 共享路径有集成测验证 α 语义——Subagent 写入 memory 后，**caller 在 spawn 之后能读到该写入**（namespace ID 一致性）；Worker 路径（target_kind=WORKER）行为不受影响（仍为 F094 AGENT_PRIVATE 独立路径）

### Gap-G：BEHAVIOR_PACK_LOADED agent_kind 验证

- [ ] **AC-G1**：Subagent 路径 dispatch 时，`EventStore` 中写入的 `BEHAVIOR_PACK_LOADED` 事件的 `agent_kind` 字段值为 `"subagent"`（非 `"worker"` 或 `"main"`）

### 审计链完整性

- [ ] **AC-AUDIT-1**：Subagent 路径的 F096 四层 audit chain 对齐：
  - `AgentProfile.profile_id → AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id`
  - 通过 `list_recall_frames(agent_runtime_id=subagent_runtime_id)` 可查到 Subagent 的 RecallFrame 记录

### 向后兼容性

- [ ] **AC-COMPAT-1**：现有 main / worker 路径的 `BEHAVIOR_PACK_LOADED.agent_kind` 值（`"main"` / `"worker"`）不受 F097 影响；`list_recall_frames` endpoint 现有过滤参数行为不变

### 事件可观测

- [ ] **AC-EVENT-1**：`delegate_task` 工具路径产生的 Subagent spawn 在 `EventStore` 中写入 `SUBAGENT_SPAWNED` 事件（F092 已有 `emit_audit_event=True`，F097 验证此路径仍正确）；Subagent 完成后写入 `SUBAGENT_COMPLETED` 事件——**T0.1 侦察判定**：(a) 若 baseline 已 emit → 仅验证；(b) 若 baseline 未 emit → Phase E 必须补充 emit 逻辑（cleanup hook 内 emit）作为 AC-E1 同步条件，Session CLOSED 状态迁移由 SUBAGENT_COMPLETED 事件覆盖（满足 Constitution C2）

> **关于 `subagents.spawn` 的 SUBAGENT_SPAWNED**：F092 保守决策中 `subagents.spawn` 路径 `emit_audit_event=False`（不写 SUBAGENT_SPAWNED）。F097 **不改变此历史决策**——F097 plan 阶段记录为设计决策，若需统一则在 F098 或独立 Feature 中评估。

### 范围边界

- [ ] **AC-SCOPE-1**：F098/F099/F100 的所有主题（A2A receiver / Worker→Worker / Ask-back / Decision Loop）在 F097 实施后代码库中无变化（git diff 验证）

### 全局回归与流程

- [ ] **AC-GLOBAL-1**：全量回归 0 regression vs F096 baseline (cc64f0c)，目标 ≥ 3260 passed（或 F096 实际 final passed 数量）
- [ ] **AC-GLOBAL-2**：每 Phase 后 e2e_smoke PASS（pre-commit hook）
- [ ] **AC-GLOBAL-3**：每 Phase 前 Codex review 闭环（0 high 残留）
- [ ] **AC-GLOBAL-4**：Final cross-Phase Codex review 通过
- [ ] **AC-GLOBAL-5**：`completion-report.md` 已产出，含"实际 vs 计划"对照 + Codex finding 闭环表 + F098 接入点说明
- [ ] **AC-GLOBAL-6**：Phase 跳过显式归档（若有）

---

## 6. User Stories

### User Story 1 — Subagent 委托在 Audit Trail 中清晰可辨（Priority: P1）

**场景描述**：当 Worker 调用 `delegate_task` 工具派出 Subagent 执行子任务时，系统在 EventStore 中保留完整的 Subagent 委托记录——包括 Subagent 的身份（`kind=subagent`）、所属委托（`SubagentDelegation`）、Recall 召回记录、Behavior Pack 加载记录。用户在 Web UI（F096 Phase E 完成后）可以按 `agent_kind=subagent` 过滤查看 Subagent 的所有活动。

**为什么是 P1**：audit 可追溯性是 OctoAgent Constitution C2（Everything is an Event）的直接体现；没有正确的 `agent_kind` 标记，Subagent 的审计数据会被归入 Worker 维度，污染 audit trail。

**独立测试**：可单独部署 Gap-A + Gap-C + Gap-G，通过 `delegate_task` 发起一个 Subagent 任务后查询 EventStore，验证 `BEHAVIOR_PACK_LOADED.agent_kind == "subagent"` 且 `SubagentDelegation` 记录存在。

**验收场景**：

1. **Given** Worker 任务中调用 `delegate_task` 工具，**When** Subagent 完成 LLM dispatch，**Then** EventStore 中的 `BEHAVIOR_PACK_LOADED` 事件 `agent_kind == "subagent"`，`SubagentDelegation` 记录包含正确的 `caller_agent_runtime_id` 和 `child_task_id`
2. **Given** Subagent 运行时召回了 Memory，**When** 调用 `list_recall_frames(agent_runtime_id=subagent_runtime_id)`，**Then** 返回该 Subagent 产生的 RecallFrame 列表，且 RecallFrame 的 `agent_runtime_id` 与 `BEHAVIOR_PACK_LOADED.agent_id`（通过 AgentRuntime 表对齐）一致

---

### User Story 2 — Subagent 继承调用方上下文（Priority: P1）

**场景描述**：Subagent 作为 caller 的延伸而非独立 Agent，应获得 caller 的 surface 信息、tool hints、最近失败约束等执行上下文（RuntimeHintBundle），而不是从零构建——使得 Subagent 的决策环与 caller 的 "执行环境语境" 一致。

**为什么是 P1**：H3-A 设计哲学的核心语义——spawn-and-die Subagent 是 caller 的延伸。没有上下文继承，Subagent 可能在错误的 surface / tool 约束下运行，产生不一致行为。

**独立测试**：部署 Gap-D 后，spawn Subagent 并检查 `child_message.control_metadata` 中是否包含 caller 的 `surface` 字段和 `tool_universe` hints。

**验收场景**：

1. **Given** caller（Worker）的 `RuntimeHintBundle` 包含 `surface="web"` 和特定 `tool_universe` hints，**When** spawn Subagent，**Then** Subagent 的 `child_message.control_metadata` 中包含这些字段（值与 caller 原始值一致）
2. **Given** Worker spawn 路径（`target_kind=WORKER`）被使用，**When** 同样的 spawn 路径执行，**Then** Worker 的 `control_metadata` 不包含 RuntimeHintBundle 字段（仅 Subagent 路径拷贝，不影响 Worker）

---

### User Story 3 — Subagent 完成后 Session 清洁关闭（Priority: P2）

**场景描述**：每次 Subagent 执行完毕（无论成功、失败还是取消），其关联的 AgentSession 自动关闭（`status=CLOSED`），不留 zombie session。系统长期运行下不积累无用的活跃 session 记录。

**为什么是 P2**：zombie session 不影响当前功能，但会导致审计数据混乱（活跃 session 列表包含已完成的 Subagent）并可能在未来引起资源泄漏。P1 Stories 提供即时用户价值，P2 清洁 Session 提供长期系统健康。

**独立测试**：部署 Gap-E 后，运行一个完整的 Subagent 任务（包括进入 succeeded 终态），然后查询 `AgentSession` 表，验证 `status=CLOSED` 且 `closed_at` 已填充。

**验收场景**：

1. **Given** Subagent 任务进入 `succeeded` 终态，**When** 清理 hook 触发，**Then** 关联的 `SUBAGENT_INTERNAL` AgentSession 的 `status=CLOSED`，`closed_at` 为终态时间戳
2. **Given** Session 已关闭（`status=CLOSED`），**When** 重复触发清理（如进程重启后），**Then** 操作幂等，不报错，`closed_at` 不被覆盖
3. **Given** Session 关闭后，**When** 查询 `list_recall_frames(agent_runtime_id=subagent_runtime_id)`，**Then** 该 Subagent 产生的 RecallFrame 仍然存在（审计记录不随 session 清理而删除）

---

### User Story 4 — Subagent 共享调用方 Memory（Priority: P2，依赖 Open Decision）

**场景描述**：Subagent 作为 caller 的延伸，能够访问 caller 的 Memory namespace 内容（根据选定的共享语义选项 α/β/γ），使得 Subagent 能在与 caller 相同的知识背景下完成子任务。

**为什么是 P2**：Memory 共享是 H3-A 设计语义完整性的关键，但具体实现语义（α/β/γ）有重要 trade-off，需要 GATE_DESIGN 阶段拍板才能实施。P2 优先级反映此 User Story 依赖外部决策，不是技术障碍导致 P2。

**独立测试**：GATE_DESIGN 拍板选项后，部署 Gap-F 并验证 Subagent 是否能读取 caller 在其 `AGENT_PRIVATE` namespace 中已存储的 fact。

**验收场景**（依赖选项拍板）：

1. **Given** caller 在 `AGENT_PRIVATE` namespace 中存储了 fact X，**When** spawn Subagent 并触发 Memory recall，**Then** Subagent 的 recall 结果包含 fact X（按选定选项的可见性规则）
2. **Given** Subagent 写入 Memory，**When** caller 查询 Memory，**Then** 可见性符合选定选项的语义（α：可见；β：不可见；γ：不可见）

---

## 7. 关键实体

### SubagentDelegation

Subagent 委托的结构化数据载体，生命周期从 spawn 到 closed。

- **标识**：`delegation_id`（ULID）
- **关系**：`parent_task_id` → 父任务；`child_task_id` → Subagent 子任务；`caller_agent_runtime_id` → 调用方 AgentRuntime
- **生命周期状态**：`closed_at == None`（活跃）→ `closed_at != None`（已关闭）
- **扩展点**：字段命名与 F098 未来 `WorkerDelegation` 保持一致性（`parent_task_id` / `caller_agent_runtime_id` 等），为 `BaseDelegation` 提取预留空间

### ephemeral AgentProfile（kind=subagent）

Subagent 的身份标识，不持久化存储。

- **标识**：ULID 生成的 `profile_id`
- **种类**：`kind="subagent"`（区别于 `"main"` 和 `"worker"`）
- **生命周期**：随 `SubagentDelegation` 绑定（`closed_at` 同步）
- **审计链**：`profile_id → AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id`

---

## 8. Open Decisions（GATE_DESIGN 阶段决策点）

以下决策在 GATE_DESIGN 阶段**必须用户拍板**，F097 spec 阶段不预设结论。

### OD-1：Memory namespace 共享语义（α/β/γ）【核心决策】

**背景**：H3-A "Subagent 共享调用方 Memory" 的具体实现有三种语义，trade-off 显著不同。

| 选项 | 实现语义 | 优势 | 劣势 |
|------|---------|------|------|
| **α 共享引用** | Subagent 直接使用 caller 的 `AGENT_PRIVATE` namespace ID，不创建新 namespace | 真共享，最符合 H3-A "延伸" 直觉；零 GC 负担；最小实现成本 | 同一 namespace 内多 Subagent 写入互相可见；F098 A2A 演化时需注意隔离边界 |
| **β 拷贝 scope_ids** | Subagent 创建自己独立的 `AGENT_PRIVATE` namespace row，但 `memory_scope_ids` 拷贝 caller 的 | 写隔离（Subagent 写入不污染 caller）；读 scope 共享（能看到 caller 数据）| 比 α 多一层间接；scope_ids 拷贝时机需设计 |
| **γ 读共享写隔离** | Subagent 读 scope 含 caller namespace；写 scope 为独立临时 namespace，cleanup 时删除 | 完全写隔离，cleanup 后 Subagent 写入内容不残留 | 实现最重（约 60 行 vs α 约 20 行）；与 F094 AGENT_PRIVATE 设计哲学有张力；cleanup 逻辑复杂 |

**tech-research 推荐意见**：**优先 α（共享引用）**——理由：spawn-and-die 模式 Subagent 是 caller 的延伸，真共享语义最直接；F098 A2A 走独立 namespace（H3-B），两者不冲突；最小实现成本。

**需要用户拍板的问题**：
- Subagent 的 Memory 写入是否希望对 caller 可见（α = 可见，β/γ = 不可见）？
- 是否需要 Subagent 产出的 Memory 在任务完成后有区分标记？
- 多 Subagent 并发时，彼此 Memory 是否应相互隔离（仅 β/γ 满足）？

### OD-2：`subagents.spawn` 工具是否统一写入 SUBAGENT_SPAWNED 事件

**背景**：F092 保守决策中，`delegate_task` 路径写 SUBAGENT_SPAWNED（`emit_audit_event=True`），`subagents.spawn` 路径不写（`emit_audit_event=False`，历史行为等价）。

**F097 候选决策**：
- **保持 F092 现状**：两条路径行为不同，`subagents.spawn` 不写事件
- **统一写入**：修改 `subagents.spawn` 路径为 `emit_audit_event=True`，统一 Subagent spawn 的可观测性

**需要用户拍板的问题**：是否希望所有 Subagent spawn（无论工具来源）都在 EventStore 中有 SUBAGENT_SPAWNED 记录？[AUTO-RESOLVED 备注：若用户拍板"保持现状"，F097 plan 阶段记录为显式设计决策，不在 spec AC 中要求统一]

> **当前 AC-EVENT-1 按"保持 F092 现状"编写**（仅 delegate_task 路径验证），待拍板后调整。

---

## 9. Success Criteria（可测量成果）

- **SC-1**：F097 实施后，通过 `delegate_task` 发起的 Subagent 任务，EventStore 中的 `BEHAVIOR_PACK_LOADED.agent_kind == "subagent"`，与 Worker（`"worker"`）和主 Agent（`"main"`）明确区分
- **SC-2**：Subagent 任务进入终态后，关联的 `AgentSession` 在 30 秒内（或下一次 task runner 回调）转为 `status=CLOSED`，长期运行后系统中不积累活跃的已完成 Subagent session
- **SC-3**：F096 audit chain 在 Subagent 路径上四层对齐可验证（`AgentProfile.profile_id → AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id`），`list_recall_frames` 可过滤查询 Subagent 维度数据
- **SC-4**：Worker / main Agent 现有路径全量回归 0 regression（≥ 3260 passed，或 F096 baseline 数量），`e2e_smoke` 全部通过

---

## 10. Edge Cases（异常场景）

| 场景 | 关联 AC | 预期行为 |
|------|---------|---------|
| Subagent 任务被强制取消（`subagents.kill`）| AC-E1 | 任务进入 `cancelled` 终态，触发同样的 cleanup 序列，session `status=CLOSED` |
| Subagent 任务 LLM dispatch 失败（模型返回错误）| AC-E1 | 任务进入 `failed` 终态，cleanup 仍触发，RecallFrame 保留（可能为空或部分填充）|
| cleanup hook 在进程重启中丢失（task runner 重启前 subagent 已完成）| AC-E2 | 进程重启后 task runner 重新扫描终态 task，补触发 cleanup；幂等保证不重复关闭 |
| ephemeral AgentProfile profile_id 与已有 profile 碰撞（ULID 冲突）| AC-C1 | ULID 碰撞概率极低（128 bit 随机），运行时检测并重新生成；profile 不写入持久化表，无唯一键冲突 |
| Memory 共享路径 α：多个 Subagent 并发写入同一 caller namespace | AC-F1 | SQLite WAL 保证写入串行化，无数据损坏；但写入内容互相可见（α 选项的已知 trade-off）|
| Subagent spawn 失败（DelegationManager gate 拒绝：深度/并发超限）| AC-A2 | `SubagentDelegation` 不创建（或创建后立即 closed）；不产生 SUBAGENT_INTERNAL session；SUBAGENT_SPAWNED 事件不 emit |
| `_ensure_agent_session` 增加第 4 路后，`target_kind` 字段信号不明确 | AC-B2 | 保守路径：仅当 `target_kind` 显式为 `"subagent"` 时走新路径；fallback 仍走 WORKER_INTERNAL（确保现有 Worker 路径不受影响）|

---

## 11. Constitution 兼容性

| Constitution 原则 | 兼容性 | 说明 |
|------------------|--------|------|
| **C1 Durability First** | ✅ 兼容 | `SubagentDelegation` 持久化（随 Task 持久化）；Session cleanup 写入 SQLite；RecallFrame 不删除 |
| **C2 Everything is an Event** | ✅ 兼容（OD-2 影响） | `SUBAGENT_SPAWNED`（delegate_task 路径）+ `BEHAVIOR_PACK_LOADED.agent_kind=subagent`；`subagents.spawn` 路径按 OD-2 决策处理 |
| **C7 User-in-Control** | ✅ 兼容 | `subagents.kill` 工具已存在，可取消 Subagent；session cleanup 不影响用户可控性 |
| **C8 Observability is a Feature** | ✅ 兼容 | ephemeral AgentProfile + SubagentDelegation + RecallFrame 使 Subagent 活动完全可审计；BEHAVIOR_PACK_LOADED.agent_kind 正确标记 |
| **C9 Agent Autonomy** | ✅ 兼容 | spawn 时机和工具选择由 LLM 自主决策；F097 仅增加基础设施，不添加硬编码业务规则 |
| **C10 Policy-Driven Access** | ✅ 兼容 | DelegationManager gate（depth/concurrent/blacklist）继续生效；F097 不绕过 gate |

---

## 12. F098 接入点（前向声明）

F097 完成后，以下制品为 F098 提供接入基础：

### SubagentDelegation 与 WorkerDelegation 的概念分离边界

| 维度 | F097 SubagentDelegation（H3-A）| F098 WorkerDelegation（H3-B，未来）|
|------|-------------------------------|----------------------------------|
| 生命周期 | spawn-and-die，随子任务终态关闭 | 长生命周期，A2A 对话会话管理 |
| Context 共享 | **共享** caller Project / Memory / Context | **独立** receiver 自己的 Project / Memory |
| target_kind | `SUBAGENT` | `WORKER` |
| 用途 | 执行 caller 的子任务 | 委托给独立 Agent 处理任务 |

F098 可基于 F097 的 `SubagentDelegation` 提取 `BaseDelegation` 基类，但 spec 阶段不强制——F098 设计时评估。

### F097 → F098 的直接接入点

- **BEHAVIOR_PACK_LOADED 的 `agent_kind` 字段**：F097 引入 `"subagent"` 值后，F098 实施 A2A Receiver 时需扩展此字段为新的 agent_kind 值（向后兼容，F097 数据可读）
- **`_enforce_child_target_kind_policy` 保持不动**：F097 不删除此 policy，F098 负责在 Worker→Worker 解禁时删除
- **F096 AC-F1（worker_capability 路径 audit chain）推迟到 F098**：等 delegate_task fixture 完备 + F098 Worker→Worker 解禁后一并实施

---

## 13. 依赖

### 必须就位（已就位）

- ✅ F092：`plane.spawn_child` 统一 spawn 入口（`delegation_plane.py:953`）+ `SpawnChildResult` 三态 + `emit_audit_event` 参数化
- ✅ F094：`AGENT_PRIVATE` namespace 体系（`_ensure_memory_namespaces` 路径完整）+ `RecallFrame.agent_runtime_id` 字段
- ✅ F096：`BEHAVIOR_PACK_LOADED` EventStore 接入完成 + `make_behavior_pack_loaded_payload` 读 `str(agent_profile.kind)` + `list_recall_frames` endpoint 稳定 + audit chain 四层对齐验证通过

### Plan 阶段需精确定位

- 清理 hook 的最佳挂载点（task runner 终态回调的精确 file:line）
- Subagent `_ensure_agent_session` 的 `target_kind` 信号传递路径（从 `child_message` 到 `_ensure_agent_session` 的参数链）
- ephemeral `AgentProfile` 的创建时机（spawn_child 内部 vs `build_task_context` 入口）

---

## 14. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| R1 | Memory 共享语义 OD-1 拍板前 Phase F 实施走错方向 | 高 | 高 | spec 阶段列为 Open Decision；Phase F 严格在 GATE_DESIGN 拍板后启动 |
| R2 | `_ensure_agent_session` 改动破坏现有 Worker session 创建逻辑 | 中 | 高 | 保守路径：仅当 `target_kind` 显式为 `"subagent"` 时走新路径；全量回归验证；0 regression 门禁 |
| R3 | Subagent cleanup hook 时机错误（过早 close 活跃 session）| 中 | 中 | 严格按 Task 终态触发；加幂等保护（已关闭不再更新）；plan 阶段精确定位 task runner 终态回调 |
| R4 | ephemeral AgentProfile ULID 生成逻辑与现有 profile store 查询产生混淆 | 低 | 中 | ephemeral profile 不写入持久化表；plan 阶段明确 ephemeral vs persistent profile 的代码路径分离 |
| R5 | `list_subagent_sessions` 当前按 `parent_worker_runtime_id` 查询，但 subagent 的 parent runtime 信号不准确 | 中 | 低 | cleanup 改用 `task_id` 维度查询（AgentSession 已有 task_id 索引）；tech-research R5 缓解策略已验证 |

---

## 15. 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 值 |
|------|-----|
| 新增组件/模块数量 | 2（`SubagentDelegation` model + session cleanup hook）|
| 新增或修改接口/契约数量 | 4（`_ensure_agent_session` + `_launch_child_task` + `_resolve_or_create_agent_profile` + cleanup hook 触发点）|
| 新引入外部依赖 | 0 |
| 跨模块耦合 | 是（`agent_context.py` + `capability_pack.py` + task runner + 可能的 `agent_context_store.py`，涉及 3+ 模块）|
| 复杂度信号 | 无递归结构；无独立状态机（cleanup 利用现有 Task 终态）；无并发控制新增；无数据迁移（ephemeral profile 不持久化）|
| **总体复杂度** | **MEDIUM** |

**复杂度判定依据**：组件 2 个（< 3 边界），接口 4 个（4-8 区间）→ MEDIUM。跨模块耦合涉及 3 个已有模块，但无新引入状态机/并发控制，整体改动风格与 F093/F094/F095 相似（利用 existing pattern + 补新路径）。

---

## 16. YAGNI 最小必要性检验

| FR / 组件 | 必要性标注 | 理由 |
|-----------|----------|------|
| SubagentDelegation model（Gap-A）| `[必须]` | 没有此 model，caller_memory_namespace_ids 无处承载，Gap-F 无法实施 |
| SUBAGENT_INTERNAL session 路径（Gap-B）| `[必须]` | 没有正确 session 路径，audit 中 Subagent 无法与 Worker 区分，AC-AUDIT-1 无法通过 |
| ephemeral AgentProfile kind=subagent（Gap-C）| `[必须]` | 没有正确 kind，BEHAVIOR_PACK_LOADED.agent_kind 无法为 "subagent"，AC-G1 无法通过 |
| RuntimeHintBundle 拷贝（Gap-D）| `[必须]` | H3-A "共享调用方 Context" 语义的直接体现；没有此拷贝，Subagent 在错误的 surface/tool 约束下运行 |
| Session cleanup（Gap-E）| `[必须]` | zombie session 积累是已验证的真实风险（tech-research §5.3）；系统长期运行健康必须 |
| Memory 共享语义（Gap-F）| `[必须]` | H3-A 的核心定义之一；但具体实现依赖 OD-1 决策，Phase F 在拍板前为 pending |
| BEHAVIOR_PACK_LOADED agent_kind 验证（Gap-G）| `[必须]` | Gap-C 自动副产品，无额外成本但有 audit 正确性保证；验证 AC 有必要性 |
| BaseDelegation 抽象基类 | `[YAGNI-移除]` | 当前仅有 SubagentDelegation，F098 WorkerDelegation 未设计；提前抽象增加复杂度。**移除理由**：YAGNI——F098 设计时评估，此 Feature 保留字段命名兼容性即可 |
| `agent_kind` 过滤参数扩展（list_recall_frames）| `[可选]` | 核心 audit 能力通过 agent_runtime_id 过滤已满足；agent_kind 过滤是便利性增强，可延迟到 F107 或 F103 顺手加 |

---

## 17. 修订记录

- v0.1（2026-05-10）：spec 草案，基于 tech-research 实测报告（9 BAP + 6 Gap）；7 AC 组（A/B/C/D/E/F/G）+ 5 全局 AC + 2 Open Decisions（OD-1 Memory α/β/γ + OD-2 SUBAGENT_SPAWNED 统一）；MEDIUM 复杂度评估；F098 接入点前向声明。
