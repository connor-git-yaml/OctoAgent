# F117 影响分析报告（impact-report.md）

> Spec-Driver Refactor 模式 — Phase 1/5 影响分析
> 目标：WorkerProfile → AgentProfile 完全合并（架构债 D2，M5 两次推迟 F090/F107 的最后一块）
> Baseline：origin/master `7199f468`
> worktree：`.claude/worktrees/F117-profile-merge`（禁 uv sync）
> 状态：**待用户拍板架构方向**（见 §9 决策点）——迁移脚本/dry-run 在方向确定后产出

---

## 0. 执行摘要（一句话结论）

**F117 不是"加 kind + 删 WorkerProfile"的机械重命名。** 侦察发现 `worker_profiles` 是**运行时一等读源**（直接驱动 Worker 工具集 + MAIN/WORKER 执行体路由），`agent_profiles` 镜像是 **materialize-on-read**（每次 dispatch 重写，且不含工具字段）。"完全合并（消除独立表）"与"运行时行为零变更"之间存在**真实张力**：彻底合并会改变运行时**解析拓扑**（镜像塌缩 + dedup key 重设计 + chat/capability 读路径改写）。这一张力 + FE 线协议 key 是否保稳，是**只能由用户拍板**的两个分叉（§9）。数据存量极小（1 worker_profile / 1 revision / 1 镜像 agent_profile），数据迁移风险低，**结构风险高**。

---

## 1. 字段逐项对照（决策点 ①）

模型定义：`octoagent/packages/core/src/octoagent/core/models/agent_context.py`

| 字段 | AgentProfile（:192）| WorkerProfile（:223）| 合并语义 |
|------|---------------------|----------------------|----------|
| `profile_id` | ✅ PK | ✅ PK | **共享**（同名 PK；镜像路径下二者 profile_id 相同）|
| `scope` | default `SYSTEM` | default `PROJECT` | 共享字段，默认值不同 → 合并后按 kind 决定默认 |
| `project_id` | ✅ | ✅ | 共享 |
| `name` | ✅ | ✅ | 共享 |
| `kind` | ✅ `main/worker/subagent`（F090 加）| ❌ | **判别器**（worker 行 kind=worker）|
| `model_alias` | default `main` | default `main` | 共享 |
| `tool_profile` | default `standard` | default `minimal` | 共享字段，默认值不同 |
| `metadata` | ✅ | ✅ | 共享（镜像标记写在此）|
| `resource_limits` | ✅（**store 层未持久化，dead**）| ✅（**store 层未持久化，dead**）| 共享，二者均为 store 层死列（§6 隐患）|
| `created_at` / `updated_at` | ✅ | ✅ | 共享 |
| `persona_summary` | ✅ | ❌（用 `summary`）| **语义重叠**：镜像把 `summary→persona_summary`（worker_profile_ops）或 `=""`（entity_ensure），两 builder 不一致 |
| `instruction_overlays` | ✅ | ❌ | AgentProfile-only；worker 行保留 |
| `policy_refs` | ✅ | ❌ | AgentProfile-only |
| `memory_access_policy` | ✅ | ❌ | AgentProfile-only |
| `context_budget_policy` | ✅ | ❌ | AgentProfile-only（镜像写 memory_recall）|
| `bootstrap_template_ids` | ✅ | ❌ | AgentProfile-only |
| `version` | ✅ `int≥1` | ❌（用 revision 机制）| AgentProfile-only；与 worker 的 revision 体系并存 |
| `summary` | ❌ | ✅ | WorkerProfile-only → 合并到 persona_summary 还是新增列？（决策点）|
| `default_tool_groups` | ❌ | ✅ | **WorkerProfile-only，运行时一等读源**（工具集）|
| `selected_tools` | ❌ | ✅ | **WorkerProfile-only，运行时一等读源** |
| `runtime_kinds` | ❌ | ✅ | **WorkerProfile-only，运行时一等读源** |
| `status` | ❌ | ✅ `draft/active/archived` | WorkerProfile-only（编辑生命周期）|
| `origin_kind` | ❌ | ✅ `builtin/custom/cloned/extracted` | WorkerProfile-only |
| `draft_revision` | ❌ | ✅ | WorkerProfile-only（与 version 二选一/并存）|
| `active_revision` | ❌ | ✅ | WorkerProfile-only |
| `archived_at` | ❌ | ✅ nullable | WorkerProfile-only |

**结论**：共享 10 列；AgentProfile-only 8 列（main/worker 都可能用）；WorkerProfile-only 9 列（其中 `default_tool_groups/selected_tools/runtime_kinds` 是**运行时权威**，不可丢）。合并后 kind=worker 行需携带全部 worker-only 字段；kind=main/subagent 行这些字段留默认值。

---

## 2. 关键架构发现：镜像 + 运行时读拓扑（合并的真正难点）

### 2.1 两个实体是不同生命周期角色
- **WorkerProfile = 编辑/设计期实体**：用户 CRUD、draft/active/archived 工作流、revision 历史、工具选择编辑。
- **AgentProfile = 运行时解析 profile**：Agent 实际运行所用；对 Worker 而言是 worker_profile 的 **materialize-on-read 镜像**。

### 2.2 worker_profiles 是运行时一等读源（不是缓存）
| 运行时直读 worker_profiles | file:line | 驱动什么 |
|---|---|---|
| `CapabilityPack._resolve_worker_binding` | `apps/gateway/.../services/capability_pack.py:410-430` | Worker **真实工具宇宙**（tool_profile/default_tool_groups/selected_tools/model_alias）；镜像 agent_profile 仅 fallback（:431-448）|
| `chat.py::_resolve_profile_model_alias` | `apps/gateway/.../routes/chat.py:256-262` | dispatch 入口 model alias（worker_profiles 优先）|
| `chat.py::_resolve_owner_turn_executor_kind` | `chat.py:271-276` | **MAIN vs WORKER 执行体路由**（worker_profile 行存在 = 路由信号）|
| `_ensure_agent_profile_from_worker_profile` | `agent_context_entity_ensure.py:936-1003` | 每次解析**重新物化**镜像 |

→ 镜像主要供给 persona/permission/memory-recall/bootstrap 元数据；**工具配置的权威读源是 worker_profiles 本身**。彻底删表必须把这些字段迁到权威新位置并改写读路径。

### 2.3 三个不一致的镜像 builder（合并必须收口）
| builder | file:line | profile_id 方案 | persona_summary | metadata key |
|---|---|---|---|---|
| 生命周期/发布（持久化）| `worker_profile_ops.py:109` `_build_agent_profile_from_worker_profile` | **同 id** | `=summary` | `worker_profile_id` / `worker_profile_revision` / `worker_profile_status` |
| 运行时 resolve（materialize-on-read，持久化）| `agent_context_entity_ensure.py:936` | **同 id** | `=""` | `source_worker_profile_id` / `source_worker_profile_revision` |
| create-worker-with-project | `agent_service.py:617→634` / `_coordinator.py:971→988` | **`agent-profile-{id}` 前缀（不同 id）** | — | 无镜像标记（靠 id 前缀约定）|

镜像消费方读 `source_worker_profile_id`（`agent_decision.py:123`、`resolver.py:683`、`paths.py:40`）；并存 `metadata["source_kind"]=="worker_profile_mirror"` 与 `kind=="worker"` 双判据。**两套 id 约定 + 两套 metadata key + persona_summary 分歧**，是合并的最大收口点。

### 2.4 dedup key 耦合
`AgentRuntime` 同时带 `agent_profile_id`（:316）+ `worker_profile_id`（:317）。`find_active_runtime`（`agent_context_store.py:485-492`）**按 role 分支 dedup**：WORKER 按 `worker_profile_id` 去重，非 WORKER 按 `agent_profile_id`，由 partial unique index 强制。**合并 id 会破坏这个 role 分支查找**，必须重设计 dedup key + 索引。

### 2.5 stale-mirror 写路径（行为依赖）
两处写 worker_profiles 但**不同步重建镜像**（依赖下次 dispatch 的 materialize-on-read 兜底）：archive（`worker_service.py:861`）、resource_limits 更新（`agent_service.py:380`）。合并后若镜像塌缩，这条"懒同步"语义需保等价。

---

## 3. 3 表合一方案（决策点 ②）

表 DDL：`octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`
- `agent_profiles`（:382）/ `worker_profiles`（:404）/ `worker_profile_revisions`（:427，FK→worker_profiles.profile_id + UNIQUE(profile_id,revision)）

**存量（~/.octoagent 实例，两个 db 一致）**：agent_profiles=2（1 main + 1 worker 镜像，镜像 profile_id == worker_profile profile_id = `worker-profile-project-default-octo`）/ worker_profiles=1 / worker_profile_revisions=1。**无任何表有 DELETE**（仅 status=archived 软删）。

### revision 表 FK 处理（子选项）
- **R-A 迁到 `agent_profile_revisions`**（re-key FK→agent_profiles.profile_id）：与"agent_profiles 统一表"一致；agent profiles 此前无 revision 体系，等于把 revision 机制提升为 profile 通用能力。
- **R-B 保留 `worker_profile_revisions` 表名 + 仅改 FK 指向**：改动最小，但留"worker"命名残渣，D2 不彻底。
- **R-C 通用 `profile_revisions`**：最干净命名，迁移成本同 R-A。

### 合一冲突点（dry-run 必报）
worker 行（`worker-profile-project-default-octo`，带工具字段）与镜像 agent_profile 行（**同 profile_id**，带 instruction_overlays/context_budget_policy/bootstrap_template_ids + 镜像 metadata）在全合并下塌缩为**一行** → 必须**并字段集**（worker 工具字段 + agent 运行时字段都保留）。这是迁移唯一真实"冲突"，且因 PK 相同**不会产生 id 碰撞**，但需要确定性的字段合并规则。`agent-profile-project-default`（主 Agent，kind=main）不受影响。

---

## 4. Importer 改动清单（决策点 ③）

`rg WorkerProfile` 命中需排除 2 个假阳性符号：`WorkerProfileDeniedError`（worker_runtime.py 错误类）、`WorkerProfileDomainService`（control_plane 服务类）——**不属合并目标**。

### 4.1 非测试真实 importer（核心 4 符号 + 6 视图模型族）
| 文件 | 分类 | 重量 |
|---|---|---|
| `packages/core/.../models/agent_context.py` | **定义站**（4 符号）| — |
| `packages/core/.../models/control_plane/agent.py` | **视图族定义站**（6 类）+ 引用 2 枚举（:10）| 中 |
| `packages/core/.../models/__init__.py` | re-export | 低 |
| `packages/core/.../models/control_plane/__init__.py` | re-export | 低 |
| `packages/core/.../store/agent_context_store.py` | **CONSTRUCT + SERIALIZE**（`_row_to_worker_profile` :1386 / `_row_to_worker_profile_revision` :1411 + save/get/list 5 方法）| **高** |
| `apps/gateway/.../control_plane/worker_profile_ops.py` | CONSTRUCT + FIELD + 镜像 builder（:109）| **最高（43 占）** |
| `apps/gateway/.../control_plane/worker_service.py` | CONSTRUCT 视图 + Documents + 字段访问 | **最高（51 占）** |
| `apps/gateway/.../control_plane/agent_service.py` | CONSTRUCT worker + 配对 agent_profile（:617/:634）| 中 |
| `apps/gateway/.../control_plane/_coordinator.py` | CONSTRUCT（默认 bootstrap :971）| 中 |
| `apps/gateway/.../control_plane/session_service.py` | TYPE + 字段（status==ARCHIVED）| 低 |
| `apps/gateway/.../services/agent_context.py` | import only | 低 |
| `apps/gateway/.../services/agent_context_entity_ensure.py` | 镜像 builder（:936）+ status 字段 | **高** |
| `apps/gateway/.../services/capability_pack.py` | status 字段（:413/:470）+ worker_binding 读 | 中 |
| `apps/gateway/.../services/agent_decision.py` | 镜像消费（is_worker_behavior_profile）| 低 |
| `packages/core/.../behavior_workspace/resolver.py` | 镜像消费（:683）| 低 |
| `packages/core/.../behavior_workspace/paths.py` | 镜像消费（slug，:40）| 低 |
| `packages/skills/.../skills/limits.py` | 仅注释（无 import）| 极低 |

**测试文件（7 真实 + 需 bulk 更新）**：`test_chat_send_route.py` / `test_control_plane_api.py` / `test_delegation_plane.py` / `test_f105_conversation_binding.py` / `test_orchestrator.py` / `test_task_service_context_integration.py` / `packages/core/tests/test_agent_context_store.py`。另 mirror-marker fixture：`test_behavior_workspace.py`。

> 注：prompt 估"18 个非测试文件"，实测核心 importer ~15 个（部分为 re-export/消费方）+ 6 视图模型族。量级一致。

---

## 5. revision 机制收口方案（决策点 ④）

- 发布路径：`worker_profile_ops.py:_publish_worker_profile_revision`（:806）——snapshot builder（:720）+ 幂等守卫（:818）+ next_revision 计算 + 写 revision 行 + 同步推进 draft/active_revision + status=ACTIVE。
- **agent_profile 无 revision 体系**，只有 `version:int`（镜像时 `version=max(worker revision,1)`）。
- 合并后：revision 历史是 worker-only 能力，需决定（§3 R-A/B/C）revision 表归属。`version` 与 `draft/active_revision` 的关系也要定（建议 version 退化为派生或保留双轨）。

---

## 6. 隐患与待清理（合并顺手）

1. **两个 dead resource_limits 列**：agent_profiles + worker_profiles 均有 DDL 列 + ALTER，但 `AgentContextStore` 既不写也不 hydrate（`_row_to_agent_profile` :1355 / `_row_to_worker_profile` :1386 都跳过）。lifecycle 代码却读它（worker_profile_ops.py:733、agent_service.py:374）——**latent bug**。
2. **镜像 metadata key 不一致**（§2.3）：合并后统一为单一 key 体系。
3. **两套 id 约定**（同 id vs `agent-profile-{id}`）：`_coordinator.py:1010` 还反向 `replace("agent-profile-","")` 解 id——合并需统一。

---

## 7. FE 类型同步面（决策点 ⑤）

FE 单树 `octoagent/frontend`。**9 src + 7 test** 引用 WorkerProfile/worker_profile。
- 类型 SoT：`src/types/index.ts`（`WorkerProfileItem` :725 等 8 类型 + `WorkerCapabilityProfile` :1156 是**另一个** capability-pack 形状按 worker_type，勿混）。
- API：`src/api/client.ts` `fetchWorkerProfileRevisions`（:331→`GET /api/control/resources/worker-profile-revisions/{id}`）+ `submitAction`（→`POST /api/control/actions`，action_id=`worker_profile.*`）。
- 重消费：`agentManagementData.ts`、`AgentCenter.tsx`、`ChatWorkbench.tsx`、`WorkbenchLayout.tsx`、`SettingsResourceLimitsSection.tsx`（已并列建模 `agent_profile`|`worker_profile` 两 target kind——天然合并点）。

**关键杠杆**：UI **已把 worker-profiles 资源标为"Agent Profiles"**、action 标为"Root Agent"管理——**用户可见命名早已合并**，只剩线协议 key（`worker_profiles` / `worker-profiles` / `worker_profile.*` / `worker_profile_id`）+ TS 接口名仍带旧词。**若后端合并时保稳线协议 key，FE blast radius 塌缩为"纯类型重命名"（甚至零改）**；若改 key，则 16 文件 + 3 端点全动。

---

## 8. 迁移基础设施范式（决策点 ⑥ 的载体）

F094 `migrate-094` 模板（`packages/memory/.../migrations/migration_094_worker_private.py`）：
- 位置：`packages/<pkg>/src/octoagent/<pkg>/migrations/migration_<NNN>_<slug>.py`；CLI：`octo memory migrate-NNN --dry-run|--apply|--rollback`（cli.py 注册 click group，`memory_commands.py:70`）。
- 三入口均返回 JSON report dict：`run_dry_run`（只读，报 `total_*_to_migrate` + 分布快照 + idempotency_key + already_applied_run_id）/ `run_apply`（幂等短路 + 写 audit 行）/ `run_rollback`（按 run_id 删 audit）。
- **无 HTTP 迁移路由**——迁移仅 CLI（与既定约定一致）。core+memory 共享一个 `octoagent.db`，F117 core 表迁移可挂新 `agent` group 或复用 memory group。
- dry-run **已是一等支持 rows-affected/conflicts/irreversible-points 报告**（测试断言计数）。

→ F117 迁移将 clone 此范式：`run_dry_run` 报"合并 1 worker_profile + 1 镜像 reconcile + 1 revision 迁移 + drop 2 表（不可逆）"，`run_apply` 事务内做真实 DML + 幂等 audit，`run_rollback` 按 run_id。**脚本在 §9 方向确定后产出**（因目标 schema = 架构决策）。

---

## 9. 决策点（必须用户拍板，先于产出迁移脚本）

侦察证明：迁移脚本的**目标 schema = 架构选择**，且"零变更"与"消除独立表"有真实张力。以下两个分叉只能用户裁。

### 决策 A：合并深度 / 运行时拓扑
| 选项 | 末态 | 风险 | D2 闭合度 |
|---|---|---|---|
| **A1 彻底物理合并（推荐）** | 删 WorkerProfile 类 + worker_profiles + worker_profile_revisions 表；统一 agent_profiles + kind，worker 行携工具字段（运行时直读统一行）；镜像塌缩；revision re-key（§3 R-A/C）| **高**：改运行时解析拓扑（capability_pack/chat 读路径改写 + find_active_runtime dedup 重设计 + 索引重建）；不可逆迁移 | **真闭合 D2** |
| **A2 合表但保 materialize-on-read 归一步** | 合表，但保留"行读自身"的归一步，最小化读路径改动 | 中：留 vestigial 步骤 | 闭合，留小尾巴 |
| **B 仅类层统一** | 合并 Pydantic 类（一个 AgentProfile + kind + 可选 worker 字段），**保两表 + 镜像** | 低：行为天然保留 | **D2 仅半闭**（=第三次推迟，违背 F117 初衷）|

→ **"运行时行为零变更"语义需重定义**：A1 下应解读为"运行时**输出**零变更 / 解析路径有意收敛"，而非"字节级路径不变"。这一点需用户确认接受。

### 决策 B：FE 线协议 key 是否保稳
| 选项 | FE 改动面 |
|---|---|
| **保稳 `worker_profiles`/`worker_profile.*` 线协议（推荐）** | FE = 纯类型重命名（甚至零改）；线协议 key 改名作为后续装饰性 pass 或永不改 |
| **本次全改名 → `agent_profiles`/`agent_profile.*`** | 16 FE 文件 + 3 端点 + 路由 map 全动 |

### 我的推荐
- **决策 A → A1**（F117 唯一目的就是真闭合 D2；B 等于第三次推迟）。但前提是用户**接受"运行时输出零变更、解析路径有意收敛"的重定义**——否则只能退 A2/B。
- **决策 B → 保稳线协议**（UI 早已显示"Agent Profiles"，改 key 是纯装饰且放大 blast radius，无产品收益）。
- revision 表 → **R-A（agent_profile_revisions）** 或 R-C（profile_revisions），二选一交用户。

确认方向后：产出统一模型 + 迁移脚本 + **dry-run 结果**，回主 session 走**第二道闸**（不可逆迁移拍板），再进 Phase 3 分批实现（每 wave Codex+Opus 双评审）。
