# F100 Decision Loop Alignment — Spec（v0.3 Post-PhaseC-Audit）

**Feature Branch**: `feature/100-decision-loop-alignment`
**Created**: 2026-05-15
**Status**: Draft（GATE_DESIGN 已通过；Codex pre-impl 4 finding 闭环 → v0.2；Phase C audit 修订 → v0.3）
**M5 Stage**: 阶段 2 **收尾 Feature**（继 F097/F098/F099 后）
**Upstream**: F099 (049f5aa) / F091 / F090
**Downstream**: F101 Notification + Attention Model（接收 F099 7 项推迟）
**Baseline passed count**: 3450 vs F098 baseline（F099 实测）

---

## 0. 决策锁定（2026-05-15 用户拍板）

### 0.1 Recon + GATE_DESIGN 决议（v0.1）

| 决议 | 锁定结果 |
|------|----------|
| **OD-1** RecallPlannerMode.AUTO 语义 | **C 混合**：默认依 delegation_mode 自动决议（main_inline/worker_inline→skip，main_delegate/subagent→full），额外暴露 override flag 让上层（典型：长 context 复杂查询）可强制 full。**不实现 partial 中间档**（推 F107）|
| **OD-4** F090 双轨收尾节奏 | **A F100 一并收尾**：移除 metadata 写入 + 移除 fallback，F107 不再碰 F090 D1 |
| **OD-2** main_inline 默认 recall_planner_mode | **保持 "skip"**（F051 性能优势）；H1 完整决策环通过 OD-1-C 的 override flag 实现 |
| **OD-3** ask_back resume 显式 skip recall planner | **不动**（baseline 已通：worker_inline 默认 skip；OD-1=C 不改 worker_inline 决议）|
| **OD-5** partial 中间档实现位置 | **F107 处理**（YAGNI）|
| **OD-6** override 字段命名 | `force_full_recall: bool` |

### 0.2 Codex pre-impl review 4 finding 闭环（v0.2 修订）

详见 [codex-review-pre-impl.md](codex-review-pre-impl.md)。3 HIGH + 2 MED 用户拍板的修复方向：

| Finding | 修复决议 | 影响 §章节 |
|---------|---------|-----------|
| **HIGH-1** AUTO/force_full_recall 无 production producer | **C minimal trigger**：F100 新增 FR-H —— orchestrator._prepare_single_loop_request 接受 `metadata["force_full_recall"]` hint 转换为 runtime_context.force_full_recall=True。上层（chat 路由 / API 参数）可显式传 hint。**不引入复杂度判断**（推 F101）。**新增 AC-H1/H2**。 | §5 FR-H 新增 / §4 AC-H1/H2 新增 / §3 US-7 新增 |
| **HIGH-2** unspecified 不是漏传，是 pre-decision 状态 | **A 保留 pre-decision 语义**：chat.py seed context 保持 unspecified；fail-fast 只在 helper consumed 处触发。AC-9 重写为"consumed 时 unspecified raise，构造时不要求"。orchestrator 被调用前必须 patch 到显式 delegation_mode（已是 baseline 行为，仅需测试覆盖）。 | §4 AC-9 重写 / §5 FR-C 缩小 / §5 FR-D2 收紧 raise 范围 |
| **HIGH-3** ask_back resume 不透传 runtime_context | **C+A**：spec 修正叙述（worker_inline 在 turn N+1 派发时由 orchestrator 重设 delegation_mode，不依赖 control_metadata 透传）+ Phase F 前置到 Phase E 之前；Phase E 拆分为 E1（移除 metadata 写入）+ E2（移除 reader fallback）。 | §10 Phase 顺序更新 / §4 AC-5 叙述修正 / §11 风险表更新 |
| **MEDIUM-1** Phase D→E→F 顺序不安全 | 与 HIGH-3 修复合并：Phase 顺序 C→F→D→E1→E2 | §10 |
| **MEDIUM-2** AC-PERF-1 5% gate 测量不足 | **A mock-based 控制变量**：simple query 用 mock + 控制变量测量 helper 调用耗时 + 分支次数（微秒级，可重复）；e2e_smoke 5x 仅作 sanity check 不作 hard gate。 | §4 AC-PERF-1 重写 / §9.3 测量方法更新 |
| **LOW-1** force_full_recall: bool vs Literal | **接受**：F100 锁定 bool；F107 partial 中间档允许破坏式升级（重命名为 `recall_override_mode: Literal[...]` 或叠加第二字段）。**handoff 给 F101/F107**记录此演进负担。 | §12 handoff 项追加 |

### 0.3 Phase C audit 实测修订（v0.3）

详见 [phase-c-audit.md](phase-c-audit.md)。Phase C 实测发现 4 个 production consumed 时点中 **3 个是 pre-decision**（`orchestrator._prepare_single_loop_request` line 771 + `orchestrator._resolve_routing_decision` line 1050 + `llm_service._call_llm_service` line 383）。v0.2 修订的"consumed 时 raise"会破坏 chat 主链。

**v0.3 修订**：

| 项 | v0.2 | v0.3 |
|----|------|------|
| unspecified consumed 行为 | raise ValueError | **return False** |
| baseline 兼容性 | 破坏 chat 主链 | 100% 兼容（与 baseline metadata flag 缺失时等价） |
| metadata fallback 移除 | 是 | 是（不变） |
| 错误检测 | fail-fast | 测试覆盖关键 patch 必经路径 |

**核心调整**：
- AC-8 重写：unspecified → return False（不 raise）
- AC-9 重写：consumed 时点一致性测试（不要求 raise）
- FR-D1/D2 调整：移除 fallback，但 unspecified return False（与 baseline 默认行为等价）
- §11 风险表：Phase E2 风险大幅降级（不再触发 chat 主链崩）

---

## 1. 目标（Why）

F100 是 M5 阶段 2 **收尾 Feature**，主责两项：

### 1.1 H1 决策环对齐（主责）

CLAUDE.local.md §"三条核心设计哲学" §H1：**主 Agent 自跑（main_inline）也应走完整决策环**。

当前 baseline（F091 之后）：main_inline 路径 hardcoded `recall_planner_mode="skip"`，单纯靠 F051 引入的性能优化逻辑跳过 recall planner phase。这违反 H1——**长 context / 复杂查询场景下，主 Agent 自跑也需要完整 recall planner phase 才能正确决策**。

F100 改造：
- 启用 `RecallPlannerMode.AUTO` 实际语义（F091 占位 → 真实现）
- 在 RuntimeControlContext 上引入 **override flag**（推荐字段名：`force_full_recall: bool = False`），让上层（典型：context-aware 派发逻辑）可强制 full
- **行为兼容**：默认行为与 baseline 完全等价（main_inline → skip）；仅 override flag = True 时改走 full

### 1.2 F090 双轨收尾（次责）

F090 Phase 4 引入 `metadata["single_loop_executor"]` ↔ `runtime_context.delegation_mode` 双轨写入；F091 切到 runtime_context 优先 + metadata fallback。

F100 收尾：
- **写入端**：移除 `metadata["single_loop_executor"]` 和 `single_loop_executor_mode` 的写入
- **读取端**：移除 `is_single_loop_main_active` / `is_recall_planner_skip` 两个 helper 的 metadata fallback
- **强制要求**：所有 production 路径必须显式设置 `delegation_mode`（≠ "unspecified"）

### 1.3 RecallPlannerMode "auto" 实际语义启用

F091 注释明确："F100 启用 auto 语义 = 依 delegation_mode 自动决议"。F100 实施：

```
delegation_mode == "main_inline"    → skip
delegation_mode == "worker_inline"  → skip
delegation_mode == "main_delegate"  → full
delegation_mode == "subagent"       → full
delegation_mode == "unspecified"    → return False（v0.3 修订：与 baseline metadata 缺失时等价）
+ override `force_full_recall=True` → full（覆盖所有上述映射）
```

---

## 2. 非目标（Out-of-Scope）

| 项目 | 范围所属 |
|------|----------|
| ❌ partial 中间档实现（RecallPlanMode.PARTIAL）| F107 |
| ❌ recall planner 内部 namespace 子集选择逻辑 | F107 |
| ❌ 复杂度信号源（prompt_length / message_count）| 不在 F100 范围（YAGNI；OD-1=C 已避免）|
| ❌ main direct 路径走 AGENT_PRIVATE | F107 |
| ❌ D2 WorkerProfile 完全合并 | F107 |
| ❌ Notification + Attention Model | F101 |
| ❌ F099 7 项推迟项（F3 HIGH state machine / F5 PARTIAL / ApprovalGate SSE / AC-E1 e2e / N-H1 PARTIAL resume 其余路径 / M-1 broad-catch / N-L1 source_kinds.py `__all__`）| F101 |
| ❌ F096 Phase E frontend agent 视角 UI | 独立 Feature |
| ❌ ApprovalGate production 接入（`ToolDeps._approval_gate`）| F101 |
| ❌ `supports_single_loop_executor` 类属性移除（mock fixture duck-type 依赖，F091 实证必须保留）| 不在 F100 范围 |

---

## 3. 用户故事（User Stories）

### US-1：主 Agent 自跑复杂查询时仍走完整决策环（H1）

**As a** 主 Agent（main_inline 路径）
**When** 用户发起长 context / 复杂查询（如跨多个 session 的回顾性问题）
**Given** 上层派发逻辑判断需要完整 recall（设置 `force_full_recall=True`）
**Then** recall planner phase **不被跳过**，按 RecallPlannerMode.AUTO 决议为 "full"
**Acceptance**: AC-1, AC-2

### US-2：simple query 性能不回退（F051 兼容）

**As a** 主 Agent（main_inline 路径）
**When** 用户发起 simple query（短 prompt + 无跨 session context）
**Given** 上层未设置 `force_full_recall`（默认 False）
**Then** 行为与 baseline 完全一致：recall planner phase **被跳过**
**Acceptance**: AC-3, AC-PERF-1

### US-3：Worker 路径不受影响

**As a** Worker（worker_inline 路径）
**When** 派发任意请求
**Given** F100 前 baseline 默认 skip
**Then** F100 后行为完全一致（worker_inline 默认 skip；除非显式 override）
**Acceptance**: AC-4

### US-4：F099 ask_back resume 路径不破

**As a** Worker 调用 `worker.ask_back` 后用户 attach_input 唤醒
**When** turn N+1 调用 LLM 之前
**Given** worker_inline 在 turn N+1 派发时由 orchestrator 重设 delegation_mode（**不依赖** control_metadata 透传——F099 实测 connection_metadata.TASK_SCOPED_CONTROL_KEYS 不含 runtime_context_json）
**Then** runtime_context.recall_planner_mode 仍按 worker_inline 解析为 skip，turn N+1 不跑 recall planner
**Acceptance**: AC-5, AC-N-H1-COMPAT

### US-7：上层 hint 触发 H1 完整决策环（HIGH-1 修复，minimal trigger）

**As a** 上层逻辑（chat 路由 / API 参数 / 调试工具）
**When** 判断主 Agent 自跑某个请求需要走完整决策环（如长 context 复杂查询）
**Given** 把 `force_full_recall: True` 写入 dispatch_metadata
**Then** orchestrator._prepare_single_loop_request 读取 hint 并设置 `runtime_context.force_full_recall=True`，AUTO 决议被 override 走 full recall
**Acceptance**: AC-H1, AC-H2

**Why**：F100 不引入复杂度判断（YAGNI；推 F101 Notification + Attention Model），但必须提供 minimal trigger 让 H1 真实可达——否则 force_full_recall / AUTO 是死能力。

### US-5：F090 双轨完全收尾

**As a** 维护者
**When** 阅读 task_service / orchestrator 代码
**Then**
- 任何 production 路径都不再读 `metadata["single_loop_executor"]`
- 任何 production 路径都不再写 `metadata["single_loop_executor"]`
- `is_single_loop_main_active` / `is_recall_planner_skip` 两个 helper 内部不再有 metadata fallback 分支
**Acceptance**: AC-6, AC-7

### US-6：未显式 delegation_mode 的路径默认走 standard routing（v0.3 修订）

**As a** 维护者
**When** 任何未来代码遗漏未显式设置 `delegation_mode`（残留 default "unspecified"）
**Then** `is_recall_planner_skip` / `is_single_loop_main_active` return False（**不 raise**；与 baseline metadata 缺失时等价）
**Why**: v0.2 原设计是 fail-fast raise，但 Phase C audit 发现 3/4 consumed 时点是 pre-decision（chat.py seed unspecified），raise 会破坏 chat 主链。v0.3 改为 return False 与 baseline 兼容。错误检测通过测试覆盖关键 patch 必经路径补强。
**Acceptance**: AC-8

---

## 4. 验收准则（Acceptance Criteria）

### AC-1：override flag 启用 full recall（H1 关键）

**Given** `RuntimeControlContext(delegation_mode="main_inline", recall_planner_mode="auto", force_full_recall=True)`
**When** 调用 `is_recall_planner_skip(rc, metadata)`
**Then** 返回 `False`

### AC-2：override flag 启用全场景一致

`force_full_recall=True` 对所有 `delegation_mode` 取值都返回 `False`（包括 main_inline / worker_inline / main_delegate / subagent）。

### AC-3：AUTO 默认依 delegation_mode 决议

| delegation_mode | recall_planner_mode | force_full_recall | is_recall_planner_skip() |
|-----------------|---------------------|-------------------|--------------------------|
| main_inline | auto | False | True (skip) |
| worker_inline | auto | False | True (skip) |
| main_delegate | auto | False | False (full) |
| subagent | auto | False | False (full) |

### AC-4：worker_inline baseline 行为不变

`RuntimeControlContext(delegation_mode="worker_inline", recall_planner_mode="skip")` 仍返回 True。
任何现有 worker_inline 代码路径行为零变化。

### AC-5：ask_back resume turn N+1 行为（v0.3 + Final review HIGH-2 修订）

**v0.1/v0.2 原假设错误**：spec 原写"runtime_context 透传机制保留 worker_inline → skip"。Phase F 实测发现 `task_runner._run_job` line 692 走 `get_latest_user_metadata` 取 metadata 时按 TASK_SCOPED_CONTROL_KEYS allowlist 过滤——**不含 runtime_context_json**。所以 resume 后 turn N+1 派发时 orchestrator 收到的 metadata 不包含 runtime_context_json。

**v0.3 实际行为**：
- baseline 与 F100 v0.3 一致：resume 后 `is_recall_planner_skip(None, metadata)` → **return False**（不 skip）
- turn N+1 **会跑 recall planner**
- 与 spec.md v0.1 / v0.2 假设的 "worker_inline → skip" 不一致；F100 不改 TASK_SCOPED_CONTROL_KEYS

**Acceptance（v0.3 修订）**：
- F099 ask_back resume 路径行为兼容 baseline（v0.3 helper 修改不影响）
- 单测覆盖：`test_ask_back_recall_planner_resume_f100.py` 显式断言 `is_recall_planner_skip(None, resume_metadata) is False`

**F101 handoff 项**：若期望 ask_back resume 保持 turn N delegation_mode（如 worker_inline → skip），需在 F101 / 独立 Feature 中：
- 把 `runtime_context_json` 加入 TASK_SCOPED_CONTROL_KEYS（连接 metadata trust boundary 改动）
- 或在 resume 路径显式 patch runtime_context（task_runner.attach_input → resume_state_snapshot 携带）

### AC-N-H1-COMPAT：is_caller_worker_signal 透传不破

F099 N-H1 路径 A 的 `CONTROL_METADATA_UPDATED` 事件 + `resume_state_snapshot` 透传 `is_caller_worker_signal` 机制不被破坏。
attach_input 路径 resume 后 WorkerRuntime 重建时仍能正确恢复 is_caller_worker flag。

### AC-6：metadata `single_loop_executor` 写入移除

`grep -r 'metadata\["single_loop_executor"\]\s*=\s*' octoagent/apps octoagent/packages` 返回 0 行 production 代码（v0.3：测试 fixture 也已迁移到显式 delegation_mode；不再依赖 metadata 写入）。

同 `single_loop_executor_mode` 字段也移除。

### AC-7：metadata fallback 移除

`runtime_control.py` 的 `is_single_loop_main_active` / `is_recall_planner_skip` 移除 `metadata_flag(metadata, "single_loop_executor")` 的 fallback 分支。

### AC-8：unspecified delegation_mode consumed 时 return False（v0.3 修订）

**v0.3 修订**：v0.2 "consumed 时 raise" 经 Phase C 实测会破坏 chat 主链（3 个 pre-decision 时点会触达 unspecified）。改为 return False：

`is_recall_planner_skip(RuntimeControlContext(delegation_mode="unspecified"), metadata)`：
- F100 前：fallback metadata flag（`return metadata_flag(metadata, "single_loop_executor")`）
- F100 后：**return False**（无 metadata fallback；与 baseline metadata 缺失时等价）

`is_single_loop_main_active` 同理：unspecified → return False。

**保留 v0.2 修订**：不在 RuntimeControlContext 构造点 raise——chat.py 等 pre-decision seed context 保持 unspecified 合法。

### AC-9：consumed 时点一致性测试（v0.3 修订）

**v0.3 修订**：原 v0.2 要求 unspecified consumed 时 raise；v0.3 改为验证：
- 测试 fixture 覆盖：unspecified consumed 时 return False（与 baseline 兼容）
- 测试 fixture 覆盖：显式 delegation_mode patch 后 helper 调用得到预期值
- 测试覆盖关键 patch 必经路径（orchestrator._prepare_single_loop_request 必经 `_with_delegation_mode`，patched runtime_context 显式 delegation_mode）

错误检测从 fail-fast 改为测试覆盖保障——容忍漏 patch 路径的静默 fallback 到 False（与 baseline 默认 routing 行为等价），不破坏主链。

### AC-H1：metadata hint 转换为 force_full_recall（HIGH-1 修复）

**Given** 上层（chat 路由 / API 参数）写入 `dispatch_metadata["force_full_recall"] = True`
**When** orchestrator._prepare_single_loop_request 处理该请求
**Then** patched runtime_context 中 `force_full_recall == True`
**And** is_recall_planner_skip 返回 False（force_full_recall 优先级最高）

### AC-H2：metadata hint 默认值兼容

**Given** dispatch_metadata 不含 `force_full_recall` key（baseline）
**When** orchestrator._prepare_single_loop_request 处理该请求
**Then** patched runtime_context 中 `force_full_recall == False`（默认值）
**And** is_recall_planner_skip 按 delegation_mode + recall_planner_mode 正常决议

### AC-PERF-1：simple query 性能不回退（MEDIUM-2 + Final review MEDIUM-1 修订）

**v0.1 原方法**（5x e2e P50/P95 + 5% hard gate）经 Codex MED-2 评估统计基础不足。

**v0.2/v0.3 修订（mock-based 控制变量）**：
- **测量入口**：pytest fixture 直接调 `is_recall_planner_skip` / `is_single_loop_main_active` helper
- **测量样本**：1000+ 次/path（运行时实测 5000）
- **通过门（绝对值）**：每路径单次调用 < 100μs（mock 环境硬门，远高于实际表现）
- **通过门（相对路径对照）**：F100 引入的额外分支次数 ≤ 2（force_full_recall early-check + AUTO switch）；实测 simple query 路径未恶化（main_inline + skip 仍 ~0.05μs，与 baseline 等价）

**v0.3 Final review MEDIUM-1 备注**：spec v0.2 写 "回归 ≤ 5%" 表述实际未真跑 F099 baseline 对比（mock 数据是 F100 内部相对路径对照）。本 AC 不要求真实 baseline 跑分——F100 helper 改动是单 if-check + switch 几行，性能影响极小，绝对值阈值（100μs）已远超合理上限。

**e2e_smoke 5x 仅作 sanity check**：验证 simple query 整体路径未崩，不作 perf hard gate（噪声大于 5%）。

### AC-PERF-2：override 启用 full recall 时延迟增加可接受（MEDIUM-2 修订）

mock 测量 `force_full_recall=True` 触发 full recall 时 recall planner LLM 调用 phase 增加的延迟。
通过门：单次增加 ≤ 5s（recall planner LLM 调用预期 1-3s）。**软门**（环境波动允许），不阻塞 commit。

### AC-10：全量回归 0 regression

`pytest octoagent` 通过数 ≥ F099 baseline (3450) + F100 新增测试数。
e2e_smoke 5x 循环 PASS。

### AC-11：supports_single_loop_executor 类属性保留

- `LLMService.supports_single_loop_executor = True` 类属性保留
- `getattr(llm_service, "supports_single_loop_executor", False)` duck-type 检测保留
- mock fixture 缺该属性的 contract 不变

### AC-12：F099 ask_back / source_runtime_kind 不破

- 三工具 ask_back / request_input / escalate_permission 行为不变
- source_runtime_kind 5 值枚举不动
- `_resolve_a2a_source_role()` 行为不变

---

## 5. 功能性需求（Functional Requirements）

### FR-A：RecallPlannerMode "auto" 实际语义

**FR-A1**：移除 `runtime_control.py:124` 的 `raise NotImplementedError`，替换为 "依 delegation_mode 自动决议" 的 switch：
```python
if runtime_context.recall_planner_mode == "auto":
    if runtime_context.delegation_mode in {"main_inline", "worker_inline"}:
        return True  # skip
    if runtime_context.delegation_mode in {"main_delegate", "subagent"}:
        return False  # full
    # delegation_mode == "unspecified"：FR-D2 已禁止此路径，但 defense-in-depth
    raise ValueError(...)
```

**FR-A2**：override flag 优先级最高：
```python
if runtime_context.force_full_recall:
    return False  # 强制 full，覆盖所有 mode 决议
```

放在 helper 函数最前面（第一道闸门）。

### FR-B：RuntimeControlContext 引入 `force_full_recall` 字段

**FR-B1**：在 `packages/core/src/octoagent/core/models/orchestrator.py` 的 `RuntimeControlContext` 上加字段：
```python
force_full_recall: bool = Field(
    default=False,
    description=(
        "F100：override flag，强制走完整 recall planner phase。"
        "用于 H1 完整决策环——上层判断主 Agent 自跑长 context 复杂查询时设 True。"
        "默认 False（行为兼容 F091 baseline）。"
    ),
)
```

**FR-B2**：encode_runtime_context / decode_runtime_context 正确 round-trip 此字段（pydantic 默认行为已支持，仅需测试覆盖）。

### FR-C：consumed 时点 delegation_mode 显式化（HIGH-2 修订）

**FR-C1**：audit `is_recall_planner_skip(` / `is_single_loop_main_active(` 调用点，沿调用链向上追溯：
- 每个调用前 runtime_context.delegation_mode 必须显式（≠ "unspecified"）
- 关键 patch 必经路径：`orchestrator._prepare_single_loop_request` / `orchestrator._with_delegation_mode`（已 baseline 显式）/ `dispatch_service._resolve_a2a_source_role`（F098/F099 已就位）

**FR-C2**：**不要求** `RuntimeControlContext(` 构造点全部显式。chat.py / web 入口的 pre-decision seed context 允许 unspecified。orchestrator 后续路径必须 patch 到显式 mode（baseline 行为，仅需测试覆盖）。

**FR-C3**：测试 fixture 覆盖：
- 单测：构造 unspecified RuntimeControlContext 直接调 helper → 验证 raise
- 集成测试：模拟 chat.py 路径，验证 orchestrator patch 后 helper 调用成功

### FR-D：移除 metadata fallback 与写入（v0.3 修订）

**FR-D1**：`runtime_control.py:is_single_loop_main_active`：
- 移除 `return metadata_flag(metadata, "single_loop_executor")` 分支
- **unspecified delegation_mode 或 runtime_context=None → return False**（v0.3 修订；与 baseline 默认行为等价）

**FR-D2**：`runtime_control.py:is_recall_planner_skip`：
- 移除 `return metadata_flag(metadata, "single_loop_executor")` 分支
- unspecified delegation_mode 或 runtime_context=None → return False（v0.3 修订）

**FR-D3**：`orchestrator.py:_prepare_single_loop_request`：
- 移除 `metadata["single_loop_executor"] = True` 写入
- 移除 `metadata["single_loop_executor_mode"]` 写入
- 保留 `delegation_mode` + `recall_planner_mode` 的 runtime_context 写入

**FR-D4**：保留 `metadata_flag()` helper 本身（其他 caller 可能用到，且属于通用 utility）

### FR-E：ask_back resume 验证（HIGH-3 + MEDIUM-1 修订）

**FR-E1**：实测验证 ask_back resume 路径上 turn N+1 调用 `is_recall_planner_skip` 返回 `True`。**关键认知**：worker_inline → skip 不依赖 control_metadata 透传 runtime_context_json——而是 orchestrator 在 turn N+1 派发时由 `_with_delegation_mode` / `_prepare_single_loop_request` 重新设置 delegation_mode。

**FR-E2**：单测覆盖 ask_back resume 路径上 `is_caller_worker_signal`（F099 N-H1 修复）透传不被破坏

**FR-E3**：实测追踪 resume 路径的 runtime_context 真实来源：
- 调用链：`task_runner.attach_input` → `_spawn_job` → `_run_job` → `process_task_with_llm` → orchestrator dispatch
- 追溯点：`get_latest_user_metadata` 返回的字段是否包含 RUNTIME_CONTEXT_JSON_KEY？
- 验证 turn N+1 派发时 orchestrator 是否重新 patch 到显式 delegation_mode

**FR-E4**：**Phase F 必须在 Phase E 移除 fallback 之前完成**（HIGH-3 修复，避免 destructive E 误删后 ask_back 静默漂移）

### FR-F：性能基准（MEDIUM-2 修订）

**FR-F1**：mock-based 控制变量测量 `is_recall_planner_skip` / `is_single_loop_main_active` helper：
- 测量入口：pytest fixture 构造 RuntimeControlContext + 直接调 helper（无 LLM / 无 DB / 无 IO）
- 测量样本：每场景 ≥ 1000 次（用 `timeit` 或 `pytest-benchmark`）
- 测量指标：均值耗时（微秒级）+ 分支访问次数

**FR-F2**：mock-based 测量 `force_full_recall=True` 场景增加的 recall planner phase 入口延迟

**FR-F3**：e2e_smoke 5x 仅作 sanity check（验证 simple query 整体路径未崩），**不作 hard gate**

### FR-H：metadata hint → force_full_recall 接入（HIGH-1 修复，minimal trigger）

**FR-H1**：`orchestrator.py:_prepare_single_loop_request` 读取 `metadata.get("force_full_recall")`，bool/str 兼容解析（复用 `_metadata_flag` helper），传递给 `_with_delegation_mode`。

**FR-H2**：`orchestrator._with_delegation_mode` helper（或新增 helper）把 `force_full_recall` 写入 patched runtime_context。

**FR-H3**：不在 main_inline 路径自动触发 force_full_recall（无复杂度判断）。仅当上层显式传 hint 时触发。

**FR-H4**：upstream 写 hint 的实现**不在 F100 范围内**——F100 仅提供 minimal trigger 接口。chat 路由 / API 参数 / 调试工具是潜在 hint writer（推 F101 Notification + Attention Model 或独立 Feature）。**handoff 给 F101**记录此接入点。

**FR-H5**：metadata hint 设计避免与 F098/F099 已有的 metadata 字段冲突。命名锁定 `"force_full_recall"`（与 RuntimeControlContext 字段同名，避免命名分歧）。

### FR-G：测试 fixture 更新

**FR-G1**：原依赖 metadata fallback 行为的测试转为显式 delegation_mode 测试

**FR-G2**（v0.3 修订）：新增 unspecified delegation_mode return False 测试（与 baseline 兼容；不 raise）

**FR-G3**：新增 force_full_recall 覆盖所有 delegation_mode 的测试

**FR-G4**：新增 AUTO mode 决议测试（4 个 delegation_mode 取值各一）

---

## 6. 非功能性需求（Non-Functional Requirements）

### NFR-1：行为兼容性

F100 默认行为（不设 override）与 F099 baseline (049f5aa) 100% 等价。仅 override flag 是新行为。

### NFR-2：性能

- simple query 延迟回归 ≤ 5%（AC-PERF-1）
- override full recall 延迟增加 ≤ recall planner LLM 单次调用时间（预期 1-3s）

### NFR-3：测试覆盖

- 全量回归 0 regression vs F099 baseline
- e2e_smoke 5x 循环 PASS
- 新增覆盖：AUTO mode 决议 / override flag / unspecified raise / metadata 移除验证

### NFR-4：可逆性

F100 改动**不可逆**（移除 fallback）。但可通过 F107 D2 路径重新引入更精细的复杂度自适应（partial 中间档）。

### NFR-5：可观测性

不引入新事件类型（OD：AUTO mode 决议无需独立审计事件，依现有 task_service 审计链路即可）。

---

## 7. 命名约定（避免概念混淆）

| 概念 | 名称 | 类型 | 范围 |
|------|------|------|------|
| caller 委托模式 | `delegation_mode` | Literal["unspecified","main_inline","main_delegate","worker_inline","subagent"] | F090 引入，F091 强化，F100 强制显式 |
| recall planner 决策 | `recall_planner_mode` | Literal["full","skip","auto"] | F090 占位，F091 实现 skip/full，F100 启用 auto |
| H1 override flag | `force_full_recall` | bool | F100 **新增** |
| caller 身份 | `source_runtime_kind` | Literal 5 值 | F099 引入，不在 F100 改动范围 |

**严格规则**：`recall_planner_mode` 和 `force_full_recall` 是不同维度——前者是默认决议，后者是 override。`force_full_recall=True` 时 `recall_planner_mode` 取何值都不重要（被覆盖）。

---

## 8. 关键文件清单

### 必改文件

| 文件 | 改动 |
|------|------|
| `octoagent/packages/core/src/octoagent/core/models/orchestrator.py` | RuntimeControlContext 加 `force_full_recall` 字段 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/runtime_control.py` | `is_recall_planner_skip` 启用 "auto" 语义 + 移除 fallback + 引入 force_full_recall override；`is_single_loop_main_active` 移除 fallback |
| `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` | 移除 metadata["single_loop_executor"] 写入；audit `RuntimeControlContext(` 构造点显式 delegation_mode |

### 可能涉及（FR-C 显式化）

| 文件 | grep 关键词 |
|------|-----------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/dispatch_service.py` | `RuntimeControlContext(` 构造点 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py` | 同上 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` | 同上 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py` | 同上 |

### 必加测试

| 文件 | 新增 |
|------|------|
| `tests/test_runtime_control_f100.py`（新） | AUTO 决议 / override / unspecified raise / fallback 移除 |
| `tests/test_orchestrator_f100.py`（新或并入现有）| metadata 写入移除验证 |
| 现有 `tests/test_runtime_control_f091.py` | unspecified 路径断言迁移（fallback 移除后变 raise）|

---

## 9. 验证策略（Verification Strategy）

### 9.1 单元测试（每 Phase 后跑）

- F091 baseline `pytest tests/test_runtime_control_f091.py` 必须先全部迁移到 F100 行为
- 新增 F100 测试：AUTO 决议 / override flag / unspecified raise

### 9.2 集成测试（Phase G）

- e2e_smoke 5x 循环（**sanity check only**，MEDIUM-2 修订后不作 hard gate）：
  - #1 工具调用基础（simple query path）
  - #2 USER.md 全链路（single-loop main path）
  - #3 冻结快照（无关）
  - #11 ThreatScanner block（无关）
  - #12 ApprovalGate SSE（无关）

### 9.3 性能基准（Phase G，MEDIUM-2 修订）

**v0.2 修订为 mock-based 控制变量测量**：

- **simple query helper 测量**：pytest fixture 直接调 `is_recall_planner_skip` / `is_single_loop_main_active`，1000+ 样本均值。F100 commit vs F099 baseline 回归 ≤ 5%（hard gate）
- **override full recall 测量**：mock fixture 触发 force_full_recall=True，测量 recall planner phase 入口延迟（软门 ≤ 5s）
- **e2e_smoke**：sanity check（不作 hard gate）

### 9.4 全量回归（每 Phase 末 + Verify Phase）

`pytest octoagent`：通过数 ≥ F099 baseline (3450) + F100 新增测试数。

### 9.5 Codex Adversarial Review

- 每 Phase（C/F/D/E1/E2/G）后做 per-Phase Codex review
- **Final cross-Phase Codex review**（强制）
- 大改动 commit 后 re-review（F099 实证 N-H1 是 re-review 抓到）

---

## 10. Phase 计划（v0.2 HIGH-3 + MEDIUM-1 修订：C→F→D→E1→E2→G→H）

| Phase | 内容 | 工作量估计 | 关联 AC/FR |
|-------|------|----------|-----------|
| **Phase 0** | Recon 实测（已完成） | — | — |
| **Phase B** (Spec/Plan) | GATE_DESIGN + Codex pre-impl review 闭环 | — | 4 finding 闭环 |
| **Phase C** | consumed 时点 audit + 测试 fixture 准备（**不**显式化构造点；保留 pre-decision unspecified） | 0.3d | FR-C, AC-9 |
| **Phase F** | **前置到 D/E 之前**：ask_back resume 真实恢复机制实测 + 单测 + 文档修正 | 0.5d | FR-E, AC-5, AC-N-H1-COMPAT, AC-12 |
| **Phase D** | RuntimeControlContext 加 `force_full_recall` 字段 + AUTO 决议启用 + metadata hint 接入（FR-H） | 1d | FR-A, FR-B, FR-H, AC-1/2/3, AC-H1/H2 |
| **Phase E1** | 移除 orchestrator metadata 写入（`single_loop_executor` / `single_loop_executor_mode`） | 0.3d | FR-D3, AC-6 |
| **Phase E2** | 移除 helper metadata fallback + consumed 时 unspecified raise + 测试 fixture 迁移 | 0.5d | FR-D1/2/4, AC-7/8 |
| **Phase G** | mock-based perf 基准 + 全量回归 + e2e_smoke 5x sanity | 0.5d | NFR-2, AC-PERF-1/2, AC-10 |
| **Phase H (Verify)** | Final Codex review 闭环 + completion-report + handoff to F101 | 0.5d | 全局 |

**总估**：3.6-4.1 天（v0.2 拆分 E1/E2 后稍增 0.1d；F 前置不增加总工作量，仅调整顺序）

**Phase 顺序理由**：
- C 先准备 audit + test fixture（不引入运行时行为变化）
- F 前置到 D/E 之前——HIGH-3 修复关键：先实测 resume 机制，再做 destructive E
- D 引入新行为（AUTO + force_full_recall + FR-H minimal trigger）—— 行为可加但不删
- E1 移除 metadata 写入（影响小，方便 bisect）
- E2 移除 reader fallback（最 destructive，最后做）
- G 验证 + H 收尾

---

## 11. 风险与缓解（v0.2 修订）

| 风险 | 严重度 | 缓解策略 |
|------|--------|----------|
| ~~v0.1：FR-C 显式化时漏掉 production 构造点~~ | ~~HIGH~~ | **v0.2 修订**：FR-C 缩范围到 consumed 时点；构造点允许 unspecified；风险降级 |
| AUTO 决议启用时 main_delegate / subagent 路径行为变化 | MED | Phase D 实施前精确 grep 这些路径的 recall_planner_mode 设置；如果 baseline 已是 "full"，行为零变化 |
| `force_full_recall` 字段在 metadata round-trip 中丢失 | MED | Phase D 必须测试 encode/decode round-trip；测试 fixture 覆盖 |
| **HIGH-3 修复**：ask_back resume 不透传 runtime_context | HIGH→LOW | Phase F 前置：先实测 turn N+1 派发时 orchestrator 是否重新 patch；FR-E3 显式追溯调用链 |
| Final Codex review 抓到 high finding 需 re-review（F099 实证 N-H1）| MED | 预留 0.3-0.5d Phase H 时间；大改动 commit 后必走 re-review |
| **MEDIUM-2 修订后**：mock-based perf 测量与真实 e2e 行为偏差 | MED | mock 仅测 helper 内部；真实 e2e 路径走 sanity check（不作 hard gate） |
| `supports_single_loop_executor` 类属性意外破坏 mock fixture | MED | Phase C 显式化时不动此属性；Phase H 全量 mock 测试覆盖验证 |
| **HIGH-1 修复 metadata hint** 与 F098/F099 已有 metadata 字段冲突 | LOW | FR-H5 锁定命名 `"force_full_recall"`（与字段同名）；grep 验证不与其他 metadata key 冲突 |
| ~~Phase E2 移除 fallback 后 chat.py seed context unspecified 触达 helper~~ | ~~HIGH~~→**NONE** | **v0.3 修订**：unspecified → return False（与 baseline 兼容），chat.py 主链零破坏。Phase C audit 实测确认 3/4 consumed 时点是 pre-decision，需保持 baseline 兼容 |

---

## 12. Handoff 给 F101 的预期承接项（v0.2 扩充）

F100 完成后 handoff.md 需要给 F101 说明：

1. **决策环改造后 Notification 触发点**：H1 完整决策环（override 触发的 full recall）是否需要新增 Notification 类型（如"主 Agent 启用了完整 recall planner phase"）
2. **Attention Model 信号源**：force_full_recall override 状态可能成为 Attention Model 的输入信号
3. **F099 7 项推迟项的当前状态评估**：F100 是否间接触及任一项？预期答案：仅 N-H1 PARTIAL is_caller_worker resume 路径间接相关（FR-E2 覆盖）
4. **RecallPlannerMode 演进路径**：F107 partial 中间档实现允许破坏式升级（重命名为 `recall_override_mode: Literal["off", "full", "partial"]` 或叠加第二字段），F100 显式承担此演进负担（LOW-1 闭环）
5. **HIGH-1 minimal trigger 演进**：F100 仅提供 metadata hint 接入（FR-H）；上层 producer 实现（chat 路由 / API 参数 / 复杂度评估等）作为 F101 或独立 Feature 主要责任。F101 在实施 Notification + Attention Model 时可借助 force_full_recall hint 实现 H1"主 Agent 自跑复杂查询完整决策环"的产品价值

---

**Status**: Draft v0.2（Codex pre-impl 4 finding 闭环），准备进入 implement。
