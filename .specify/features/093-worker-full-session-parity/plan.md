# Implementation Plan: F093 Worker Full Session Parity

**Feature Branch**: `feature/093-worker-full-session-parity`
**Baseline**: `7e52bc6` (F092 完成点)
**Spec**: [spec.md](./spec.md)
**Quality Checklist**: [quality-checklist.md](./quality-checklist.md)（21 PASS / 0 WARN / 0 FAIL）
**Status**: Draft（待 GATE_TASKS 用户拍板）

---

## 0. Plan 阶段额外侦察结论（坐实 spec §2.2 Gap-1~Gap-5）

> Plan 阶段第一步是用代码确认 spec 阶段提出的疑点。以下基于 F092 baseline (7e52bc6) 的实测结论。

### 0.1 Worker turn 写入端到端链路（Gap-1 / Gap-2）

实测路径（grep + Read 已确认每一跳）：

1. **`_build_context_resolve_request`** (`agent_context.py:1003`)：根据 `requested_worker_profile_id` / `parent_agent_session_id` / `target_agent_session_id` / `turn_executor_kind ∈ {WORKER, SUBAGENT}` 判定 `is_worker_request`，进而决定 `request_kind = WORKER`。
2. **`_resolve_agent_runtime_role`** (`agent_context.py:2033`)：`request_kind=WORKER` → `AgentRuntimeRole.WORKER`。
3. **`_ensure_agent_session`** (`agent_context.py:2229`)：根据 `(agent_runtime.role, parent_agent_session_id, work_id)` 三元组决定 `AgentSessionKind ∈ {DIRECT_WORKER, WORKER_INTERNAL, MAIN_BOOTSTRAP}`，复用或新建 session。
4. **`CompiledTaskContext.effective_agent_session_id`** (`agent_context.py:905, 934`)：直接赋值 `agent_session.agent_session_id`（即 worker session id）。
5. **`_build_llm_dispatch_metadata`** (`task_service.py:870`)：把 `compiled_context.effective_agent_session_id` 注入 `dispatch_metadata["agent_session_id"]`（仅当 metadata 中尚无值，避免覆盖 explicit 上传值）。
6. **`SkillExecutionContext`** (`llm_service.py:427`)：从 `metadata["agent_session_id"]` 读取 → `context.agent_session_id`。
7. **`AgentSessionTurnHook.before_tool_execute / after_tool_execute`** (`agent_session_turn_hook.py:59-83`)：仅依赖 `context.agent_session_id`，agent-agnostic，调用 `record_tool_call_turn / record_tool_result_turn` → 写 `agent_session_turns`。
8. **`_append_agent_session_turn`** (`agent_context.py:1764`)：完全 agent-agnostic，无 worker/main 过滤。

**结论 Gap-1 / Gap-2**：链路在 baseline **理论上已通**。F093 的真实工作分两类：
- (a) **写一个最小 e2e 失败测试**（pytest）刻意触发 worker dispatch → 跑工具 → 断言 worker session turn 数 ≥ 2。如果通过，说明运行时已通；如果失败，定位具体哪一跳断了。
- (b) **加 worker 维度的 hook unit test**（已有 `apps/gateway/tests/test_agent_session_turn_hook.py` 只覆盖 MAIN_BOOTSTRAP；需要 mirror 一个 WORKER_INTERNAL/DIRECT_WORKER 变体）。

### 0.2 RecentConversation 读路径（Gap-3）

`build_agent_session_replay_projection` (`agent_context.py:1764` 附近) 与 `_append_session_transcript_entries` (`agent_context.py:1810`) 是按 `agent_session_id` 严格过滤的（store 读取以 session id 为 PK 维度）。**结论 Gap-3**：读隔离应已成立，但仍需新增一条 isolation 断言（构造 main+worker 两条 session 各写 turn，分别读各自的 transcript 互不混入）。

### 0.3 Worker session 持久化 round-trip（Gap-4）

`AgentSession.rolling_summary` 与 `memory_cursor_seq` 字段已存在（packages/core/agent_context.py:303/306）；`agent_context_store.save_agent_session / get_agent_session` 已 round-trip 包含这两字段（store 实现层无 kind-aware 过滤）。**结论 Gap-4**：模型层与持久化层 OK；F093 仅需在 worker kind 维度补一条 round-trip 单测，外加"互不污染" 断言。

### 0.4 拆分边界（Gap-5）

`apps/gateway/src/octoagent/gateway/services/agent_context.py` 4112 行结构：
- 顶部 imports（line 1-90）
- `AgentContextService` 类（line 200-3500+）：含管理 / 查询 / 上下文递推 / budget 协作 / Memory Recall / 持久化辅助
- 后段含若干 helper 函数（如 `_render_snapshot`、`_normalize_session_transcript_entries`）

**Plan 决策（Open-1）**：选 **候选 C**（最少拆分，2 文件），降低拆分本身的工程风险：
- 主文件保留 `agent_context.py`，仍含 `AgentContextService` 类壳与对外 `__init__.py` re-export 接口
- 抽出 `agent_context_turn_writer.py`（约 200-400 行），承载所有 `_append_agent_session_turn` / `record_tool_call_turn` / `record_user_turn` / `record_tool_result_turn` 系列与配套 helper
- **理由**：(a) F093 主要新增的是 turn 写入维度，把 turn 相关逻辑独立出去就解决了「继续在 4112 行单文件里塞」的痛；(b) 最小破坏面，回归风险最低；(c) 后续 Feature（F098 D7 顺手清 task_service / orchestrator dispatch）有明确的扩拆模式可循。

候选 A（3 文件）/ B（5 文件）暂不采纳，理由：拆得过细会增加 import 表面积与回归风险，块 A/B 主任务才是重点；进一步拆分留给 F098 / 后续顺手 Feature。

> **拒绝注释/补丁式拆分**：所有移动到新文件的方法必须从 `agent_context.py` 中**删除**（不留 stub / `# moved to ...` 注释）；原 `agent_context.py` 通过 imports 顶部 `from .agent_context_turn_writer import ...` 形式与同一类作业通过 mixin / 显式委派完成，确保对外 `from octoagent.gateway.services.agent_context import AgentContextService` 无变化。

---

## 1. Phase 切分（C → A → B → D）

### Phase C：`agent_context.py` 拆分（最小风险，先建脚手架）

**目标**：把 turn-writer 系列方法从 `AgentContextService` 抽到 `agent_context_turn_writer.py` 作为 mixin，行为零变更，全量回归 0 regression vs F092 baseline。

**任务清单**：

- C1：抽出 `AgentContextTurnWriterMixin` 类到 `agent_context_turn_writer.py`，搬迁方法：
  - `_append_agent_session_turn`
  - `record_tool_call_turn`
  - `record_tool_result_turn`
  - 其他 USER/ASSISTANT/CONTEXT_SUMMARY 类 turn 写入辅助（视实测扫描结果）
- C2：`AgentContextService(...)` 类签名改为继承 mixin（`class AgentContextService(AgentContextTurnWriterMixin):` 或直接组合 `self._writer = AgentContextTurnWriter(stores)`）。**首选 mixin**（保留方法直接调用语义，调用方零改动）。
- C3：`agent_context.py` 中删除被搬走的方法定义（彻底删，不留 stub）。
- C4：导入路径修复（`agent_context_turn_writer.py` 反向 import 必要符号；避免循环 import）。
- C5：跑全量 unit + integration 回归 + e2e_smoke。
- C6：commit Phase C。

**验收**：
- 全量回归 0 regression vs F092 baseline (7e52bc6)
- e2e_smoke PASS
- `from octoagent.gateway.services.agent_context import AgentContextService` 仍 OK
- `agent_context.py` 行数减少 ≥ 200（目标：从 4112 → 3700~3900）

**Codex review 触发**：Phase C commit 前。重点检查：(1) 是否有遗漏未搬迁的方法导致 mixin 间接调用循环，(2) `__init__.py` 是否需要同步更新 re-export 列表，(3) Pyright/mypy 是否仍 clean。

---

### Phase A：Worker turn 写入端到端验证 + 隔离断言

**目标**：用测试坐实 spec §2.2 Gap-1~Gap-3 在 baseline 是否真通，必要时修补；产出至少 3 条新单测覆盖 acceptance A1-A4。

**任务清单**：

- A1：写 `apps/gateway/tests/test_agent_session_turn_hook.py` 新增 `test_hook_records_tool_turns_for_direct_worker_session` 与 `test_hook_records_tool_turns_for_worker_internal_session`：复用既有 fixture，仅把 runtime role 改为 WORKER、session kind 改为 DIRECT_WORKER / WORKER_INTERNAL、parent_agent_session_id 设值；断言 turn store 写入 ≥ 2 条且 `agent_session_id` 与构造的 worker session 一致。
- A2：写 `apps/gateway/tests/test_worker_session_turn_isolation.py` 新增隔离断言：
  - 构造一个 main session + 一个 worker session（同 project / 不同 runtime）
  - 给 worker hook 触发 1 次 tool call → 1 条 tool_call + 1 条 tool_result turn 写到 worker session
  - 断言 main session 的 turn count 为 0（隔离）
  - 断言 worker session 的 replay projection 不含 main 的内容（反向隔离）
- A3：写 `tests/integration/test_f093_worker_full_session_e2e.py`（或合入既有 `test_f009_worker_runtime_flow.py`）端到端断言：
  - 用现成 e2e harness（OctoHarness）跑一次 worker dispatch（DIRECT_WORKER 路径），让 worker 实际跑工具
  - 断言 `agent_session_turns` 表里 worker session 的 turn 数 ≥ 2 且 main session 不变
  - **此测试是 Gap-1/2 的真接受验**——如果跑挂，说明 propagate 链有断点，**先记录失败模式，再修补**（修补方式视具体断点：可能在 `_build_context_resolve_request` 或 `_build_llm_dispatch_metadata` 或 worker_runtime envelope metadata 注入）
- A4：写 RecentConversation 读路径单测（`test_recent_conversation_filters_by_session_id`）：构造 main + worker session 各写若干 turn，断言读 worker session id 不返回 main 的 turn，反之亦然。
- A5：事件 emit 验证（NFR-2 / acceptance A5）：
  - **Plan 决策（Open-3）**：复用现有事件 schema（不新增 `AGENT_SESSION_TURN_RECORDED` 枚举）。`save_agent_session_turn` 路径已有什么事件就保留什么；若 baseline 没事件，则 Phase A 末尾**新增一个 store-side TurnPersisted 事件**（统一 main / worker），事件 schema 含 `agent_session_id` / `task_id` / `turn_seq` / `kind` / `agent_session_kind`（前 4 项必须，第 5 项为新加便于 control_plane 区分）。
  - 事件类型决策放到 Codex review 后定夺；目前先扫一遍 baseline 已有事件 schema。
- A6：所有新增测试 PASS + 全量回归 0 regression + e2e_smoke PASS。
- A7：commit Phase A。

**验收**：
- A1 / A2 / A3 / A4 / A5 全部 PASS
- 全量回归 0 regression
- 事件 emit 决策已落到 plan / 实现一致

**Codex review 触发**：Phase A commit 前。重点：(1) 隔离断言是否充分（多 turn 多 task），(2) e2e 测试是否真覆盖 worker 主循环（非仅 hook 单元层），(3) 事件 schema 决策是否已闭环。

---

### Phase B：Worker session 字段持久化 round-trip

**目标**：补充 worker kind 维度的 `rolling_summary` / `memory_cursor_seq` round-trip 单测；显式断言 SessionMemoryExtractor 不在 worker 上启用。

**任务清单**：

- B1：写 `apps/gateway/tests/test_worker_session_field_round_trip.py`：
  - 构造一个 worker session 与一个 main session（同 project / 不同 runtime）
  - 给 worker session：写 `rolling_summary="W-summary"` + `memory_cursor_seq=7`，store.save → get
  - 给 main session：保持默认值
  - 断言：worker round-trip 完全一致（"W-summary", 7）；main session 字段不变（"", 0）
- B2：写 `test_session_memory_extractor_does_not_run_on_worker`：
  - 复用 `tests/integration/test_f067_session_memory_pipeline.py` 模式
  - 构造 worker session 形成 ≥ 3 条 turn
  - 触发 SessionMemoryExtractor 跑 main session（应正常工作）
  - 断言 worker session 的 `memory_cursor_seq` 仍是 0（即 extractor 没跑 worker；F094 才启用）
  - **如果 baseline extractor 实际已跑 worker**（导致 cursor 推进），Plan 阶段需在 Phase B 增加 short-circuit：让 extractor 显式只对 `kind=MAIN_BOOTSTRAP` 触发；否则 F094 将与"worker 已被处理"的脏状态对接
- B3：跑全量回归 + e2e_smoke。
- B4：commit Phase B。

**验收**：
- B1 / B2 PASS
- 全量回归 0 regression
- 显式断言 SessionMemoryExtractor 在 worker 上的当前行为（启用还是跳过）已坐实并写到 [completion-report.md](./completion-report.md) 给 F094 接力

**Codex review 触发**：Phase B commit 前。重点：(1) round-trip 是否真覆盖所有持久化路径（save_agent_session 全字段 vs 部分字段更新），(2) extractor 与 worker 的"显式不跑" 决策是否已落到代码（不依赖 baseline 偶然行为）。

---

### Phase D：Final 验证 + completion-report

**目标**：跑 Final cross-Phase Codex review；写 completion-report；准备等用户拍板 push。

**任务清单**：

- D1：跑全量回归 + e2e_smoke 一次（最终基线）。
- D2：触发 **Final cross-Phase Codex review**（参考 F091 / F092 实证有效）：
  - 输入：plan.md + 全 Phase commit diff（C→A→B）
  - 范围：是否漏 Phase / 是否偏离原计划且未在 commit message 说明 / 是否有跨 Phase 不一致
- D3：处理 Final review finding：
  - HIGH：必须修，再回归 + commit
  - MEDIUM：默认修；不修则 commit message 显式 reject
  - LOW：可推迟，commit message 标注 ignored
- D4：写 [completion-report.md](./completion-report.md)，含：
  - **实际 vs 计划** 对照表（Phase C/A/B 各自计划任务列表 → 实际做了什么 / 为什么偏离）
  - **Codex finding 闭环表**（per-Phase + Final cross-Phase，列出 N high / M medium / K low 的处理结果）
  - **acceptance 验收 checklist**（spec §5 全部条目逐条 ✓/✗ + 证据指针）
  - **F094 / F095 接入点重述**（spec §6 落地版）
  - **下一步建议**（push origin/master / 等用户拍板等）
- D5：不主动 push（按 CLAUDE.local.md §"Spawned Task 处理流程"），等用户拍板。

**验收**：spec §5 全局验收 G1-G7 全部 ✓。

---

## 2. 决策记录（Open Points 终结）

| Open Point | 决策 | 理由 |
|------------|------|------|
| **Open-1（拆分边界）** | 候选 C：2 文件（`agent_context.py` + `agent_context_turn_writer.py`） | 最小破坏面 + F093 主任务是块 A/B；拆分越细回归风险越大 |
| **Open-2（块 A propagate 真位置）** | Phase A1-A3 用 e2e 测试坐实；如有断点再修补 | TDD 原则：先证伪现状再下结论 |
| **Open-3（事件复用 vs 新增）** | 复用现有 schema；Phase A5 末尾按需新增 TurnPersisted（如果 baseline 完全没事件） | spec NFR-2 已要求；新增枚举只在确实没有时做 |
| **Open-4（块 B 范围）** | 仅 round-trip + 互不污染 + extractor 不跑 worker 断言；不动 cursor 推进逻辑 | F094 范围内事 |
| **Open-5（迁移影响）** | 拆分前 grep 确认无 alembic / dynamic import 依赖 `agent_context.py` 的私有方法 | 已 grep：`grep -rn "from .agent_context import _append_agent_session_turn"` 0 匹配；Phase C 起始再确认一次 |
| **Open-6（pre-commit hook）** | 不动；每 Phase commit 前手工跑全量 + e2e_smoke | F087 已建好 |

---

## 3. 测试策略

### 3.1 现有测试基线（不修改）

- `apps/gateway/tests/test_agent_session_turn_hook.py` — MAIN_BOOTSTRAP 路径已覆盖
- `tests/integration/test_f067_session_memory_pipeline.py` — main session memory extractor 已覆盖
- `apps/gateway/tests/test_session_memory_cursor.py` — cursor 行为已覆盖（main）
- `tests/integration/test_f009_worker_runtime_flow.py` — worker 主循环已覆盖（非 turn store 维度）

### 3.2 F093 新增测试

| 测试 | 文件 | 覆盖 acceptance |
|------|------|-----------------|
| `test_hook_records_tool_turns_for_direct_worker_session` | `apps/gateway/tests/test_agent_session_turn_hook.py` | A1 |
| `test_hook_records_tool_turns_for_worker_internal_session` | `apps/gateway/tests/test_agent_session_turn_hook.py` | A1 |
| `test_worker_session_turn_isolation` | `apps/gateway/tests/test_worker_session_turn_isolation.py`（新建） | A2 |
| `test_recent_conversation_filters_by_session_id` | `apps/gateway/tests/test_worker_session_turn_isolation.py` 同文件 | A3 / A4 |
| `test_f093_worker_full_session_e2e` | `tests/integration/test_f093_worker_full_session_e2e.py`（新建） | A1 / A3（端到端） |
| `test_worker_session_field_round_trip` | `apps/gateway/tests/test_worker_session_field_round_trip.py`（新建） | B1 |
| `test_session_memory_extractor_does_not_run_on_worker` | `tests/integration/test_f067_session_memory_pipeline.py` 追加 | B2 |
| 事件 emit 单测（视 A5 决策） | `apps/gateway/tests/test_agent_session_turn_event.py` 或追加 | A5 |

### 3.3 全量回归

- 每 Phase commit 前：`pytest`（全量）+ `pytest -m e2e_smoke`（5 域 smoke）
- F092 baseline 数据：3100 passed（参考 [F092 completion-report](../092-delegation-plane-unification/completion-report.md)）
- F093 目标：≥ 3100 + F093 新增（约 5-8 条），0 regression

---

## 4. Codex Review 强制节点

按 CLAUDE.local.md §"Codex Adversarial Review 强制规则"：

| 节点 | 时机 | 范围 | 模式 |
|------|------|------|------|
| **pre-Phase 4 (plan.md)** | 进入 Phase 5 (Tasks) 前 | 本 plan.md 设计是否走错 | foreground（小） |
| **per-Phase C** | Phase C commit 前 | mixin 拆分是否完整 / import 兼容 | foreground |
| **per-Phase A** | Phase A commit 前 | propagate 测试是否充分 / 事件 emit 决策 | foreground |
| **per-Phase B** | Phase B commit 前 | round-trip + extractor 显式跳过决策 | foreground（小） |
| **Final cross-Phase** | Phase D 内 | 是否漏 Phase / 跨 Phase 不一致 / 漏 spec 条目 | background（大） |

每条 finding 的处理流程：HIGH 必修；MEDIUM 默认修，拒绝必显式说明；LOW 可推迟。commit message 必须含闭环说明。

---

## 5. 风险更新（vs spec §9）

| 风险 | 严重度 | spec 阶段缓解 | Plan 阶段补充 |
|------|--------|---------------|---------------|
| 块 C 拆分撞隐式 import | 高 → 中 | grep 全 codebase | Phase C 起始再 grep；mixin 模式保留方法直接调用语义，无 BREAKING |
| 块 A propagate gap 比想象复杂 | 中 → 低 | TDD 测试坐实 | 实测链路在 baseline 已通；F093 主要是补测试，gap 风险已大幅降低 |
| Worker turn 误启动 SessionMemoryExtractor | 中 | F094 范围 | Phase B2 显式断言；如真有，加 short-circuit |
| 拆分边界过细 | 低 → 已规避 | spec 倾向最少 | Plan 选候选 C（仅 2 文件） |
| Codex review 抓到 design-level high | 中 | Final 兜底 | pre-Phase 4 + per-Phase + Final 三层 |

---

## 6. 给 Tasks 阶段的接力

- 任务粒度：以 Phase C / A / B / D 各自任务清单（§1）为蓝本拆 task list
- 每 task 必须有：file path（绝对）+ 验收命令 + 依赖关系 + 是否阻塞下一 task
- Tasks 阶段不重新做 §1 的设计决策，只做颗粒度细化
- 每 Phase 末尾的 Codex review 在 tasks.md 中作为单独 task 列出

---

## 7. 不变量（重申）

- 块 C 行为零变更，全量回归 0 regression vs F092 baseline (7e52bc6)
- 块 A/B 新行为必须有可观测事件 + 单测覆盖
- 不主动 push origin/master（等用户拍板）
- 不 force push
- 每 Phase commit 前跑 e2e_smoke
- 必产出 completion-report.md（含 Codex finding 闭环表）
- Phase 跳过必须显式归档（commit message + completion-report）

---

**Plan 编写完毕。** 准备进入 Phase 5（Tasks）。Plan 阶段 Codex review 待主 session 在合适节点触发。
