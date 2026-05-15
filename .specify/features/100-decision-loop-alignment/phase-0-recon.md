# F100 Phase-0 Recon — 实测侦察报告

**Date**: 2026-05-14
**Worktree**: `.claude/worktrees/F100-decision-loop-alignment`
**Baseline**: 049f5aa (F099 完成)
**调研方式**：grep + Read，零假设，全量代码验证

---

## 0. 关键发现总览（TL;DR）

| 编号 | 发现 | 影响 |
|------|------|------|
| **K1** | F091 已实现 `is_recall_planner_skip` helper 集中读取；`RecallPlannerMode "auto"` 在 `runtime_control.py:124` raise `NotImplementedError`，预留 F100 启用 | F100 启用 "auto" 是单点改动 |
| **K2** | F091 注释明确："auto" 的预期语义是**"依 delegation_mode 自动决议"**——不是用户 prompt 描述的"按复杂度自适应" | OD-1 候选需重新评估：用户 prompt 设计 vs F091 设计意图 |
| **K3** | 无独立 `recall_planner.py` 文件：recall planner 实现就在 `_build_memory_recall_plan` (task_service.py:1117-1199)，决策门是 `is_recall_planner_skip + prefetch_mode + planner_enabled + memory_scope_ids + query` 五道闸门 | "按复杂度自适应"实现必须在 `is_recall_planner_skip` 之外的层做（否则推翻 F091 单一入口） |
| **K4** | F090 metadata 双轨：`single_loop_executor` 仍由 `orchestrator.py:829` 单点写入；F091 读取统一进 `is_single_loop_main_active` + `is_recall_planner_skip` 两个 helper。fallback 路径**仅在 `delegation_mode == "unspecified"` 时触发** | F100 移除 fallback 风险：先验证所有 production caller 显式 set `delegation_mode` |
| **K5** | F099 ask_back resume 路径：`task_runner.attach_input` → `_spawn_job(resume_from_node="state_running")` → 不重设 runtime_context（依赖原 task metadata 中的 runtime_context 透传）| F099 handoff §2 描述"WAITING_INPUT 期间不运行"是 PARTIAL TRUE——RUNNING 后的 turn N+1 仍走 `_build_task_context`，是否跑 recall 取决于 `runtime_context.recall_planner_mode` 的值 |
| **K6** | `supports_single_loop_executor` 类属性在 `llm_service.py:220, 224` 定义；duck-type 检测在 `orchestrator.py:906`；mock fixture 缺该属性表达"不支持" | **必须保留** —— F091 实证 |
| **K7** | F099 `is_caller_worker_signal` 与 `recall_planner_mode` 完全正交（前者管 WorkerRuntime 重建，后者管 recall planner 跳过）| F100 改造不与 F099 冲突 |

---

## A. 现状代码片段（任务 1-4）

### A.1 [task 1] single_loop_executor 跳过 recall planner 的入口

文件：`octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:1117-1135`

```python
async def _build_memory_recall_plan(
    self,
    *,
    task_id: str,
    trace_id: str,
    model_alias: str | None,
    llm_service,
    compiled: CompiledTaskContext,
    dispatch_metadata: dict[str, Any],
    worker_capability: str | None,
    tool_profile: str | None,
    runtime_context: RuntimeControlContext | None,
) -> RecallPlan | None:
    precomputed_plan = self._parse_precomputed_recall_plan(dispatch_metadata)
    if precomputed_plan is not None:
        return precomputed_plan
    # F091 Phase C: 优先读 runtime_context.recall_planner_mode == "skip"；fallback metadata flag
    if is_recall_planner_skip(runtime_context, dispatch_metadata):
        return None
```

注：用户 prompt 中说的 line 1044 不准（baseline 已进化，F098/F099 改动后偏移至 1134）。F100 实施时统一以 grep 定位。

### A.2 `is_recall_planner_skip` 当前实现

文件：`octoagent/apps/gateway/src/octoagent/gateway/services/runtime_control.py:96-129`

```python
def is_recall_planner_skip(
    runtime_context: RuntimeControlContext | None,
    metadata: Mapping[str, Any] | None,
) -> bool:
    """...
    读取优先级：
    1. runtime_context.delegation_mode != "unspecified"（已显式）→ 看 recall_planner_mode：
       - "skip" → True
       - "full" → False
       - "auto" → raise NotImplementedError（防止 F091 隐式定义；F100 启用）
    2. runtime_context.delegation_mode == "unspecified" 或 runtime_context = None
       → fallback metadata flag（保持旧逻辑等价）

    F100 收口：实施 "auto" 实际语义（依 delegation_mode 自动决议）+ 删除 metadata fallback。
    """
    if runtime_context is not None and runtime_context.delegation_mode != "unspecified":
        if runtime_context.recall_planner_mode == "skip":
            return True
        if runtime_context.recall_planner_mode == "full":
            return False
        # "auto" 显式 fail-fast
        raise NotImplementedError(
            'RecallPlannerMode "auto" not implemented in F091; F100 will enable.'
            ' Use "skip" or "full" explicitly.'
        )
    # fallback metadata flag
    return metadata_flag(metadata, "single_loop_executor")
```

**F091 注释口径**：F100 启用 "auto" 语义 = **"依 delegation_mode 自动决议"**（与用户 prompt 的"按复杂度自适应"不同）

### A.3 RecallPlannerMode Literal 定义

文件：`octoagent/packages/core/src/octoagent/core/models/orchestrator.py:39-52`

```python
DelegationMode = Literal[
    "unspecified",
    "main_inline",
    "main_delegate",
    "worker_inline",
    "subagent",
]

RecallPlannerMode = Literal["full", "skip", "auto"]

class RuntimeControlContext(BaseModel):
    delegation_mode: DelegationMode = Field(default="unspecified", ...)
    recall_planner_mode: RecallPlannerMode = Field(default="full", ...)
```

### A.4 `recall_planner_mode` 写入端（production）

实测唯一明确写入点：`octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:780-854` (within `_prepare_single_loop_request`)

```python
# F091 Phase D：single_loop 请求 patch runtime_context（与 metadata flag 双轨）
patched_runtime_context = self._with_delegation_mode(
    request=request,
    metadata=metadata,
    delegation_mode="main_inline",
    recall_planner_mode="skip",
)
```

其他构造点（如 `_build_runtime_context_for_*`）大多通过 `_with_delegation_mode` helper 走，没有直接硬编码 "auto"/"full"/"skip"。default = "full"。

### A.5 `runtime_context_from_metadata` 调用点（读取端 fallback 路径）

| 文件 | 行号 | 角色 |
|------|------|------|
| `task_service.py` | 102 (import), 1214 (`_build_task_context`) | 从 dispatch_metadata 提取 runtime_context |
| `orchestrator.py` | 107, 770, 883, 1047 | 多处 `request.runtime_context or runtime_context_from_metadata(metadata)` |
| `delegation_plane.py` | 51, 736 | delegation 路径透传 |
| `llm_service.py` | 36, 382 | `_call_llm_service` 内读取 |
| `dispatch_service.py` | 65, 102 (A2ADispatchMixin) | A2A 派发路径 |

所有路径都是优先用显式 `request.runtime_context`，缺失才从 metadata fallback。

### A.6 [task 1] `single_loop_executor` metadata flag 出现位置

| 文件 | 行号 | 角色 |
|------|------|------|
| `orchestrator.py` | 829 | 写入端（`metadata["single_loop_executor"] = True`）+ 829 附近 `single_loop_executor_mode` 字段 |
| `runtime_control.py` | 93 (in `is_single_loop_main_active`), 129 (in `is_recall_planner_skip`) | 读取 fallback（仅 delegation_mode=="unspecified" 触发）|
| `llm_service.py` | 220, 224 | `supports_single_loop_executor` **类属性**（duck-type 检测，**与 metadata flag 不同源**——这是给 mock fixture 用的）|
| `orchestrator.py` | 906 | `getattr(llm_service, "supports_single_loop_executor", False)` duck-type 检测 |
| 测试 | 多处 | test_orchestrator.py / test_llm_service_tool_guidance.py 等 |

**结论（K6）**：`supports_single_loop_executor` 类属性 vs metadata `single_loop_executor` flag 是两个独立信号源：
- **类属性**：表达"LLMService 实现是否支持 single-loop 路径"（mock 缺属性 → False）—— **F100 必须保留**
- **metadata flag**：表达"当前请求是否走 single-loop 路径"（F090 双轨 + F091 fallback）—— **F100 收尾删除目标**

### A.7 [task 2] recall planner 内部分层

实测：**无 `recall_planner.py` 模块文件**。recall planner 决策门集中在 `task_service._build_memory_recall_plan`：

```
入口：_build_memory_recall_plan(task_id, runtime_context, dispatch_metadata, ...)
门 1: precomputed_plan（dispatch_metadata 已带 plan）→ 直接 return
门 2: is_recall_planner_skip(runtime_context, dispatch_metadata) → return None
门 3: _supports_recall_planning(llm_service) → return None（LLMService 不支持）
门 4: task = get_task(task_id) → return None（任务消失）
门 5: planning_context = build_recall_planning_context(...)
门 6: prefetch_mode ∉ {"agent_led_hint_first", "hint_first"} → return None
门 7: not planner_enabled / not memory_scope_ids / empty query → return None
otherwise: 跑 LLM recall planner，产 plan
```

**关键**：当前无"按复杂度自适应"的现成 affordance。所有 grep 关键词（complexity / prompt_length / message_count / token_count）都没在 recall planner 路径里命中。

`build_recall_planning_context`（`agent_context.py`）也只是构造 LLM 输入，没有按复杂度短路的逻辑。

### A.8 [task 3] F090 metadata 双轨状态

写入双轨：`orchestrator.py:827-854`（同时写 `metadata["single_loop_executor"]=True` + `runtime_context.delegation_mode="main_inline"+recall_planner_mode="skip"`）

读取 fallback：
- `is_single_loop_main_active`（runtime_control.py:76-93）：unspecified → fallback metadata flag
- `is_recall_planner_skip`（runtime_control.py:96-129）：unspecified → fallback metadata flag

**实测 production reader 数量 = 2**（都是 helper 函数，无 production code 直接读 `metadata["single_loop_executor"]`）

**F100 移除 metadata 写入是否安全？**
- 所有 caller 的 metadata 是 helper 的 fallback，但 fallback 仅在 unspecified 时触发
- **未来风险**：任何遗漏未显式设置 `delegation_mode` 的 caller，移除 fallback 后会从 "skip 路径" 变成 "走 full recall" 路径（行为变化）
- F100 实施时必须 grep 全部 RuntimeControlContext 构造点，验证 100% 显式

### A.9 [task 4] F099 ask_back resume 路径

文件：`octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:577-640` (`attach_input` 主路径)

```
worker.ask_back tool call
  ↓
execution_context.request_input(prompt)
  ↓
task.status = WAITING_INPUT + session = WAITING_INPUT
  ↓
（task_runner 轮询挂起）
  ↓
user attach_input(task_id, text)
  ↓
F099 N-H1 修复：读 user_metadata 中的 is_caller_worker_signal → resume_state_snapshot
  ↓
_spawn_job(task_id, resume_from_node="state_running", resume_state_snapshot={...})
  ↓
（后台）process_task_with_llm()
  ↓
_build_task_context(... runtime_context, dispatch_metadata ...)
  ↓
_build_memory_recall_plan(... runtime_context ...)  ← ★ 关键路径
```

**runtime_context 透传机制**：
- task_runner.attach_input **不重新设置 runtime_context**
- runtime_context 通过 metadata 透传，来源于 turn N 派发时的 `_with_delegation_mode` 结果
- worker.ask_back 触发的 Worker turn 当前 `delegation_mode = "worker_inline"` + `recall_planner_mode = "skip"`（实测：orchestrator.py 内 worker_inline 路径同样设 skip）

**意义**：
- **F099 handoff §2 说"WAITING_INPUT 期间不运行"是字面成立**（WAITING_INPUT 时 task_runner 挂起，不调 task_service.run_turn）
- 但 RUNNING 恢复后 turn N+1 **会调** `_build_memory_recall_plan`，是否真跑 recall 取决于 `runtime_context.recall_planner_mode`
- 当前 worker_inline 路径默认 "skip"，所以 ask_back resume 后 turn N+1 **实测 skip recall planner**（baseline 已通）

**与 F099 handoff §2 的关系**：handoff §2 表述精确但不完整。F100 OD-3 是确认这条路径未来仍 skip。

---

## B. 7 连 pattern 验证：baseline 是否已部分通

| F100 范围块 | 任务 | baseline 状态 | 证据 |
|-------------|------|---------------|------|
| 块 B (auto 启用) | RecallPlannerMode.AUTO 占位 | **占位已就绪** | runtime_control.py:124 raise NotImplementedError；单点改动可启用 |
| 块 B (auto 启用) | 决策语义 | **F091 注释定义清楚** | "依 delegation_mode 自动决议"——非用户 prompt 的"按复杂度自适应" |
| 块 C (移除 hack) | single_loop_executor 跳过 hack | **F091 已收敛** | task_service.py:1134 已用 `is_recall_planner_skip` 单入口；移除 hack 实际是改 helper 内部，不动 task_service |
| 块 D (双轨收尾) | metadata fallback 读取面 | **已收敛到 2 个 helper** | runtime_control.py:93/129 是仅有 reader（除 mock 测试 fixture）|
| 块 E (ask_back resume) | recall planner 跳过 | **baseline 已通** | worker_inline delegation_mode 默认 recall_planner_mode="skip" |
| 全局 | `supports_single_loop_executor` 类属性保留 | **必须保留** | mock fixture duck-type 依赖（F091 实证）|

**结论（沿用 F093-F099 七连 pattern）**：F100 baseline 大部分基础设施已就绪，主要工作量是：
1. 启用 `RecallPlannerMode.AUTO` 实际语义（**单点改动 helper**）
2. 全量 grep 显式化 `delegation_mode`（**多点小改动 + 测试覆盖**）
3. 移除 metadata fallback + 写入（**小改动，但需全测试 fixture 更新**）

**估计实际工作量比用户 prompt 描述少 30-50%**——核心新逻辑只在一个 helper。

---

## C. F099 handoff §2 / §3 实测验证

### §2: "ask_back recall planner 期间不运行"

**字面成立**（WAITING_INPUT 时 task_runner 挂起）。但 RUNNING 恢复后的 turn N+1 仍走 `_build_memory_recall_plan`，是否 skip 取决于 `runtime_context.recall_planner_mode`。

baseline 行为：worker_inline 路径默认 "skip" → ask_back resume 后 turn N+1 **实测 skip**（baseline 已通）。

F100 OD-3 候选：
- A：保持 baseline（worker_inline + skip）—— 无需任何 ask_back 专用改动
- B：在 attach_input 路径显式 patch `recall_planner_mode="skip"`（防御性）—— 多余，因为 worker_inline 已 skip

**推荐 OD-3 = A**（YAGNI）。仅在 OD-1 决策启用"按复杂度自适应"使 worker_inline default 从 "skip" 变成 "full" 时，B 才有意义。

### §3: `supports_single_loop_executor` 保留

**完全成立**。F100 实施必须保留此类属性。

---

## D. 风险清单：F100 改动可能破坏的路径

### D.1 metadata fallback 移除破坏面

**风险层级**：HIGH

任何遗漏未显式设置 `delegation_mode` 的 caller，移除 fallback 后行为变化：
- 旧：unspecified → fallback metadata flag → 可能 skip
- 新（fallback 移除）：unspecified → recall_planner_mode default "full" → 不 skip

**Mitigation**：
1. F100 实施前 grep `RuntimeControlContext(` 找所有构造点
2. 验证所有 production 路径都显式 set `delegation_mode`（≠ "unspecified"）
3. 测试 fixture 用 `delegation_mode="unspecified"` 的，构造一遍 baseline 行为对照

### D.2 测试 fixture 兼容性

**风险层级**：MEDIUM

`test_runtime_control_f091.py` 等含针对 "unspecified + metadata flag" 行为的断言。F100 移除 fallback 后这些断言需要更新（要么转为显式 delegation_mode，要么测试 NotImplementedError）。

### D.3 `RecallPlannerMode.AUTO` 启用语义偏离

**风险层级**：HIGH（设计风险，非实施风险）

用户 prompt 描述："按请求复杂度自适应（simple/partial/full）+ 多档"
F091 注释口径："依 delegation_mode 自动决议"

**偏离影响**：
- 用户 prompt 设计：需要新建复杂度信号源 + 阈值参数 + partial 中间档实现
- F091 设计：在 helper 内单 switch（main_delegate → full，main_inline/worker_inline → skip，subagent → ?）

**Mitigation**：spec 阶段必须 OD-1 用户拍板，二选一。

### D.4 `partial` 中间档实现成本

**风险层级**：MEDIUM

用户 prompt 提"partial = 只跑关键 namespace recall，不跑跨 scope"。实测：当前 RecallPlanMode 在 `behavior.py` 中是 `Literal["SKIP", "FULL", "PARTIAL", ...]`（多模式已就绪），但 `_build_memory_recall_plan` 出口是布尔（return None 或 RecallPlan）。

**结论**：partial 中间档需在 plan return path 上加新逻辑（plan.mode = PARTIAL）—— 实施复杂度高。

### D.5 ask_back resume 与 OD-1 启用"按复杂度"的交互

**风险层级**：LOW（若 OD-1 选 F091 设计意图则此风险消失）

若 OD-1 启用"按复杂度自适应"，worker_inline default 可能从 "skip" 变成 "auto" → ask_back 后 turn N+1 复杂度判断可能跑 recall planner。OD-3 = B（attach_input 显式 skip）成为必要。

---

## E. 建议的 Phase 顺序（根据实测调整）

用户 prompt 建议：A 实测 → D 双轨收尾 → B auto 启用 → E ask_back → C 移除 hack

实测后建议：

| Phase | 内容 | 工作量 | 风险 | 备注 |
|-------|------|--------|------|------|
| **Phase A** | 实测侦察 | 完成 | 低 | 本文档 |
| **Phase B (设计)** | 在 GATE_DESIGN 锁定 OD-1（用户 prompt 设计 vs F091 设计意图） | spec/clarify 阶段 | HIGH（决定后续工作量）| 用户必须拍板 |
| **Phase C** | 显式化所有 production 构造点的 `delegation_mode`（grep + 改 + 测试） | 0.5d | MED | 准备工作，零运行时行为变化 |
| **Phase D** | 启用 RecallPlannerMode.AUTO 实际语义（按 OD-1 锁定方向）| 1-2d | HIGH | 核心新行为 |
| **Phase E** | 移除 metadata fallback + 写入 | 0.5d | MED | 收口 |
| **Phase F** | ask_back resume 路径决策（按 OD-3 拍板）| 0.2d | LOW | 大概率 no-op |
| **Phase G** | 性能基准 + 全量回归 + e2e_smoke 5x | 0.5d | MED | 验证 |
| **Phase H** | Final Codex review + 闭环 | 0.3d | MED | 必走 |

**总工作量估计**：4-5 天（不含 GATE_DESIGN 等待）

**与用户 prompt 估计的差异**：实测后大幅压缩——主要因为 F091 已建立单一入口（is_recall_planner_skip），F100 核心改动是单 helper 内的 switch + 多点显式化。

---

## F. 关键决策点（Open Decisions）

### OD-1 [HIGH]：RecallPlannerMode.AUTO 实际语义

| 候选 | 说明 | 工作量 | 风险 |
|------|------|--------|------|
| **A**（F091 设计意图）| "依 delegation_mode 自动决议"——helper 内单 switch：`main_inline/worker_inline → skip`，`main_delegate/subagent → full`，`unspecified → raise` | 0.5d | LOW |
| **B**（用户 prompt 设计）| "按请求复杂度自适应"——新建复杂度信号源（prompt 长度/message_count/工具历史）+ 阈值参数 + simple/partial/full 三档 + RecallPlanMode.PARTIAL 实现 | 3-5d | HIGH（含新增 partial mode 实现 + 阈值调参 + 性能基准）|
| **C**（混合）| 默认按 delegation_mode 决议；额外暴露 override flag 让上层可强制 "full"（保 H1 完整决策环）；不实现 partial 中间档 | 0.8d | MED |

**推荐 = C**：满足 H1 "主 Agent 自跑也应走完整决策环" 的核心诉求（通过 override 让 complex query 走 full），同时避免引入复杂度评估 + partial 中间档的高成本。M5 不必把所有事都做完——partial 中间档可推到 F107 顺手清。

⚠️ **用户必须在 GATE_DESIGN 拍板**

### OD-2 [MED]：main_inline 默认 `recall_planner_mode`

| 候选 | 说明 | 影响 |
|------|------|------|
| A（保持 "skip"）| F090 现状，行为零变化 | F051 性能优势保留 |
| B（改为 "auto"）| 让 OD-1 决议 | 行为可能变（取决于 OD-1）|

**推荐 = A**（保 F051 性能优势；H1 完整决策环通过 OD-1-C 的 override flag 实现）

### OD-3 [LOW]：ask_back resume 显式 skip recall planner

| 候选 | 说明 | 工作量 |
|------|------|--------|
| A（不动）| baseline 已通：worker_inline 默认 skip | 0d |
| B（显式 patch）| attach_input 路径强制 recall_planner_mode="skip" | 0.2d |

**推荐 = A**（baseline 已通；OD-1=C 路径 worker_inline 仍默认 skip）

### OD-4 [MED]：F090 双轨收尾节奏

| 候选 | 说明 | 风险 |
|------|------|------|
| A（F100 一并收尾）| 移除 metadata 写入 + 移除 fallback | MED（需全量 grep + 测试 fixture 更新）|
| B（F100 仅移读 fallback，写入留 F107）| 保守 | LOW，但 F107 还要再做一次 |
| C（F100 不动 D，仅做 B/C/E）| F090 双轨收尾推迟到 F107 | LOW，F100 范围缩小 |

**推荐 = A**（F107 已有 D2 WorkerProfile 合并、D9 capability 重构，F090 D1 一并清零更干净）

### OD-5 [LOW]：partial 中间档实现位置

| 候选 | 说明 |
|------|------|
| A（F100 不实现 partial）| auto mode 只有 skip / full 两档 |
| B（F100 实现 partial）| RecallPlanMode.PARTIAL 出口 + namespace 子集逻辑 |

**推荐 = A**（YAGNI；推到 F107 顺手清，与 D9 capability refactor 合并）

---

## G. F099 7 项推迟项现状评估（F100 spec 阶段一并 audit）

| 项目 | F099 严重度 | F100 是否触及 | 现状 |
|------|-----------|---------------|------|
| F3 HIGH state machine（WAITING_APPROVAL）| HIGH | ❌ F101 范围 | 未实施 |
| F5 PARTIAL RUNNING guard 空串返回 | LOW | ❌ F101 范围 | 未实施 |
| ApprovalGate SSE production 接入 | MED | ❌ F101 范围 | sse_push_fn=None |
| AC-E1 e2e 完整三条事件序列 | MED | ❌ F101 范围 | 单测覆盖局部 |
| N-H1 PARTIAL is_caller_worker resume 其余路径 | MED | **F100 间接相关**（runtime_context resume 路径同源）| F099 2f867e6 已覆盖 attach_input 路径；manual resume / startup orphan / deferred dispatch 三路径依赖 WorkerRuntime hardcoded fallback | 
| M-1 broad-catch 吞异常 | LOW | ❌ F101 范围 | 未实施 |
| N-L1 source_kinds.py `__all__` | LOW style | ❌ 任意 commit | 未实施 |

**对 F100 的实施约束**：F100 改 runtime_context 透传机制时，必须确认 ask_back attach_input resume 路径上 runtime_context 仍正确恢复（F099 N-H1 路径 A 的 `is_caller_worker_signal` 透传机制不被破坏）。

---

## H. F091 实证关键约束清单（F100 必须遵守）

1. **保留** `supports_single_loop_executor` 类属性（mock fixture 依赖）
2. **保留** `_metadata_flag` helper（F091 抽到 runtime_control 作为单一来源）
3. **保留** `is_single_loop_main_active` / `is_recall_planner_skip` 两个 public helper（外部调用方依赖）
4. F091 注释 / 文档化的"F100 启用 auto 语义"必须实施（不能推迟）
5. `RecallPlannerMode` Literal 不增加新值（如果 OD-1=A/C，"auto" 内部分发；如果 OD-1=B，复杂度信号通过 RuntimeControlContext 额外字段携带）

---

## I. 性能基准计划（spec 阶段定）

F100 是 M5 阶段 2 首个**真改运行时行为**的 Feature。必须有性能基准。

**测量场景**：
1. **simple query**（短 prompt + 单工具调用）：F100 前 vs 后 LLM call 延迟
2. **complex query**（长 prompt + 多工具历史 + 跨 session context）：F100 前 vs 后 LLM call 延迟 + recall plan 命中率
3. **ask_back resume**：attach_input 到 LLM 调用之间的延迟

**通过门**：
- simple query 延迟回归 < 5%（F051 性能优势兼容）
- complex query（OD-1=C 触发 full）：延迟增加 ≤ recall plan 单次 LLM 调用时间（预期 1-3s）

**测试入口候选**：
- e2e_smoke 已有 simple query 路径
- 长 prompt 场景可能需 new e2e_full case（或 mock + 多 turn 上下文构造）

**spec 阶段不锁具体数字，由 GATE_DESIGN 后 plan 阶段定**。

---

## J. 实施风险总评

| 维度 | 评级 | 说明 |
|------|------|------|
| 实施复杂度 | LOW-MED | F091 已建立单一入口，单 helper 改动 |
| 行为变更面 | MED | 取决于 OD-1（A/C 低，B 高）|
| 测试覆盖更新 | MED | fixture 显式化 delegation_mode + 测试用例更新 |
| 性能回归风险 | LOW（OD-1=C）/ HIGH（OD-1=B partial 实现）| simple query 路径不动 |
| F099 ask_back 路径兼容性 | LOW | 仅 runtime_context 透传机制相关 |
| Final Codex review 闭环成本 | MED | 大改动 commit 后必走 re-review（F099 实证）|

**总评**：F100 是 M5 阶段 2 收尾的"小而精"Feature。GATE_DESIGN 锁定 OD-1=A/B/C 是核心拍板点，决定后续 60% 工作量。
