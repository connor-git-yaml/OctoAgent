# Feature Specification: F093 Worker Full Session Parity

**Feature Branch**: `feature/093-worker-full-session-parity`
**Created**: 2026-05-08
**Status**: Draft
**Baseline**: `7e52bc6` (F092 完成点)
**Mode**: spec-driver feature（完整 10 阶段编排）
**Input**: User description（M5 阶段 1 第 1 个 Feature，主责 H2 完整对等性 + 顺手清 D6）

---

## 0. 总览（Overview）

F093 是 OctoAgent M5 战略 **阶段 1（Agent 完整上下文栈对等）** 的第 1 个 Feature，主责 **H2「Worker 完整 Session 对等」**：让 Worker (kind=worker) 拥有与主 Agent (kind=main) 同样的 turn store、rolling_summary、memory_cursor 槽位三件套，并顺手清 **架构债 D6**（`apps/gateway/src/octoagent/gateway/services/agent_context.py` 4112 行拆分）。

### 与前 3 个 Feature（F090/F091/F092）的本质区别

F090/F091/F092 都是 **纯架构债清理**（行为零变更约束）。F093 是 **第一个真正改 Worker 运行时行为** 的 Feature——「Worker 写 turn」是新行为，意味着行为零变更约束部分放宽，但必须有「新行为可观测且可审计」的强约束替代（事件 emit + 单测覆盖 + 隔离断言）。

### 三段范围（块 A / 块 B / 块 C）

- **块 A（新行为）**：Worker session 形成持久化 turn 链 → `agent_session_turns` 表，与 main session 严格隔离
- **块 B（新行为，最小槽位）**：Worker session 的 `rolling_summary` / `memory_cursor_seq` 槽位准备就绪供 F094 接入；F093 范围内 SessionMemoryExtractor **不在** Worker 上启用
- **块 C（纯重构）**：D6 拆分 `agent_context.py` 4112 行至合理粒度，全量回归 0 regression vs F092 baseline

### 哲学锚点（CLAUDE.local.md §三条核心设计哲学 H2）

> **H2 完整 Agent 对等性**：Worker = 主 Agent − {hire/fire/reassign Worker} − {user-facing 表面}；每个 Agent 都有完整上下文栈（Project / Memory / Behavior / Session / Persona / 决策环）

F093 推进 H2 的「Session」轴；Memory 轴留给 F094，Behavior 轴留给 F095，Recall provenance 留给 F096。

---

## 1. User Scenarios & Testing

### User Story 1 — Worker turn 持久化与读回（Priority: P1）

**Journey**：当主 Agent 派工给 Worker（不论 DIRECT_WORKER 直接派工还是 WORKER_INTERNAL 嵌套派工），Worker 在自己的 session 内执行多轮工具调用与 LLM 交互。每一轮都会把 user / assistant / tool_call / tool_result 写到 `agent_session_turns` 表，且只与 Worker 自己的 `agent_session_id` 关联。重启进程后，Worker 仍可从 turn store 重建对话上下文，无需 Butler 在 prompt 里塞历史。

**Why this priority**：H2 哲学的最小可观测落地点；不实现这条，「Worker 完整对等」就不真。

**Independent Test**：用 Worker 单独跑一个长任务（≥ 3 轮工具调用），断言 `agent_session_turns` 表里 worker session 的 turn 数 ≥ 3，且 main session 同期 turn 数不变。

**Acceptance Scenarios**：

1. **Given** Worker session（kind=DIRECT_WORKER）已创建，**When** Worker 在 SkillRunner 主循环中调用 1 个工具并拿到结果，**Then** `agent_session_turns` 表新增 ≥ 2 条记录（tool_call + tool_result），均关联到 Worker 自己的 `agent_session_id`，且 main session 的 turn 计数不变。
2. **Given** Worker session（kind=WORKER_INTERNAL，由 main 派出 work_id=W），**When** Worker 跑 3 轮决策循环，**Then** 这 3 轮的所有 turn 进 worker `agent_session_id`，**parent main session 的 transcript 不出现 worker 的 tool 记录**（隔离断言）。
3. **Given** Worker session 已积累若干 turns，**When** 调用 `RecentConversation`/`session.export` 类 API 传入 worker `agent_session_id`，**Then** 返回的是 worker 自己的 turn 链，不混入 main session 内容。

---

### User Story 2 — Worker session 的 rolling_summary / memory_cursor_seq 槽位就绪（Priority: P2）

**Journey**：F094 将启用 Worker 维度的记忆提取与长会话压缩。F093 必须把承载这两个能力的 session 字段先备齐，让 F094 可以"插上即用"——具体是：Worker session 的 `rolling_summary`（字符串）和 `memory_cursor_seq`（整数）字段持久化 round-trip 正确，与 main session 互不干扰。

**Why this priority**：F094 接入零返工；模型层已存在字段（实测 `AgentSession` line 303/306），F093 范围内主要是验证 worker 路径上的写入与读出对称。

**Independent Test**：构造一个 Worker session，写入非默认 `rolling_summary`、把 `memory_cursor_seq` 推到 N，重启 store，读回这两个字段值与写入值完全一致；同时不影响同 project 下任何 main session 的同名字段。

**Acceptance Scenarios**：

1. **Given** 一个 Worker session 与一个 main session 共存于同一 project，**When** 给 Worker session 写 `rolling_summary="W-summary"` 与 `memory_cursor_seq=7`，**Then** main session 的 `rolling_summary` 与 `memory_cursor_seq` 不变；重启后 worker 字段值仍是 `("W-summary", 7)`。
2. **Given** Worker session 形成 N 个 turn，**When** F093 范围内 SessionMemoryExtractor **不**对 worker 触发提取，**Then** `memory_cursor_seq` 仍保持初始值 0（即 F093 不动 cursor 推进逻辑，仅准备槽位）。

---

### User Story 3 — agent_context.py 拆分维持完全行为兼容（Priority: P3）

**Journey**：D6 顺手清——把 `apps/gateway/src/octoagent/gateway/services/agent_context.py`（4112 行）按 **管理 / 查询 / 递推 / 持久化辅助 / 模型** 维度拆分到合理粒度的多个文件，但不改任何对外行为，所有现有 import 路径保留（通过 `__init__.py` 或同名 re-export）。

**Why this priority**：是块 A/B 的前置施工脚手架——继续在 4112 行单文件里塞 worker turn 写入会让文件不可维护；先拆再加。

**Independent Test**：跑全量回归测试（`pytest`），与 F092 baseline (7e52bc6) 对比 0 regression；e2e_smoke 通过；任何 `from octoagent.gateway.services.agent_context import X` 的现有 import 仍能解析到同名符号。

**Acceptance Scenarios**：

1. **Given** F092 baseline 全量回归 N passed，**When** 拆分完成，**Then** 全量回归仍是 N passed（允许 +新增 worker turn 测试 / 拆分后新增的单测）。
2. **Given** 任何调用方 `from octoagent.gateway.services.agent_context import AgentContextService`，**When** 运行调用，**Then** 行为完全不变（无 ImportError、无 AttributeError、无新 deprecation 警告）。
3. **Given** e2e_smoke 套件，**When** 在拆分 commit 后运行，**Then** 全 5 个 smoke 域 PASS。

---

## 2. 关键模型 / 契约（Key Models & Contracts）

> 本节只列出**需要触及的现状与目标**。具体接口与实现留 Plan 阶段。

### 2.1 现状（基于 F092 baseline 实测）

- `packages/core/src/octoagent/core/models/agent_context.py`：
  - `AgentSession` **已含** `rolling_summary: str` 与 `memory_cursor_seq: int` 字段（line 303 / 306）。这两个字段对 worker / main 同样适用。
  - `AgentSessionKind` **已含** `WORKER_INTERNAL` / `DIRECT_WORKER` / `SUBAGENT_INTERNAL`。
  - `AgentSessionTurn` 已建模，含 `task_id` / `turn_seq` / `kind` / `role` / `tool_name` / `summary` / `dedupe_key`。
  - `AgentSessionTurnKind` 含 `USER_MESSAGE` / `ASSISTANT_MESSAGE` / `TOOL_CALL` / `TOOL_RESULT` / `CONTEXT_SUMMARY`。
- `apps/gateway/src/octoagent/gateway/services/agent_session_turn_hook.py`：83 行，**完全 agent-agnostic**——仅检查 `context.agent_session_id` 是否非空，与 worker / main 无关。Hook 在 `octo_harness.py:755` 唯一构造一次，挂在 `SkillRunner.hooks` 上。
- `apps/gateway/src/octoagent/gateway/services/agent_context.py`：4112 行，包含 `_ensure_agent_session` 已根据 `AgentRuntimeRole.WORKER + parent_agent_session_id + work_id` 三元组判定 `DIRECT_WORKER` vs `WORKER_INTERNAL` vs `MAIN_BOOTSTRAP`，并复用或新建 session（line 2228-2253）。
- `apps/gateway/src/octoagent/gateway/services/llm_service.py`：`SkillExecutionContext` 在 line 427 构造，从 `metadata["agent_session_id"]` 读取，主 / worker 走同一 `LLMService` 实例。
- `apps/gateway/src/octoagent/gateway/services/task_service.py`：`_build_llm_dispatch_metadata`（line 870）从 `compiled_context.effective_agent_session_id` 注入 `agent_session_id` 到 dispatch metadata（F091 闭环已确保 runtime_context 同步序列化）。
- `apps/gateway/src/octoagent/gateway/services/worker_runtime.py`：构造 `WorkerDispatchState` 与 `ExecutionRuntimeContext`，line 497 把 `envelope.metadata["agent_session_id"]` 透传。

**结论**：F093 不是从 0 到 1 建模型，主要是 **打通端到端真实 propagate 链** + **补充隔离 / 持久化测试** + **拆分 D6**。

### 2.2 待 Plan 阶段精确定位的潜在 gap

> 这些点是 spec 阶段已发现的疑点，**Plan 阶段必须先用代码 + 测试坐实「现状到底通不通」**，然后决定是修补还是确认零改动。

- **Gap-1（块 A）**：`compiled_context.effective_agent_session_id` 在 worker 派工路径上是否真的回传 worker 自己的 session id？还是有路径让 main 的 session id "穿透" 到 worker dispatch？
- **Gap-2（块 A）**：Worker 在主循环内反复跑 SkillRunner 时，`SkillExecutionContext.agent_session_id` 是否每次都是同一个 worker `agent_session_id`？（不能跑半截切换）
- **Gap-3（块 A）**：Worker turn 在 agent_session_turns 表里写入后，`RecentConversation` / `session.export` / `session.reset` 等读路径是否按 `agent_session_id` 严格过滤？需要一条端到端断言证明「读 worker 不会读到 main」。
- **Gap-4（块 B）**：Worker session 持久化 round-trip 测试是否已存在？若已存在则 F093 主要补 worker kind 维度，不重写。
- **Gap-5（块 C）**：拆分边界由 Plan 阶段按依赖图决定；spec 不锁死「必须 3 个文件」。

### 2.3 事件契约（块 A）

Worker turn 写入路径必须 emit 可审计事件，建议复用现有 `agent_session_turn` 相关事件 schema（不增加新枚举），改而通过 metadata 区分 main / worker。具体事件名留 Plan 阶段决定，但 **必须满足**：

- 在 control_plane 可按 `agent_session_id` 查询到 worker turn 写入序列
- 事件含 `agent_session_id` / `task_id` / `turn_seq` / `kind` 四件套
- main 与 worker 写 turn 的事件流 shape 一致，仅 session_id / kind 不同（保持 H2「对等」哲学）

---

## 3. 范围 / Out of Scope

### 3.1 In Scope（F093 范围内）

| 块 | 内容 | 类型 |
|----|------|------|
| C  | 拆分 `apps/gateway/src/octoagent/gateway/services/agent_context.py` 4112 行 | 纯重构 |
| A  | Worker session 端到端 turn 写入 + 隔离断言 + 读回 | 新行为 |
| B  | Worker session 的 `rolling_summary` / `memory_cursor_seq` 持久化 round-trip 验证（仅准备槽位） | 新行为（最小） |
| ALL | 新单测覆盖块 A/B；e2e_smoke 通过；全量回归 0 regression vs F092 baseline | 验证 |
| ALL | 完成时产出 `completion-report.md` 含「实际 vs 计划」对照 + Codex finding 闭环表 | 制品 |

### 3.2 Out of Scope（明确排除）

| 排除项 | 落入 Feature |
|--------|--------------|
| Worker memory namespace（`AGENT_PRIVATE` / `WORKER_PRIVATE`）真生效；RecallFrame 填 agent_id；recall preferences 改读 AgentProfile | F094 |
| Worker behavior 4 层覆盖扩展；加 SOUL / HEARTBEAT / BOOTSTRAP；IDENTITY.worker.md 默认生效 | F095 |
| Worker recall audit & provenance；Web Memory Console 加 agent 视角 | F096 |
| Subagent Mode（H3-A）显式建模；SubagentDelegation；完成后清理 SubagentSession | F097 |
| A2A Mode（H3-B）receiver 在自己 context 工作；删除 `_enforce_child_target_kind_policy`；Worker→Worker 解绑；orchestrator/task_service 拆 dispatch_service（D7） | F098 |
| Ask-Back 工具（`worker.ask_back` / `worker.request_input` / `worker.escalate_permission`）；A2AConversation source_type 泛化 | F099 |
| Decision Loop Alignment（去 single_loop_executor 跳 recall planner 的 hack）；F090 D1 双轨收尾 | F100 |
| WorkerProfile 与 AgentProfile 完全合并（独立 SQL 表数据迁移 + revision 收口 + FE 类型同步） | F107 |
| SessionMemoryExtractor 在 worker 上真跑（fact 提取写到 worker memory namespace） | F094 |

---

## 4. 关键约束 / 不变量（Non-Functional）

### NFR-1 行为零变更（块 C）+ 行为可观测变更（块 A/B）

- **块 C**：`agent_context.py` 拆分——纯重构，全量回归 **0 regression vs F092 baseline (7e52bc6)**。任何对外 API / 行为变化均视为 spec 违反。
- **块 A/B**：Worker 写 turn / session 字段 round-trip 是新行为，必须有：
  - 单测覆盖 Worker turn 写入路径（至少 1 条 happy path）
  - 单测覆盖 Worker session 隔离（worker turn 不进 main transcript）
  - 单测覆盖 RecentConversation 读 Worker turn（按 session_id 严格过滤）
  - 单测覆盖 Worker session `rolling_summary` / `memory_cursor_seq` 持久化 round-trip

### NFR-2 新行为可审计

Worker turn 写入路径必须 emit 事件（事件名复用现有 schema，由 Plan 阶段确认），control_plane 可按 `agent_session_id` 查询。

### NFR-3 F091 状态映射 raise 模式沿用

调用 `work_status_to_task_status` 前若源状态可能是 `MERGED` / `ESCALATED` / `DELETED`，必须显式 check（F091 已建立）。F093 涉及 worker session lifecycle 的代码若新增 status 映射调用，必须遵此模式。

### NFR-4 F092 SpawnChildResult 三态无影响

F093 不动 `plane.spawn_child` 编排入口。Worker session 创建路径（`_ensure_agent_session`）与 spawn_child 不耦合（前者是 context resolve 阶段、后者是 dispatch 创建阶段）。Plan 阶段需在 `plan.md` 显式确认「F093 不影响 SpawnChildResult 三态语义」。

### NFR-5 每 Phase 后回归 0 regression vs F092 baseline (7e52bc6)

- e2e_smoke 必过（pre-commit hook 自动跑 180s portable watchdog）
- 全量 unit + integration 回归 0 regression（块 A/B 新增测试 OK，已有测试不能掉）

### NFR-6 每 Phase 前 Codex review + Final cross-Phase Codex review

- 命中「重大架构变更 commit 前」节点（涉及跨包接口 + 新行为）
- F091/F092 实证 Final Review 抓到 5 真 bug，价值显著——F093 必走

### NFR-7 拆分后 import 兼容（块 C）

任何外部 import：

```python
from octoagent.gateway.services.agent_context import (
    AgentContextService,
    # 以及任何当前已 export 的符号
)
```

必须在拆分后保持可用（通过 `__init__.py` 或同模块 re-export）。

### NFR-8 不主动 push origin/master

按 CLAUDE.local.md §「Spawned Task 处理流程」：归总报告等用户拍板。F093 不主动 push。

---

## 5. Acceptance Criteria（验收 checklist）

### 块 A 验收

- [ ] **A1** Worker session（kind=`WORKER_INTERNAL` / `DIRECT_WORKER`）在 SkillRunner 路径下自动写 turn 到 `agent_session_turns` 表
- [ ] **A2** Worker turn 与 main session turn 严格按 `agent_session_id` 隔离（断言：写 worker N 条后，main session 的 turn count 不变）
- [ ] **A3** RecentConversation / session.export / session.reset 类读路径按 `agent_session_id` 过滤（worker 调用不返回 main 的内容）
- [ ] **A4** 新增单测覆盖：(a) Worker 写 turn happy path，(b) 隔离断言，(c) RecentConversation 读 worker turn
- [ ] **A5** Worker turn 写入路径 emit 事件，control_plane 可查（事件 schema 与 main 一致，仅 session_id 不同）

### 块 B 验收

- [ ] **B1** Worker `AgentSession.rolling_summary` 持久化 round-trip（写后读回）
- [ ] **B2** Worker `AgentSession.memory_cursor_seq` 持久化 round-trip（写后读回）
- [ ] **B3** 新增单测覆盖 B1 / B2，且断言不影响同 project 同期 main session 的同名字段
- [ ] **B4** 显式记录「F093 范围内 SessionMemoryExtractor **不**对 worker 触发」（in spec / completion-report，让 F094 接入时有清晰锚点）

### 块 C 验收

- [ ] **C1** `apps/gateway/src/octoagent/gateway/services/agent_context.py` 4112 行拆分到合理粒度（具体拆分边界 Plan 阶段定）
- [ ] **C2** 所有现有 `from octoagent.gateway.services.agent_context import X` 调用方仍能解析到同名符号（无 BREAKING）
- [ ] **C3** 全量回归 **0 regression vs F092 baseline (7e52bc6)**

### 全局验收

- [ ] **G1** 全量回归 vs F092 baseline (7e52bc6)：块 A/B 测试新增 + 块 C 0 regression
- [ ] **G2** e2e_smoke 每 Phase 后 PASS（pre-commit hook 验证）
- [ ] **G3** 每 Phase Codex review 闭环（0 high 残留）
- [ ] **G4** **Final cross-Phase Codex review** 通过（输入 plan.md + 全 Phase commit diff）
- [ ] **G5** **completion-report.md** 已产出 @ `.specify/features/093-worker-full-session-parity/completion-report.md`，含「实际 vs 计划」对照 + Codex finding 闭环表
- [ ] **G6** F094 / F095 接入点说明：
  - `memory_cursor_seq` 槽位在哪、F094 SessionMemoryExtractor 怎么从这接入
  - Worker `agent_session_id` 怎么传递给 memory namespace resolver（F094）
  - Worker behavior 4 层覆盖在哪扩展（F095 接入点）
- [ ] **G7** **Phase 跳过必须显式归档**（若发生）

---

## 6. F094 / F095 接口点（前向声明）

> 本节服务于 G6 验收项，让下一个 Feature 接入零返工。

### 6.1 给 F094（Worker Memory Parity）

- **memory_cursor_seq 槽位**：在 `AgentSession.memory_cursor_seq`（packages/core 模型层），所有 worker session 默认值 0。F094 启用 SessionMemoryExtractor 时，应按 worker `agent_session_id` 读 cursor，处理 turn_seq > cursor 的新 turn，提取后回写 cursor。
- **Worker session id 传递给 memory namespace resolver**：`AgentContextService` 在 `_ensure_agent_session` 后已持有 worker `agent_session_id`，F094 可在 context resolve 路径下游（compiled_context 输出阶段）增加 `worker_memory_namespace_id` 字段，由 namespace resolver 按 `(project_id, agent_runtime_id, kind=WORKER_PRIVATE)` 解析。
- **F093 不动**：F093 不增加 namespace resolver 与 cursor 推进逻辑。

### 6.2 给 F095（Worker Behavior Workspace Parity）

- 当前 worker behavior 加载入口：Plan 阶段需精确定位 `BehaviorLoadProfile.WORKER` 当前涵盖文件清单（用户 prompt 说"扩到 9 文件，加回 SOUL / HEARTBEAT / BOOTSTRAP；IDENTITY.worker.md 默认生效"）。
- **F093 不动**：F093 不修改 worker behavior overlay；只确保拆分后的 agent_context 模块对 worker behavior 加载点的依赖路径不变。

---

## 7. Phase 顺序建议（先简后难）

> F091 / F092 实证「先建立 baseline 信心，再做主责改动」是好 pattern。F093 沿用：

1. **Phase C（块 C，最简单）**：`agent_context.py` 拆分。纯重构，行为零变更，全量回归 0 regression。先把脚手架立好。
2. **Phase A（块 A，主责）**：Worker turn 写入端到端。补 propagate gap（如有）+ 加单测。
3. **Phase B（块 B，最少改）**：Worker session 字段 round-trip 验证 + 隔离断言。多半只是补单测。
4. **Phase D（验证收尾）**：跑 e2e_smoke + 全量回归 + Final cross-Phase Codex review + 写 completion-report.md。

具体到 commit 粒度的 Phase A/B/C 子拆分留给 Plan 阶段。

---

## 8. 待 Plan 阶段决策的开放点

> 这些点不是 ambiguity，是 Plan 阶段必须落地的开放接力。

1. **Open-1（块 C 拆分边界）**：`agent_context.py` 拆成几个文件、各文件分别承载哪些类与方法、`__init__.py` re-export 的策略。建议候选：
   - 候选 A：3 文件（`session_service.py` / `context_resolver.py` / 模型保留 packages/core）
   - 候选 B：5 文件（管理 / 查询 / 递推 / budget / 持久化辅助）
   - 候选 C：先拆 2 文件（CRUD vs resolver），后续 Feature 再细分
2. **Open-2（块 A propagate 真实 gap）**：Plan 阶段第一步必须用代码 + 测试坐实「目前 Worker 写 turn 在哪一步实际断了」。可能性：
   - (a) `compiled_context.effective_agent_session_id` 在 worker 路径返回 main session id
   - (b) Worker subloop 的 dispatch_metadata 没注入 worker session id
   - (c) `SkillExecutionContext.agent_session_id` 被覆盖
   - (d) 多步骤组合断了
3. **Open-3（块 A 事件复用）**：是否新增 `AGENT_SESSION_TURN_RECORDED` 事件，还是复用现有事件？倾向复用。
4. **Open-4（块 B 范围）**：F093 是否仅做 round-trip 单测？还是也要做 Worker session 创建时的 cursor 默认值显式断言？
5. **Open-5（迁移影响）**：拆分 `agent_context.py` 是否会撞 alembic 或其他模块？Plan 阶段需扫一次 import graph。
6. **Open-6（pre-commit hook）**：F087 已建 e2e_smoke pre-commit hook（180s portable watchdog）；F093 不动 hook，但每 Phase commit 前需手工跑全量 + e2e_smoke 一次。

---

## 9. 风险（Risks）

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| 块 C 拆分撞到隐式 import 路径（reflection / dynamic import） | 高 | Plan 阶段先 `grep` 全 codebase 的 `agent_context` 引用；拆分后跑全量回归 + e2e_smoke |
| 块 A propagate gap 比想象复杂（多路径混杂） | 中 | Phase A 第一步先写「最小复现单测」固化 gap；不修代码先确认 gap |
| Worker turn 写入触发 SessionMemoryExtractor 误启动（应 F094 才启动） | 中 | F093 显式确认 cursor 推进逻辑不挂 worker；新增单测断言 cursor=0 |
| 拆分边界过细造成阅读成本反升 | 低 | 倾向最少拆分（Open-1 候选 C） |
| Codex review 抓到设计层 high finding 推迟 | 中 | 沿用 F091/F092 经验，Phase 前 review 暴早，Final review 兜底 |

---

## 10. 索引（References）

- **CLAUDE.local.md** § "M5 / M6 战略规划"（F093 在阶段 1 的位置）
- **CLAUDE.local.md** § "三条核心设计哲学"（H2 完整对等性）
- **CLAUDE.local.md** § "F091 实施记录" / "F092 实施记录"（前置 baseline）
- **CLAUDE.local.md** § "F090 实施偏离记录"（AgentProfile.kind 已加）
- **CLAUDE.local.md** § "工作流改进"（completion-report 强制 + Final cross-Phase Codex review）
- **`.specify/features/092-delegation-plane-unification/spec.md` + completion-report.md**（F092 起点）
- **`.specify/features/091-state-machine-unification/spec.md`**（状态映射 raise 模式）
- **`packages/core/src/octoagent/core/models/agent_context.py:283`**（AgentSession 模型，含 rolling_summary + memory_cursor_seq）
- **`apps/gateway/src/octoagent/gateway/services/agent_context.py`**（4112 行，块 C 拆分目标）
- **`apps/gateway/src/octoagent/gateway/services/agent_session_turn_hook.py`**（83 行，agent-agnostic hook）
- **`apps/gateway/src/octoagent/gateway/harness/octo_harness.py:755`**（hook 唯一构造点）

---

**Spec 生成完毕。** 等待 GATE_DESIGN 用户审查后进入 Phase 4（plan）。

---

## 11. Clarifications

### Session 2026-05-08

无需澄清——开放点已标注待 Plan 阶段决策。

扫描全文后，未发现任何「读 spec 后无法判断 acceptance 标准会通过还是失败」的真正 ambiguity：

- 所有验收条件（A1-A5, B1-B4, C1-C3, G1-G7）均有明确的 pass/fail 判据。
- §2.2 的 Gap-1 至 Gap-5 已显式标注为 Plan 阶段用代码坐实的疑点，不影响验收判断。
- §8 的 Open-1 至 Open-6 是已知的 Plan 阶段接力点，各有备选策略，不构成 ambiguity。
- 块 C 拆分边界故意留给 Plan 阶段——C1/C2/C3 验收项本身的判据清晰（0 regression + import 不 BREAKING）。
- SessionMemoryExtractor 在 F093 内不启用已在 B4 验收项和 §3.2 Out of Scope 双重锚定。

**结论**：spec 质量合格，0 ambiguity，0 CRITICAL 问题，可直接进入 Plan 阶段。
