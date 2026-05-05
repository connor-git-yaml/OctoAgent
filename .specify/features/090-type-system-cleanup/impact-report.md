# F090 Phase 1 影响分析报告

生成时间：2026-05-05
扫描范围：`octoagent/apps/` + `octoagent/packages/` + `octoagent/frontend/`
（不含 .specify/ docs/ tests/ _references/ _research/，但 tests/ docs/ 单独计数）
基线 commit：`ff4635d` (master HEAD)

## 1. D1 metadata flag → RuntimeControlContext

### 1.1 关键发现

**`RuntimeControlContext` 类已存在**于 [orchestrator.py:33-71](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:33)，
含 25 字段（task_id / surface / scope_id / hop_count / max_hops /
worker_capability / route_reason / model_alias / tool_profile / work_id /
parent_work_id / pipeline_run_id / session_owner_profile_id /
inherited_context_owner_profile_id / delegation_target_profile_id /
**turn_executor_kind: TurnExecutorKind** / agent_profile_id /
context_frame_id / metadata 等）。

`OrchestratorRequest` (orchestrator.py:97) 与 `DispatchEnvelope` (orchestrator.py:133)
已经持有 `runtime_context: RuntimeControlContext | None` 字段。

`TurnExecutorKind` 枚举（orchestrator.py:25-30）已有 `SELF / WORKER / SUBAGENT`。

**结论**：D1 是"扩展现有 model + 把 metadata flag 吸收成显式字段"，不是"新建 model"。

### 1.2 直接影响范围（必改）

| 文件 | 命中行 | 性质 |
|------|--------|------|
| [orchestrator.py](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py) | 9 处 | metadata dict 写入 + 控制流读取 |
| ↳ L758 | `if self._metadata_flag(metadata, "single_loop_executor") and pack_rev == stored_rev:` |
| ↳ L785 | `f"single_loop_executor=main_{worker_type}"` (route_reason 拼接，可保留为日志) |
| ↳ L799-808 | metadata dict 写入 7 个键：single_loop_executor / single_loop_executor_mode / agent_execution_mode / selected_worker_type / selected_tools / recommended_tools / selected_tools_json / tool_selection / _pack_revision |
| ↳ L825 | `getattr(self._llm_service, "supports_single_loop_executor", False)` |
| [llm_service.py](octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py) | 9 处 | 控制流消费端 |
| ↳ L218 | `supports_single_loop_executor = True` 类属性 |
| ↳ L374 | `metadata.get("selected_worker_type", "")` 读取 |
| ↳ L375 | `single_loop_executor = self._metadata_flag(metadata, "single_loop_executor")` |
| ↳ L379, L423, L912, L919, L921, L984 | 条件分支与参数传递 |
| [task_service.py](octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py) | 2 处 | 通用方法 + recall planner skip |
| ↳ L1022-1026 | `_metadata_flag()` 静态方法定义 |
| ↳ L1044 | `if self._metadata_flag(dispatch_metadata, "single_loop_executor"): return None` |

**直接命中**：3 文件 / 22 处。

### 1.3 间接影响（数据载荷，建议留给 F107）

`selected_worker_type` 在 24 个文件 47 处分布（包括 delegation_plane.py /
agent_context.py / work_store.py / work.py / connection_metadata.py 等）。

**这些是 dispatch 数据载荷**，不是控制信号——属于 capability_pack 的工具选择产物，
应留给 F107 Capability Layer Refactor 一并清理（合并 D9/D11/D12）。

### 1.4 D1 范围拍板建议

**[选项 A 建议]** F090 仅清理 22 处控制信号 → ~30 行改动 / 3 文件

**[选项 B 原 spec]** 同时清 47 处 selected_worker_type → ~80 行改动 / 27 文件
但混淆了"控制信号"与"数据载荷"边界，不推荐。

---

## 2. D2 WorkerProfile 合并

### 2.1 关键发现

WorkerProfile（[agent_context.py:142-163](octoagent/packages/core/src/octoagent/core/models/agent_context.py:142)）
与 AgentProfile（[agent_context.py:120-139](octoagent/packages/core/src/octoagent/core/models/agent_context.py:120)）
**字段集差异显著**：

**WorkerProfile 独有**（生命周期管理）：
- `summary` (vs AgentProfile 的 `persona_summary`)
- `default_tool_groups: list[str]`
- `selected_tools: list[str]`
- `runtime_kinds: list[str]`
- `status: WorkerProfileStatus` (DRAFT / ACTIVE / ARCHIVED)
- `origin_kind: WorkerProfileOriginKind` (CUSTOM / TEMPLATE / SYSTEM)
- `draft_revision: int`
- `active_revision: int`
- `archived_at: datetime | None`

**AgentProfile 独有**（运行时上下文）：
- `persona_summary`
- `instruction_overlays: list[str]`
- `policy_refs: list[str]`
- `memory_access_policy: dict`
- `context_budget_policy: dict`
- `bootstrap_template_ids: list[str]`
- `version: int`

**共有但默认值不同**：
- `scope`: Agent=SYSTEM, Worker=PROJECT
- `tool_profile`: Agent="standard", Worker="minimal"

### 2.2 持久化层独立性

**独立 SQL 表**：`worker_profiles`（含 status / origin_kind / draft_revision /
active_revision / archived_at 5 个 worker 独有列）

**独立 revision 表**：`worker_profile_revisions`（管理已发布 revision 快照）

**外键关系**：`agent_runtimes` 表同时持有 `agent_profile_id` + `worker_profile_id`
（[agent_context.py:235-236](octoagent/packages/core/src/octoagent/core/models/agent_context.py:235)），
`role` 字段区分。`works` 表有 `requested_worker_profile_id` +
`requested_worker_profile_version` 列。

### 2.3 production 命中分布

| 文件 | 命中 |
|------|------|
| octoagent/packages/core/src/octoagent/core/models/agent_context.py | 类定义 + 文档注释 |
| octoagent/packages/core/src/octoagent/core/models/__init__.py | 导出 |
| octoagent/packages/core/src/octoagent/core/store/agent_context_store.py | 16 处（save / get / list / revision / 行映射） |
| octoagent/packages/core/src/octoagent/core/models/control_plane/agent.py | 类型注解 |
| octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py | 异常类（WorkerProfileNotFound 等） |
| octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py | 类型注解 |
| octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py | 类型注解 |
| octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/worker_service.py | WorkerProfileDomainService（17 方法） |
| octoagent/packages/skills/src/octoagent/skills/limits.py | 文档注释 |
| octoagent/frontend/src/types/index.ts | TS 类型定义 |
| octoagent/frontend/src/api/client.ts | API client |
| octoagent/frontend/src/domains/agents/agentManagementData.ts | UI 数据流 |

**production**：17 文件 / ~230 行
**tests/**：9 文件 / 92 行
**docs/**：0 处

### 2.4 `_is_worker_behavior_profile` 隐式判断

实现：[behavior_workspace.py:1551](octoagent/packages/core/src/octoagent/core/behavior_workspace.py:1551)
```python
def _is_worker_behavior_profile(agent_profile: AgentProfile) -> bool:
    metadata = agent_profile.metadata
    return (
        str(metadata.get("source_kind", "")).strip() == "worker_profile_mirror"
        or bool(str(metadata.get("source_worker_profile_id", "")).strip())
    )
```

调用点：1 处（同文件 L960，`resolve_behavior_workspace_files` 中按是否 worker
选不同行为文件加载策略）。

**这是 D2 真正的"隐式判断"反模式**——靠读 metadata 字符串判断，且这个判断分散在
behavior_workspace 内部（不在领域模型层），让"Worker 是什么"这个核心问题答案分散。

### 2.5 D2 范围拍板建议

**[选项 A 建议]** F090 仅做：
1. AgentProfile 加 `kind: Literal["main", "worker", "subagent"]` 字段（默认 "main"）
2. WorkerProfile→AgentProfile 镜像逻辑写入 `kind="worker"`
3. `_is_worker_behavior_profile()` 改读 `agent_profile.kind == "worker"`
4. WorkerProfile 类完全保留（不动 SQL / 不动 FE / 不动 store）

改动量级：~3 文件 / ~10 行（agent_context.py + behavior_workspace.py + 镜像逻辑）

**[选项 B 原 spec]** 完全合并 WorkerProfile→AgentProfile：
- 必须改 SQL schema（worker_profiles 表合并 / 加列 / migration 脚本）
- 必须改 store/agent_context_store.py 16 处
- 必须改 FE 3 个文件（types / client / agentManagementData）
- revision 机制需要扩展到 AgentProfile（draft/active/archived）
- 改动量级：~25 文件 / ~500 行 + schema migration + FE 类型重写
- **超"行为零变更"红线**（schema 变更不可能零行为变更）

强烈建议选 A，把 B 留给 F107 大手术。

---

## 3. D5 WorkerSession 合并

### 3.1 关键发现

**WorkerSession 与 AgentSession 语义不重叠**。

WorkerSession（[orchestrator.py:165-187](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:165)）
是 **dispatch 瞬时状态计数器**：
- 字段：session_id / dispatch_id / task_id / worker_id / state(WorkerRuntimeState) /
  loop_step / max_steps / budget_exhausted / tool_profile / backend (10 字段)
- 不持久化（无 SQL 表，仅在 worker_runtime.py 内存中创建一次）
- 生命期 = 一次 dispatch
- `_validate_loop` validator 强制 loop_step <= max_steps

AgentSession（[agent_context.py:257-289](octoagent/packages/core/src/octoagent/core/models/agent_context.py:257)）
是 **持久化长期会话**：
- 字段：agent_session_id / agent_runtime_id / kind / status / project_id /
  surface / thread_id / legacy_session_id / alias / parent_agent_session_id /
  parent_worker_runtime_id / work_id / a2a_conversation_id / last_context_frame_id /
  last_recall_frame_id / recent_transcript / rolling_summary / metadata /
  memory_cursor_seq / created_at / updated_at / closed_at (22 字段)
- 持久化（agent_sessions SQL 表）
- 生命期 = Agent 一次"工作会话"周期（可跨多个 dispatch）
- AgentSessionKind 枚举已有 WORKER_INTERNAL / DIRECT_WORKER / SUBAGENT_INTERNAL

**字段重叠**：仅 `tool_profile` 一项。其余字段含义都不同（worker_id 不是
agent_runtime_id，state 不是 status，loop_step 不是 memory_cursor_seq...）

### 3.2 production 命中分布

| 文件 | 命中数 | 性质 |
|------|--------|------|
| orchestrator.py (services) | 2 | import + 参数注解 |
| orchestrator.py (models) | 2 | 类定义 + validator |
| worker_runtime.py | 4 | import + 参数注解×2 + 构造×1 |
| adapters.py | 2 | import + 参数注解 |
| a2a_runtime.py | 1 | docstring |
| models/__init__.py | 2 | import + export |

**总计**：6 文件 / 13 处。tests/ 2 处（test_a2a_models.py / test_orchestrator.py）。

### 3.3 D5 范围拍板建议

**[选项 A 建议]** 重命名 WorkerSession → WorkerDispatchState：
- 改 6 production 文件 + 2 tests / ~17 处替换
- 不导出 deprecated alias（13 处一次性改完）

**[选项 B 原 spec]** 删除 WorkerSession，改 AgentSession(kind=WORKER_INTERNAL)：
- 字段集冲突：WorkerSession 的 dispatch_id / loop_step / max_steps /
  budget_exhausted / state(WorkerRuntimeState) / backend 在 AgentSession 都没有
- 强行合并需要：
  - 加 dispatch_id / loop_step / max_steps / budget_exhausted 字段进
    AgentSession（持久化层动土）
  - 或者把这些字段塞 metadata（又走回 D1 问题）
  - WorkerRuntimeState 与 AgentSessionStatus 语义不同，强映射会丢信息
- 建议不做（语义不通 + 持久化动土）

强烈建议选 A，让命名清晰即可，不做语义合并。

---

## 4. Butler 残留

### 4.1 命中聚合

**production**：3 文件 / 35 处
**tests/**：1 文件 / 14 处（test_migration_063.py）
**docs/**：1 文件 / 5 处（octoagent-architecture.md "Butler Direct"）

### 4.2 production 残留分布

[startup_bootstrap.py:329-339](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:329)（17 处）：
```python
def _migrate_butler_naming(conn) -> None:  # L329
    conn.execute("UPDATE agent_runtimes SET role = 'main' WHERE role = 'butler'")
    conn.execute("UPDATE agent_sessions SET kind = 'main_bootstrap' WHERE kind = 'butler_main'")
    conn.execute("UPDATE memory_namespaces SET kind = 'agent_private' WHERE kind = 'butler_private'")
    # ... 还有更多 SQL UPDATE

def _migrate_butler_suffix(store_group, agent_profile) -> None:  # L337
    # 改 metadata 中带 butler 后缀的字段
```

[agent_context.py:80-94](octoagent/packages/core/src/octoagent/core/models/agent_context.py:80)（4 处）：
```python
def normalize_runtime_role(value: str) -> AgentRuntimeRole:
    # 含 "butler" → MAIN 兼容映射

def normalize_session_kind(value: str) -> AgentSessionKind:
    # 含 "butler_main" → MAIN_BOOTSTRAP 兼容映射
```

### 4.3 BUTLER 枚举别名

**0 命中**。AgentRuntimeRole / AgentSessionKind 中无 BUTLER 枚举值。

### 4.4 Butler 处理建议

| 残留 | 建议 | 理由 |
|------|------|------|
| `_migrate_butler_naming` / `_migrate_butler_suffix` 函数体 | **删除** | 已运行过；新装实例从 master DDL 直接建表无 butler 列；这是死代码 |
| `normalize_runtime_role` / `normalize_session_kind` 兼容映射 | **保留** | 数据防御层，外部数据导入路径仍可能携带 butler 字符串 |
| test_migration_063.py fixture | **保留** | migration 测试需要历史数据样本 |
| docs/octoagent-architecture.md "Butler Direct" 术语 | **修订** → "Main Direct" | 术语已废弃，文档清晰度优先 |

⚠️ **删除 migration 函数前必须确认**：所有 active 实例（octoagent-agent /
octoagent-master / 各 worktree 的 ~/.octoagent）已经过这些 migration——通过
检查 SQLite `PRAGMA user_version` 或扫表确认无 butler 字符串残留。

---

## 5. 总影响面汇总

| 债 | 选项 A（建议） | 选项 B（原 spec） |
|----|---------------|------------------|
| D1 | 3 文件 / 30 行 | 27 文件 / 80 行（含数据载荷） |
| D2 | 3 文件 / 10 行 | 25 文件 / 500 行 + SQL migration + FE 重构 |
| D5 | 6+2 文件 / 17 处 | 不可行（字段集冲突） |
| Butler | 2 文件 / 50 行 + docs | 同 |
| **总计** | **~14 文件 / ~107 行** | **~52+ 文件 / ~700 行 + schema + FE** |

**建议路径（全选 A）**：
- 改动 < 50 文件 / < 200 行（含测试更新）
- 4 个 Phase 串行（D1 → D2 → D5 → Butler）
- 每 Phase 可独立 commit + Codex review + 全量回归
- 完全符合"行为零变更"原则
- 不阻塞 F091 / F092 推进

**激进路径（选 B）**：
- 必须先做 schema migration + FE 重构 spec
- 已超 F090 边界（建议另起 feature）
- 行为零变更不可能保证
