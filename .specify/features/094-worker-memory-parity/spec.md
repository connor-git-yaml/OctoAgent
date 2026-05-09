# Feature Specification: F094 Worker Memory Parity

**Feature Branch**: `feature/094-worker-memory-parity`
**Created**: 2026-05-09
**Status**: Draft（GATE_DESIGN 用户决策 + Codex pre-Phase review 闭环已落地）
**Baseline**: `284f74d` (F093 完成点)
**Mode**: spec-driver feature（完整 9 阶段编排，研发模式 = `--research skip` 内部架构重构）
**Parallel With**: F095 Worker Behavior Workspace Parity（feature/095-worker-behavior-workspace-parity）
**Input**: User description（M5 阶段 1 第 2 个 Feature，主责 H2 Worker Memory 完整对等）

---

## 0. 总览（Overview）

F094 是 OctoAgent M5 战略 **阶段 1（Agent 完整上下文栈对等）** 的第 2 个 Feature，主责 **H2「Worker 完整 Memory 对等」**：

- **Worker 完整对等性的「Memory」轴**：F093 让 Worker 有自己的 session/turn 链；F094 让 Worker 有自己的 **私有 Memory namespace + 隔离的 recall 池 + 自属 RecallFrame 审计维度 + 可配置的 recall preferences（从 AgentProfile 读，不再硬编码）**。
- **统一私有 namespace 路径**：baseline 实测发现 worker → WORKER_PRIVATE / main → AGENT_PRIVATE **双轨已生效**（agent_context.py:2323-2327）。F094 按"架构简单 + 维护性"原则**统一到 AGENT_PRIVATE**，废弃 WORKER_PRIVATE 写入路径。
- **顺手清存量数据**：F063 历史决策把 WORKER_PRIVATE scope 全部迁到 PROJECT_SHARED，且 audit metadata 未保留任何 (memory_id → 原 scope_id) mapping——存量迁移**确认无可迁移记录**，migrate-094 设计为 CLI 完整 + 底层 no-op。

### 与 F093 / F095 的本质区别

| Feature | 主责轴 | 行为零变更约束 | 数据迁移 |
|---------|--------|----------------|----------|
| **F093（已完成）** | Worker Session / turn 链 | 块 C 拆分纯重构；块 A/B 写新 turn 是新行为 | 无 |
| **F094（本 Feature）** | Worker Memory namespace / RecallFrame / preferences | 块 D（preferences 改读 profile）行为零变更；块 B/C/E 是新行为 | **块 E migrate-094 设计为 no-op**（GATE_DESIGN + Codex review 锁定） |
| **F095（并行）** | Worker Behavior 4 层覆盖 | 主路径新行为 | 无 |

### 五段范围（块 A 实测 / B-E 主责改动）

- **块 A（spec 阶段实测纪要）**：把 4 项实测发现 + Codex pre-Phase review 闭环结论固化到 §2.1，作为 Plan 阶段制定边界的事实基础
- **块 B（核心新行为，含废弃 WORKER_PRIVATE）**：worker dispatch 路径废弃 WORKER_PRIVATE namespace 创建，改用 AGENT_PRIVATE（统一 worker / main）；Worker recall 优先查 AGENT_PRIVATE，fallback PROJECT_SHARED；同 project 不同 worker 私有 fact 严格隔离
- **块 C（数据补全 + 前置 schema 修复）**：RecallFrame 加 `queried_namespace_kinds` + `hit_namespace_kinds` 双字段（含 DDL）；`memory_maintenance_runs` DDL 补 `idempotency_key` + `requested_by` 列；`MemoryNamespace` 表 `(project_id, agent_runtime_id, kind)` 加 unique 约束 + 数据清理
- **块 D（最小行为变更）**：删除 `_default_worker_memory_recall_preferences()` 硬编码，改从 module-level 常量字典；保持 `existing_profile.context_budget_policy["memory_recall"]` override defaults 的 merge 顺序
- **块 E（不可逆迁移工具，no-op 实现）**：`octo memory migrate-094 --dry-run` / `--apply` / `--rollback` CLI 完整实现 + 底层 no-op；CLI 模板与 `config migrate` 对齐；写审计记录用块 C 补好的 idempotency_key 列

### 哲学锚点（CLAUDE.local.md §三条核心设计哲学 H2）

> **H2 完整 Agent 对等性**：Worker = 主 Agent − {hire/fire/reassign Worker} − {user-facing 表面}；每个 Agent 都有完整上下文栈（Project / Memory / Behavior / Session / Persona / 决策环）

F094 推进 H2 的「Memory」轴；Session 轴 F093 已完成，Behavior 轴 F095 并行进行，Recall provenance 留给 F096。

### 与 F063 历史决策的对齐声明（**重要**）

F063 用 Migration 063 把所有 `scope_id LIKE '%/private/%'` 的 SoR 记录从 WORKER_PRIVATE scope 迁到 PROJECT_SHARED。F063 的 audit metadata（`memory_maintenance_runs.metadata`）**完全未保留**任何 `(memory_id → 原 scope_id)` 或 `(memory_id → 原 agent_runtime_id)` mapping——只记录了 counts / target_scope / partition_stats（migration_063_scope_partition.py:192-200 已确认）。

F094 设计：

- **路径选择**：用 **AGENT_PRIVATE namespace** 承担 worker 私有语义。**baseline 中 worker dispatch 路径生成的 WORKER_PRIVATE namespace 在 F094 块 B 中废弃**——worker / main 统一只用 AGENT_PRIVATE（按 agent_runtime_id 区分），WORKER_PRIVATE enum 保留作为历史枚举值不再被任何写入路径生成。
- **F063 决策仍生效**：Migration 063 已完成的存量迁移不回滚。
- **新创建的 worker fact**：由 F094 块 B 路径直接写到该 worker 对应的 AGENT_PRIVATE namespace（按 `(project_id, agent_runtime_id, kind=AGENT_PRIVATE)` 三元组解析）。
- **存量数据迁移决策**（GATE_DESIGN 用户拍板 2026-05-09 + Codex review 闭环）：**降级方案 A**——`octo memory migrate-094 --dry-run` / `--apply` / `--rollback` CLI 完整实现且功能一致，但**实际迁移逻辑为 no-op**：dry-run 输出"零记录可迁移 + reason='F063_legacy_no_provenance' + namespace 分布快照"；apply 时写一条 `memory_maintenance_runs` 记录显式标注"零记录可迁移"；rollback 删除审计记录。这样 F094 在新版本下功能完善 + 接口统一 + 维护成本最低；未来若引入 worker 私有数据需要迁移，改用一个新的 migrate-NNN 命令而不是回头改 migrate-094 语义。
- **结构简单清晰锚点**：F094 引入的 worker 私有 fact 路径**靠 `MemoryNamespace` 表的 `(project_id, agent_runtime_id, kind=AGENT_PRIVATE)` 三元组表达**——上游代码识别 worker 归属**靠表元数据查询，不依赖 scope_id 字符串解析**（GATE_DESIGN 用户拍板 2026-05-09 + Codex MED-5 闭环）。
  - **scope_id 字符串保留 baseline 形态**：F094 范围内**不改** `build_private_memory_scope_ids` 函数生成的 `memory/private/{owner}/...` 编码（避免数据库内已有 namespace 数据破坏）；但所有 SoT 决策**不再引用** scope_id 字符串里的 `/private/` 子串作为判断依据。

---

## 1. User Scenarios & Testing

### User Story 1 — Worker 私有 fact 的 recall 隔离（Priority: P1）

**Journey**：主 Agent 派工给两个 Worker（A 与 B），同一 project 下。Worker A 在执行任务时学到了一条事实"客户 X 偏好深色 UI"，写到自己的 memory。Worker B 在另一个独立子任务里 recall memory 时，**不应**看到 Worker A 的私有 fact——它们应该在各自的 AGENT_PRIVATE namespace（按 agent_runtime_id 区分）中隔离。主 Agent 的 recall 也**不命中**任何 worker 的 AGENT_PRIVATE fact，除非该 fact 显式写到 PROJECT_SHARED。

**Why this priority**：H2 Memory 轴最小可观测落地点；不实现这条，"Worker 完整 Memory 对等"就不真。同时承接 F063 历史决策——F063 把 WORKER_PRIVATE 数据迁到 PROJECT_SHARED 后，F094 才真正补齐"worker 各自有私有 memory 池"的语义（但用 AGENT_PRIVATE 而非 WORKER_PRIVATE）。

**Independent Test**：
1. 同 project 下创建 Worker A / B（不同 agent_runtime_id），各自走 dispatch 路径
2. Worker A 写 fact → 落到 A 的 AGENT_PRIVATE namespace（`(project_id, agent_runtime_id_A, AGENT_PRIVATE)`）
3. Worker B 在自己的 dispatch 路径 recall memory → 返回结果**不含** Worker A 的 fact
4. 主 Agent recall 同 project 的 PROJECT_SHARED → 也**不含** Worker A 的 AGENT_PRIVATE fact
5. 断言 worker dispatch 路径不再创建 WORKER_PRIVATE namespace（grep 数据库 + 代码引用）

**Acceptance Scenarios**：

1. **Given** Worker A 已写一条 fact 到自己的 AGENT_PRIVATE namespace，**When** Worker B 在同 project 下做 memory recall（无论 query 是否相关），**Then** 结果中不含 Worker A 的 fact（按 agent_runtime_id 严格隔离）。
2. **Given** Worker A 写 fact 时未显式指定 namespace，**When** memory propose_write 路径执行，**Then** 默认进 AGENT_PRIVATE namespace（按当前 context.agent_runtime_id 推断），写入审计事件含 `namespace_kind=AGENT_PRIVATE` + `agent_runtime_id=<A>`。
3. **Given** Worker A 写到 AGENT_PRIVATE 的 fact，**When** 主 Agent 在同 project 下 recall（namespace_ids 列表只含主 Agent 的 PROJECT_SHARED + 主 Agent 自己的 AGENT_PRIVATE），**Then** 结果不含 Worker A 的 AGENT_PRIVATE fact。
4. **Given** 任何 worker dispatch 创建 namespace 路径（agent_context.py:2323-2327）执行后，**When** 查 `MemoryNamespace` 表，**Then** 该 worker 对应的 namespace 记录 `kind=AGENT_PRIVATE`，**不存在 `kind=WORKER_PRIVATE` 的新记录**。

> **删除原 Acceptance 5（promote 流转）**：Codex HIGH-2 闭环——baseline 的 `memory_candidates.promote` 是 observation candidate → USER.md 写入路径，**不操作 memory_sor / MemoryNamespace.kind 流转**。F094 不在范围内做"AGENT_PRIVATE → PROJECT_SHARED"的 fact 移动；该需求留给后续 Feature（如 F096 audit / F097 Subagent / 单独 namespace lifecycle Feature 评估）。

---

### User Story 2 — Worker recall preferences 从 AgentProfile 派生（Priority: P2）

**Journey**：当前 worker AgentProfile 创建时，recall preferences（`prefetch_mode / planner_enabled / scope_limit / per_scope_limit / max_hits`）由 `_default_worker_memory_recall_preferences()` 硬编码注入。F094 把这部分硬编码挪到 module-level 常量字典，并保留**关键 merge 顺序**：`existing_profile.context_budget_policy["memory_recall"]` 优先级高于 module-level defaults（与 baseline `{**defaults, **existing}` 完全一致）。

**Why this priority**：解 D 块在 spec 实测项 3 中已验证工作量极小（1 处调用点），但是 H2 哲学合规的必要清理。

**Independent Test**：
1. 删除 `_default_worker_memory_recall_preferences()` 硬编码后，跑 worker dispatch 路径
2. 断言 recall 时实际使用的 5 个参数与历史硬编码值完全一致（行为零变更）
3. 断言已存在 worker AgentProfile 重建时，existing `context_budget_policy["memory_recall"]` 仍优先于 module-level defaults（merge 顺序保留）

**Acceptance Scenarios**：

1. **Given** 一个新建的 Worker AgentProfile（无 existing memory_recall），**When** 通过 `_create_worker_agent_profile` 路径创建，**Then** `agent_profile.context_budget_policy["memory_recall"]` 包含 `{prefetch_mode="hint_first", planner_enabled=True, scope_limit=4, per_scope_limit=4, max_hits=8}` 5 个 key，与 F093 baseline 硬编码值完全一致。
2. **Given** 已存在 Worker AgentProfile 含自定义 `memory_recall = {scope_limit: 10}`，**When** 走 worker create / update 路径，**Then** 最终值为 `{prefetch_mode="hint_first", planner_enabled=True, scope_limit=10（existing override）, per_scope_limit=4, max_hits=8}`——existing override defaults 的 merge 顺序保留。
3. **Given** Worker 在主循环中 recall memory，**When** recall service 读取 `_resolve_memory_prefetch_mode` / `memory_recall_scope_limit` 等接口，**Then** 取值路径只走 `agent_profile.context_budget_policy["memory_recall"]`，**不再调用** `_default_worker_memory_recall_preferences`（grep 全 codebase 确认零调用点）。

---

### User Story 3 — RecallFrame 双层 namespace 维度审计就绪（Priority: P2）

**Journey**：F096 将做 Worker Recall Audit & Provenance + Web Memory Console agent 视角。F094 的责任是把 RecallFrame 持久化层补齐——`agent_runtime_id` / `agent_session_id` baseline 已通（实测项 2 已确认），但 namespace 维度信息**完全缺失**（模型层 + DDL 都没有顶层 namespace 字段）。F094 加上**双字段**：`queried_namespace_kinds`（这次 recall 查询了哪些 namespace kind）+ `hit_namespace_kinds`（实际有 hit 命中的 namespace kind），让 F096 audit 既能做"曾查过私有但 0 命中"也能做"实际命中私有"两类查询。

**Why this priority**：F096 接入零返工；F093 实证"提前给下一 Feature 备好接口点"价值大。Codex MED-5 闭环：单值 / 单 list 不足以表达 audit 语义，明确双字段。

**Independent Test**：
1. 触发一次 Worker recall 路径，namespace_ids 列表包含 AGENT_PRIVATE + PROJECT_SHARED，但 AGENT_PRIVATE 0 hits（worker 未写过）
2. 持久化的 RecallFrame：`queried_namespace_kinds=["AGENT_PRIVATE","PROJECT_SHARED"]`、`hit_namespace_kinds=["PROJECT_SHARED"]`（仅 hit 命中的）
3. control_plane 的 `list_recall_frames` API 接受 `queried_namespace_kind` 与 `hit_namespace_kind` 过滤参数，返回结果按字段精确匹配
4. main agent 路径同样填双字段（值常常一致 `["PROJECT_SHARED","AGENT_PRIVATE"]` queried；hit 视实际命中而定）

**Acceptance Scenarios**：

1. **Given** Worker 触发 memory recall 命中 AGENT_PRIVATE 与 PROJECT_SHARED 两个 namespace，**When** RecallFrame 持久化，**Then** `queried_namespace_kinds` = recall 实际查的全 list，`hit_namespace_kinds` = 实际命中的子集。
2. **Given** 已持久化若干 worker / main RecallFrame，**When** 调用 `list_recall_frames(agent_runtime_id=<W>, hit_namespace_kind="AGENT_PRIVATE")`，**Then** 返回该 worker 实际命中私有 namespace 的 recall 记录。

---

### User Story 4 — migrate-094 CLI 接口完备（降级方案 A：no-op 迁移）（Priority: P2）

**Journey**：用户跑 `octo memory migrate-094 --dry-run`，CLI 扫描数据库并输出"零条记录可迁移"+ 显式说明原因（"F063 Migration 已抹掉 agent_runtime_id 痕迹，无法反推存量归属，本命令为占位"）+ 当前 PROJECT_SHARED / AGENT_PRIVATE namespace 分布快照。用户审查后跑 `--apply`，CLI 写入一条 `memory_maintenance_runs` 审计记录（idempotency_key="migration_094_worker_private"），实际**不改任何 SoR 记录**。重新跑 `--apply` 幂等短路返回。`--rollback <run_id>` 可清除审计记录（rollback 后 idempotency 失效，可重新 apply）。

**前置依赖**：本 US 依赖块 C 完成 `memory_maintenance_runs` DDL 补 `idempotency_key` + `requested_by` 列（Codex MED-3 闭环）。Phase 顺序锁定 C → D → B → E，本 US 在 E 阶段实施时块 C 已就绪。

**Why this priority**：CLAUDE.local.md §"执行约束"要求 migrate-094 命令必须 dry-run + 用户拍板。GATE_DESIGN 用户已确认接受降级方案 A：**功能完善 + 接口统一 + 实现简单**——CLI 完整可用，但底层是 no-op，避免不可靠的反推算法引入数据风险。

**Independent Test**：
1. 测试库种入若干 PROJECT_SHARED fact（任意分布）
2. 跑 `--dry-run`：输出零迁移记录 + namespace 快照；返回值为 dict 含 `total=0 / reason="F063_legacy_no_provenance" / namespace_snapshot={...}`；库内任何记录未变更
3. 跑 `--apply`：写入审计记录，库内记录不变
4. 再跑 `--apply`：短路返回（已执行过），库内仍不变
5. 跑 `--rollback <run_id>`：删除审计记录，可再次 apply（验证审计层 rollback 路径）

**Acceptance Scenarios**：

1. **Given** 数据库内任意 PROJECT_SHARED fact 分布，**When** 跑 `octo memory migrate-094 --dry-run`，**Then** stdout 输出 `total_facts_to_migrate=0` + `reason="F063_legacy_no_provenance"` + 当前 namespace 分布快照，库内任何记录未变更。
2. **Given** dry-run 已运行，**When** 跑 `octo memory migrate-094 --apply`，**Then** `memory_maintenance_runs` 表新增一条 `idempotency_key="migration_094_worker_private"` / `kind="migration"` / `metadata.no_op=true` 的记录，**SoR 表零修改**。
3. **Given** `--apply` 已成功执行一次，**When** 再次跑同一命令，**Then** stdout 输出"已执行过（run_id=...），幂等性检查通过"，库内任何记录不变。
4. **Given** 已 apply，**When** 跑 `--rollback <run_id>`，**Then** 审计记录被删除，`--apply` 可再次执行（验证 rollback 路径完整可用）。

---

## 2. 关键模型 / 契约（Key Models & Contracts）

> 本节固化 spec 阶段实测发现 + Codex pre-Phase review 闭环结论，作为 Plan 阶段制定边界的事实基础。具体接口与实现留 Plan 阶段。

### 2.1 块 A 实测纪要（基于 F093 baseline `284f74d` + Codex pre-Phase review 校正）

#### 2.1.1 MemoryNamespaceKind 状态（Codex HIGH-1 校正）

- 枚举定义 ✅：`packages/core/src/octoagent/core/models/agent_context.py:131-134`
  - `PROJECT_SHARED` / `AGENT_PRIVATE` / `WORKER_PRIVATE`
- MemoryNamespace 表持久化 ✅：`agent_context_store` 已 round-trip 三种 kind
- **baseline 实际运行时双轨已生效**（`apps/gateway/src/octoagent/gateway/services/agent_context.py:2323-2327`）：
  - worker dispatch（agent_runtime.role == WORKER）→ 创建 **WORKER_PRIVATE** namespace
  - main dispatch（agent_runtime.role == MAIN）→ 创建 **AGENT_PRIVATE** namespace
- **但 SoR 数据为空**：F063 Migration 已迁走所有 `/private/` 痕迹的 SoR；F093 显式排除 SessionMemoryExtractor 在 worker 上跑（session_memory_extractor.py:63 注释）→ 实际 worker WORKER_PRIVATE namespace 下没有 SoR fact 数据
- recall service 当前不按 worker 维度自动解析 namespace ❌：`recall_memory(scope_ids: list[str])` 接受 scope_id 列表，由调用方负责构建
- 写入路径默认进 PROJECT_SHARED ❌：worker 写 fact 默认走 PROJECT_SHARED scope_id，无"自动判断 AGENT_PRIVATE / WORKER_PRIVATE"
- WORKER_PRIVATE 现有引用清单（F094 块 B 必须清理或保留判断）：
  - `agent_context.py:2324`（worker dispatch 创建路径）—— **F094 块 B 废弃**
  - `agent_context.py:470, 473`（`build_private_memory_scope_ids` 函数中按 worker/main 分支生成 scope_id）—— **F094 保留**（避免 namespace.memory_scope_ids 字段值变化破坏向后兼容；但块 B 不再触发 worker_private 分支）
  - `agent_context.py:3222`（recall 入口排序时把 WORKER_PRIVATE 与 AGENT_PRIVATE 同等优先于 PROJECT_SHARED）—— **F094 保留**（向后兼容历史 namespace 数据）
  - `task_service.py:1946`（具体上下文 Plan 阶段实测）—— **Plan 阶段决策**
  - `migration_063_scope_partition.py`（F063 历史代码）—— **不动**

#### 2.1.2 RecallFrame 字段状态

- 模型定义：`packages/core/src/octoagent/core/models/agent_context.py:430-447`
- ✅ `agent_runtime_id` / `agent_session_id` 字段已存在 + main 路径已写入（`apps/gateway/src/octoagent/gateway/services/agent_context.py:800-825`）
- ❌ 顶层 `namespace` / `queried_namespace_kinds` / `hit_namespace_kinds` 字段完全缺失
- ⚠️ `memory_namespace_ids` 字段已存在（`apps/gateway/.../agent_context.py:731, 800-810`）：写的是 **resolved namespaces 全量列表**（namespace_id 维度）—— Codex MED-5 校正：**不是** queried 也不是 hit 维度
- ⚠️ 实际 hit namespace 信息当前嵌在 `memory_hits[i].metadata` 中，由 `scope_entry_map` 注入（`agent_context.py:2986-2995, 3105-3114`）：要做 audit 必须 join memory_hits 字段
- API 过滤维度（`session_service.list_recall_frames`）：仅支持 `project_id / task_id / agent_session_id / context_frame_id`，**缺 agent_runtime_id 与 namespace kind 过滤**
- 写入路径（baseline）：worker / main 共用同一条 `agent_context.py:800-825` 路径——F093 pattern 部分成立，但 namespace 字段补完是真新行为

#### 2.1.3 recall preferences 状态

- 硬编码定义：`apps/gateway/src/octoagent/gateway/services/agent_context.py:183-195`
  - 5 个 key：`prefetch_mode="hint_first"` / `planner_enabled=True` / `scope_limit=4` / `per_scope_limit=4` / `max_hits=8`
- 调用点（仅 1 处）：`agent_context.py:2693-2707`，仅 `_create_worker_agent_profile` 路径
  - **关键 merge 顺序**：`{**_default_worker_memory_recall_preferences(...), **existing_profile_memory_recall}` —— **existing 覆盖 defaults**（Codex LOW-7 校正：F094 块 D 必须保留此顺序，避免破坏已存在 worker profile 的自定义配置）
- 流程：硬编码注入 → 写入 `worker_profile.context_budget_policy["memory_recall"]` → recall 时由 `_memory_recall_preferences(agent_profile)` 从 profile 读
- AgentProfile.context_budget_policy 当前 schema：`dict[str, Any]`（`packages/core/src/octoagent/core/models/agent_context.py:159`）——开放 dict，无强 schema
- **判定**：行为零变更工作——把硬编码字典挪到 module-level 常量 `_WORKER_MEMORY_RECALL_DEFAULTS`；删除独立的硬编码函数；保留 merge 顺序

#### 2.1.4 memory_sor + memory_maintenance_runs 数据状态

- `memory_sor` 表 schema：`packages/memory/src/octoagent/memory/store/sqlite_init.py:18-33`
  - 字段：`memory_id / schema_version / scope_id / partition / subject_key / content / version / status / metadata / evidence_refs / created_at / updated_at`
  - **无 agent_id 字段**——agent 信息隐式在 `scope_id` 字符串里（如 `memory/private/worker/runtime:agent-001`）
- F063 Migration（`migration_063_scope_partition.py`）：
  - 用 `scope_id LIKE '%/private/%'` 识别 WORKER_PRIVATE
  - 已把这些记录的 `scope_id` 改为 PROJECT_SHARED `target_scope_id`，并按 content 重新推断 partition
  - 用 `memory_maintenance_runs` 表 + `idempotency_key="migration_063_scope_partition"` 实现幂等
- **F063 audit metadata 完全未保留 mapping**（Codex LOW-6 校正）：
  - F063 内部 `migrated_scope_ids` 是 set 类型的 **局部变量**（`migration_063_scope_partition.py:147-173`），仅用于 fragments 表同步迁移，**未写入 `memory_maintenance_runs.metadata`**
  - 实际 metadata 仅含：`migration / total_records / migrated_count / repartitioned_count / fragment_update_count / target_scope / partition_stats`（line 192-200），**无任何 `(memory_id → 原 scope_id)` 或 `(memory_id → 原 agent_runtime_id)` mapping**
  - 结论：migrate-094 反推 100% 不可行 → 锁定降级方案 A
- migrate-094 CLI 模板：参考 `packages/provider/src/octoagent/provider/dx/config_commands.py:897-964`（`config migrate` 命令的 plan / apply / rollback 三段式）

#### 2.1.5 memory_maintenance_runs DDL 缺列（Codex MED-3 闭环 - F094 块 C 前置）

**问题**：F063 migration（line 181-200）插入 `memory_maintenance_runs` 时使用了 `idempotency_key` + `requested_by` 列，但**真实 canonical DDL 中这两列不存在**：

`packages/memory/src/octoagent/memory/store/sqlite_init.py` 真实 DDL：

```sql
CREATE TABLE IF NOT EXISTS memory_maintenance_runs (
    run_id            TEXT PRIMARY KEY,
    schema_version    INTEGER NOT NULL DEFAULT 1,
    command_id        TEXT NOT NULL,
    kind              TEXT NOT NULL,
    scope_id          TEXT NOT NULL DEFAULT '',
    partition         TEXT,
    status            TEXT NOT NULL,
    backend_used      TEXT NOT NULL DEFAULT '',
    fragment_refs     TEXT NOT NULL DEFAULT '[]',
    proposal_refs     TEXT NOT NULL DEFAULT '[]',
    derived_refs      TEXT NOT NULL DEFAULT '[]',
    diagnostic_refs   TEXT NOT NULL DEFAULT '[]',
    error_summary     TEXT NOT NULL DEFAULT '',
    metadata          TEXT NOT NULL DEFAULT '{}',
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    backend_state     TEXT NOT NULL DEFAULT 'healthy'
);
-- 无 idempotency_key 列 / 无 requested_by 列
```

测试是手写 schema 补了这些列，不代表真实 `init_memory_db()` 建库。F063 在真实库上跑会因列缺失抛错（必须有手工 ALTER TABLE 才能跑通）。

**F094 块 C 必须前置 schema task**：
- 给 `memory_maintenance_runs` 补 `idempotency_key TEXT NOT NULL DEFAULT ''` + `requested_by TEXT NOT NULL DEFAULT ''` 列
- 测试用真实 `init_memory_db()`，不手写补丁
- 此前置 task 同时为 F063（已存在但实际未在生产跑）+ F094 块 E migrate-094 提供基础

#### 2.1.6 MemoryNamespace 三元组无 unique 约束（Codex MED-4 闭环 - F094 块 C 前置）

**问题**：spec §0 + §2.2 Gap-6 锁定 `(project_id, agent_runtime_id, kind=AGENT_PRIVATE)` 为 worker 私有 namespace 的 SoT，但**真实 DDL 仅有 `namespace_id PRIMARY KEY`**：

`packages/core/src/octoagent/core/store/sqlite_init.py:536-550`：

```sql
CREATE TABLE IF NOT EXISTS memory_namespaces (
    namespace_id       TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL DEFAULT '',
    agent_runtime_id   TEXT NOT NULL DEFAULT '',
    kind               TEXT NOT NULL DEFAULT 'project_shared',
    -- ...
);
-- idx_memory_namespaces_project_kind 是普通 index，非 unique
```

store upsert 也只按 `namespace_id` 冲突（`agent_context_store.py:827-846`），不检查三元组重复。

**F094 块 C 必须前置 schema task**：
- 给 `memory_namespaces` 加 `UNIQUE(project_id, agent_runtime_id, kind)` 约束（IF NOT EXISTS 安全 ALTER）
- 数据清理：迁移时如有同三元组重复记录，按 `created_at DESC` 保留最新 1 条 + archive 其他（保留 archived_at 字段）
- block B 隔离断言 `_resolve_worker_private_namespace(project_id, agent_runtime_id)` 才能依赖唯一性

### 2.2 待 Plan 阶段精确坐实的 Gap

> spec 阶段实测发现的疑点，**Plan 阶段必须先用代码 + 测试坐实**，再决定具体实现。

#### Gap-1（块 E）：F063 已抹掉的 agent_runtime_id 痕迹（GATE_DESIGN + Codex 已锁定降级方案 A）

**问题历史背景**：F063 Migration 已把 `scope_id` 从 `<...>/private/<agent_runtime_id>/...` 改为 PROJECT_SHARED scope。F063 audit metadata 完全未保留 (memory_id → 原 scope_id) mapping（Codex LOW-6 已实测：`migrated_scope_ids` 是局部变量未持久化；metadata 字段仅含 counts 与 stats）。

**spec 锁定**：接受**降级方案 A**（CLI 完整 + 底层 no-op）。详见 §0 对齐声明 + §1 US4 + §5 块 E 验收。Plan 阶段不再做反推方案设计。

#### Gap-2（块 B）：写默认 namespace 的推断时机

**问题**：worker 写 fact 默认进 AGENT_PRIVATE namespace——这个推断在哪里发生？
- 选项 (a)：memory propose_write 工具的 input 默认值
- 选项 (b)：scope resolver 层（在 store 写入前自动判断）
- 选项 (c)：context resolver 层（构建 compiled_context 时预派 worker 默认 namespace_id）

**Plan 阶段必须**：grep 当前 `propose_write` / `propose_observation` 工具调用栈，确认推断点。

#### ~~Gap-3（块 B）：promote 路径的语义~~（Codex HIGH-2 闭环 - 删除）

F094 不动 promote。`memory_candidates.promote` baseline 是 observation candidate → USER.md 写入路径，**不操作 memory_sor / MemoryNamespace.kind**。F094 范围内不涉及"AGENT_PRIVATE → PROJECT_SHARED" fact 流转；US1 Acceptance 已删除相关条目。

#### Gap-4（块 C）：RecallFrame 双字段语义（Codex MED-5 闭环 - 已锁定）

**spec 锁定**：分**双字段**：
- `queried_namespace_kinds: list[MemoryNamespaceKind]`：本次 recall 实际查询了哪些 namespace 的 kind 维度（去重）；从 `memory_namespace_ids` 派生（resolved namespaces）
- `hit_namespace_kinds: list[MemoryNamespaceKind]`：本次 recall 实际有 hit 命中的 namespace kind（从 `memory_hits[i].metadata.namespace_kind` 归一化生成）

理由：F096 audit 双查询模式（"曾查过私有"+"实际命中私有"）都需要；单字段语义混淆。

**Plan 阶段确认**：`memory_hits[i].metadata.namespace_kind` 在 `scope_entry_map` 注入时是否一定有值（agent_context.py:2986-2995, 3105-3114 实测验证）。

#### Gap-5（块 D）：默认值放哪里（已锁定 module-level 常量字典）

**spec 锁定**：module-level 常量 `_WORKER_MEMORY_RECALL_DEFAULTS`，与 `_default_worker_memory_recall_preferences()` 函数一一对应；保留 baseline merge 顺序 `{**defaults, **existing}`。

#### Gap-6（块 B）：worker 私有 namespace 编码方式（GATE_DESIGN + Codex 联合锁定）

**spec 锁定**（Codex MED-5 + LOW-7 闭环 + GATE_DESIGN 决策 2）：
- **SoT 在 MemoryNamespace 表**：F094 块 B 上游代码识别 worker 私有 namespace 必须靠 `(project_id, agent_runtime_id, kind=AGENT_PRIVATE)` 三元组查询
- **scope_id 字符串保留 baseline 形态**：F094 范围内**不改** `build_private_memory_scope_ids` 生成的 `memory/private/{owner}/...` 编码（避免 namespace.memory_scope_ids 字段已有数据破坏）；但所有 SoT 决策**不再引用** scope_id 字符串里的 `/private/` 子串作为判断依据
- **WORKER_PRIVATE enum 保留**：作为历史枚举值保留，**不再被任何写入路径生成新记录**（块 B 验收 B7）；recall 入口排序逻辑（agent_context.py:3222）暂时保留兼容历史 namespace 数据

### 2.3 事件契约（块 B + 块 C）

worker memory 写入 / recall 路径必须 emit 可审计事件：

- **MEMORY_FACT_WRITTEN**（或复用现有）：worker 写 fact 时 emit，含 `agent_runtime_id` / `agent_session_id` / `namespace_kind` / `namespace_id` / `memory_id` / `subject_key` / `partition`
- **MEMORY_RECALL_COMPLETED**（或复用现有）：worker recall 结束时 emit，含 `agent_runtime_id` / `agent_session_id` / `query` / `queried_namespace_kinds[]` / `hit_namespace_kinds[]` / `hit_count` / `recall_frame_id`

具体事件名复用 vs 新增由 Plan 阶段决定（F093 实证倾向复用现有 schema，加字段而不加新枚举）。**必须满足**：

- control_plane 可按 `agent_runtime_id` + `namespace_kind` 双维度查询
- 事件 payload 与 RecallFrame 字段语义一致（让 F096 audit endpoint 复用）
- main 与 worker 写 / recall 事件流 shape 一致，仅 namespace 维度不同（保持 H2 对等）

### 2.4 与 F063 / F095 的耦合点

- **与 F063 的耦合**：见 §0 "与 F063 历史决策的对齐声明"。F094 不复活 WORKER_PRIVATE 写入路径，用 AGENT_PRIVATE 承担 worker 私有语义。Migration 063 不回滚。
- **与 F095（并行）的耦合**：F095 改 `behavior_workspace.py` / `agent_decision.py` / behavior 模板；F094 严禁动这些文件。**最终 Final review 前必须 rebase F095 完成的 master 后跑全量回归**，验证两条 Feature 合并后无冲突。

---

## 3. 范围 / Out of Scope

### 3.1 In Scope（F094 范围内）

| 块 | 内容 | 类型 |
|----|------|------|
| A | spec 阶段 4 项实测 + Codex 7 finding 闭环（§2.1）作为 Plan 阶段事实基础 | 制品 |
| B | worker dispatch 路径**废弃 WORKER_PRIVATE namespace 创建**，改用 AGENT_PRIVATE（统一 worker / main，Codex HIGH-1 闭环）；Worker 写 fact 默认进 AGENT_PRIVATE；Worker recall 优先查 AGENT_PRIVATE，fallback PROJECT_SHARED；同 project 不同 worker 私有 fact 严格隔离 | 新行为（核心） |
| C | RecallFrame 模型 + DDL 加 `queried_namespace_kinds` + `hit_namespace_kinds` 双字段；`memory_maintenance_runs` DDL **前置补 `idempotency_key` + `requested_by` 列**（Codex MED-3 闭环）；`MemoryNamespace` 表**前置加 `(project_id, agent_runtime_id, kind)` unique 约束 + 数据清理**（Codex MED-4 闭环）；main + worker 路径都填新字段；`list_recall_frames` API 加 `agent_runtime_id` + `queried_namespace_kind` + `hit_namespace_kind` 过滤维度 | 新行为（数据补全 + schema 修复） |
| D | 删除 `_default_worker_memory_recall_preferences()` 硬编码；改在 module-level 常量字典 `_WORKER_MEMORY_RECALL_DEFAULTS`；保留 baseline `{**defaults, **existing}` merge 顺序（Codex LOW-7 闭环） | 行为零变更（清理） |
| E | `octo memory migrate-094 --dry-run` / `--apply` / `--rollback`：CLI 接口完整（与 config migrate 模板对齐）+ 幂等（依赖块 C 补好的 idempotency_key 列） + 底层为 no-op（GATE_DESIGN 锁定降级方案 A，不做反推） | CLI 工具 + no-op 实现 |
| ALL | 新单测覆盖块 B / C / D / E；e2e_smoke 通过；全量回归 0 regression vs F093 baseline (`284f74d`) | 验证 |
| ALL | 完成时产出 `completion-report.md` 含「实际 vs 计划」对照 + Codex finding 闭环表 + 与 F095 并行的合并结果 | 制品 |

### 3.2 Out of Scope（明确排除）

| 排除项 | 落入 Feature |
|--------|--------------|
| Worker session / turn 写入路径（已 F093 完成） | F093 ✅ |
| Worker behavior 4 层覆盖；加 SOUL/HEARTBEAT/BOOTSTRAP；IDENTITY.worker.md 默认生效 | F095（并行） |
| **`memory_candidates.promote` 路径修改 / "AGENT_PRIVATE → PROJECT_SHARED" fact 流转**（Codex HIGH-2 闭环）：F094 不改 promote 工具实现；不引入新 fact namespace 移动 API | F095/F096 / 单独 namespace lifecycle Feature 评估 |
| **WORKER_PRIVATE namespace 历史数据迁移到 AGENT_PRIVATE**（Codex HIGH-1 衍生）：baseline 中已存在的 worker_private namespace records（namespace_id 维度，非 SoR 数据）保留不动；新 worker dispatch 不再创建 worker_private kind | 永远不做（F094 决策） |
| **scope_id 字符串编码改造**（去 `/private/{owner}` 痕迹）：F094 保留 baseline 形态；上游靠 MemoryNamespace 表元数据查询 | 永远不做（F094 决策） |
| Worker Recall Audit & Provenance；Web Memory Console 加 agent 视角 | F096 |
| Subagent Mode（H3-A）显式建模；SubagentDelegation；完成后清理 SubagentSession | F097 |
| A2A Mode（H3-B）receiver 在自己 context 工作；Worker→Worker 解绑；删除 `_enforce_child_target_kind_policy` | F098 |
| Ask-Back 工具（`worker.ask_back` / `worker.request_input`）；A2AConversation source_type 泛化 | F099 |
| Decision Loop Alignment；F090 D1 双轨收尾 | F100 |
| WorkerProfile 与 AgentProfile 完全合并 | F107 |
| 复活 WORKER_PRIVATE 写入路径（F063 抛弃 + F094 块 B 废弃，永远不复活） | 永远不做 |
| 回滚 Migration 063（F063 完成的存量迁移不动） | 永远不做 |
| F094 范围内启用 SessionMemoryExtractor 在 worker 上真跑（F093 §B4 已锁定，F094 不做） | F094 之后单独 Feature 评估 |
| 修改 `memory_sor` 表加 agent_id 字段（agent 维度仍由 MemoryNamespace 表 + namespace_id 关联表达） | 永远不做（F094 决策） |

---

## 4. 关键约束 / 不变量（Non-Functional）

### NFR-1 行为零变更（块 D）+ 新行为可观测（块 B/C/E）

- **块 D**：`_default_worker_memory_recall_preferences` 硬编码迁移到 module-level 常量——必须**值完全一致**（5 个 key/value）+ **保留 `{**defaults, **existing}` merge 顺序**；行为零变更（Codex LOW-7 闭环）。
- **块 B/C/E**：是新行为，必须有：
  - 单测覆盖 Worker 写 fact 默认 AGENT_PRIVATE namespace
  - 单测覆盖同 project 不同 worker 私有 fact 隔离（A 写入后 B recall 不命中）
  - 单测覆盖 worker dispatch 路径**不再创建 WORKER_PRIVATE namespace**
  - 单测覆盖 RecallFrame `queried_namespace_kinds` + `hit_namespace_kinds` 持久化 round-trip
  - 单测覆盖 `list_recall_frames` 新过滤维度
  - 单测覆盖 `memory_maintenance_runs` DDL 含 idempotency_key 列（用 init_memory_db() 真实建库，不手写补丁）
  - 单测覆盖 `memory_namespaces` 三元组 unique 约束生效
  - 单测覆盖 migrate-094 dry-run / apply / 幂等性 / 回滚

### NFR-2 新行为可审计

worker memory 写入 / recall 路径 emit 事件，control_plane 可按 `agent_runtime_id` + `namespace_kind` 双维度查询。事件 payload 与 RecallFrame 字段语义一致。

### NFR-3 F091 状态映射 raise 模式沿用 + namespace 解析失败显式 raise

- 沿用 F091 已建立的 raise 模式
- **F094 新约束**：所有 namespace 解析失败必须显式 raise，不允许 silent fallback 到 PROJECT_SHARED；具体场景：
  - 无法从 context 推断 `agent_runtime_id` 但代码路径要求 AGENT_PRIVATE namespace
  - MemoryNamespace 查询返回多条但语义要求唯一（块 C unique 约束就位后理论上不应发生，发生即数据污染）
  - Plan 阶段补充其他场景

### NFR-4 与 F092 SpawnChildResult 三态无影响

F094 不动 `plane.spawn_child` 编排入口。Worker AgentProfile 创建路径（`_create_worker_agent_profile`）走的是 dispatch service / capability_pack，不直接走 spawn_child。Plan 阶段需在 `plan.md` 显式确认。

### NFR-5 与 F093 Worker session 字段无影响

F094 不读不写 `AgentSession.rolling_summary` / `memory_cursor_seq`（这两槽位 F093 已就位等 F094 接入，但**真正接入留给后续 Feature**——F094 §3.2 已显式排除"启用 SessionMemoryExtractor 在 worker"）。

### NFR-6 migrate-094 不可逆约束

- `--apply` 前必须先跑 `--dry-run` + 用户拍板（CLAUDE.local.md §"执行约束"）
- 必须 idempotent（用 `memory_maintenance_runs` + `idempotency_key="migration_094_worker_private"`）
- 必须可回滚（rollback 命令删除审计记录，可重新 apply）
- 命令分三段：`octo memory migrate-094 --dry-run` / `--apply` / `--rollback <run_id>`

### NFR-7 每 Phase 后回归 0 regression vs F093 baseline (`284f74d`)

- e2e_smoke 必过（pre-commit hook 自动跑 180s portable watchdog）
- 全量 unit + integration 回归 0 regression（块 B/C/D/E 新增测试 OK，已有测试不能掉）

### NFR-8 每 Phase 前 Codex review + Final cross-Phase Codex review

- 命中"重大架构变更 commit 前"节点（涉及跨包接口 + 新行为 + 数据迁移 + DDL schema）
- F091/F092/F093 实证 Final Review 抓真 bug 价值显著——F094 必走

### NFR-9 与 F095 并行约束

- **严禁动 F095 改动文件**：`behavior_workspace.py` / `agent_decision.py` / behavior 模板
- 每 Phase commit 前 grep 自检（实施阶段 task list 含此项）
- **Final review 前必须 rebase F095 完成的 master 后跑全量回归**（验证两个并行 Feature 合并无冲突）；若 F095 此时未完成，rebase 步骤推到 F095 完成后再做（在主 session 归总报告中显式说明）

### NFR-10 不主动 push origin/master

按 CLAUDE.local.md §"Spawned Task 处理流程"：归总报告等用户拍板。F094 不主动 push。

### NFR-11 块 C 前置 schema 修复优先级（Codex MED-3 / MED-4 闭环）

- **必须前置**：`memory_maintenance_runs` 补列 + `memory_namespaces` 三元组 unique 约束在 Phase C 完成
- Phase B 启动前块 C 验收必须 PASS（B 的隔离断言依赖 unique 约束生效）
- Phase E 启动前块 C 必须 PASS（E 的 idempotency 依赖 idempotency_key 列存在）

---

## 5. Acceptance Criteria（验收 checklist）

### 块 A 验收（spec 阶段产出 / Plan 阶段引用）

- [x] **A1** baseline MemoryNamespaceKind 三 enum 真实运行状态对照表（§2.1.1，含 Codex HIGH-1 校正）
- [x] **A2** RecallFrame 字段 + 写入路径当前状态（§2.1.2，含 Codex MED-5 校正）
- [x] **A3** recall preferences 硬编码 + merge 顺序（§2.1.3，含 Codex LOW-7 校正）
- [x] **A4** 存量 facts namespace 分布 + F063 历史决策影响 + audit metadata 真实状态（§2.1.4，含 Codex LOW-6 校正）
- [x] **A5** memory_maintenance_runs DDL 缺列发现（§2.1.5，Codex MED-3 闭环）
- [x] **A6** MemoryNamespace 三元组 unique 缺约束发现（§2.1.6，Codex MED-4 闭环）

### 块 B 验收（核心新行为，含废弃 WORKER_PRIVATE 路径）

- [ ] **B1** Worker 写 fact 默认进 AGENT_PRIVATE namespace（按 context.agent_runtime_id 推断）
- [ ] **B2** 同 project 下 Worker A 写的 AGENT_PRIVATE fact 不出现在 Worker B 的 recall 结果中
- [ ] **B3** Worker recall 优先查 AGENT_PRIVATE，fallback PROJECT_SHARED；命中两个 namespace 时按 hit score 合并
- [ ] **B4** 主 Agent recall 不命中任何 worker 的 AGENT_PRIVATE fact
- [ ] **B5** 写 fact 路径 emit 事件含 `agent_runtime_id` / `namespace_kind` / `namespace_id` 三字段
- [ ] **B6** 单测覆盖 B1-B5
- [ ] **B7**（**Codex HIGH-1 闭环**）worker dispatch 创建 namespace 路径（agent_context.py:2323-2327）改为：worker / main 都生成 AGENT_PRIVATE namespace；grep 全 codebase 确认 worker 路径无 `MemoryNamespaceKind.WORKER_PRIVATE` 写入；现有 baseline 数据中 worker_private kind 的 namespace records 保留不动（不迁不改不删）

### 块 C 验收（数据补全 + 前置 schema 修复）

- [ ] **C1** RecallFrame 模型加 `queried_namespace_kinds: list[MemoryNamespaceKind]` + `hit_namespace_kinds: list[MemoryNamespaceKind]` 双字段
- [ ] **C2** `recall_frames` 表 DDL 加对应两列（TEXT NOT NULL DEFAULT '[]'）
- [ ] **C3** main + worker recall 路径都填双字段；`hit_namespace_kinds` 从 `memory_hits[i].metadata.namespace_kind` 归一化生成
- [ ] **C4** `list_recall_frames` API 接受 `agent_runtime_id` / `queried_namespace_kind` / `hit_namespace_kind` 过滤参数
- [ ] **C5** 单测覆盖 C1-C4 + RecallFrame 持久化 round-trip 含双字段
- [ ] **C6**（**Codex MED-3 闭环**）`memory_maintenance_runs` DDL 补 `idempotency_key TEXT NOT NULL DEFAULT ''` + `requested_by TEXT NOT NULL DEFAULT ''` 列；测试用真实 `init_memory_db()` 验证（不手写补丁）；F063 migration 在新 schema 下可幂等运行
- [ ] **C7**（**Codex MED-4 闭环**）`memory_namespaces` 表加 `UNIQUE(project_id, agent_runtime_id, kind)` 约束（IF NOT EXISTS 安全 ALTER）；既有数据若有同三元组重复，按 `created_at DESC` 保留最新 + 其他 `archived_at` 归档；store upsert 在三元组冲突时走 unique 约束失败处理（NFR-3 显式 raise）

### 块 D 验收（行为零变更清理）

- [ ] **D1** `_default_worker_memory_recall_preferences` 函数从 codebase 中删除（grep 0 命中）
- [ ] **D2** module-level 常量 `_WORKER_MEMORY_RECALL_DEFAULTS` 含 5 个 key 与 baseline 完全一致：`{prefetch_mode="hint_first", planner_enabled=True, scope_limit=4, per_scope_limit=4, max_hits=8}`
- [ ] **D3** Worker recall 实际使用的 5 参数与 baseline 完全一致（行为零变更断言）
- [ ] **D4** 单测覆盖 D2-D3
- [ ] **D5**（**Codex LOW-7 闭环**）`existing_profile.context_budget_policy["memory_recall"]` 优先级高于 module-level defaults（保留 baseline `{**defaults, **existing}` merge 顺序）；单测构造 existing_profile 含部分自定义值，断言最终值是 defaults + existing override 的合并结果

### 块 E 验收（CLI 工具 + no-op 实现，GATE_DESIGN 锁定降级方案 A）

- [ ] **E1** `octo memory migrate-094 --dry-run` 输出 `total_facts_to_migrate=0` + `reason="F063_legacy_no_provenance"` + 当前 namespace 分布快照；库内任何记录未变更
- [ ] **E2** `octo memory migrate-094 --apply` 写入一条 `memory_maintenance_runs` 审计记录（`idempotency_key="migration_094_worker_private"` / `kind="migration"` / `metadata.no_op=true` / `metadata.reason="F063_legacy_no_provenance"`），**SoR 表零修改**；前置依赖块 C C6 已通（idempotency_key 列存在）
- [ ] **E3** 第二次 `--apply` 短路返回（输出"已执行过（run_id=...）"），库内不变
- [ ] **E4** `octo memory migrate-094 --rollback <run_id>` 删除审计记录，apply 可再次执行（rollback 路径可用）
- [ ] **E5** CLI 模板与 `config migrate` 一致：dry-run / apply / rollback 三段式 + click async + 同形 console 输出
- [ ] **E6** 单测覆盖 E1-E5（含 idempotency 短路 + rollback 后再 apply 路径）

### 全局验收

- [ ] **G1** 全量回归 vs F093 baseline (`284f74d`)：块 B/C/D/E 测试新增 + 块 D 0 regression
- [ ] **G2** e2e_smoke 每 Phase 后 PASS（pre-commit hook 验证）
- [ ] **G3** 每 Phase Codex review 闭环（0 high 残留）
- [ ] **G4** **Final cross-Phase Codex review** 通过（输入 plan.md + 全 Phase commit diff）
- [ ] **G5** **Final 阶段 rebase F095 完成的 master 后再跑一次全量回归**（验证两个并行 Feature 合并无冲突）
- [ ] **G6** **completion-report.md** 已产出 @ `.specify/features/094-worker-memory-parity/completion-report.md`，含「实际 vs 计划」对照 + Codex finding 闭环表 + 与 F095 并行的合并结果
- [ ] **G7** F096 (Worker Recall Audit) 接口点说明：
  - RecallFrame `queried_namespace_kinds` / `hit_namespace_kinds` 双字段语义
  - F096 audit endpoint 怎么按 `(agent_runtime_id, hit_namespace_kind)` 维度过滤
  - Memory Console agent 视角的数据基础（RecallFrame + MemoryNamespace 关联查询模式）
- [ ] **G8** **Phase 跳过必须显式归档**（若发生）

---

## 6. F096 接口点（前向声明）

> 服务于 G7 验收项，让下一个 Feature 接入零返工。

### 6.1 给 F096（Worker Recall Audit & Provenance）

- **RecallFrame 双字段**（块 C 产出）：`queried_namespace_kinds` 用于"曾查过私有"审计；`hit_namespace_kinds` 用于"实际命中私有"审计
- **list_recall_frames API 新过滤维度**（块 C 产出）：`agent_runtime_id` + `queried_namespace_kind` + `hit_namespace_kind`；F096 Memory Console agent 视角直接复用此 endpoint
- **事件契约**（块 B 产出）：MEMORY_FACT_WRITTEN / MEMORY_RECALL_COMPLETED 含 `agent_runtime_id` / `namespace_kind` / 双 list 字段；F096 audit log 直接订阅
- **Schema 基础**（块 C 产出）：`memory_maintenance_runs` 含 idempotency_key / `memory_namespaces` 含三元组 unique 约束——F096 audit 与 lifecycle 操作可信
- **F094 不动**：F096 范围内的 audit endpoint 实现 / Memory Console UI / agent 视角切换

### 6.2 给 F107（WorkerProfile 与 AgentProfile 完全合并）

- **块 D 产出**：worker memory_recall 默认值已迁到 module-level 常量字典 `_WORKER_MEMORY_RECALL_DEFAULTS`；F107 合并 WorkerProfile 时只需保留这个常量，不需要再重构 recall preferences 注入路径

---

## 7. Phase 顺序（先简后难，与用户 prompt 一致 + Codex MED-3/MED-4 前置约束）

> F091 / F092 / F093 实证"先建立 baseline 信心，再做主责改动"是好 pattern。F094 沿用，但用户 prompt 已给出顺序：A → C → D → B → E。Codex review 闭环后 Phase 顺序保持，但 Phase C 范围扩大含前置 schema 修复（NFR-11）。

1. **Phase A（spec 阶段实测，已完成）**：4 项实测发现 + Codex 7 finding 闭环固化到 §2.1 ✅（spec.md commit 即完成）
2. **Phase C（块 C，最简单的代码改动 + 前置 schema 修复）**：
   - C 子任务 1：`memory_maintenance_runs` DDL 补 idempotency_key + requested_by 列（前置）
   - C 子任务 2：`memory_namespaces` 加 (project_id, agent_runtime_id, kind) unique 约束 + 既有数据清理（前置）
   - C 子任务 3：RecallFrame.queried_namespace_kinds / hit_namespace_kinds 字段 + DDL + 写入路径 + API 过滤维度
3. **Phase D（块 D，行为零变更清理）**：删除 `_default_worker_memory_recall_preferences`，迁移到 module-level 常量字典；保留 merge 顺序。行为零变更，回归断言简单。
4. **Phase B（块 B，核心新行为）**：worker dispatch 路径废弃 WORKER_PRIVATE namespace 创建，统一 AGENT_PRIVATE；写默认推断 + recall 优先级 + 隔离断言。**前置依赖 Phase C C7（unique 约束就位）**。
5. **Phase E（块 E，不可逆迁移）**：migrate-094 CLI。**前置依赖 Phase C C6（idempotency_key 列就位）**。
6. **Phase F（验证收尾）**：跑 e2e_smoke + 全量回归 + rebase F095 完成的 master + Final cross-Phase Codex review + 写 completion-report.md。

具体到 commit 粒度的子拆分留给 Plan 阶段。

---

## 8. 待 Plan 阶段决策的开放点

> 这些点不是 ambiguity，是 Plan 阶段必须落地的开放接力。

1. ~~Open-1（Gap-1 决策）~~：✅ **GATE_DESIGN + Codex 锁定降级方案 A**
2. **Open-2（Gap-2 决策）**：写默认 namespace 推断时机 - propose_write 调用栈实测后选 (a)/(b)/(c)
3. ~~Open-3（Gap-3 决策）~~：✅ **Codex HIGH-2 闭环**——F094 不动 promote，US1 Acceptance 已删除相关条目
4. ~~Open-4（Gap-4 决策）~~：✅ **Codex MED-5 锁定双字段** `queried_namespace_kinds` + `hit_namespace_kinds`
5. ~~Open-5（Gap-5 决策）~~：✅ **spec 锁定** module-level 常量 `_WORKER_MEMORY_RECALL_DEFAULTS`
6. ~~Open-6（Gap-6 决策）~~：✅ **GATE_DESIGN + Codex 锁定**——SoT 在 MemoryNamespace 表三元组；scope_id 字符串保留 baseline 形态
7. **Open-7（事件名）**：复用现有 MEMORY_* 事件 + 加字段，还是新建？倾向复用（F093 实证）
8. **Open-8（block C schema migration 模式）**：sqlite_init.py 直接建表无 alembic—— Plan 阶段确认补列 + unique 约束的实施模式（IF NOT EXISTS ALTER TABLE / 检查 schema_version / 还是新建迁移文件）
9. **Open-9（block C 既有重复数据清理策略）**：`memory_namespaces` 三元组 unique 约束就位前如有重复记录（baseline worker_private + agent_private 双轨可能造成同 (project_id, agent_runtime_id) 多记录），清理顺序：(a) archived_at 标记 / (b) 直接 DELETE / (c) 保留并加 unique 约束失败处理
10. **Open-10（与 F095 rebase 时机）**：F094 完成时若 F095 未完成，rebase 步骤推到 F095 完成后；具体处理在主 session 归总报告中说明
11. **Open-11（worker baseline 已存在的 WORKER_PRIVATE namespace records 保留处理）**：F094 块 B 废弃新写入路径，但 baseline 数据库可能已有 `kind=worker_private` 的 namespace records——保留方式：(a) 完全保留不动 / (b) archived_at 标记 / (c) 数据迁移到 AGENT_PRIVATE。**spec 倾向 (a) 完全保留**——简单、不破坏向后兼容；Plan 阶段最终确认

---

## 9. 风险（Risks）

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| ~~Gap-1 F063 抹掉痕迹无法恢复~~ | ~~高~~ | ✅ GATE_DESIGN + Codex 已锁定降级方案 A，风险消解 |
| 块 B 写默认 namespace 推断点选错（断在 worker 工具未传 agent_runtime_id 时） | 高 | NFR-3 显式 raise；单测覆盖 namespace 解析失败场景 |
| 块 B 废弃 WORKER_PRIVATE 路径漏改散落 reference（task_service.py:1946 等） | 高 | 块 B 验收 B7 强制 grep 全 codebase 自检；Codex per-Phase review 抓 |
| 块 C 加 namespace 字段触发持久化 schema 变更，与现有 RecallFrame 数据不兼容 | 中 | Plan 阶段评估 store 层 migration 模式；schema_version bump + alembic-style migration 独立 commit |
| 块 C `memory_maintenance_runs` 补列时 F063 已存在的 audit 记录是否兼容 | 中 | DEFAULT '' 让既有记录拿空值；新 record 必填；Plan 阶段验证 |
| 块 C `memory_namespaces` 三元组 unique 约束失败（既有重复数据）| 中 | 数据清理策略 Plan 阶段定（Open-9）；archived_at 标记是首选 |
| 块 D module-level 默认值与硬编码值不一致（隐式行为变更） | 中 | 单测断言所有 5 个 key/value 完全相等 + merge 顺序保留 |
| 块 D 删硬编码函数破坏 existing profile override（merge 顺序错） | 中 | NFR-1 D5 验收 + 单测专项 |
| 块 E 与 F063 idempotency_key 命名冲突 | 低 | 已验证不同（migration_094_worker_private vs migration_063_scope_partition）|
| 块 E 写 audit 记录但底层 no-op 造成 audit 表污染 | 低 | metadata.no_op=true 显式标注；F096 audit query 可识别忽略 |
| 与 F095 并行的隐式耦合 | 中 | NFR-9 grep 自检；Final review 前 rebase + 全量回归 |
| Codex Final review 抓到设计层 high finding 推迟 | 中 | 沿用 F091/F092/F093 经验，Phase 前 review 暴早；Final review 兜底 |

---

## 10. 索引（References）

- **CLAUDE.local.md** § "M5 / M6 战略规划"（F094 在阶段 1 的位置）
- **CLAUDE.local.md** § "三条核心设计哲学"（H2 完整对等性 + Memory 轴）
- **CLAUDE.local.md** § "F092/F091/F090 实施记录"（前置 baseline 已清架构债）
- **CLAUDE.local.md** § "F093 实施记录"（Plan 阶段实测 pattern + 完成的 Worker Session 轴）
- **CLAUDE.local.md** § "工作流改进"（completion-report 强制 + Final cross-Phase Codex review）
- **CLAUDE.local.md** § "执行约束"（migrate-094 必须 dry-run + 用户拍板）
- **CLAUDE.local.md** § "Spawned Task 处理流程"（不主动 push）
- **`.specify/features/093-worker-full-session-parity/spec.md`**（spec.md 格式范本）
- **`.specify/features/093-worker-full-session-parity/completion-report.md`**（baseline 完成点）
- **`packages/core/src/octoagent/core/models/agent_context.py:131-134`**（MemoryNamespaceKind 定义）
- **`packages/core/src/octoagent/core/models/agent_context.py:430-447`**（RecallFrame 模型）
- **`packages/core/src/octoagent/core/store/sqlite_init.py:536-550`**（memory_namespaces DDL）
- **`packages/memory/src/octoagent/memory/store/sqlite_init.py:18-33`**（memory_sor 表 schema）
- **`packages/memory/src/octoagent/memory/store/sqlite_init.py:190-208`**（memory_maintenance_runs DDL，含 Codex MED-3 缺列发现）
- **`packages/memory/src/octoagent/memory/migrations/migration_063_scope_partition.py`**（F063 迁移代码）
- **`packages/provider/src/octoagent/provider/dx/config_commands.py:897-964`**（migrate CLI 模板）
- **`apps/gateway/src/octoagent/gateway/services/agent_context.py:183-195`**（_default_worker_memory_recall_preferences 硬编码）
- **`apps/gateway/src/octoagent/gateway/services/agent_context.py:2323-2327`**（baseline worker→WORKER_PRIVATE / main→AGENT_PRIVATE 双轨创建路径，块 B 废弃目标）
- **`apps/gateway/src/octoagent/gateway/services/agent_context.py:2693-2707`**（merge 顺序 `{**defaults, **existing}`）
- **`apps/gateway/src/octoagent/gateway/services/agent_context.py:800-825`**（RecallFrame 写入路径）
- **`apps/gateway/src/octoagent/gateway/services/agent_context.py:2986-2995, 3105-3114`**（hit namespace_kind 通过 scope_entry_map 注入到 memory_hits[i].metadata）
- **`apps/gateway/src/octoagent/gateway/services/control_plane/session_service.py:225`**（list_recall_frames API）
- **`apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py:63`**（F094 接入注释 + 显式排除 worker extractor）

---

**Spec 生成完毕。** 等待 GATE_DESIGN 用户审查后进入 Phase 4（plan）。

---

## 11. Clarifications

### Session 2026-05-09 GATE_DESIGN

主 session 向用户呈递 3 项关键决策，用户拍板结果：

| 决策 | 用户回应原文 | 落地章节 |
|------|--------------|----------|
| **决策 1**：F063 抹掉痕迹的存量 fact 是否设计反推算法？ | "原则上我可以接受不支持迁移，但是，我们需要新的版本的功能是完善的统一一致的，结构简单清晰的。" | §0 + §1 US4 + §2.2 Gap-1 + §3.1 块 E + §5 块 E |
| **决策 2**：AGENT_PRIVATE scope_id 编码方式？ | "假如不影响用户实际的功能的话，我们以架构简单和维护性为追求的目标。" | §0 + §2.2 Gap-6 + §3.2 显式排除 scope_id 改造 |
| **决策 3**：Phase 顺序 A → C → D → B → E？ | "决策 3 没问题。" | §7 Phase 顺序 |

### Session 2026-05-09 Codex pre-Phase Adversarial Review（model_reasoning_effort=high）

Codex review 抓 7 finding，全部闭环：

| Finding | 严重度 | 处理决议 | 落地章节 |
|---------|--------|----------|----------|
| **HIGH-1**：AGENT_PRIVATE 设计建立在错误 baseline 上（worker→WORKER_PRIVATE / main→AGENT_PRIVATE 双轨已生效） | HIGH | **接受** | §0（"统一私有 namespace 路径"段）+ §2.1.1 重写 + §3.1 块 B 范围扩大 + §3.2 显式排除复活 + §5 B7 验收新增 + §9 Risks 新增 |
| **HIGH-2**：US1 promote 验收依赖的 SoR namespace promote API 不存在 | HIGH | **接受** | §1 US1 删除 Acceptance 5 + §2.2 Gap-3 删除 + §3.2 显式排除 promote + §8 Open-3 删除 |
| **MED-3**：memory_maintenance_runs DDL 缺 idempotency_key / requested_by 列 | MED | **接受** | §2.1.5 新增 + §3.1 块 C 范围扩大（前置 task）+ §5 C6 验收 + NFR-11 + §9 Risks |
| **MED-4**：MemoryNamespace 三元组 unique 约束当前不存在 | MED | **接受** | §2.1.6 新增 + §3.1 块 C 范围扩大（前置 task）+ §5 C7 验收 + NFR-11 + §9 Risks + §8 Open-9 |
| **MED-5**：RecallFrame namespace 字段 list[NamespaceKind] 语义混淆（queried vs hit） | MED | **接受** | §1 US3 改为双字段 + §2.1.2 描述纠正 + §2.2 Gap-4 锁定双字段 + §3.1 块 C 描述 + §5 C1-C5 验收 + §6.1 |
| **LOW-6**：F063 metadata 描述不准确（migrated_scope_ids 是局部变量未持久化） | LOW | **接受** | §0 对齐声明 + §2.1.4 + §2.2 Gap-1 描述纠正 |
| **LOW-7**：块 D 行为零变更要保留 merge 顺序 | LOW | **接受** | §2.1.3 描述 + §1 US2 Acceptance 2 新增 + §5 D5 验收新增 + §9 Risks |

**Final 阶段必须再次跑 Codex cross-Phase review**（按 NFR-8）。

### 剩余开放点（Plan 阶段必落地，不构成 ambiguity）

- §8 Open-2 / Open-7 / Open-8 / Open-9 / Open-10 / Open-11（共 6 项；Open-1/3/4/5/6 已锁定划掉）
- §2.2 Gap-2 / Gap-5（Gap-1/3/4/6 已锁定）

**spec 验收判断（A1-A6 / B1-B7 / C1-C7 / D1-D5 / E1-E6 / G1-G8）**：每个验收项都有明确 pass/fail 判据，无需澄清。

**结论**：spec 质量合格，0 ambiguity，0 CRITICAL 问题；GATE_DESIGN 用户决策 + Codex pre-Phase review 全部闭环，可进入 Plan 阶段。
