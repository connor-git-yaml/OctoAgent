# Tasks: F094 Worker Memory Parity

**Feature Branch**: `feature/094-worker-memory-parity`
**Baseline**: `284f74d` (F093 完成点) → `124ed91` (plan.md commit 后)
**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)
**Status**: Draft（待 GATE_TASKS 用户拍板）

---

## 0. 任务表说明

| 列 | 含义 |
|----|------|
| **Task ID** | Phase 字母 + 序号（C1 / B0 等）|
| **类型** | impl / test / cleanup / commit |
| **文件路径** | 相对仓库根；含 line 范围（仅 impl）|
| **依赖** | 前置 task ID list；空 = 无前置 |
| **预估行数** | 含 impl + 单测；不含 boilerplate |
| **验收锚点** | spec.md / plan.md 中对应验收项 |

**强制顺序约束**（plan §1）：
- Phase C 必须先于 Phase B（B7 需 C2 unique 约束 + C3 RecallFrame 字段就位）
- Phase C 必须先于 Phase E（E2 需 C6 idempotency_key 列就位）
- Phase D 与 Phase C 弱耦合（可在 C 后立即跑）
- Phase E 必须最后（最复杂 + 不可逆）
- 每 Phase 内 task 强依赖 = 严格顺序；弱依赖 = 同 commit 可并发

---

## Phase C：数据补全 + 前置 schema 修复（10 task，~500-600 行）

### C1（impl - schema setup）memory_maintenance_runs DDL 补列

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/memory/src/octoagent/memory/store/sqlite_init.py`（DDL 字符串 + `init_memory_db` 函数）|
| 改动 | (1) `_MEMORY_MAINTENANCE_RUNS_DDL` 字符串加 `idempotency_key TEXT NOT NULL DEFAULT ''` + `requested_by TEXT NOT NULL DEFAULT ''`；(2) `init_memory_db` 加启动时 PRAGMA table_info 检测 + ALTER TABLE ADD COLUMN IF 缺列 |
| 依赖 | 无 |
| 预估行数 | ~30 行 impl |
| 验收 | spec C6 / plan §0.3 / NFR-11 |

### C2（impl - schema setup）memory_namespaces 三元组 partial unique index + dedupe

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`（DDL 字符串 + 对应 init 函数）|
| 改动 | (1) 加 `CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_namespaces_unique_triple ON memory_namespaces(project_id, agent_runtime_id, kind) WHERE archived_at IS NULL`；(2) init 函数前先扫描重复（`SELECT project_id, agent_runtime_id, kind, COUNT(*) GROUP BY ... HAVING cnt > 1`）；(3) 每组重复保留 `created_at DESC` 最新 1 条，其他写 `archived_at = now()` + metadata 加 `archived_reason="F094_dedupe_unique_constraint_setup"` |
| 依赖 | 无 |
| 预估行数 | ~50 行 impl |
| 验收 | spec C7 / plan §0.4 / NFR-3 |

### C3（impl - schema setup）recall_frames DDL 加双字段

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`（recall_frames DDL）|
| 改动 | (1) 加 `queried_namespace_kinds TEXT NOT NULL DEFAULT '[]'` + `hit_namespace_kinds TEXT NOT NULL DEFAULT '[]'` 列；(2) init 函数加 ALTER TABLE 兜底已存在库 |
| 依赖 | 无 |
| 预估行数 | ~25 行 impl |
| 验收 | spec C2 / plan §1 Phase C C3 |

### C4（impl - model）RecallFrame 模型加双字段

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/core/src/octoagent/core/models/agent_context.py:430-447`（RecallFrame 类）|
| 改动 | 加 `queried_namespace_kinds: list[MemoryNamespaceKind] = Field(default_factory=list)` + `hit_namespace_kinds: list[MemoryNamespaceKind] = Field(default_factory=list)` |
| 依赖 | C3（DDL 字段必须先在）|
| 预估行数 | ~10 行 impl |
| 验收 | spec C1 |

### C5（impl - store roundtrip）RecallFrame save/get 持久化双字段

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py`（RecallFrame 持久化方法）|
| 改动 | (1) `save_recall_frame` 序列化新字段（list[Enum] → JSON）；(2) `_row_to_recall_frame` 反序列化（JSON → list[MemoryNamespaceKind]）；(3) 反序列化失败显式 raise（NFR-3）|
| 依赖 | C3, C4 |
| 预估行数 | ~30 行 impl + ~40 行单测 |
| 验收 | spec C5 |

### C6（impl - write path）RecallFrame 写入路径填双字段

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py:800-825`（RecallFrame 构造点）|
| 改动 | (1) `queried_namespace_kinds = sorted({n.kind for n in namespaces})` from resolved namespaces；(2) `hit_namespace_kinds = sorted({hit.metadata.get("namespace_kind") for hit in hits if hit.metadata and hit.metadata.get("namespace_kind")})`；(3) main + worker 共用此路径 |
| 依赖 | C4, C5 |
| 预估行数 | ~20 行 impl |
| 验收 | spec C3 |

### C7（impl - API filter）list_recall_frames 加新过滤维度

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/session_service.py:225` 附近 + 上游 store API |
| 改动 | (1) `list_recall_frames` 加 `agent_runtime_id` / `queried_namespace_kind` / `hit_namespace_kind` 参数；(2) JSON list contains 过滤实现；(3) RecallFrameItem 投影模型加新字段 |
| 依赖 | C5 |
| 预估行数 | ~50 行 impl |
| 验收 | spec C4 |

### C7b（impl - store API archived 过滤）（**Codex MED-3 闭环**）

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py:863-890`（list_memory_namespaces + get_memory_namespace）|
| 改动 | (1) `list_memory_namespaces(*, include_archived: bool = False)` 默认 WHERE 加 `archived_at IS NULL`；(2) `get_memory_namespace(namespace_id, *, include_archived: bool = False)` 同步加；(3) 调用方迁移：业务路径默认 active；控制台/审计显式 include_archived=True；(4) 影响调用点：`session_memory_extractor.py:740, 750` / `session_service.py:222` 等——业务路径不传新参（保持默认 False） |
| 依赖 | C2（archived 数据已就位）|
| 预估行数 | ~40 行 impl + ~30 行单测（构造 archived 数据 + 默认/显式 include 双场景）|
| 验收 | plan §0.4 store API 必须同步过滤 archived |

### C8（test - integration）schema + RecallFrame + API 单测

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | `octoagent/packages/core/tests/test_agent_context_store.py` + `octoagent/apps/gateway/tests/test_session_service.py`（如有）|
| 改动 | (1) DDL 改后新建库 + 已存在库（mock ALTER）双场景 OK；(2) memory_maintenance_runs 含 idempotency_key 后 F063 migration 可幂等；(3) memory_namespaces 三元组 unique 约束生效（重复 insert 触发 IntegrityError）；(4) 既有重复 archived_at 标记后不影响新插入；(5) RecallFrame 持久化 round-trip 含双字段；(6) list_recall_frames 新过滤维度命中正确；(7) list_memory_namespaces 默认 active path / 显式 include_archived 双场景 |
| 依赖 | C1-C7b 全部 |
| 预估行数 | ~150 行 test |
| 验收 | spec C1-C7 + plan §0.4 |

### C9（test - regression）跑全量回归 + e2e_smoke

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | - |
| 改动 | `cd octoagent && uv run pytest`（不限 marker）；e2e_smoke 通过 |
| 依赖 | C8 |
| 预估行数 | - |
| 验收 | spec G1, G2 / NFR-7 |

### C10（commit）Phase C commit + Codex per-Phase review 闭环

| 项 | 值 |
|----|---|
| 类型 | commit |
| 文件 | `.specify/features/094-worker-memory-parity/codex-review-phase-c.md`（finding 闭环表）|
| 改动 | (1) 跑 codex exec adversarial review (model_reasoning_effort=high) 输入 Phase C 全 diff；(2) 处理 finding 至 0 high 残留；(3) commit message 含 "Codex review: N high / M medium 已处理 / K low ignored" 格式 |
| 依赖 | C9 |
| 预估行数 | review 报告 ~100 行 doc |
| 验收 | NFR-8 |

---

## Phase D：行为零变更清理（7 task，~80-120 行）

### D1（impl - model）module-level 默认值常量定义

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/core/src/octoagent/core/models/agent_context.py`（顶部 module-level）|
| 改动 | 加常量：`DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES: dict[str, Any] = {"prefetch_mode": "hint_first", "planner_enabled": True, "scope_limit": 4, "per_scope_limit": 4, "max_hits": 8}` |
| 依赖 | 无 |
| 预估行数 | ~10 行 impl |
| 验收 | spec D2 |

### D2（impl - callsite）改 `_create_worker_agent_profile` 调用点

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py:2693-2707` |
| 改动 | (1) `from octoagent.core.models.agent_context import DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES`；(2) 替换 `_default_worker_memory_recall_preferences(worker_profile)` 为 `DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES`；(3) **保留 baseline `{**defaults, **existing}` merge 顺序**（Codex LOW-7 闭环）|
| 依赖 | D1 |
| 预估行数 | ~10 行 impl |
| 验收 | spec D2, D3, D5 |

### D3（cleanup）删除 `_default_worker_memory_recall_preferences` 函数

| 项 | 值 |
|----|---|
| 类型 | cleanup |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py:183-195`（整个函数定义）|
| 改动 | 删除函数定义（不留 stub）；grep 确认 0 调用点 |
| 依赖 | D2 |
| 预估行数 | -13 行（删除）|
| 验收 | spec D1 |

### D4（test）单测 D2 verbatim + D5 merge order

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | `octoagent/apps/gateway/tests/test_agent_context_service.py`（或对应已有测试文件）|
| 改动 | (1) **D2 verbatim**：新建 Worker AgentProfile（无 existing memory_recall），断言 `context_budget_policy["memory_recall"]` 5 个 key 与 baseline 完全一致；(2) **D5 merge order**：构造 existing_profile.memory_recall = `{"scope_limit": 10}`，走 `_create_worker_agent_profile`，断言最终 merged_memory_recall：`scope_limit=10 (existing override)` + 其他 4 key = defaults |
| 依赖 | D2, D3 |
| 预估行数 | ~50 行 test |
| 验收 | spec D4, D5 |

### D5（grep）grep 全 codebase 0 残留

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | - |
| 改动 | `grep -rn '_default_worker_memory_recall_preferences' octoagent/` 必须 0 命中 |
| 依赖 | D3 |
| 预估行数 | - |
| 验收 | spec D1 |

### D6（test - regression）跑全量回归 + e2e_smoke

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | - |
| 改动 | `cd octoagent && uv run pytest`；e2e_smoke 通过 |
| 依赖 | D4, D5 |
| 预估行数 | - |
| 验收 | spec G1, G2 |

### D7（commit）Phase D commit + Codex per-Phase review 闭环

| 项 | 值 |
|----|---|
| 类型 | commit |
| 文件 | `.specify/features/094-worker-memory-parity/codex-review-phase-d.md` |
| 改动 | Codex review + finding 闭环 |
| 依赖 | D6 |
| 预估行数 | review 报告 ~50 行 doc |
| 验收 | NFR-8 |

---

## Phase B：核心新行为 + 废弃 worker WORKER_PRIVATE 路径（10 task，~700-900 行）

### B0（impl - 前置接线）（**Codex MED-2 闭环**）ExecutionRuntimeContext.agent_runtime_id

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | (1) `octoagent/apps/gateway/src/octoagent/gateway/services/execution_context.py:26-35`（ExecutionRuntimeContext 类）；(2) `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1367-1378`（构造点）；(3) `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py:488-499`（构造点）|
| 改动 | (1) ExecutionRuntimeContext 加 `agent_runtime_id: str = ""` 默认空字段；(2) orchestrator.py 构造点：从 `dispatch_metadata` / `compiled_context.agent_runtime_id`（如 metadata 含）填；(3) worker_runtime.py 构造点：从 envelope.metadata 或 agent_runtime context 填 |
| 依赖 | 无 |
| 预估行数 | ~30 行 impl + ~30 行单测（worker context agent_session_id + agent_runtime_id 同时非空）|
| 验收 | plan §0.5 + Codex MED-2 闭环 |

### B1（impl - dispatch path）废弃 worker_private kind 创建

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py:2323-2390`（worker dispatch private namespace 创建路径）|
| 改动 | (1) line 2323-2327：`private_kind = MemoryNamespaceKind.AGENT_PRIVATE`（删除 worker / main 条件分支）；(2) line 2347-2380：删除 `if private_kind is MemoryNamespaceKind.WORKER_PRIVATE` 分支（name / description 直接用 "Agent Private" / "Agent 私有记忆命名空间。"）；(3) `build_private_memory_scope_ids` 调用时 kind 传 AGENT_PRIVATE，scope_ids 自动按 owner="main" 生成（不动 build_private_memory_scope_ids 函数本身）|
| 依赖 | C2（unique 约束就位才能验证 worker 改 AGENT_PRIVATE 后无冲突）|
| 预估行数 | -30 行（简化条件分支）|
| 验收 | spec B7 / plan §1 Phase B B1 |

### B2（cleanup - grep verify）保留 WORKER_PRIVATE 引用 grep 自检

| 项 | 值 |
|----|---|
| 类型 | cleanup |
| 文件 | - |
| 改动 | `grep -rn 'MemoryNamespaceKind.WORKER_PRIVATE\|worker_private' octoagent/apps/gateway octoagent/packages` 后逐个核对：(1) `agent_context.py:470, 473`（build_private_memory_scope_ids）—— **保留**；(2) `agent_context.py:3215-3228`（recall 排序）—— **保留**；(3) `task_service.py:1946`（_resolve_compaction_flush_scope）—— **保留**（纯读路径含历史兼容）；(4) 无新 worker_private 写入路径 |
| 依赖 | B1 |
| 预估行数 | - |
| 验收 | spec B7 |

### B3（impl - scope resolver）（**Codex HIGH-1 闭环**）新建 helper

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/_deps.py` |
| 改动 | (1) 新建 `WorkerMemoryNamespaceNotResolved(Exception)` 异常类（NFR-3）；(2) 新建 helper `async def resolve_worker_default_scope_id(stores, project_id: str, agent_runtime_id: str) -> str`：按 `(project_id, agent_runtime_id, kind=AGENT_PRIVATE)` 调 `list_memory_namespaces(include_archived=False)`（C7b 已加默认参数）；命中（应该唯一，因 C2 unique 约束）即用其 `memory_scope_ids[0]`；未命中 raise；(3) **同时**扩展 `session_memory_extractor.py:705-766 _resolve_scope_id` 接 `agent_runtime_id` + `kind_preference` 参数（为后续启用 worker session memory extraction 留接口；F094 范围内 `_EXTRACTABLE_SESSION_KINDS = frozenset({MAIN_BOOTSTRAP})` **不变**） |
| 依赖 | B0, B1, C7b（list_memory_namespaces 默认过滤 archived 必须就位）|
| 预估行数 | ~60 行 impl + ~50 行单测（命中 / 未命中 / archived 不算）|
| 验收 | spec B1 / plan §0.1 + §1 Phase B B3 |

### B4（impl - tool integration）memory.write 集成 worker default

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/memory_tools.py:330-580`（memory_write 工具入口）|
| 改动 | (1) 工具内部从 `get_current_execution_context()` 拿 `agent_runtime_id`（B0 已接线）；(2) 在 `resolve_memory_scope_ids()` 路径前加判断：若调用方未显式传 `scope_id` + `agent_runtime_id` 非空，先调 `resolve_worker_default_scope_id` 得 default scope_id；(3) 显式传 scope_id 时仍走 `resolve_memory_scope_ids` 现有白名单校验路径（兼容显式 PROJECT_SHARED 写）|
| 依赖 | B0, B3 |
| 预估行数 | ~40 行 impl + ~60 行单测 |
| 验收 | spec B1, B2 / plan §1 Phase B B4 |

### B5（impl - recall priority verify）

| 项 | 值 |
|----|---|
| 类型 | impl + test |
| 文件 | `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py:3215-3228`（recall 排序）|
| 改动 | (1) **代码无需改动**——现有逻辑已把 PRIVATE 系（AGENT_PRIVATE + WORKER_PRIVATE）排在 PROJECT_SHARED 前；(2) 加单测：worker recall 命中 AGENT_PRIVATE + PROJECT_SHARED 时 AGENT_PRIVATE hits 排前 |
| 依赖 | B1, B3, B4 |
| 预估行数 | ~30 行单测 |
| 验收 | spec B3, B4 |

### B6（impl - events）（**Codex MED-4 闭环**）emit 路径

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | (1) `memory_tools.py` memory.write commit 后路径；(2) recall 路径（具体 emit 点 plan 阶段实测确认）|
| 改动 | (1) **新增** memory.write commit 成功后 emit `MEMORY_ENTRY_ADDED` 事件，payload 含 `namespace_kind` / `namespace_id` / `agent_runtime_id` / `agent_session_id` / `subject_key` / `partition`（**新 emit 路径**——不修改 user_profile.update / memory_candidates.promote 现有 emit）；(2) 现有 `MEMORY_RECALL_COMPLETED` emit 路径加 payload 字段 `agent_runtime_id` / `queried_namespace_kinds` / `hit_namespace_kinds` |
| 依赖 | B4, C6（recall 路径已填双字段）|
| 预估行数 | ~30 行 impl + ~50 行单测 |
| 验收 | spec B5 / plan §0.2 + §1 Phase B B6 |

### B7（test - integration）端到端隔离 + 测试 5

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | `octoagent/apps/gateway/tests/test_task_service_context_integration.py` 或新建专项测试文件 |
| 改动 | (1) 测试 1：worker dispatch 后 MemoryNamespace 表新增 1 条 `kind=AGENT_PRIVATE`，**0 条新增 `kind=WORKER_PRIVATE`**；(2) 测试 2（隔离）：同 project 创建 worker A / B 不同 agent_runtime_id，A 写 fact 后 B recall 0 命中；(3) 测试 3（main isolation）：worker A 写 AGENT_PRIVATE，main agent recall 0 命中；(4) 测试 4（recall priority）：worker 同时有 AGENT_PRIVATE + PROJECT_SHARED hits，前者排前；(5) 测试 5（events）：写 fact + recall 都 emit 事件含新字段；(6) 测试 6（grep）：worker dispatch 路径全 codebase 无 `MemoryNamespaceKind.WORKER_PRIVATE` 写入 |
| 依赖 | B1-B6 全部 |
| 预估行数 | ~250 行 test |
| 验收 | spec B1-B7 |

### B8（test - regression）跑全量回归 + e2e_smoke

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | - |
| 改动 | `cd octoagent && uv run pytest`；e2e_smoke 通过 |
| 依赖 | B7 |
| 预估行数 | - |
| 验收 | spec G1, G2 |

### B9（commit）Phase B commit + Codex per-Phase review 闭环

| 项 | 值 |
|----|---|
| 类型 | commit |
| 文件 | `.specify/features/094-worker-memory-parity/codex-review-phase-b.md` |
| 改动 | Codex review + finding 闭环 |
| 依赖 | B8 |
| 预估行数 | review 报告 ~150 行 doc |
| 验收 | NFR-8 |

---

## Phase E：migrate-094 CLI no-op 实现（6 task，~300-400 行）

### E1（impl - CLI 命令骨架）

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/provider/src/octoagent/provider/dx/memory_commands.py`（新建或扩展现有 octo memory 命令组）|
| 改动 | (1) 加 `migrate_094` click subcommand（异步 click handler）；(2) 支持 `--dry-run` / `--apply` / `--rollback <run_id>` 三段式（同 `config migrate` 模板）；(3) 输出格式：`_print_migration_run` 同形 console 输出 |
| 依赖 | C1（memory_maintenance_runs idempotency_key 列）|
| 预估行数 | ~100 行 impl |
| 验收 | spec E5 |

### E2（impl - migration 模块）no-op 实现

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/memory/src/octoagent/memory/migrations/migration_094_worker_private.py`（新建，参考 `migration_063_scope_partition.py`）|
| 改动 | (1) 函数 `run_migration(db_path, *, dry_run, apply_no_op)`：(a) **dry-run**：查 `memory_namespaces GROUP BY kind COUNT` 输出 namespace 分布快照；可选 join `memory_sor.scope_id` 给"按 namespace kind 分类的 fact 数"；返回 `total_facts_to_migrate=0` + `reason="F063_legacy_no_provenance"`；(b) **apply**：写一条 `memory_maintenance_runs` 记录（idempotency_key="`octoagent.memory.migration.094.worker_memory_parity.noop.v1`" / kind="migration" / metadata={"no_op": true, "reason": "F063_legacy_no_provenance"}）；SoR 表零修改；(c) **idempotent**：apply 前 SELECT idempotency_key 已存在则短路返回 |
| 依赖 | E1 |
| 预估行数 | ~120 行 impl |
| 验收 | spec E1, E2, E3 / plan §1 Phase E E2（含 Codex MED-5 + LOW-6 闭环）|

### E3（impl - rollback path）

| 项 | 值 |
|----|---|
| 类型 | impl |
| 文件 | `octoagent/packages/memory/src/octoagent/memory/migrations/migration_094_worker_private.py`（rollback 函数）|
| 改动 | 函数 `rollback_migration(db_path, run_id)`：DELETE FROM memory_maintenance_runs WHERE run_id=? AND idempotency_key=`octoagent.memory.migration.094.worker_memory_parity.noop.v1`（精确匹配）；rollback 后 idempotency 失效 |
| 依赖 | E2 |
| 预估行数 | ~30 行 impl |
| 验收 | spec E4 |

### E4（test - integration）单测 5 项

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | `octoagent/packages/memory/tests/migrations/test_migration_094.py`（新建）|
| 改动 | (1) 测试 1（dry-run）：种入测试库（用真实 init_memory_db()），跑 dry-run 断言库内 0 改动 + 返回值 dict 结构正确；(2) 测试 2（apply）：跑 apply 断言新增 1 条 maintenance_runs 记录 + SoR 表 0 改动；(3) 测试 3（idempotent）：再跑 apply 短路返回；(4) 测试 4（rollback）：跑 rollback，audit 记录消失，再跑 apply 可执行；(5) 测试 5（CLI）：通过 click testing 跑三段式命令 + 输出格式 |
| 依赖 | E1-E3 |
| 预估行数 | ~150 行 test |
| 验收 | spec E6 |

### E5（test - regression）跑全量回归 + e2e_smoke

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | - |
| 改动 | `cd octoagent && uv run pytest`；e2e_smoke 通过 |
| 依赖 | E4 |
| 预估行数 | - |
| 验收 | spec G1, G2 |

### E6（commit）Phase E commit + Codex per-Phase review 闭环

| 项 | 值 |
|----|---|
| 类型 | commit |
| 文件 | `.specify/features/094-worker-memory-parity/codex-review-phase-e.md` |
| 改动 | Codex review + finding 闭环 |
| 依赖 | E5 |
| 预估行数 | review 报告 ~50 行 doc |
| 验收 | NFR-8 |

---

## Phase F：验证收尾（6 task）

### F1（test - regression）全量回归 vs F093 baseline

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | - |
| 改动 | `cd octoagent && uv run pytest`（不限 marker）；断言 0 regression（块 B/C/D/E 测试新增允许）；与 F093 baseline (`284f74d`) 对比 |
| 依赖 | C10, D7, B9, E6 |
| 预估行数 | - |
| 验收 | spec G1 |

### F2（test - e2e）e2e_smoke + e2e_full

| 项 | 值 |
|----|---|
| 类型 | test |
| 文件 | - |
| 改动 | `cd octoagent && uv run octo e2e smoke`；如有可跑 `octo e2e full` |
| 依赖 | F1 |
| 预估行数 | - |
| 验收 | spec G2 |

### F3（rebase）rebase F095 完成的 master 后跑回归（**Codex LOW-7 闭环**）

| 项 | 值 |
|----|---|
| 类型 | impl + test |
| 文件 | - |
| 改动 | (1) 实测 F095 状态：`git fetch origin && git log origin/master --oneline | grep '095\|behavior'`；(2) 若 F095 已合：`git rebase origin/master` + 解决 import / API 级冲突（agent_context.py imports agent_decision.py）；(3) **跨模块测试**：跑专项覆盖 `agent_context` + `agent_decision` + behavior workspace 整体 import 链 + 集成测试；(4) 跑全量回归 + e2e_smoke；(5) 若 F095 未合：归总报告显式说明，rebase 步骤推到 F095 合后再做 |
| 依赖 | F2 |
| 预估行数 | - |
| 验收 | spec G5 / NFR-9 |

### F4（review）Final cross-Phase Codex review

| 项 | 值 |
|----|---|
| 类型 | review |
| 文件 | `.specify/features/094-worker-memory-parity/codex-review-final.md` |
| 改动 | (1) 跑 codex exec adversarial review (model_reasoning_effort=high)：输入 plan.md + 全 Phase commit diff（C / D / B / E）；(2) 重点检查：是否漏 Phase / 是否偏离原计划且未在 commit message 说明 / 跨 Phase 隐性耦合；(3) 处理 finding 至 0 high 残留 |
| 依赖 | F3 |
| 预估行数 | review 报告 ~150-200 行 doc |
| 验收 | spec G4 / NFR-8 |

### F5（doc）completion-report.md

| 项 | 值 |
|----|---|
| 类型 | doc |
| 文件 | `.specify/features/094-worker-memory-parity/completion-report.md`（新建）|
| 改动 | (1) 「实际 vs 计划」对照表（每个 Phase / Task 标"做了 / 跳过 / 改了什么"）；(2) Codex finding 闭环表（spec 阶段 7 finding + plan 阶段 7 finding + 每 Phase per-Phase review + Final review）；(3) 与 F095 并行的合并结果（rebase 实际冲突 / 测试 regression / 最终状态）；(4) F096 接口点说明（RecallFrame 双字段 + list_recall_frames API + 事件 payload 新字段）；(5) Phase 跳过显式归档（若发生） |
| 依赖 | F4 |
| 预估行数 | ~300 行 doc |
| 验收 | spec G6, G7, G8 |

### F6（commit）Phase F docs commit

| 项 | 值 |
|----|---|
| 类型 | commit |
| 文件 | F5 产出 + F4 review 报告 |
| 改动 | commit + 主 session 归总报告等用户拍板（NFR-10 不主动 push）|
| 依赖 | F5 |
| 预估行数 | - |
| 验收 | NFR-10 |

---

## 任务统计

| Phase | Task 数 | impl 行数 | test 行数 | doc 行数 | 总行数 |
|-------|---------|-----------|-----------|----------|--------|
| C | 10（C1-C10）| ~225 | ~190 | ~100 | ~515 |
| D | 7（D1-D7）| ~7（含-13 删除）| ~50 | ~50 | ~94 |
| B | 10（B0-B9）| ~140 | ~470 | ~150 | ~760 |
| E | 6（E1-E6）| ~250 | ~150 | ~50 | ~450 |
| F | 6（F1-F6）| - | - | ~450 | ~450 |
| **总计** | **39** | **~622** | **~860** | **~800** | **~2270** |

**说明**：行数估算含：impl 代码（含 boilerplate / typing / docstring）、单测（含 fixture 复用）、doc（review 报告 + completion-report）。实施过程中允许 ±20% 浮动。

---

## 跨 Phase 依赖图（细化版）

```
C1 (memory_maintenance_runs DDL)  ─┐
C2 (memory_namespaces unique idx)  ├→ C7b (store API archived) ─┐
C3 (recall_frames DDL)             │                               │
                                   ↓                               │
C4 (RecallFrame model) → C5 (store roundtrip) → C6 (write path) → C7 (API filter)
                                   ↓                               │
                              C8 (test) → C9 (regression) → C10 (commit + Codex)
                                                            │
            ┌───────────────────────────────────────────────┤
            ↓                                                │
D1 (常量) → D2 (callsite) → D3 (cleanup) → D4 (test) ─┐    │
                                                       ↓     │
                                         D5 (grep) → D6 → D7 │
            ┌──────────────────────────────────────────┘    │
            ↓                                                │
B0 (agent_runtime_id 接线) → B1 (dispatch path) → B2 (grep verify)
                              ↓
                    B3 (resolve_worker_default_scope_id) → B4 (memory.write 集成)
                                                          ↓
                                                      B5 (recall priority verify)
                                                          ↓
                                            B6 (events) → B7 (端到端测试)
                                                          ↓
                                                    B8 (regression) → B9 (commit + Codex)
                                                          ↓
            ┌─────────────────────────────────────────────┘
            ↓
E1 (CLI 骨架) → E2 (no-op impl) → E3 (rollback) → E4 (test) → E5 (regression) → E6 (commit + Codex)
                                                                                  ↓
                                            ┌─────────────────────────────────────┘
                                            ↓
                F1 (regression) → F2 (e2e) → F3 (rebase F095) → F4 (Final Codex) → F5 (completion-report) → F6 (commit)
```

---

## 估算工时

按 plan §1 Phase 估算 + tasks 细化（每个 task 含 impl + test + 内部 review）：

- **Phase C**：~6-8 小时（schema 改 + RecallFrame 双字段 + store API + 单测）
- **Phase D**：~1.5-2 小时（行为零变更最简）
- **Phase B**：~10-12 小时（核心新行为 + 接线 + 集成 + 大块单测）
- **Phase E**：~4-6 小时（CLI no-op + 单测）
- **Phase F**：~3-4 小时（rebase + Final review + completion-report）
- **Per-Phase Codex review 总计**：~3-4 小时（每 Phase 1-1.5 小时 review + 处理 finding）
- **Final Codex review**：~1.5-2 小时
- **总计**：~30-40 小时（不含意外阻塞 / Codex finding 重大返工）

---

**Tasks 生成完毕。** 等待 GATE_TASKS 用户审查后开始 Phase C 实施。
