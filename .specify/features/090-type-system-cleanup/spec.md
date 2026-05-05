---
feature_id: "090-type-system-cleanup"
title: "Type System & Naming Cleanup"
milestone: M5
phase: M5-阶段0-第1个
status: draft
created: 2026-05-05
updated: 2026-05-05
depends_on: []
blocks: ["091-state-machine-unification", "092-delegation-plane-unification"]
---

# Feature 090: Type System & Naming Cleanup

## 0. TL;DR

F090 是 M5 阶段 0 的第 1 个 Feature，**纯类型/命名重构，行为零变更**。原始范围覆盖
4 个债（D1 metadata flag / D2 WorkerProfile 合并 / D5 WorkerSession 合并 /
butler 残留）。Phase 1 影响分析后发现 3 处事实与原 spec 描述存在偏差，需在进入实施
前与决策方对齐范围收敛方案：

1. **`RuntimeControlContext` 已存在**（[orchestrator.py:33-71](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:33)）——
   D1 实际是"扩展现有 model + 吸收 metadata flag"而非"新建 model"
2. **`WorkerProfile` 有独立 SQL 表 + revision 机制 + FE 集成**——完全合并到
   `AgentProfile + kind` 涉及 schema 迁移 + FE 类型重构，超"零行为变更"红线，
   建议范围收敛到"显式化 worker kind"，完全合并推迟到 F107（M6）
3. **`WorkerSession` 与 `AgentSession` 语义不重叠**——前者是 dispatch 瞬时状态计数器
   （不持久化、随 dispatch 销毁），后者是 Agent 长期会话持久化对象，字面"合并"
   不通；建议改为"重命名 WorkerSession"消除命名歧义
4. **`BUTLER` 枚举别名已经删除**（grep 0 命中），剩余仅 normalize 兼容函数 +
   1 处 docs 术语 + migration 代码——量级 low

## 1. 动机

M5 阶段 0 的目的是把 4 个债（D1-D14 中的 D1/D2/D3/D4/D5）清干净，让阶段 1
（F093-F096 Worker 完整对等性）能在干净的类型系统上推进。F090 负责 D1/D2/D5/butler
四类**类型与命名**层面的债。F091 处理 D3（状态枚举），F092 处理 D4（委托代码统一）。

**为什么必须做**：

- D1（metadata flag）：`metadata.get("single_loop_executor")` 这类隐式 dict 访问散在
  3 个核心文件 22 处，类型系统无法约束 → 编译期发现不了 typo / 漏字段，运行期才崩
- D2（WorkerProfile 与 AgentProfile）：当前用 `_is_worker_behavior_profile()`
  靠读 `metadata["source_kind"]` 字符串判断 worker → 隐式判断难审计且易绕过
- D5（WorkerSession 命名歧义）：`Worker*Session*` 与持久化 `AgentSession` 同名层
  让阅读者误以为是同一类型；实际是 dispatch 计数器，名实不符
- Butler 命名残留：清理已完成 99%，剩余收尾

## 2. 范围与不变量

### 2.1 设计原则

| 原则 | 含义 |
|------|------|
| **行为零变更** | 纯类型/命名重构。运行时行为必须 100% 等价。任何 e2e 回归都是 bug |
| **不动 schema** | 不改 SQL 表结构、不加列、不删列。如果某个目标必须动 schema 才能完成，
该目标退出 F090 范围（推迟到对应 Feature） |
| **不改决策环 / 状态枚举 / 委托代码** | 这是 F100/F091/F092 范围 |
| **不动行为文件加载逻辑** | F095 范围（Worker Behavior Workspace Parity） |
| **保留向后兼容数据防御** | normalize_runtime_role / normalize_session_kind 等
兼容函数是数据防御层（应对历史脏数据），保留 |

### 2.2 进入 F090 实施的范围（**待用户拍板**）

四个债的"实际可做范围"基于 Phase 1 影响分析的事实重新判定。每条标 [建议] 的项
都需用户在 Phase 2 分批规划前确认。

#### D1 metadata flag → 扩展 RuntimeControlContext

**事实**（与原 spec 描述偏差）：
- `RuntimeControlContext` 已存在 25 字段（task_id / surface / scope_id /
  turn_executor_kind / agent_profile_id / context_frame_id / metadata 等）
- `OrchestratorRequest` / `DispatchEnvelope` 已有 `runtime_context: RuntimeControlContext | None` 字段
- `TurnExecutorKind` 枚举已存在（SELF / WORKER / SUBAGENT）

**[建议] F090 实施方案**：

- 扩展 `RuntimeControlContext` 加 2 个字段：
  - `delegation_mode: Literal["main_inline", "main_delegate", "worker_inline", "subagent"]`
    （比现有 `turn_executor_kind` 更精细——区分 main 是 inline 执行还是 delegate
    给 worker）
  - `recall_planner_mode: Literal["full", "skip", "auto"]`（替代当前 `single_loop_executor` 隐式语义）
- 改造 3 个核心文件 22 处 metadata flag 读写：
  - [orchestrator.py:758-841](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:758)
    `single_loop_executor` / `single_loop_executor_mode` / `agent_execution_mode`
    写入 → 改写到 `runtime_context.delegation_mode`
  - [llm_service.py:218, 374-379, 423](octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py:218)
    `supports_single_loop_executor` 类属性 + `metadata.get("single_loop_executor")`
    读取 → 改读 `runtime_context.delegation_mode`
  - [task_service.py:1022-1044](octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:1022)
    `_metadata_flag` 通用方法 + recall planner skip 判断 →
    改读 `runtime_context.recall_planner_mode`
- **不改的部分**（这些是数据载荷不是控制信号）：
  - `selected_worker_type` / `selected_tools` / `recommended_tools` / `tool_selection`
    这些 47 处分布在 24 个文件，是 **DispatchEnvelope.metadata 的合理用法**（数据载荷），
    不属于 RuntimeControlContext 控制信号范畴 → 留在 F107 capability layer 重构

**改动量级估计**：~3 文件 / ~30 行（含 RuntimeControlContext 字段定义）

#### D2 WorkerProfile 合并 → **[范围收敛建议]** 仅显式化 worker kind

**事实**（与原 spec 描述偏差）：
- `WorkerProfile` 有独立 SQL 表 `worker_profiles`（含 status / origin_kind /
  draft_revision / active_revision / archived_at 等生命周期字段）
- 独立 `worker_profile_revisions` 表管理已发布 revision
- AgentProfile 缺这些字段（version 字段是另一个语义）
- FE 已集成 WorkerProfile（agentManagementData.ts / client.ts / types/index.ts）
- `_is_worker_behavior_profile()` 通过读 `metadata["source_kind"] == "worker_profile_mirror"`
  判断（[behavior_workspace.py:1551](octoagent/packages/core/src/octoagent/core/behavior_workspace.py:1551)），
  但 1551 是 metadata 探测的根因——这才是 F090 应该消除的隐式判断

**[建议] F090 实施方案**（范围收敛）：

- 给 AgentProfile 增加 `kind: Literal["main", "worker", "subagent"]` 字段（默认 `"main"`）
- WorkerProfile→AgentProfile 镜像逻辑里写入 `kind="worker"`
- `_is_worker_behavior_profile()` 改读 `agent_profile.kind == "worker"`
- WorkerProfile 类**完全保留**（独立 SQL + 独立类型）
- WorkerProfile 不导出为 deprecated alias（保留就不需要 alias）

**[建议] 不在 F090 做**（推迟到 F107 Capability Layer Refactor）：

- 完全合并 WorkerProfile→AgentProfile（含 schema 迁移 + revision 机制扩展 + FE 重构）
- 这是大手术，需要单独 spec 评估 schema 变更风险

**改动量级估计**：~3 文件 / ~10 行（AgentProfile 加字段 + 镜像逻辑写 kind +
behavior_workspace 改读 kind）

#### D5 WorkerSession 合并 → **[范围调整建议]** 重命名

**事实**（与原 spec 描述偏差）：
- WorkerSession 是 **dispatch 瞬时状态计数器**：[orchestrator.py:165](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:165)，
  字段 = session_id / dispatch_id / task_id / worker_id / state(WorkerRuntimeState) /
  loop_step / max_steps / budget_exhausted / tool_profile / backend
- **不持久化**（无 SQL 表，仅在 worker_runtime.py 内存中创建）
- 与 AgentSession（持久化 + 长期会话 + recent_transcript / rolling_summary /
  memory_cursor_seq 等 19 字段）**字段集几乎不重叠**
- 仅 17 处 production 命中 / 7 文件
- AgentSessionKind 已有 WORKER_INTERNAL / DIRECT_WORKER 枚举值（但语义是
  "Agent 长期会话归属于 worker runtime"而非"dispatch 计数器"）

**[建议] F090 实施方案**（重命名而非合并）：

- 把 `WorkerSession` 重命名为 `WorkerDispatchState`（更准确反映其"dispatch 瞬时状态"
  语义）
- 改 7 个 production 文件的 import / 类型注解 / 构造调用
- 改 2 个 tests/ 测试文件
- 不导出 deprecated alias（17 处全部一次性改完）

**[建议] 不在 F090 做**：

- 真"合并"WorkerSession → AgentSession：语义不通（瞬时计数器 vs 持久化会话）。
  原 spec 描述基于"两者都叫 Session"的命名误解，但实际字段集差异巨大。
- 如果未来确实需要 dispatch 状态持久化（比如恢复中断 dispatch），那是 F091
  State Machine Unification 范畴，不是 F090

**改动量级估计**：~7 文件 + 2 测试 / ~17 行（仅是 grep + replace 命名）

#### Butler 命名残留清理

**事实**：
- `BUTLER` / `BUTLER_MAIN` / `BUTLER_PRIVATE` 枚举别名 **0 命中**（已删除）
- production 残留 35 处分布在 3 文件：
  - [startup_bootstrap.py:329-339](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:329)（17 处，
    一次性 SQL migration `_migrate_butler_naming` + `_migrate_butler_suffix`，已完成迁移）
  - [agent_context.py:80-94](octoagent/packages/core/src/octoagent/core/models/agent_context.py:80)（4 处，
    normalize_runtime_role / normalize_session_kind 兼容函数）
  - test_migration_063.py（14 处，测试 fixture）
- docs/ 5 处（octoagent-architecture.md "Butler Direct/Inline" 术语）

**[建议] F090 实施方案**：

- **删除**：startup_bootstrap.py 的 `_migrate_butler_naming` / `_migrate_butler_suffix`
  函数体（已运行过、用户实例已迁移完毕，留着是死代码）。**注意**：删除前要确认所有
  active 实例（octoagent-agent / octoagent-master / 各 worktree 的 ~/.octoagent）
  已经过这些 migration——不然新实例首启会带脏数据
- **保留**：normalize_runtime_role / normalize_session_kind 函数（数据防御层，
  即使 SQL migration 完成，外部数据导入路径仍可能携带 butler 字符串）
- **保留**：test_migration_063.py 测试 fixture（migration 测试需要历史数据样本）
- **修订**：docs/octoagent-architecture.md "Butler Direct" 术语 → "Main Direct"
  （除非文档里这个术语指特定历史阶段，否则建议改）

**改动量级估计**：~2 文件 / ~50 行删除 + ~5 行 docs 修订

### 2.3 不在 F090 范围（明确排除）

- 不改决策环行为（F100 范围）
- 不动状态枚举（F091 范围）
- 不动 DelegationPlane 委托代码统一（F092 范围）
- 不改 Worker 真实运行行为（F093-F096 范围）
- 不改 capability_pack / tooling / harness 三层职责（F107 M6 范围）
- 不动 agent_context.py 4111 行拆分（F093 顺手清的债）
- 不动 orchestrator.py 拆 dispatch_service（F098 顺手清的债）
- 不动 SQL schema（worker_profiles 表保留 / agent_profiles 表保留）
- 不动 FE WorkerProfile 类型（FE 类型清理推迟到 F107）

## 3. 不变量

| 不变量 | 验证方法 |
|--------|---------|
| 全量 pytest 与 F089 baseline 0 regression | `pytest octoagent/` 比对 passed/failed 数量 |
| e2e_smoke 与 F089 baseline 行为一致 | `pytest -m e2e_smoke` 5x 循环 |
| `single_loop_executor` 在 production 0 命中（除 RuntimeControlContext 内部 deprecated comment） | `grep -rn "single_loop_executor" octoagent/{apps,packages}/` |
| `_metadata_flag` 在 production 0 命中（已被 RuntimeControlContext 取代） | grep |
| `class WorkerSession` 0 命中 | grep |
| `_is_worker_behavior_profile` 实现里不再 `metadata.get("source_kind")` | 读 behavior_workspace.py 1551 行 |
| 现有 worker / main / subagent 三种执行路径全部跑通 | e2e #5 delegate_task / #6 max_depth / #4 Skill |
| WorkerProfile / AgentProfile / WorkerSession→WorkerDispatchState 的 `from x import` 兼容性 | tests/导入路径不变 |
| Codex adversarial review 每 Phase commit 前通过 | `/codex:adversarial-review` |

## 4. 验收 checklist（完成时回报用户）

- [ ] 影响面统计（D1/D2/D5/butler 各影响多少文件 + 多少行）
- [ ] Phase 切分图 + 每 Phase commit hash + Codex review 闭环结果
- [ ] 残留 grep 结果：
  - `single_loop_executor` 在 production 命中数（应为 0）
  - `class WorkerSession` 命中数（应为 0）
  - WorkerProfile alias 决定（保留 / 删除）+ 理由
  - BUTLER 枚举别名（已 0 命中，确认）
- [ ] 全量回归 passed / failed 数 vs F089 baseline
- [ ] e2e_smoke 状态（每 Phase）
- [ ] WorkerProfile 处理决定 + 理由
- [ ] BUTLER 残留处理决定 + 理由（migration 函数删 vs 保留）
- [ ] F091 (State Machine Unification) 接口点说明：
  - F090 引入 `delegation_mode` Literal（不是 enum）→ F091 决定是否转 enum 一并归并
  - WorkerRuntimeState 状态枚举留给 F091 改动
  - AgentSession.kind / AgentSessionStatus 留给 F091

## 5. **暂停决策点**：F090 范围最终拍板

进入 Phase 2 分批规划前，请用户在以下三个收敛建议各自拍板：

| 决策点 | 选项 A（建议保守） | 选项 B（按原 spec） | 我的推荐 |
|-------|------------------|------------------|---------|
| **D1 范围** | 仅扩展 RuntimeControlContext + 22 处 flag 改造 | 同时清理 47 处 selected_worker_type 等数据载荷 | A（B 是 F107 capability layer 范围） |
| **D2 范围** | 仅加 AgentProfile.kind 字段 + 改 _is_worker_behavior_profile 读 kind | 完全合并 WorkerProfile→AgentProfile（含 SQL schema + FE 重构） | A（B 是 F107 大手术，行为不再零变更） |
| **D5 范围** | 重命名 WorkerSession → WorkerDispatchState | 删除 WorkerSession 改 AgentSession(kind=WORKER_INTERNAL) | A（B 字段集差异巨大语义不通） |

如果三项都选 A：F090 实际改动 < 50 文件 / < 200 行，可在 4 个 Phase（D1 / D2 /
D5 / Butler）内完成，每 Phase 可独立 commit + Codex review + 回归。

如果选 B：F090 必须扩成"前置 schema migration + 双对象迁移 +
FE 重构 + 数据防御"，需要先 spec 一个 migration plan，已超 F090 边界（建议另起 feature）。

---

## 附录 A：与 CLAUDE.local.md M5 规划的对齐

本 spec 范围调整不改变 M5 整体目标——D1/D2/D5/butler 仍然清完，只是 D2/D5 的"完全合并"
推迟到 M6 F107 Capability Layer Refactor（与 D9/D11/D12 一并做）。M5 阶段 0 的
"前置债清理"目的（让 F091/F092 在干净类型上跑）仍然达成：

- F091 State Machine Unification 需要的前提：状态枚举边界清晰 → F090 加的
  `delegation_mode` 提供了控制信号锚点
- F092 DelegationPlane Unification 需要的前提：runtime context 显式 → F090
  扩展了 RuntimeControlContext 字段

## 附录 B：Phase 1 影响分析详细数据

详见 [impact-report.md](./impact-report.md)（独立文件）。
