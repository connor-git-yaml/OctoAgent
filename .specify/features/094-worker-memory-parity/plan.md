# Implementation Plan: F094 Worker Memory Parity

**Feature Branch**: `feature/094-worker-memory-parity`
**Baseline**: `284f74d` (F093 完成点)
**Spec**: [spec.md](./spec.md)（GATE_DESIGN + Codex pre-Phase review 闭环已落地）
**Status**: Draft（待 GATE_TASKS 用户拍板）

---

## 0. Plan 阶段额外侦察结论（坐实 spec §2.2 剩余 Gap + §8 剩余 Open）

> Plan 阶段第一步用代码 + 测试坐实 spec 阶段留下的开放点。F093 实证 pattern：很多 Gap 一旦实测落地，工程边界会大幅收紧。

### 0.1 Gap-2 / Open-2 坐实：worker 写 fact 默认 namespace 推断时机选择（Codex HIGH-1 闭环纠正）

**实测路径**（grep + Read 已确认每一跳，**Codex review 校正注入点**）：

1. **memory.write 工具入口**（`apps/gateway/src/octoagent/gateway/services/builtin_tools/memory_tools.py:330-580`）：
   - 工具签名：`memory_write(subject_key, content, partition, evidence_refs, scope_id, project_id)`
   - **input schema 无 `namespace_kind` / `namespace_id` 字段**——LLM 工具调用层无法显式指定 AGENT_PRIVATE
   - 工具实现获取 ExecutionRuntimeContext 拿 `worker_id` 仅用于 audit actor_id（line 298-302），未传给 propose_write
2. **memory.write 实际走的 scope 解析路径是 `resolve_memory_scope_ids()`**（`apps/gateway/src/octoagent/gateway/services/builtin_tools/_deps.py:resolve_memory_scope_ids`，被 memory_tools.py:395-425 调用）：
   - **Codex HIGH-1 校正**：plan v1 误把 `_resolve_scope_id`（SessionMemoryExtractor 内部函数）当成 memory.write 注入点
   - 实际 `_resolve_scope_id` 只在 `SessionMemoryExtractor.extract_for_session()` 内部调用（session_memory_extractor.py:293-296）——是 F094 之后启用 worker session memory extraction 时才用
   - F094 主注入点应在 `resolve_memory_scope_ids` 或独立 helper
3. **propose_write 入口**（`packages/memory/src/octoagent/memory/write_service.py:51-87`）：
   - 接受 `scope_id: str`，**不含 namespace_kind 推断**
4. **CompiledTaskContext 已有 `memory_namespace_ids` 字段**（`apps/gateway/.../context_compaction.py:191`）：
   - task_service.py:895-896 已把 namespace_ids 注入 dispatch metadata
   - 但 memory.write 工具未消费这个字段

**Plan 决议（Gap-2 选 (b)，注入点纠正）**：

- **位置选 (b) Scope Resolver 层**——具体在 `apps/gateway/.../services/builtin_tools/_deps.py:resolve_memory_scope_ids()` 扩展或新增独立 helper（如 `resolve_worker_default_scope_id`），**而不是** `session_memory_extractor._resolve_scope_id`
- **流程**：
  1. memory.write 工具从 ExecutionRuntimeContext 拿 `worker_id` / `agent_runtime_id`（agent_runtime_id 接线见 §0.5）
  2. 调用方未显式传 `scope_id` 参数时，调用新 helper `resolve_worker_default_scope_id(stores, project_id, agent_runtime_id)`：
     - 按 `(project_id, agent_runtime_id, kind=AGENT_PRIVATE)` 查 MemoryNamespace 表（active records, archived_at IS NULL）
     - 命中即用其 `memory_scope_ids[0]` 作为 fact 写入 scope_id
     - 未命中显式 raise `WorkerMemoryNamespaceNotResolved`（NFR-3）
  3. 调用方显式传 `scope_id` 时仍走 `resolve_memory_scope_ids` 现有白名单校验路径（保留向后兼容 + 让 LLM 显式写 PROJECT_SHARED 时可走）
- **同时**（不是必需，但为了一致性）：扩展 `session_memory_extractor._resolve_scope_id` 也接受 `agent_runtime_id` 参数——为 F094 之后启用 worker session memory extraction 留接口；**但 F094 范围内不启用**（NFR-5 锁定 worker extractor 仍只跑 MAIN_BOOTSTRAP，已实测白名单 `_EXTRACTABLE_SESSION_KINDS = frozenset({MAIN_BOOTSTRAP})` 不变）
- **理由**：
  - 实施成本最小（resolve_memory_scope_ids 已是 memory 工具实际入口）
  - 单测性最优（独立 helper 函数 + 多场景覆盖）
  - 维护性最优（注入点真实存在 + 不破坏 SessionMemoryExtractor 边界）

### 0.5 Open-2 衍生：ExecutionRuntimeContext.agent_runtime_id 接线（Codex MED-2 闭环）

**实测**（plan v1 低估了接线范围）：

- `apps/gateway/.../execution_context.py:26-35`：ExecutionRuntimeContext 当前**无 `agent_runtime_id` 字段**——只有 `worker_id` / `session_id` / `agent_session_id`
- 两个构造点都未传 agent_runtime_id：
  - `apps/gateway/.../orchestrator.py:1367-1378`
  - `apps/gateway/.../worker_runtime.py:488-499`

**Plan 决议**：

- Phase B 前置任务：在 ExecutionRuntimeContext 加 `agent_runtime_id: str = ""` 默认空字段
- 两个构造点 source-of-truth：
  - `orchestrator.py`：从 `dispatch_metadata` / `compiled_context.agent_runtime_id`（如有）填充
  - `worker_runtime.py`：从 envelope.metadata 或已解析的 agent_runtime context 填充
- 新增单测：worker 工具执行上下文里 `agent_session_id` + `agent_runtime_id` 同时非空
- 这是 Phase B 内的**前置 task**（Phase B B0），优先级高于 B1（避免 B1 改 dispatch path 后没法验证）

### 0.2 Open-7 坐实：MEMORY_* 事件复用 vs 新增

**实测发现**（`packages/core/src/octoagent/core/models/enums.py:80-82, 203-209`）：
- 现有 MEMORY_* 事件枚举：
  - `MEMORY_RECALL_SCHEDULED` / `MEMORY_RECALL_COMPLETED` / `MEMORY_RECALL_FAILED`
  - `MEMORY_ENTRY_ADDED` / `MEMORY_ENTRY_REPLACED` / `MEMORY_ENTRY_REMOVED` / `MEMORY_ENTRY_BLOCKED`
  - `OBSERVATION_OBSERVED` / `OBSERVATION_STAGE_COMPLETED` / `OBSERVATION_PROMOTED` / `OBSERVATION_DISCARDED`
- Event.payload 是 `dict[str, Any]` 开放结构（`event.py:38-41`），天然支持加字段

**Plan 决议**：

- **复用现有事件 + 加 payload 字段**（与 F093 实证一致）：
  - `MEMORY_ENTRY_ADDED` payload 加 `namespace_kind` / `namespace_id` / `agent_runtime_id` / `agent_session_id` 字段（块 B 写 fact 路径）
  - `MEMORY_RECALL_COMPLETED` payload 加 `agent_runtime_id` / `queried_namespace_kinds` / `hit_namespace_kinds` 字段（块 B/C recall 路径）
- **不新增 EventType 枚举**——避免枚举膨胀；F096 audit 直接订阅现有事件 + 解析新字段

### 0.3 Open-8 坐实：sqlite_init.py schema migration 实施模式

**实测发现**：

1. `packages/memory/src/octoagent/memory/store/sqlite_init.py`：
   - 无 schema_version 字段；DDL 字符串直接执行（line 307-329 `init_memory_db`）
   - 现有 migration 范本：`migration_063_scope_partition.py`（独立可执行命令，不在 init 时跑）
2. `packages/core/src/octoagent/core/store/sqlite_init.py:536-550`：
   - memory_namespaces 表无 schema_version；无 ALTER TABLE 历史
3. **关键决策依据**：spec §3.1 块 C 是"前置 schema 修复"——**必须在 F094 commit 后自动到位**，不能依赖用户手动跑 migrate 命令

**Plan 决议（Open-8 选项 C：双轨）**：

- **DDL 字符串改造**（保证新建库的形态）：
  - `memory_maintenance_runs` DDL 加 `idempotency_key TEXT NOT NULL DEFAULT ''` + `requested_by TEXT NOT NULL DEFAULT ''` 列（C6 任务）
  - `memory_namespaces` DDL 加 `UNIQUE(project_id, agent_runtime_id, kind)` 约束（C7 任务，含 `WHERE archived_at IS NULL` 子句兼容 archived 数据）
  - `recall_frames` DDL 加 `queried_namespace_kinds TEXT NOT NULL DEFAULT '[]'` + `hit_namespace_kinds TEXT NOT NULL DEFAULT '[]'` 列（C1-C5 任务）
- **启动时 ALTER TABLE IF NOT EXISTS 兜底**（已存在库的升级）：
  - 在 `init_memory_db` / `init_core_db`（具体函数名 Plan 阶段实测）加 PRAGMA table_info 检测 + ALTER TABLE ADD COLUMN 兜底
  - 对 UNIQUE 约束：SQLite 不支持直接 ALTER ADD UNIQUE 多列——用 `CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_namespaces_unique_triple ON memory_namespaces(project_id, agent_runtime_id, kind) WHERE archived_at IS NULL` 等价替代（partial unique index 是 SQLite 支持的）
- **migration_094_*.py 文件**：**仅作为 `octo memory migrate-094` CLI 命令的实施载体**（块 E 范围），**不**承担 schema 升级职责（schema 升级在启动 init 时自动到位）

### 0.4 Open-9 坐实：memory_namespaces 既有重复数据清理策略

**Plan 决议**：

- **清理路径**：
  - 启动 init 跑 schema migration 前，先扫描 `(project_id, agent_runtime_id, kind)` 重复记录
  - 对每组重复，按 `created_at DESC` 保留最新 1 条；其他记录写 `archived_at = now()` + metadata 加 `archived_reason="F094_dedupe_unique_constraint_setup"`
  - 重复扫描的 SQL 查询范本：

    ```sql
    SELECT project_id, agent_runtime_id, kind, COUNT(*) as cnt
    FROM memory_namespaces
    WHERE archived_at IS NULL
    GROUP BY project_id, agent_runtime_id, kind
    HAVING cnt > 1;
    ```

- **partial unique index 等价 UNIQUE 约束**：
  - `CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_namespaces_unique_triple ON memory_namespaces(project_id, agent_runtime_id, kind) WHERE archived_at IS NULL`
  - 这样 archived 数据不影响新约束；新插入的 (project_id, agent_runtime_id, kind) 三元组按业务路径强制唯一
  - **Codex 已验证**：当前 Python SQLite ≥ 3.51.2，repo 已有 partial unique index 用法（`packages/core/.../sqlite_init.py:62-63, 797-807`、`packages/memory/.../sqlite_init.py:222-223`）——语法兼容 OK
- **store API 必须同步过滤 archived**（**Codex MED-3 闭环**）：
  - 当前 `AgentContextStore.list_memory_namespaces()` (`agent_context_store.py:870-890`) 仅按 `project_id / agent_runtime_id / kind` 拼 WHERE，**没有 `archived_at IS NULL`**
  - `get_memory_namespace()` (`863-868`) 也无 archived 语义
  - **Phase C 必须同步改 store API**：
    - `list_memory_namespaces(*, include_archived: bool = False)` 默认过滤 `archived_at IS NULL`
    - 仅控制台 / 审计场景需要时显式 `include_archived=True`
    - 否则 dedupe archived 后仍可能被 resolver / UI 读回——破坏 unique 约束的语义意图
  - `get_memory_namespace(namespace_id, *, include_archived: bool = False)` 同步加该参数（默认 active path 过滤 archived）
- **数据安全**：archived 不删——保留向后排查能力（agent_context_store 仍可按 namespace_id 查 archived 记录，但活跃路径过滤 `archived_at IS NULL`）

### 0.5 Open-11 坐实：baseline worker_private namespace records 处理

**实测关键发现**（`apps/gateway/.../agent_context.py:2333-2335`）：

```python
private_existing = await self._stores.agent_context_store.get_memory_namespace(
    private_namespace_id
)
```

- worker dispatch 路径**复用同一 namespace_id**（按 `(project_id, agent_runtime_id, kind)` 派生 namespace_id）
- 一个用户跑 N 个 worker dispatch 后，`memory_namespaces` 表**每 (project_id, agent_runtime_id) 仅 1 条 worker_private 记录**——不会扩散
- 多次 dispatch 只更新 namespace 的 `memory_scope_ids` + `metadata`

**Plan 决议（Open-11 选项 A）**：

- **完全保留 baseline worker_private records 不动**——不迁不删不改
- 块 B 改 line 2323-2327：worker 路径的 `private_kind` 从 `WORKER_PRIVATE` 改为 `AGENT_PRIVATE`
- 块 B 改 line 2336-2339：`build_private_memory_scope_ids` 调用时传 kind=AGENT_PRIVATE，scope_ids 形态变为 `memory/private/main/...`（因 `build_private_memory_scope_ids:473` 的 `owner = "worker" if kind is WORKER_PRIVATE else "main"`）
- **关键约束**：spec §0 锁定 "scope_id 字符串保留 baseline 形态" 是指**不改 build_private_memory_scope_ids 函数本身**，但 worker 路径调用此函数时 kind 改为 AGENT_PRIVATE 后，**worker 新生成的 scope_id 形态从 `memory/private/worker/...` 变成 `memory/private/main/...`**——这是行为变化，但 spec §0 已把"语义 SoT 不依赖 scope_id 字符串"作为锚点，scope_id 形态变化是上游代码不该感知的实现细节
- **三元组 unique 约束兼容性**：worker 改用 AGENT_PRIVATE 后，同 (project_id, agent_runtime_id) 三元组：
  - 老：(project_id, agent_runtime_id, kind=WORKER_PRIVATE)
  - 新：(project_id, agent_runtime_id, kind=AGENT_PRIVATE)
  - 三元组**不同 kind**——天然唯一，partial unique index 不冲突 ✅
- 历史 worker_private namespace records 在新 dispatch 下不再被读写（agent_context.py:2333 查询的 `private_namespace_id` 用新 kind 派生，不会撞到老 worker_private namespace_id）

### 0.6 Gap-4 收口：hit_namespace_kinds 数据来源一致性

**实测路径**（`apps/gateway/.../agent_context.py:2947-2949 + 3105-3114 + 3242`）：

1. `_build_memory_scope_entries` (line 3208-3250)：每条 entry 含 `"namespace_kind": namespace.kind.value`（line 3242）
2. `scope_entry_map = {scope_id: dict(item) for item in scope_entries}` (line 2947-2951)
3. `**scope_entry_map.get(hit.scope_id, {})` (line 3110)——注入 namespace_kind 到 `memory_hits[i].metadata`

**Plan 决议**：

- ✅ **数据来源一致**：每条 memory_hit 都有 `metadata.namespace_kind`（除非 hit.scope_id 不在 scope_entry_map——理论上不应发生）
- 块 C C3 实现：

  ```python
  hit_namespace_kinds = sorted({
      hit.metadata.get("namespace_kind", "")
      for hit in memory_hits
      if hit.metadata and hit.metadata.get("namespace_kind")
  })
  ```

- 加 debug-mode assertion（NFR-3 显式 raise 适用）：scope_entry_map 缺 hit.scope_id 时 raise（理论 baseline 不应发生）

### 0.7 Open-10 时机锁定（与 F095 rebase）

- F094 完成时若 F095 仍在跑：rebase F095 完成的 master 步骤推到 F095 完成后；主 session 归总报告显式说明
- F094 完成时若 F095 已完成：rebase 步骤在 Phase F 验证收尾内完成

---

## 1. Phase 切分（C → D → B → E → F，与 spec §7 一致）

> spec §7 锁定 Phase 顺序 A → C → D → B → E（A 已在 spec.md commit 完成）。Plan 阶段细化每个 Phase 的子任务清单 + 验收 + Codex review 节点。

### Phase C：数据补全 + 前置 schema 修复（最简单，先建脚手架）

**目标**：把所有 schema 修复 + RecallFrame 双字段 + API 过滤维度都做完，让块 B / 块 E 启动时 schema 已就位。

**任务清单**：

- **C1**（schema_setup）`packages/memory/.../store/sqlite_init.py` 改 DDL：
  - `memory_maintenance_runs` 表加 `idempotency_key TEXT NOT NULL DEFAULT ''` + `requested_by TEXT NOT NULL DEFAULT ''` 列
  - `init_memory_db` 加启动时 PRAGMA table_info 检测 + ALTER TABLE ADD COLUMN IF 缺列
- **C2**（schema_setup）`packages/core/.../store/sqlite_init.py` 改 DDL：
  - `memory_namespaces` 加 partial unique index：`CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_namespaces_unique_triple ON memory_namespaces(project_id, agent_runtime_id, kind) WHERE archived_at IS NULL`
  - `init_core_db`（或对应函数）加启动时重复检测 + archived_at 标记冲突记录
- **C3**（schema_setup）`packages/core/.../store/sqlite_init.py` 改 DDL：
  - `recall_frames` 表加 `queried_namespace_kinds TEXT NOT NULL DEFAULT '[]'` + `hit_namespace_kinds TEXT NOT NULL DEFAULT '[]'` 列
  - 启动时 ALTER TABLE 兜底
- **C4**（model）`packages/core/.../models/agent_context.py:430-447` 改 RecallFrame 模型：
  - 加 `queried_namespace_kinds: list[MemoryNamespaceKind] = Field(default_factory=list)`
  - 加 `hit_namespace_kinds: list[MemoryNamespaceKind] = Field(default_factory=list)`
- **C5**（store roundtrip）`packages/core/.../store/agent_context_store.py` 改 RecallFrame save/get：
  - 持久化 / 读取时序列化 + 反序列化双字段
  - 单测 round-trip 含双字段
- **C6**（write path）`apps/gateway/.../services/agent_context.py:800-825` 改 RecallFrame 写入路径：
  - `queried_namespace_kinds = sorted({n.kind for n in namespaces})`（namespaces 是 resolved namespaces 全集）
  - `hit_namespace_kinds = sorted({hit.metadata.get("namespace_kind") for hit in hits if hit.metadata.get("namespace_kind")})`
  - main + worker 路径都填（因为复用同一函数）
- **C7**（API filter）`apps/gateway/.../services/control_plane/session_service.py:225` 改 list_recall_frames：
  - 加 `agent_runtime_id` / `queried_namespace_kind` / `hit_namespace_kind` 过滤参数（query string list contains）
  - 单测覆盖新过滤维度
- **C7b**（store API archived 过滤 - **Codex MED-3 闭环**）：
  - `packages/core/.../store/agent_context_store.py:870-890` `list_memory_namespaces()` 加 `include_archived: bool = False` 参数：默认 WHERE 加 `archived_at IS NULL`
  - `agent_context_store.py:863-868` `get_memory_namespace()` 同步加 `include_archived: bool = False` 参数
  - 调用方改造：实测所有 `list_memory_namespaces()` 调用点（`session_memory_extractor.py:740, 750` / `session_service.py:222` / 测试用）确认行为：
    - 业务路径（dispatch / recall / 工具）→ 默认 active 路径（不传 include_archived，自动过滤 archived）
    - 控制台 / 审计路径 → 显式传 `include_archived=True`
  - 单测：构造 archived 数据，验证默认调用 0 命中 archived；显式 include 命中
- **C8**（test）跑全量 unit + integration 回归 + e2e_smoke
- **C9**（test）单测专项：
  - DDL 改后 init_*_db 在新建库 + 已存在库（mock ALTER）双场景 OK
  - memory_maintenance_runs 含 idempotency_key 列后，F063 migration 在新 schema 下可幂等运行
  - memory_namespaces 三元组 unique 约束生效（重复 insert 触发 IntegrityError）
  - 既有重复数据 archived_at 标记后不影响新插入
  - list_memory_namespaces 默认 active path 过滤 archived；显式 include_archived=True 命中
- **C10**（commit）commit Phase C，commit message 含 Codex per-Phase review 闭环（Phase C 完成前必走 codex review）

**验收**（与 spec §5 块 C C1-C7 映射）：

- C1 / C2 / C3 / C4 / C5 / C6 / C7 全部 PASS
- 全量回归 0 regression vs F093 baseline (`284f74d`)
- e2e_smoke PASS

**前置依赖**：无（Phase C 是 F094 的前置）

**预估改动量**：~400-500 行（DDL 改 + RecallFrame 模型 + store + 写入路径 + API filter + 单测）

---

### Phase D：行为零变更清理（删除硬编码，改读 module-level 常量）

**目标**：把 `_default_worker_memory_recall_preferences` 硬编码挪到 module-level 常量；保留 baseline merge 顺序；行为零变更。

**任务清单**：

- **D1**（model）`packages/core/.../models/agent_context.py` 顶部加 module-level 常量：

  ```python
  # F094: Module-level worker memory recall defaults（替代 gateway 内部硬编码函数）
  DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES: dict[str, Any] = {
      "prefetch_mode": "hint_first",
      "planner_enabled": True,
      "scope_limit": 4,
      "per_scope_limit": 4,
      "max_hits": 8,
  }
  ```

- **D2**（callsite）`apps/gateway/.../services/agent_context.py:2693-2707` 改调用点：

  ```python
  # 之前：
  merged_memory_recall = {
      **_default_worker_memory_recall_preferences(worker_profile),
      **(dict(_memory_recall_preferences(existing_profile)) if existing_profile else {}),
  }
  # 改为：
  from octoagent.core.models.agent_context import DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES
  merged_memory_recall = {
      **DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES,
      **(dict(_memory_recall_preferences(existing_profile)) if existing_profile else {}),
  }
  ```

- **D3**（cleanup）删除 `_default_worker_memory_recall_preferences` 函数定义（agent_context.py:183-195）
- **D4**（test）单测专项（`apps/gateway/tests/test_agent_context_*.py` 类）：
  - 测试 1（D2 verbatim）：新建 Worker AgentProfile，断言 `context_budget_policy["memory_recall"]` 5 个 key 与 baseline 完全一致
  - 测试 2（D5 merge order）：构造 existing_profile.memory_recall = `{"scope_limit": 10}`，走 _create_worker_agent_profile，断言最终 merged_memory_recall["scope_limit"] = 10（existing override）+ 其他 4 个 key = defaults
- **D5**（grep）grep 全 codebase 确认 `_default_worker_memory_recall_preferences` 0 命中
- **D6**（test）跑全量回归 + e2e_smoke
- **D7**（commit）commit Phase D，含 Codex per-Phase review 闭环

**验收**（与 spec §5 块 D D1-D5 映射）：

- D1 / D2 / D3 / D4 / D5 全部 PASS
- 全量回归 0 regression vs F093 baseline + Phase C 完成点

**前置依赖**：Phase C（C5 RecallFrame 字段必须先就位，否则 merge 测试可能受影响）—— 实际上 Phase D 与 Phase C 关系弱，可安全跟在 C 后

**预估改动量**：~80-120 行（常量定义 + 调用点改 + 单测）

---

### Phase B：核心新行为（AGENT_PRIVATE 真生效 + 废弃 worker WORKER_PRIVATE 路径）

**目标**：worker dispatch 路径改用 AGENT_PRIVATE namespace；写 fact 默认进 AGENT_PRIVATE；recall 优先级 + 隔离断言；废弃 worker WORKER_PRIVATE 写入路径（baseline records 保留不动）。

**任务清单**：

- **B0**（前置 - 接线 agent_runtime_id）（**Codex MED-2 闭环**）：
  - `apps/gateway/.../execution_context.py:26-35` ExecutionRuntimeContext 加 `agent_runtime_id: str = ""` 默认空字段
  - `apps/gateway/.../orchestrator.py:1367-1378` 构造点：从 `dispatch_metadata` / `compiled_context` 填 agent_runtime_id
  - `apps/gateway/.../worker_runtime.py:488-499` 构造点：从 envelope.metadata 或已解析的 agent_runtime context 填
  - 单测：worker 工具执行上下文 `agent_session_id` + `agent_runtime_id` 同时非空
- **B1**（dispatch path）`apps/gateway/.../services/agent_context.py:2323-2327` 改 worker 路径：

  ```python
  # 之前：
  private_kind = (
      MemoryNamespaceKind.WORKER_PRIVATE
      if agent_runtime.role is AgentRuntimeRole.WORKER
      else MemoryNamespaceKind.AGENT_PRIVATE
  )
  # 改为：
  private_kind = MemoryNamespaceKind.AGENT_PRIVATE  # F094: 统一 worker / main 用 AGENT_PRIVATE
  ```

- **B2**（cleanup）保留以下 WORKER_PRIVATE 引用（向后兼容，不动）：
  - `agent_context.py:470, 473`（`build_private_memory_scope_ids`）—— **保留**（spec §0 锁定）
  - `agent_context.py:3215-3228`（recall 入口排序）—— **保留**（兼容历史 namespace 数据）
  - `task_service.py:1946`（`_resolve_compaction_flush_scope` 用 PRIVATE 系筛 namespace，纯读路径包含历史 worker_private 兼容）—— **保留**（新 dispatch 后只会出现 AGENT_PRIVATE）
- **B3**（scope resolver - **Codex HIGH-1 闭环**：注入点改正）：
  - **新建 helper** `apps/gateway/.../services/builtin_tools/_deps.py:resolve_worker_default_scope_id(stores, project_id, agent_runtime_id) -> str`：
    - 实现：按 `(project_id, agent_runtime_id, kind=AGENT_PRIVATE)` 调 `list_memory_namespaces(include_archived=False)` 查 MemoryNamespace 表
    - 命中（应该唯一，因 C2 unique 约束）即用其 `memory_scope_ids[0]` 作为 scope_id
    - 未命中显式 raise `WorkerMemoryNamespaceNotResolved`（NFR-3）
  - **同时**扩展 `session_memory_extractor.py:705-766 _resolve_scope_id` 接受新参数 `agent_runtime_id` + `kind_preference`（为 F094 之后启用 worker session memory extraction 留接口；F094 范围内白名单 `_EXTRACTABLE_SESSION_KINDS` 不变，worker extractor 不启用）
- **B4**（memory.write tool integration - **Codex HIGH-1 闭环**：注入点改正）：
  - `apps/gateway/.../services/builtin_tools/memory_tools.py:330-580` 的 memory_write 工具：
    - 从 ExecutionRuntimeContext 拿 `agent_runtime_id`（B0 已接线）+ `worker_id`
    - 在 `resolve_memory_scope_ids()` 路径（line 395-425）前：若调用方未显式传 `scope_id` 参数 + agent_runtime_id 非空，先调 `resolve_worker_default_scope_id` 得 worker 默认 scope_id
    - 调用方显式传 scope_id 时仍走 `resolve_memory_scope_ids` 现有白名单校验路径（兼容显式 PROJECT_SHARED 写）
- **B5**（recall priority）`apps/gateway/.../services/agent_context.py:3215-3228` recall 入口：
  - 现有逻辑已把 PRIVATE 系（AGENT_PRIVATE + WORKER_PRIVATE）排在 PROJECT_SHARED 前——**已生效，无需改**
  - 验证 worker recall 命中 AGENT_PRIVATE 时实际优先返回（单测覆盖）
- **B6**（events - **Codex MED-4 闭环**：明确新增 emit 路径不是修改现有）：
  - **关键纠正**：memory.write 当前**不 emit MEMORY_ENTRY_ADDED**！只调 propose_write/commit_memory（line 485-500）。现有 MEMORY_ENTRY_ADDED 主要来自 user_profile.update / memory_candidates.promote。
  - **F094 块 B B6 实施**：在 memory.write commit 成功后**新增** emit `MEMORY_ENTRY_ADDED` 事件（**新 emit 路径**），payload 含 `namespace_kind` / `namespace_id` / `agent_runtime_id` / `agent_session_id`
  - **不修改** user_profile.update / memory_candidates.promote 的现有 emit 语义
  - `MEMORY_RECALL_COMPLETED` 在现有 emit 路径上加 payload 字段：`agent_runtime_id` / `queried_namespace_kinds` / `hit_namespace_kinds`
- **B7**（test）端到端单测：
  - 测试 1（B7 verbatim）：跑一次 worker dispatch，断言 MemoryNamespace 表新增 1 条 `kind=AGENT_PRIVATE` 记录，**0 条新增 `kind=WORKER_PRIVATE`**
  - 测试 2（隔离）：同 project 创建两个 worker（runtime_id_A / runtime_id_B），A 写 fact 后 B recall，断言 0 命中
  - 测试 3（main isolation）：worker A 写 AGENT_PRIVATE fact，main agent recall，断言 0 命中
  - 测试 4（recall priority）：worker 同时有 AGENT_PRIVATE 与 PROJECT_SHARED hits，AGENT_PRIVATE 排前
  - 测试 5（events）：写 fact + recall 都 emit 事件含新字段
  - 测试 6（grep）：worker dispatch 路径全 codebase 无 `MemoryNamespaceKind.WORKER_PRIVATE` 写入
- **B8**（test）跑全量回归 + e2e_smoke
- **B9**（commit）commit Phase B，含 Codex per-Phase review 闭环

**验收**（与 spec §5 块 B B1-B7 映射）：

- B1 / B2 / B3 / B4 / B5 / B6 / B7 全部 PASS
- 全量回归 0 regression vs F093 baseline + Phase C/D 完成点
- e2e_smoke PASS

**前置依赖**：Phase C（C2 三元组 unique 约束 + C3 RecallFrame 字段 + C5 store 持久化）

**预估改动量**：~600-800 行（dispatch path + scope resolver 扩展 + memory.write 集成 + 事件 + 单测大块）

---

### Phase E：migrate-094 CLI（no-op 实现）

**目标**：`octo memory migrate-094 --dry-run / --apply / --rollback` CLI 完整可用；底层为 no-op；幂等 + rollback 路径完整。

**任务清单**：

- **E1**（CLI 命令骨架）`packages/provider/src/octoagent/provider/dx/memory_commands.py`（或新建 / 复用现有 memory_commands）：
  - 加 `migrate-094` click subcommand 组
  - 支持 `--dry-run` / `--apply` / `--rollback <run_id>` 三段式
  - 输出格式与 `config migrate` 一致
- **E2**（implementation）migrate-094 实施代码（参考 `migration_063_scope_partition.py` 模板）（**Codex MED-5 + LOW-6 闭环**）：
  - 实施模块：`packages/memory/.../migrations/migration_094_worker_private.py`
  - 函数 `run_migration(db_path, *, dry_run, apply_no_op)`：
    - dry-run：扫描 + 输出 `total_facts_to_migrate=0` + `reason="F063_legacy_no_provenance"` + **当前 namespace 分布快照**——查 **`memory_namespaces` 表 GROUP BY kind COUNT**（**memory_sor 表无 kind 列！** Codex MED-5 校正）；可选附加：通过 `memory_namespaces.memory_scope_ids` JSON 字段 join `memory_sor.scope_id` 给出"按 namespace kind 分类的 fact 数"，无匹配 scope 单独分类
    - apply：写一条 `memory_maintenance_runs` 记录（**idempotency_key 改用稳定分层命名**：`octoagent.memory.migration.094.worker_memory_parity.noop.v1`，Codex LOW-6 校正）/ kind="migration" / metadata={"no_op": true, "reason": "F063_legacy_no_provenance"}；SoR 表零修改
    - 幂等：apply 前查 `memory_maintenance_runs WHERE idempotency_key=<完整分层 key>`，已存在则短路返回
- **E3**（rollback）支持 rollback 命令：
  - rollback CLI：`octo memory migrate-094 --rollback <run_id>`
  - 实施：DELETE FROM memory_maintenance_runs WHERE run_id=? AND idempotency_key=<完整分层 key>（精确匹配，不做前缀匹配）
  - rollback 后 idempotency 失效，可重新 apply
- **E4**（test）单测：
  - 测试 1（dry-run）：种入测试库，跑 dry-run 断言库内 0 改动 + 返回值结构正确
  - 测试 2（apply 写 audit）：跑 apply 断言新增 1 条 maintenance_runs 记录 + SoR 表 0 改动
  - 测试 3（idempotent）：再跑 apply 短路返回，库内不变
  - 测试 4（rollback）：跑 rollback，audit 记录消失，再跑 apply 可执行（路径完整）
  - 测试 5（前置依赖）：用真实 init_memory_db()（C6 已加 idempotency_key 列），不手写 schema 补丁
- **E5**（test）跑全量回归 + e2e_smoke
- **E6**（commit）commit Phase E，含 Codex per-Phase review 闭环

**验收**（与 spec §5 块 E E1-E6 映射）：

- E1 / E2 / E3 / E4 / E5 / E6 全部 PASS
- 全量回归 0 regression vs F093 baseline + Phase C/D/B 完成点
- e2e_smoke PASS

**前置依赖**：Phase C（C6 memory_maintenance_runs idempotency_key 列必须就位）

**预估改动量**：~300-400 行（CLI 骨架 + migration 模块 + 单测）

---

### Phase F：验证收尾（Final review + rebase F095 + completion-report）

**目标**：F094 范围内全部代码改动完成；与 F095 合并验证；写 completion-report 等用户拍板。

**任务清单**：

- **F1**（regression）跑全量回归 vs F093 baseline (`284f74d`)：
  - `cd octoagent && uv run pytest`（不限 marker）
  - 断言 0 regression（块 B/C/D/E 测试新增允许）
- **F2**（e2e）跑 e2e_smoke + e2e_full（如有可跑）
- **F3**（rebase）若 F095 已完成（`feature/095-worker-behavior-workspace-parity` 合到 master）（**Codex LOW-7 闭环**）：
  - `git fetch origin master`
  - `git rebase origin/master`
  - 解决冲突（**预期无文本冲突**——F094 改 `agent_context.py` + memory 工具，F095 改 `agent_decision.py` + behavior_workspace + behavior 模板，文本不重叠；**但有 API/import 级依赖**：`agent_context.py` import `agent_decision.py`，`agent_decision.py` import `core.behavior_workspace`——rebase 后 import 可能因 F095 改 API 而失败）
  - **强制跨模块测试**（不止文件级回归）：跑专项覆盖 `agent_context` + `agent_decision` + behavior workspace 的整体 import 链 + 集成测试
  - 跑全量回归 + e2e_smoke 验证 rebase 后无 regression
  - 若 F095 未完成：rebase 步骤推到 F095 完成后；归总报告显式说明
- **F4**（Codex Final review）跑 Final cross-Phase Codex review：
  - 输入：plan.md + 全 Phase commit diff（C / D / B / E）
  - 重点检查：是否漏 Phase / 是否偏离原计划且未在 commit message 说明 / 跨 Phase 隐性耦合
  - 处理 finding 至 0 high 残留
- **F5**（completion-report）写 `.specify/features/094-worker-memory-parity/completion-report.md`：
  - 「实际 vs 计划」对照表（每个 Phase / Task 标"做了 / 跳过 / 改了什么"）
  - Codex finding 闭环表（spec 阶段 7 finding + 每 Phase per-Phase review + Final review）
  - 与 F095 并行的合并结果（rebase 实际冲突 / 测试 regression / 最终状态）
  - F096 接口点说明（RecallFrame 双字段 + list_recall_frames API + 事件 payload 新字段）
  - Phase 跳过显式归档（若发生）
- **F6**（commit）commit Phase F docs（completion-report.md）

**验收**（与 spec §5 全局 G1-G8 映射）：

- G1-G8 全部 PASS
- F095 rebase 实际状态显式记录

**前置依赖**：Phase C / D / B / E 全部完成

**预估改动量**：~200 行（completion-report 文档）

---

## 2. Phase 间依赖图

```
Phase A (spec) ✅ → Phase C (schema setup + RecallFrame fields)
                         ↓
                    Phase D (行为零变更 cleanup) ──┐
                         ↓                          ↓
                    Phase B (核心新行为) ←─────────┘
                         ↓
                    Phase E (migrate-094 CLI)
                         ↓
                    Phase F (验证 + Final review + completion-report)
```

**强制顺序**：
- Phase C 必须先于 Phase B（B7 需 C2 unique 约束 + C3 RecallFrame 字段就位）
- Phase C 必须先于 Phase E（E2 需 C6 idempotency_key 列就位）
- Phase D 与 Phase C 弱耦合（可在 C 后立即跑，行为零变更 + 单测独立）
- Phase E 必须最后做（最复杂 + 不可逆 + 依赖块 B 写路径已稳定）

**可优化**：D 与 B 可考虑并行（D 行为零变更 + B 核心新行为不冲突文件），但 spec §7 锁定 D → B 顺序保持。

---

## 3. NFR 实施细节落地

### NFR-1（行为零变更 + 新行为可观测）

- 块 D：D2 单测 verbatim + D5 merge 顺序断言 = 行为零变更证据
- 块 B/C/E：每个验收项都有单测 + Codex review 兜底

### NFR-2（事件可审计）

- Phase B B6 emit 事件含新字段；plan §0.2 锁定复用 MEMORY_ENTRY_ADDED + MEMORY_RECALL_COMPLETED

### NFR-3（namespace 解析失败显式 raise）

- Phase B B3 `_resolve_scope_id` 未命中显式 raise `WorkerMemoryNamespaceNotResolved` 异常
- Phase C C2 重复 insert 触发 IntegrityError（partial unique index）
- 单测覆盖：构造无 worker namespace 场景 + 三元组重复场景

### NFR-4（与 F092 SpawnChildResult 无影响）

- F094 不动 `plane.spawn_child` 编排入口
- Phase B / Phase E 实施前 grep 自检：`_create_worker_agent_profile` 路径不走 spawn_child（已实测）

### NFR-5（与 F093 Worker session 字段无影响）

- F094 不读不写 `AgentSession.rolling_summary` / `memory_cursor_seq`
- 块 B B4 memory.write 工具仅读 ExecutionRuntimeContext.worker_id / agent_runtime_id，不动 session 字段

### NFR-6（migrate-094 不可逆约束）

- Phase E E1-E4 三段式 CLI；idempotent + rollback 完整可用
- 命令文档化（CLAUDE.md / dx 子命令文档 docs/）

### NFR-7（每 Phase 后回归 0 regression）

- Phase C / D / B / E / F 每个 Phase commit 前跑全量 + e2e_smoke

### NFR-8（每 Phase 前 Codex review + Final cross-Phase review）

- Phase C / D / B / E commit 前 per-Phase Codex review（codex exec adversarial mode）
- Phase F 末 Final cross-Phase review

### NFR-9（与 F095 并行约束）

- 实施前 grep 自检：每 Phase 改动文件 vs F095 改动文件清单（`behavior_workspace.py` / `agent_decision.py` / behavior 模板）
- Phase F F3 rebase 验证

### NFR-10（不主动 push）

- 全 Phase 完成后归总报告等用户拍板

### NFR-11（前置 schema 修复优先级）

- Phase C 必须先于 Phase B / Phase E
- Plan §1 强制顺序已锁定

---

## 4. Risks（plan-stage refined）

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| Phase B B1 改 line 2323-2327 的 worker_private 切换涉及 build_private_memory_scope_ids 副作用（worker scope_ids 形态从 `/private/worker/...` 变 `/private/main/...`） | 中 | spec §0 已锁定"语义 SoT 不依赖 scope_id 字符串"；上游代码若 grep 解析 scope_id 字符串则违反约束（NFR-3 视为隐式 raise）；Phase B B7 grep 自检 worker dispatch 路径是否依赖旧 scope_id 编码 |
| Phase C C2 partial unique index 在 SQLite 不同版本兼容性 | 低 | partial unique index 是 SQLite 3.8.0+ 标配；OctoAgent 依赖 Python 3.12 自带 SQLite ≥ 3.40，兼容 OK |
| Phase C C2 既有重复 archived_at 标记策略破坏 baseline namespace_id 引用（其他表 FK 引用 archived 记录） | 中 | 实测验证：grep `agent_runtime_id REFERENCES memory_namespaces` 与类似 FK；若有则 archived 路径不能删，仅标记 archived_at，FK 引用仍可解（已确认 store 层用 namespace_id 主键查） |
| Phase C C5 RecallFrame round-trip 双字段反序列化 list[MemoryNamespaceKind] 时枚举值不匹配 | 中 | TEXT JSON 列 + 反序列化后 `MemoryNamespaceKind(value)` 校验；非法值 raise（NFR-3） |
| Phase D D2 删硬编码后破坏 existing profile 重启行为（merge 顺序错） | 中 | D5 单测专项 + D2 verbatim 测试值完全一致 |
| Phase B B3 `_resolve_scope_id` 扩展引起 SessionMemoryExtractor 启动 worker 提取（原 F093 已锁定不启动）| 高 | spec NFR-5 + spec §3.2 显式排除——Phase B 实施前确认 SessionMemoryExtractor 在 worker 仍不跑（session_memory_extractor.py:69 _EXTRACTABLE_SESSION_KINDS 只含 MAIN_BOOTSTRAP）；不动这个白名单 |
| Phase B B6 emit 事件新字段破坏 F087 e2e 测试（payload 严格匹配） | 低 | 加字段不删不改，向后兼容；F087 e2e 检查 schema_version + 关键字段不严格 match all keys |
| Phase E E2 写 maintenance_runs 但底层 no-op 造成 audit 表逐次 apply 累积 | 低 | idempotency_key 短路返回；rollback 删除路径完整可用 |
| Phase F F3 rebase F095 冲突在 agent_context.py 间接耦合（块 B / 块 D 都改 agent_context.py） | 中 | F095 改 `agent_decision.py` 不在同一文件；F094 块 B 改 line 2323-2327 + 块 D 改 line 2693-2707，与 F095 改动 line 不重叠（实测验证）|
| Codex Final review 抓 high finding 推迟 | 中 | F091/F092/F093 实证应对方法：Phase 前 review 暴早；Final review 兜底；命中 high 立即处理或显式拒绝带原因 |

---

## 5. 验收映射（spec → plan task）

| Spec 验收项 | Plan Phase Task | Plan 测试 |
|-------------|-----------------|-----------|
| A1-A6（spec 阶段产出）| 已完成（spec.md commit）| - |
| B1-B6（核心新行为）| Phase B B3 / B4 / B5 / B6 | B7 测试 1-5 |
| B7（worker 不再创建 WORKER_PRIVATE）| Phase B B1 / B2 | B7 测试 1 + 测试 6（grep）|
| C1-C5（RecallFrame 双字段 + DDL + API）| Phase C C3 / C4 / C5 / C6 / C7 | C9 单测 |
| C6（memory_maintenance_runs DDL 补列）| Phase C C1 | C9 单测 |
| C7（MemoryNamespace 三元组 unique 约束）| Phase C C2 | C9 单测 |
| D1-D4（行为零变更清理）| Phase D D1 / D2 / D3 / D5 | D4 单测 |
| D5（merge 顺序保留）| Phase D D2 | D4 测试 2 |
| E1-E6（migrate-094 CLI）| Phase E E1 / E2 / E3 / E4 | E4 单测 |
| G1（全量回归）| Phase F F1 | - |
| G2（e2e_smoke 每 Phase 后 PASS）| Phase C/D/B/E 末 + Phase F F2 | - |
| G3（每 Phase Codex review）| Phase C/D/B/E 末 commit 前 | - |
| G4（Final cross-Phase Codex review）| Phase F F4 | - |
| G5（rebase F095 后回归）| Phase F F3 | - |
| G6（completion-report）| Phase F F5 | - |
| G7（F096 接口点说明）| Phase F F5 | - |
| G8（Phase 跳过显式归档）| Phase F F5（若发生）| - |

---

## 6. Open（plan 阶段确认接力，仅剩 1 项）

> Plan 阶段 + Codex plan-stage review 已落地 spec §8 + plan §0.5 大部分 Open。

1. ~~Open-2~~：✅ Codex MED-2 闭环——agent_runtime_id 接线纳入 Phase B B0；source-of-truth 已锁定
2. **Open-10（rebase 时机）**：F094 完成时 F095 状态？
   - Phase F F3 实施时实测 `feature/095-*` 是否合到 master；分情况处理（已在 F3 加跨模块测试要求）

---

## 7. 索引（References）

- **spec.md** §0 + §2.1 + §2.2 + §5 + §7（plan 直接对应章节）
- **CLAUDE.local.md** §"M5 / M6 战略规划" / §"F093 实施记录" / §"工作流改进" / §"执行约束" / §"Spawned Task 处理流程"
- **`.specify/features/093-worker-full-session-parity/plan.md`**（plan.md 格式范本）
- **`apps/gateway/src/octoagent/gateway/services/agent_context.py:183-195, 2323-2327, 2693-2707, 2986-2995, 3105-3114, 3215-3228`**（块 B/D 主改文件 / 重要 line）
- **`apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py:59-66, 705-766`**（块 B B3 _resolve_scope_id 扩展点）
- **`apps/gateway/src/octoagent/gateway/services/builtin_tools/memory_tools.py:330-580`**（块 B B4 memory.write 工具）
- **`packages/core/src/octoagent/core/store/sqlite_init.py:536-550`**（块 C C2 memory_namespaces DDL）
- **`packages/memory/src/octoagent/memory/store/sqlite_init.py`**（块 C C1 memory_maintenance_runs DDL）
- **`packages/core/src/octoagent/core/models/enums.py:80-82, 203-209`**（块 B B6 事件枚举复用）
- **`packages/memory/src/octoagent/memory/migrations/migration_063_scope_partition.py`**（块 E migration 模板）
- **`packages/provider/src/octoagent/provider/dx/config_commands.py:897-964`**（块 E CLI 模板）

---

**Plan 生成完毕。** 等待 GATE_TASKS 用户审查后进入 Phase 6（tasks）+ 实施 Phase。

---

## 8. Plan-stage Codex Adversarial Review 闭环

Plan 阶段触发 Codex plan-stage review（model_reasoning_effort=high），抓 7 finding，全部接受闭环：

| Finding | 严重度 | 处理决议 | 落地章节 |
|---------|--------|----------|----------|
| **HIGH-1**：Gap-2 选 (b) 注入点错误（`memory.write` 走 `resolve_memory_scope_ids` 不是 `_resolve_scope_id`）| HIGH | **接受** | §0.1 重写注入点描述 + §1 Phase B B3/B4 改用新 helper `resolve_worker_default_scope_id` in `builtin_tools/_deps.py` |
| **MED-2**：B4 低估 agent_runtime_id 接线范围（ExecutionRuntimeContext 当前无该字段）| MED | **接受** | §0.5 新增子节固化接线方案 + Phase B 新增前置 task B0 |
| **MED-3**：archived namespace 过滤是 plan 假设非现状（list_memory_namespaces 无该过滤）| MED | **接受** | §0.4 新增"store API 必须同步过滤 archived" + Phase C 新增 task C7b |
| **MED-4**：B6 事件 MEMORY_ENTRY_ADDED 来源混淆（memory.write 当前不 emit）| MED | **接受** | Phase B B6 改为"memory.write commit 后**新增** emit 路径"，与 user_profile.update / memory_candidates.promote 现有 emit 解耦 |
| **MED-5**：Phase E dry-run "按 kind GROUP BY memory_sor" 不可执行（memory_sor 无 kind 列）| MED | **接受** | Phase E E2 改为"查 `memory_namespaces` GROUP BY kind + 可选 join `memory_sor.scope_id`" |
| **LOW-6**：idempotency_key 命名不规范 | LOW | **接受** | Phase E E2/E3 改用稳定分层命名 `octoagent.memory.migration.094.worker_memory_parity.noop.v1` |
| **LOW-7**：F095 "无重叠"表述过强（实际有 API/import 级依赖）| LOW | **接受** | Phase F F3 改描述 + 增加跨模块测试要求 |

**Codex 已验证无 finding 项**：
- Open-8 partial unique index：SQLite 3.51.2 + repo 已有 partial unique index 用法（`packages/core/.../sqlite_init.py:62-63, 797-807`、`packages/memory/.../sqlite_init.py:222-223`）；aiosqlite 兼容
- Open-11 namespace_id：`build_memory_namespace_id()` 包含 `kind.value`（agent_context.py:452-459），WORKER_PRIVATE → AGENT_PRIVATE 不会撞旧 namespace_id
- NFR-5：worker extractor 仍由 `_EXTRACTABLE_SESSION_KINDS = frozenset({MAIN_BOOTSTRAP})` 严守不启用
- D5 merge-order 测试：`_memory_recall_preferences()` 读 raw `context_budget_policy["memory_recall"]`；merge 顺序 `{**defaults, **existing}` 真实

**Final 阶段必须再次跑 Codex cross-Phase review**（按 spec NFR-8）。
