# Tasks: F093 Worker Full Session Parity

**Feature Branch**: `feature/093-worker-full-session-parity`
**Baseline**: `7e52bc6` (F092)
**Plan**: [plan.md](./plan.md) §1 是本 tasks 的蓝本
**Status**: Draft（待 GATE_TASKS 用户拍板）

> 工作目录约定（所有路径均相对此 worktree root）：
> `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F093-worker-full-session-parity/octoagent/`

## 0. 通用约定

- **每 Phase commit message 模板**：
  - `refactor(F093-Phase-C): agent_context.py 拆分到 mixin（行为零变更）`
  - `feat(F093-Phase-A): Worker session turn 写入端到端 + 隔离断言`
  - `test(F093-Phase-B): Worker session 字段持久化 round-trip + extractor 不跑 worker 断言`
  - `docs(F093): 留档 completion-report / Final Codex review 闭环`
- **commit message 必须含 Codex review 闭环说明**（per-Phase + Final）
- **每 Phase commit 前**：跑全量 `pytest` + `pytest -m e2e_smoke` + Codex review
- **不主动 push**；F093 完成后归总报告等用户拍板
- **失败处理**：测试 fail 时**先记录失败模式**（screenshot / log），坐实是 baseline 缺陷还是新引入；不要为了让测试过而绕开断言

---

## Phase C — `agent_context.py` 拆分

### Task C-0：Phase C 启动前 grep 验证（10 min）

**目的**：再次确认 `agent_context.py` 私有方法没有被外部 import / dynamic load。

**命令**：
```bash
cd octoagent
grep -rn "from .agent_context import _" apps/gateway/src/octoagent/gateway/services/
grep -rn "agent_context.AgentContextService\._" apps/gateway/
grep -rn "_append_agent_session_turn\|record_tool_call_turn\|record_tool_result_turn" apps/gateway/ packages/core/ tests/ | grep -v __pycache__
```

**通过标准**：上述命令输出已知调用方（`agent_session_turn_hook.py` 是唯一外部 caller）。如发现额外 dynamic import，立即记录并向主 session 报告。

---

### Task C-1：抽出 `AgentContextTurnWriterMixin`（45 min）

**文件**：新建 `apps/gateway/src/octoagent/gateway/services/agent_context_turn_writer.py`

**搬迁内容**（从 `agent_context.py`）：
- `_append_agent_session_turn` (line 1764-1804)
- `record_tool_call_turn` (line 1967-1991)
- `record_tool_result_turn` (line 1993-2025)
- 任何仅服务于上述方法的私有 helper（C-0 grep 后视实际定）

**实现**：
```python
class AgentContextTurnWriterMixin:
    """提供 AgentSessionTurn 写入能力，由 AgentContextService 继承。"""
    # 期望 self._stores: StoreGroup 由继承类提供
    async def _append_agent_session_turn(...) -> ...:
        ...
    async def record_tool_call_turn(...) -> ...:
        ...
    async def record_tool_result_turn(...) -> ...:
        ...
```

**通过标准**：新文件 lint clean，独立可 import。

---

### Task C-2：`AgentContextService` 改为继承 mixin（15 min）

**文件**：`apps/gateway/src/octoagent/gateway/services/agent_context.py`

**改动**：
```python
from .agent_context_turn_writer import AgentContextTurnWriterMixin

class AgentContextService(AgentContextTurnWriterMixin):
    # ... 其余不变
```

**通过标准**：所有调用 `self._append_agent_session_turn(...)` / `self.record_tool_call_turn(...)` / `self.record_tool_result_turn(...)` 的内部方法仍能解析方法（无 AttributeError）。

---

### Task C-3：删除 `agent_context.py` 中已搬走的方法定义（10 min）

**约束**：彻底删除，**不留 stub / `# moved to ...` 注释 / 死代码**（CLAUDE.md "去掉功能时直接删除所有相关代码"）。

**通过标准**：`agent_context.py` 行数减少 ≥ 100。

---

### Task C-4：跑全量回归 + e2e_smoke（30 min）

**命令**：
```bash
cd octoagent && uv run pytest 2>&1 | tail -50
cd octoagent && uv run pytest -m e2e_smoke 2>&1 | tail -30
```

**通过标准**：
- 全量回归：N passed = F092 baseline N passed（参考 ~3100 passed）
- e2e_smoke：5 域全 PASS

**失败处理**：
- 如有 regression：定位到具体 import / 方法解析问题；修；再回归
- 如 e2e_smoke fail：定位是否 import circular；修

---

### Task C-5：Phase C Codex review（30 min）

**触发**：`/codex:adversarial-review`（foreground）或通过 codex:codex-rescue 子代理

**输入**：Phase C 全部改动 diff（`git diff main...HEAD`）

**关注点**：
1. mixin 拆分是否完整（无遗漏方法）
2. import 是否兼容（`from octoagent.gateway.services.agent_context import X` 仍 OK）
3. 类型注解 / Pyright clean
4. 无新增 deprecation 警告

**finding 处理**：
- HIGH：必修
- MEDIUM：默认修，拒绝必显式说明
- LOW：可推迟，commit message 标注

---

### Task C-6：Phase C commit（10 min）

**commit message**：
```
refactor(F093-Phase-C): agent_context.py 拆分到 turn-writer mixin（行为零变更）

- 新增 apps/gateway/src/octoagent/gateway/services/agent_context_turn_writer.py
  承载 _append_agent_session_turn / record_tool_call_turn / record_tool_result_turn
- AgentContextService 改为继承 AgentContextTurnWriterMixin
- agent_context.py 行数 4112 → ~3700（减 ~400 行）

行为零变更：
- 全量回归 N passed = F092 baseline (7e52bc6)
- e2e_smoke 5 域 PASS
- 所有 from octoagent.gateway.services.agent_context import X 兼容

Codex review (per-Phase C): N high / M medium 已处理 / K low ignored
```

---

## Phase A — Worker turn 写入端到端验证 + 隔离断言

### Task A-1：扩展 hook unit test 加 worker kind 变体（30 min）

**文件**：`apps/gateway/tests/test_agent_session_turn_hook.py`（追加测试）

**新增**：
- `test_hook_records_tool_turns_for_direct_worker_session`：复制现有测试，将 `AgentRuntimeRole.MAIN` → `WORKER`，`AgentSessionKind.MAIN_BOOTSTRAP` → `DIRECT_WORKER`，断言 turn 写入路径与 main 一致。
- `test_hook_records_tool_turns_for_worker_internal_session`：构造 parent main session + 一个 WORKER_INTERNAL 子 session，给 worker session 触发 hook，断言 turn 进 worker session（不进 parent main）。

**通过标准**：两条新测试 PASS；既有测试不变。

---

### Task A-2：Worker turn 隔离断言（45 min）

**文件**：新建 `apps/gateway/tests/test_worker_session_turn_isolation.py`

**测试**：
- `test_main_and_worker_session_turns_are_isolated`：
  - 构造 (project P, main runtime + main session, worker runtime + worker session)
  - 给 worker session 触发 hook 1 次（tool_call + tool_result，2 条 turn）
  - 给 main session 触发 hook 1 次（独立的 1 个 tool_call + tool_result）
  - 断言：main session turn count = 2（仅 main 的）；worker session turn count = 2（仅 worker 的）
  - 断言：main session 的 replay projection 不含 worker tool_name
  - 断言：worker session 的 replay projection 不含 main tool_name

**通过标准**：测试 PASS；如 fail，定位 store 层是否真按 session_id 严格过滤；先记录后修。

---

### Task A-3：RecentConversation 读路径单测（30 min）

**文件**：同上 `test_worker_session_turn_isolation.py` 追加

**测试**：
- `test_recent_conversation_filters_by_session_id`：
  - 复用 A-2 的 fixture
  - 调用 `build_agent_session_replay_projection(agent_session=worker_session)` 获得 worker 的 transcript_entries
  - 断言只含 worker turn
  - 反向：build for main → 只含 main turn

**通过标准**：测试 PASS。

---

### Task A-4：端到端 e2e 测试（60 min，可能含修补）

**文件**：新建 `tests/integration/test_f093_worker_full_session_e2e.py`（或合并到 `test_f009_worker_runtime_flow.py` 末尾）

**测试**：
- `test_worker_dispatch_writes_turns_to_worker_session`：
  - 用 OctoHarness 跑 1 次 worker dispatch（builder pattern 参考 `test_f009_worker_runtime_flow.py`）
  - Worker 实际跑 1 个 stub tool（fixture 注入）
  - 断言：`agent_session_turns` 表里 worker session 有 ≥ 2 条 turn (tool_call + tool_result)
  - 断言：同 project 内 main session 的 turn 数不变（捕获 baseline 后 - baseline 前）

**关键**：如果测试失败（即 baseline 实际不写 worker turn）：
1. **不要**为了过测试改测试逻辑
2. 先用 log + breakpoint 定位 propagate 链断点（参考 plan.md §0.1 8 跳）
3. 修补对应位置（候选：`_build_context_resolve_request` / `_build_llm_dispatch_metadata` / `worker_runtime` envelope.metadata 注入）
4. 修补必须独立成一个 commit 子任务，commit message 标明定位的 root cause

**通过标准**：测试 PASS，e2e_smoke 不破。

---

### Task A-5：事件 emit 验证（30-60 min，视基线）

**Step 1 — 扫描基线事件**：
```bash
cd octoagent
grep -rn "TURN_RECORDED\|turn_persisted\|agent_session_turn" packages/core/src/octoagent/core/events/ apps/gateway/src/ | grep -v __pycache__
```

**Step 2 — 决策**：
- 如果 baseline 已有事件 schema 包含 `agent_session_id` / `task_id` / `turn_seq` / `kind`：复用，写一条单测断言事件 emit
- 如果 baseline 完全无 turn 持久化事件：在 `save_agent_session_turn` 路径**加一条** `AGENT_SESSION_TURN_PERSISTED` 事件 emit（或类似命名），含 `agent_session_id` / `task_id` / `turn_seq` / `kind` / `agent_session_kind`

**Step 3 — 单测**：
- `test_worker_turn_persisted_event_emitted`：触发 worker turn 写入，断言 EventStore 收到对应事件，metadata 含 worker session id

**通过标准**：单测 PASS；事件 schema 决策已落到 plan.md / completion-report.md 一致。

---

### Task A-6：Phase A 全量回归 + e2e_smoke（30 min）

**命令**：同 C-4

**通过标准**：N+5~6 passed（块 A 新增测试），0 regression vs Phase C 末态。

---

### Task A-7：Phase A Codex review（30 min）

**输入**：Phase A 全部 diff（含 A-4 可能的修补 commit）

**关注点**：
1. 隔离断言是否充分（多 turn 多 task 维度）
2. e2e 测试是否真覆盖 worker 主循环（非仅 hook 单元层）
3. 事件 emit 决策是否一致
4. A-4 如有修补，root cause 是否真定位到（非 patch over）
5. F091 状态映射 raise 模式是否被影响（F093 应不动 work_status_to_task_status）

---

### Task A-8：Phase A commit（10 min）

**commit message** 模板：
```
feat(F093-Phase-A): Worker session turn 写入端到端 + 隔离断言

- apps/gateway/tests/test_agent_session_turn_hook.py：新增 worker kind 变体（DIRECT_WORKER + WORKER_INTERNAL）
- apps/gateway/tests/test_worker_session_turn_isolation.py：新增 main/worker 隔离断言 + RecentConversation 读路径过滤
- tests/integration/test_f093_worker_full_session_e2e.py：新增 worker dispatch 端到端 turn 写入断言
- [事件 emit 视 A-5 决策填]

测试新增：约 5-7 条；全量回归 N+5 passed = F092 baseline + 块 A
[如 A-4 含修补]：发现 propagate 链 X 节点断点，修补理由：…

Codex review (per-Phase A): N high / M medium 已处理 / K low ignored
```

---

## Phase B — Worker session 字段 round-trip + extractor 不跑 worker

### Task B-1：Worker session 字段 round-trip 单测（30 min）

**文件**：新建 `apps/gateway/tests/test_worker_session_field_round_trip.py`

**测试**：
- `test_worker_session_rolling_summary_round_trip`：写 → save → get → 断言一致
- `test_worker_session_memory_cursor_seq_round_trip`：写 → save → get → 断言一致
- `test_worker_session_field_isolation_from_main`：同 project main + worker，给 worker 写非默认字段，断言 main 字段不变

**通过标准**：3 条测试 PASS。

---

### Task B-2：SessionMemoryExtractor 不跑 worker 断言（45 min，视实际行为）

**文件**：`tests/integration/test_f067_session_memory_pipeline.py`（追加）

**Step 1 — 实测当前行为**：
```bash
cd octoagent
grep -rn "SessionMemoryExtractor\|extract_session_memory\|memory_cursor_seq" apps/gateway/src/ packages/core/src/ | grep -v __pycache__ | grep -v test
```
找到 extractor 触发条件，确认是否 kind-aware。

**Step 2 — 写测试**：
- `test_session_memory_extractor_skips_worker_session`：
  - 构造 worker session 并写 ≥ 3 条 turn
  - 触发 extractor 流程（按既有 fixture 模式）
  - 断言 worker session 的 `memory_cursor_seq` 仍为 0（即 extractor 跳过 worker）

**Step 3 — 如果实测 extractor 跑了 worker（导致 cursor 推进 ≠ 0）**：
- 加 short-circuit：在 extractor 入口处 check `agent_session.kind != MAIN_BOOTSTRAP` → skip
- 修补独立成 commit，commit message 写明 "F093 显式不让 extractor 跑 worker，让 F094 接入零返工"
- 重跑测试

**通过标准**：测试 PASS，Phase B end-state extractor 在 worker 上确认不跑。

---

### Task B-3：Phase B 全量回归 + e2e_smoke（20 min）

**命令**：同 C-4

**通过标准**：N+8~10 passed，0 regression。

---

### Task B-4：Phase B Codex review（20 min）

**关注点**：
1. round-trip 测试是否覆盖 store 全字段（save_agent_session 完整 / partial update 路径）
2. extractor 显式跳过决策是否落到代码（不依赖 baseline 偶然行为）
3. F094 接入点是否清楚（completion-report.md 草稿审查）

---

### Task B-5：Phase B commit（10 min）

**commit message**：
```
test(F093-Phase-B): Worker session 字段 round-trip + extractor 不跑 worker 断言

- apps/gateway/tests/test_worker_session_field_round_trip.py：rolling_summary / memory_cursor_seq 持久化 round-trip + main 字段隔离
- tests/integration/test_f067_session_memory_pipeline.py：extractor 跳过 worker session 断言
[如 B-2 含 short-circuit 修补]：理由：…

测试新增：约 3-4 条；全量回归 N+8 passed
F094 接入点已写到 completion-report.md：cursor 槽位路径 + extractor 启用条件

Codex review (per-Phase B): N high / M medium 已处理 / K low ignored
```

---

## Phase D — Final 验证 + completion-report

### Task D-1：最终全量回归 + e2e_smoke（30 min）

**命令**：同 C-4

**通过标准**：N+8~10 passed = F092 baseline + F093 新增（约 8-10 条），0 regression；e2e_smoke 5 域 PASS。

---

### Task D-2：Final cross-Phase Codex review（60 min）

**触发**：`/codex:adversarial-review`（background，预期 ≥ 30min）或 codex:codex-rescue 子代理

**输入**：
- spec.md
- plan.md
- 全 Phase commit diff（C → A → B 全部 commit）
- 涉及的 baseline 文件原始版本（diff 对比基准）

**关注点**：
1. 是否漏 Phase / 跨 Phase 不一致
2. 是否 spec acceptance 条目漏覆盖
3. 块 C 拆分是否真零行为变更（diff 是否含语义改动）
4. 块 A propagate 修补（如有）是否定位到 root cause
5. 块 B extractor 跳过逻辑是否会撞 F094

**finding 处理**：HIGH 必修；MEDIUM 默认修；LOW 可推迟，commit message 标注。

---

### Task D-3：写 completion-report.md（45 min）

**文件**：`.specify/features/093-worker-full-session-parity/completion-report.md`

**章节**：
1. **总览**：F093 完成状态 + 时间线 + commit list
2. **实际 vs 计划对照表**：plan.md §1 各 Phase 任务列表 → 实际做了什么 / 偏离
3. **Codex finding 闭环表**：per-Phase C/A/B + Final cross-Phase 各自 N high / M medium / K low 处理
4. **acceptance 验收清单**：spec §5 全部条目逐条 ✓/✗ + 证据指针（commit hash / 测试文件路径）
5. **F094 / F095 接入点重述**（spec §6 落地版）
6. **架构债状态**：D6 状态变更（4112 行 → ~3700 + ~400 行新文件，债部分清；剩余 ~3700 行待 F098 / 未来 Feature 顺手清）
7. **下一步建议**：建议合入 master / 等用户拍板，含 push 命令

---

### Task D-4：Phase D commit（5 min）

**commit message**：
```
docs(F093): 留档 completion-report / Final Codex review 闭环

- 添加 .specify/features/093-worker-full-session-parity/completion-report.md
- 含 实际 vs 计划对照 / Codex 4 次 review 闭环表 / acceptance 验收清单 / F094-F095 接入点
- F093 全部 acceptance 关闭，等用户拍板 push origin/master

Final cross-Phase Codex review: N high / M medium 已处理 / K low ignored
```

---

### Task D-5：归总报告给用户（10 min）

按 CLAUDE.local.md §"Spawned Task 处理流程" §"主 session 接到 spawn task 完成通知后"——主 session 呈现归总报告等用户拍板 push。

**报告格式**：
- 改动文件清单 + 净增减行数
- 解决的问题（用户视角）
- Codex review 闭环结果
- F094 / F095 接入点
- 建议合入 origin/master / 用户决定

---

## 1. 依赖图

```
C-0 → C-1 → C-2 → C-3 → C-4 → C-5 → C-6
                                       ↓
                  A-1 ┐
                  A-2 ├→ A-4（含可能的修补）→ A-5 → A-6 → A-7 → A-8
                  A-3 ┘                                            ↓
                                                          B-1 ┐
                                                          B-2 ├→ B-3 → B-4 → B-5
                                                                                ↓
                                                                D-1 → D-2 → D-3 → D-4 → D-5
```

- C → A 严格串行（C 是 A 的 mixin 前置）
- A 内部：A-1/A-2/A-3 可并行（独立测试文件）
- A-4 依赖 A-1~A-3 完成（端到端 e2e 在 mixin 已稳定后跑更可信）
- A → B 严格串行（B 在 A 的 turn 写入路径上加测）
- B 内部：B-1 / B-2 可并行
- B → D 严格串行

## 2. 工时估算

| Phase | 工时 | 说明 |
|-------|------|------|
| Phase C | ~2.5h | 拆分 + 回归 + Codex |
| Phase A | ~4-5h | 含可能的 propagate 修补（A-4） |
| Phase B | ~2h | 视 extractor short-circuit 是否需要 |
| Phase D | ~2.5h | Final review + completion-report |
| **小计** | **~11-12h** | 单 session 完成有挑战，建议分 2 会话（C+A 一次，B+D 一次） |

---

**Tasks 编写完毕。** 等待 GATE_TASKS 用户审查。
