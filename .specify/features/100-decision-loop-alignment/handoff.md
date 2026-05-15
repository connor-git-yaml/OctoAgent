# F100 Handoff → F101 Notification + Attention Model

**来源 Feature**: F100 Decision Loop Alignment（H1 决策环对齐 + F090 双轨收尾）
**目标 Feature**: F101 Notification + Attention Model（M5 阶段 3 起点；范围扩大承接 F099 7 项推迟 + F100 minimal trigger producer）
**参考 baseline**: F100 完成后（6 commits + Final review fixes）

---

## §1. F100 落地状态摘要

### 核心改动

1. **`RuntimeControlContext.force_full_recall: bool = False`** 字段引入（H1 override flag）
   - 文件：`packages/core/src/octoagent/core/models/orchestrator.py`
   - 语义：True → 强制走完整 recall planner phase；False（默认）→ 按 recall_planner_mode 正常决议

2. **`RecallPlannerMode "auto"` 实际语义启用**
   - 文件：`apps/gateway/src/octoagent/gateway/services/runtime_control.py`
   - 决议：依 delegation_mode 自动决议
     - main_inline / worker_inline → skip (True)
     - main_delegate / subagent → full (False)
   - 优先级（高 → 低）：
     1. force_full_recall=True → 始终 False（H1 override，优先级最高）
     2. delegation_mode 显式 + recall_planner_mode (skip / full)
     3. delegation_mode 显式 + recall_planner_mode "auto" → switch
     4. delegation_mode = unspecified 或 runtime_context = None → return False（v0.3 修订）

3. **FR-H minimal trigger 接入**（HIGH-1 修复）
   - 文件：`apps/gateway/src/octoagent/gateway/services/orchestrator.py:_with_delegation_mode`
   - 新增 `force_full_recall: bool | None = None` 参数
   - 优先级：显式 kwarg > `metadata["force_full_recall"]` hint > base.force_full_recall > False
   - 上层（chat 路由 / API 参数 / 调试工具）通过 `dispatch_metadata["force_full_recall"] = True` 触发 H1

4. **F090 D1 双轨彻底收尾**
   - 移除 orchestrator metadata 写入：`single_loop_executor` / `single_loop_executor_mode`
   - 移除 helper fallback：is_recall_planner_skip + is_single_loop_main_active
   - unspecified → return False（与 baseline metadata 缺失时等价；不破坏 chat 主链）

5. **HIGH-1 修复：patched runtime_context 同步 metadata**
   - orchestrator._prepare_single_loop_request 在 patched runtime_context 后同步覆盖 `metadata[RUNTIME_CONTEXT_JSON_KEY]`
   - 防止 LLMService 通过 runtime_context_from_metadata(metadata) 读到 chat.py 写入的 stale unspecified seed

### 不变量保留

- ✅ `LLMService.supports_single_loop_executor = True` 类属性保留（F091 实证 mock fixture 依赖）
- ✅ F099 ask_back 三工具（ask_back / request_input / escalate_permission）行为不变
- ✅ F099 source_runtime_kind 5 值枚举不动
- ✅ F099 is_caller_worker_signal resume 持久化机制不破

### 测试覆盖

- 69+ 新增/迁移 tests
- 全量回归 1469 passed in 53s（vs F099 baseline 0 regression）
- mock-based perf 基准：simple query 0 增延（0.05μs）

---

## §2. F101 决策环改造后 Notification 触发点

F100 引入 H1 完整决策环 override；F101 Notification 可考虑以下信号源/触发点：

### 2.1 force_full_recall 触发事件

当 orchestrator 检测到 `metadata["force_full_recall"]=True` 或 `runtime_context.force_full_recall=True`，可触发：
- Notification 类型："主 Agent 启用了完整 recall planner phase（H1 决策环）"
- Attention Model 输入：force_full_recall 状态 = 用户对"复杂请求"的隐式标识

### 2.2 RecallPlannerMode 决议事件

AUTO 决议路径在 helper 内决定 skip/full，F101 可考虑：
- 决议事件：AUTO + delegation_mode → skip/full 决议结果
- Notification：仅在"非预期"决议结果时触发（如 main_inline 路径意外走 full）

### 2.3 chat.py seed → orchestrator patch 链路

F100 已建立 chat.py（pre-decision unspecified seed）→ orchestrator._prepare_single_loop_request（patch 到 main_inline）→ runtime_context_json 同步 metadata 的链路。F101 实施 Notification 时可：
- 利用此链路追加 force_full_recall hint（chat 路由层判断 complex query → 写 metadata）
- 不需要新建 schema / 通道

---

## §3. Attention Model 信号源

F100 提供的信号源：

| 信号 | 来源 | F101 用途 |
|------|------|----------|
| `runtime_context.force_full_recall` | RuntimeControlContext per-request | 标记"复杂请求"——Attention Model 输入 |
| `runtime_context.delegation_mode` | RuntimeControlContext per-request | 区分主 Agent 自跑 vs 委托——Attention Model 区分 |
| `dispatch_metadata["force_full_recall"]` | chat 路由 / API hint | 上层显式信号 |
| F099 source_runtime_kind | RuntimeControlContext per-request | caller 身份枚举（5 值） |

**F101 Attention Model 设计建议**：force_full_recall 不是"通知用户"的信号——它是"系统决策走完整 recall"的内部信号。Notification 触发应基于其他维度（如 high-cost LLM 调用 / approval pending / 工具失败重试等），不要把 force_full_recall 状态暴露给用户。

---

## §4. F099 7 项推迟项当前状态评估

F099 handoff §"推迟项"7 项的 F100 后状态：

| 项目 | 严重度 | F099 状态 | F100 后是否触及 | F101 接收建议 |
|------|--------|----------|----------------|---------------|
| F3 HIGH escalate_permission WAITING_APPROVAL 状态机 | HIGH | DEFERRED | ❌ 不触及 | F101 主要任务 |
| F5 PARTIAL RUNNING guard 空串返回 | LOW | PARTIAL | ❌ 不触及 | F101 顺手清 |
| ApprovalGate SSE production 接入 | MED | sse_push_fn=None | ❌ 不触及 | F101 主要任务 |
| AC-E1 e2e 完整三条事件序列 | MED | 单测覆盖局部 | ❌ 不触及 | F101 验证扩展 |
| N-H1 PARTIAL is_caller_worker resume 其余路径 | MED | F099 attach_input 路径已修 | ❌ 不触及 | F101 / F107 |
| M-1 broad-catch 吞异常 | LOW | DEFERRED | ❌ 不触及 | F101 顺手清 |
| N-L1 source_kinds.py `__all__` | LOW style | DEFERRED | ❌ 不触及 | 任意 commit |

**F100 间接相关项**：N-H1 PARTIAL（runtime_context resume 路径同源）—— F100 Phase F 实测发现 ask_back resume 后 runtime_context 信息丢失（TASK_SCOPED_CONTROL_KEYS 不含 runtime_context_json）。F101 可考虑把 runtime_context_json 加入 allowlist（见 §5）。

---

## §5. RecallPlannerMode 演进路径（F107 partial 中间档）

F100 锁定 `force_full_recall: bool`。LOW-1 finding 提示：F107 partial 中间档实现可能需要：

**选项 A：破坏式升级**
- 重命名为 `recall_override_mode: Literal["off", "full", "partial"]`
- 移除 force_full_recall: bool

**选项 B：叠加第二字段**
- 保留 force_full_recall: bool（向后兼容）
- 新增 partial_recall_namespace: list[str]（指定 namespace 子集）

**F100 不预设方向**——F107 实施时按当时需求决定。本 handoff 记录此演进点作为 follow-up。

---

## §6. HIGH-1 minimal trigger 演进（F101 producer 实现）

F100 仅提供 minimal trigger 接口（FR-H）。**真正的 producer 实现需要 F101 / 独立 Feature**：

### 6.1 candidate producers

| Producer 候选 | 触发逻辑示例 | F101 范围？ |
|--------------|------------|------------|
| chat 路由层 | 长 prompt + 跨 session context 检测 → 设 hint | F101 推荐 |
| API 参数 | 用户/admin 通过 API 显式传 `force_full_recall=true` | F101 / 独立 Feature |
| 调试工具 | gateway 调试端点 / e2e 测试 fixture 设 hint | 已就绪（测试覆盖） |
| Notification 反向触发 | 用户响应 attention notification 时设 hint | F101 主路径 |
| Attention Model 输出 | Model 判断"需要完整决策环" | F101 主路径 |

### 6.2 集成点

`octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py`：
- 写入位置：`dispatch_metadata["force_full_recall"] = True`
- 自动经过 orchestrator._prepare_single_loop_request → _with_delegation_mode → patched runtime_context
- 自动经过 HIGH-1 修复路径 → metadata[RUNTIME_CONTEXT_JSON_KEY] 同步

### 6.3 验证

F100 已建立的链路 + 测试 fixture（test_runtime_control_f100.py TestForceFullRecallHintInjection）可被 F101 producer 实现直接复用。

---

## §7. 其他 F101 deferred 项

### 7.1 AC-5 ask_back resume baseline 行为承接（Final review HIGH-2 闭环）

F100 Phase F 实测确认 ask_back resume 后 runtime_context 信息丢失，turn N+1 跑 recall planner。spec.md AC-5 / FR-E 已修订反映此实际行为。

**F101 / 独立 Feature 可选择**：
- 选项 A：把 `runtime_context_json` 加入 TASK_SCOPED_CONTROL_KEYS（连接 metadata trust boundary，invasive 改动）
- 选项 B：resume 路径显式 patch runtime_context（task_runner.attach_input → resume_state_snapshot 携带）
- 选项 C：保持 baseline 行为（resume 后跑 recall planner；某些场景反而是好的，因为 context 已更新）

**推荐**：F101 实施 Notification 时再判断；不预设方向。

### 7.2 RuntimeControlContext 序列化覆盖率（F091 baseline 已通）

F100 Phase D 测试覆盖 force_full_recall 字段 encode/decode round-trip。但全字段 round-trip 覆盖率（如 surface / scope_id 等）依赖 F091 baseline 测试。F101 不需要额外补强。

---

## §8. 关键文件指针

### F100 核心改动文件

| 文件 | 改动 |
|------|------|
| `packages/core/src/octoagent/core/models/orchestrator.py` | RuntimeControlContext.force_full_recall 字段 |
| `apps/gateway/src/octoagent/gateway/services/runtime_control.py` | is_recall_planner_skip AUTO 启用 + override + 移除 fallback |
| `apps/gateway/src/octoagent/gateway/services/orchestrator.py` | metadata 写入移除 + FR-H 接入 + HIGH-1 修复 + MED-2 修复 |

### F100 制品文件

| 文件 | 用途 |
|------|------|
| `.specify/features/100-decision-loop-alignment/spec.md` v0.3 | 完整 spec |
| `.specify/features/100-decision-loop-alignment/plan.md` v0.3 | 完整 plan |
| `.specify/features/100-decision-loop-alignment/phase-0-recon.md` | 实测侦察 |
| `.specify/features/100-decision-loop-alignment/phase-c-audit.md` | consumed audit + v0.3 修订理由 |
| `.specify/features/100-decision-loop-alignment/phase-f-resume-trace.md` | ask_back resume 实测 |
| `.specify/features/100-decision-loop-alignment/phase-g-perf-report.md` | perf 基准数据 |
| `.specify/features/100-decision-loop-alignment/codex-review-pre-impl.md` | pre-impl review |
| `.specify/features/100-decision-loop-alignment/codex-review-final.md` | Final review |
| `.specify/features/100-decision-loop-alignment/completion-report.md` | 完成报告 |
| `.specify/features/100-decision-loop-alignment/handoff.md` | 本 handoff |

### F100 commit 链

| Commit | Phase | 内容 |
|--------|-------|------|
| 3c0d0c4 | C | spec/plan v0.3 + recon + review + audit |
| 7c3c241 | F | ask_back resume 实测 + 单测 |
| 162a8d0 | D | force_full_recall + AUTO + FR-H |
| 665f7cf | E1 | 移除 metadata 写入 |
| 5d617c5 | E2 | 移除 fallback + fixture 迁移 |
| c5b157e | G | perf 基准 + 报告 |
| (本 commit) | H | Final review fixes + completion-report + handoff |

---

**Status**: F100 → F101 handoff 完整。F101 可基于本文档启动。

**M5 阶段 2 全部关闭**（F097/F098/F099/F100 ✅）；**M5 阶段 3 F101 起点就绪**。
