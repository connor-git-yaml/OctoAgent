# F101 Notification + Attention Model — Tasks

**Spec**: [spec.md](spec.md)
**Plan**: [plan.md](plan.md)
**Baseline**: `182e9ed`（F100 Phase H 完成，origin/master）
**Baseline passed count**: 3450（F099 实测全量基线）
**Tasks date**: 2026-05-16

---

## 0. 总览

### 0.1 Task 分布

| Phase | Task 数 | 主要 FR/AC | 关键约束 |
|-------|---------|-----------|---------|
| Phase 0（侦察 + spec 修订）| 9 | 风险 R1/R3 实测 + 4 WARN 修复 | 入口，无代码改动 |
| Phase A（force_full_recall Producer）| 7 | FR-D1/D2/D3；AC-D1/D2/D3 | US4，独立可先行 |
| Phase B（ApprovalGate SSE + 状态机 + 超时，联合）| 12 | FR-C1/C2/C3/C6；AC-C1/C2/C3/C6 | US1，联合不可拆分 |
| Phase C（Notification 主体 + quiet hours + dismiss）| 12 | FR-B1~B7；AC-B1~B6 | US2/US3，依赖 Phase B |
| Phase D（ask_back integration test + 顺手清）| 7 | FR-C4/C5/C7；US5；AC-C4/C5/C7 | US5，依赖 Phase B |
| Phase E（D8 顺手清，条件实施）| 4 | FR-E1；AC-E1（条件）| 条件实施 |
| Phase F（AC-F1 验证 + Final 准备）| 6 | AC-F1；全量回归；e2e_smoke 5x | 所有 Phase 后 |
| Phase Final（Codex Final review + 文档）| 5 | Codex Final + completion-report + handoff | 最后 |
| **合计** | **62** | — | — |

### 0.2 命名约定

- Task ID 格式：`T-{Phase}-{序号两位}` 如 `T-0-01`、`T-A-01`、`T-B-01`
- Phase 字母：0 / A / B / C / D / E / F / Final
- 状态：`pending` / `in_progress` / `blocked` / `completed`

### 0.3 关键约束

- **Phase B 联合 Phase**：T-B-03 到 T-B-10 的 FR-C1 + FR-C2 + FR-C3 + FR-C6 实现不可拆分独立验收，必须同时通过 AC-C1 + AC-C2 + AC-C3 + AC-C6 四个测试才允许 commit
- **Phase 0 必须先完成**：R1（SSEHub 广播能力）和 R3（notification_service 注入）的实测结论是 Phase B/C 实施的前置输入
- **Phase C 依赖 Phase B**：FR-B1（WAITING_APPROVAL 通知）需要 Phase B 状态机正确后才能接入
- **Phase D 依赖 Phase B**：FR-C4 integration test 需要真实 approval_gate 状态机工作
- **Phase E 条件实施**：若 Phase C 已通过 task_runner 路径覆盖所有通知场景，Phase E 可降级为不实施

### 0.4 Codex review 节点汇总

| Review 类型 | 触发 Task | 模式 |
|-------------|---------|------|
| **pre-impl review** | **plan/tasks commit 后立即（GATE_TASKS 通过后，进入 Phase 0 实施前）** | **foreground** |
| per-Phase A | T-A-07（Phase A commit 后）| foreground |
| per-Phase B | T-B-12（Phase B commit 后）| foreground |
| per-Phase C | T-C-12（Phase C commit 后）| foreground |
| per-Phase D | T-D-07（Phase D commit 后）| foreground |
| per-Phase E | T-E-04（若实施，Phase E commit 后）| foreground |
| Final cross-Phase | T-Final-01（Phase F commit 后）| foreground |
| re-review（预备）| T-Final-02（Final review 抓 HIGH 修复后）| foreground |

---

## Phase 0 — 侦察 + spec 修订

**目标**：在写一行 production 代码之前，实测所有依赖的风险点（R1/R3）和修复 4 个 WARN，产出 phase-0-recon.md，修订 spec.md。

**不涉及 User Story**：此 Phase 为纯侦察 + 文档，无 production 代码改动。

---

### T-0-01: 读取 sse_hub.py 完整实现，确认 broadcast per-session_id 能力（R1）

- **Phase**: 0
- **对应 FR/AC**: 风险 R1（spec §9）
- **依赖 task**: 无
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/harness/sse_hub.py`（完整读取）
- **验证方式**: 确认方法签名列表：是否存在 `broadcast_to_session(session_id, payload)` 或等价接口
- **完成判据**: 得出结论 `SSEHub_BROADCAST_CAPABILITY = PER_SESSION | TASK_ONLY | NEEDS_NEW_METHOD`，写入 phase-0-recon.md §R1
- **预估改动**: 0 行（只读）
- **风险**: MED（R1，tech-research 行 316-320）
- **Codex review 节点**: 否

---

### T-0-02: 读取 task_runner.py __init__ 和 _notify_completion，确认 notification_service 注入状态（R3）

- **Phase**: 0
- **对应 FR/AC**: 风险 R3（spec §9）
- **依赖 task**: 无
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`（读 `__init__` 和 `_notify_completion` 方法）
- **验证方式**: 确认 `notification_service` 是否已作为构造参数注入
- **完成判据**: 结论 `NOTIFICATION_SERVICE_INJECTED = YES | NO` 写入 phase-0-recon.md §R3
- **预估改动**: 0 行（只读）
- **风险**: MED（R3）
- **Codex review 节点**: 否

---

### T-0-03: 读取 notification.py 92-229，确认 dismiss 持久化状态（WARN-4）

- **Phase**: 0
- **对应 FR/AC**: FR-B5；WARN-4
- **依赖 task**: 无
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/notification.py:92-229`
- **验证方式**: 确认 `_sent_notifications` set 是否存在；跨通道 dismiss 是否共享 set
- **完成判据**: FR-B5 幂等实现方案（内存 set 共享 or 持久化 or 无需新建）写入 phase-0-recon.md §dismiss
- **预估改动**: 0 行（只读）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-0-04: 读取 worker_runtime.py 和 agent.py:55，确认 attention_work_count 更新路径（WARN-3）

- **Phase**: 0
- **对应 FR/AC**: FR-B7；WARN-3
- **依赖 task**: 无
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`（dispatch 开始和终态路径）；`octoagent/packages/core/src/octoagent/core/models/control_plane/agent.py:55`（WorkerProfileDynamicContext）
- **验证方式**: 确认 attention_work_count +1/-1 是否已有更新调用
- **完成判据**: FR-B7 更新路径是否已存在或需新建，写入 phase-0-recon.md §attention_work_count
- **预估改动**: 0 行（只读）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-0-05: 读取 approval_gate.py __init__ 和 wait_for_decision，确认超时配置（R2 前置）

- **Phase**: 0
- **对应 FR/AC**: 风险 R2；FR-C3；AC-C3
- **依赖 task**: 无
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/harness/approval_gate.py:88-105`（`__init__`）+ `wait_for_decision` 完整方法
- **验证方式**: 确认超时参数名称 + 默认值 + 超时后返回值（"rejected" or raise）
- **完成判据**: FR-C3 超时修复的实现入口确认，写入 phase-0-recon.md §ApprovalGate_timeout
- **预估改动**: 0 行（只读）
- **风险**: HIGH（R2）
- **Codex review 节点**: 否

---

### T-0-06: 读取 task_runner.py:770-800，分析超时监控 WAITING_APPROVAL continue 结构（R2 前置）

- **Phase**: 0
- **对应 FR/AC**: 风险 R2；FR-C3；spec ref-2（tech-research 行 15）
- **依赖 task**: T-0-05（需先了解 ApprovalGate 超时签名）
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:770-800`（超时监控循环全文）
- **验证方式**: 确认 `continue` 跳过的条件和相邻代码结构；设计 FR-C3 最小侵入方案
- **完成判据**: 最小侵入方案（continue 改为超时转 FAILED or ApprovalGate callback）写入 phase-0-recon.md §task_runner_timeout
- **预估改动**: 0 行（只读）
- **风险**: HIGH（R2）
- **Codex review 节点**: 否

---

### T-0-07: 修订 spec.md — 4 WARN 修复（AC-C4 Given + AC-F1 Then + §12 注 + FR-B7 路径 + §7 dismiss）

- **Phase**: 0
- **对应 FR/AC**: WARN-1/2/3/4 修复
- **依赖 task**: T-0-03（dismiss 存储结论）；T-0-04（attention_work_count 路径结论）
- **改动文件**: `.specify/features/101-notification-attention/spec.md`
  - 改写 AC-C4 Given 段（WARN-1）
  - AC-F1 Then 补 is_recall_planner_skip spy 验证（WARN-1）
  - §12 引用索引表头部加注"行号列指 tech-research.md 文档行，括号内为源码文件行"（WARN-2）
  - FR-B7 attention_work_count 更新路径补充（WARN-3）
  - §7 依赖表末尾补 dismiss 存储机制（WARN-4）
- **验证方式**: grep spec.md 中 AC-C4 / AC-F1 / §12 / FR-B7 / §7 对应段落是否已修订
- **完成判据**: 4 WARN 全部在 spec.md 中有对应修订文本
- **预估改动**: 改 30-50 行（spec.md 修订）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-0-08: 产出 phase-0-recon.md（6 项结论 + 侦察报告）

- **Phase**: 0
- **对应 FR/AC**: Phase 0 出口条件
- **依赖 task**: T-0-01 ~ T-0-06 全部完成
- **改动文件**: `.specify/features/101-notification-attention/phase-0-recon.md`（新建）
  - §R1 SSEHub_BROADCAST_CAPABILITY 结论
  - §R3 NOTIFICATION_SERVICE_INJECTED 结论
  - §dismiss dismiss 存储机制
  - §attention_work_count 更新路径
  - §ApprovalGate_timeout 超时配置
  - §task_runner_timeout 最小侵入方案
- **验证方式**: 6 项结论均明确（无"待确认"或"不清楚"）
- **完成判据**: phase-0-recon.md 产出，6 项结论全部确定，不允许 Phase A/B/C 实施时再现"未知"
- **预估改动**: 新建 ~60-80 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-0-09: Phase 0 回归验证 + commit

- **Phase**: 0
- **对应 FR/AC**: Phase 0 出口条件
- **依赖 task**: T-0-07 + T-0-08
- **改动文件**: spec.md（WARN 修复）+ phase-0-recon.md（新建）
- **验证方式**: `pytest -m e2e_smoke` baseline 稳定（无代码改动，只需确认环境 OK）
- **完成判据**: e2e_smoke PASS；commit `chore(F101-Phase-0): 侦察报告 + spec WARN 修复`
- **预估改动**: 只有文档文件
- **风险**: LOW
- **Codex review 节点**: 否

---

## Phase A — force_full_recall Producer 实现（US4）

**目标**：在 chat.py 两处 dispatch_metadata 构造点注入 force_full_recall producer，让长 prompt 场景真正触发完整决策环（FR-D1/D2/D3）。

**User Story**: US4（长 prompt 自动触发完整决策环，Priority: P2）

**独立测试**：发送超过 THRESHOLD 字符的消息，验证 dispatch_metadata 中 `force_full_recall=True`，recall planner 被 orchestrator 激活（不 skip）。

**依赖**: Phase 0 完成（但不依赖 R1/R3 结论，Phase A 可与 Phase 0 文档 commit 后同步启动）

---

### T-A-01: 读取 chat.py 完整，确认两处 dispatch_metadata 构造上下文

- **Phase**: A
- **对应 FR/AC**: FR-D1/D2
- **依赖 task**: T-0-09（Phase 0 commit）
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:422-493`
- **验证方式**: 确认新对话路径（行 422-444）和续对话路径（行 479-493）各自 dispatch_metadata 构造的确切位置和 encode_runtime_context 调用时机
- **完成判据**: 两处注入点位置确认（具体行号），记录到 task 注释或 phase-0-recon.md §chat_inject
- **预估改动**: 0 行（只读）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-A-02: 定义 LONG_PROMPT_THRESHOLD 常量（FR-D3）

- **Phase**: A
- **对应 FR/AC**: FR-D3；AC-D2
- **依赖 task**: T-A-01
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py`（模块顶部新增常量）
  - `LONG_PROMPT_THRESHOLD: int = 2000`（Unicode 字符数）
  - 常量放在模块顶部 import 之后、路由函数之前
- **验证方式**: `grep -n 'LONG_PROMPT_THRESHOLD' chat.py` 确认常量定义；确认为 int 类型注解
- **完成判据**: 常量存在且值为 2000；无 hardcode 魔法数字
- **预估改动**: 新增 2-3 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-A-03: 新对话路径注入 force_full_recall producer（FR-D1）

- **Phase**: A
- **对应 FR/AC**: FR-D1；AC-D1
- **依赖 task**: T-A-02
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:422-444`
  - 在 `dispatch_metadata` dict 构造之后、`encode_runtime_context(RuntimeControlContext(...))` 调用之前
  - 插入：`if len(body.message) > LONG_PROMPT_THRESHOLD: dispatch_metadata["force_full_recall"] = True`
- **验证方式**: 代码审查确认注入位置正确（在 encode_runtime_context 之前，不在之后）；确认判断使用 `len(body.message)`（Unicode 字符数）
- **完成判据**: 新对话路径注入完整；短消息不写入（无 else 分支清除）
- **预估改动**: 增 3-4 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-A-04: 续对话路径注入 force_full_recall producer（FR-D2）

- **Phase**: A
- **对应 FR/AC**: FR-D2；AC-D3
- **依赖 task**: T-A-03（同模式，先确认新对话路径可行）
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:479-493`
  - 相同逻辑（FR-D2），与新对话路径完全一致
  - 在 dispatch_metadata 构造后、encode_runtime_context 调用前注入
- **验证方式**: 代码审查确认两路径逻辑完全对称（逻辑无差异，只是代码位置不同）
- **完成判据**: 续对话路径注入完整；与新对话路径行为一致
- **预估改动**: 增 3-4 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-A-05: 新建 test_f101_force_full_recall.py + 单测（AC-D1/D2/D3）

- **Phase**: A
- **对应 FR/AC**: AC-D1；AC-D2；AC-D3
- **依赖 task**: T-A-04（实现完成后写测试）
- **改动文件**: `octoagent/tests/test_f101_force_full_recall.py`（新建）
  - 测点 1（AC-D1）：new_chat 路径，`len(message) > 2000` → `dispatch_metadata["force_full_recall"] == True`
  - 测点 2（AC-D2）：new_chat 路径，`len(message) <= 2000` → `dispatch_metadata` 不含 force_full_recall
  - 测点 3（AC-D3）：continue_chat 路径，`len(message) > 2000` → `dispatch_metadata["force_full_recall"] == True`
  - 测点 4（AC-D3 配对）：continue_chat 路径，`len(message) <= 2000` → 不含 force_full_recall
  - 测点 5：mock orchestrator，验证 `runtime_context.force_full_recall = True`（不跑 LLM）
  - 测点 6：验证 `is_recall_planner_skip` 返回 False（spy `runtime_control.py:106-124`）
- **验证方式**: `pytest tests/test_f101_force_full_recall.py -v`，全 PASS
- **完成判据**: 6 个测点全 PASS；全量回归 ≥ 3450 passed，0 regression
- **预估改动**: 新增 ~80-100 行
- **风险**: LOW
- **Codex review 节点**: 否（per-Phase A commit 后触发）

---

### T-A-06: 全量回归 + e2e_smoke 验证

- **Phase**: A
- **对应 FR/AC**: Phase A 出口回归门
- **依赖 task**: T-A-05
- **改动文件**: 无（只运行测试）
- **验证方式**: `pytest octoagent` ≥ 3450 passed，0 regression；`pytest -m e2e_smoke` PASS
- **完成判据**: 两个命令均通过
- **预估改动**: 0 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-A-07: Phase A commit + 触发 Codex per-Phase A review

- **Phase**: A
- **对应 FR/AC**: AC-D1/D2/D3 全部验收
- **依赖 task**: T-A-06
- **改动文件**: `chat.py`（常量 + 两路径注入）+ `test_f101_force_full_recall.py`（新建）
- **验证方式**: commit message 含 `feat(F101-Phase-A): force_full_recall producer 两路径注入`；Codex foreground review 通过
- **完成判据**: commit 完成；per-Phase A Codex review 0 HIGH 残留
- **Codex review 节点**: **是（per-Phase A，foreground）**
  - 重点：两路径注入是否对称；LONG_PROMPT_THRESHOLD 常量位置；len() 是 Unicode 字符数；baseline 短 prompt 行为不受影响

---

## Phase B — ApprovalGate SSE 接入 + escalate_permission 状态机 + 超时修复（联合 Phase）

**目标**：联合实施 FR-C1 + FR-C2 + FR-C3 + FR-C6，修复 production 中 ApprovalGate 永远为 None 的根本缺陷。

**User Story**: US1（审批请求真正到达用户，Priority: P1）

**独立测试**：触发一个 Worker 工具调用 escalate_permission，验证：task 进入 WAITING_APPROVAL 状态；Web SSE 推送审批事件；超时后走 FAILED 终态；重启后 is_caller_worker_signal 正确恢复。

**依赖**: Phase 0 完成（R1/R3 结论是前提输入）

**联合约束**：T-B-03 到 T-B-10 必须在同一 Phase 实施，不允许 FR-C1/C2/C3/C6 各自独立验收。联合验收门：AC-C1 + AC-C2 + AC-C3 + AC-C6 同时通过才允许 commit（T-B-12）。

---

### T-B-01: 读取 approval_gate.py 完整实现（request_approval + wait_for_decision）

- **Phase**: B
- **对应 FR/AC**: FR-C1/C2/C3
- **依赖 task**: T-0-09（Phase 0 完成，R1/R3 结论已明确）
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/harness/approval_gate.py`（完整读取）
- **验证方式**: 确认 `request_approval` 完整参数（含 session_id 参数位置）；`wait_for_decision` 超时参数名和默认值
- **完成判据**: 接口签名明确，对照 Phase 0 R2 结论验证；确认 sse_push_fn 回调类型
- **预估改动**: 0 行（只读）
- **风险**: HIGH（R2）
- **Codex review 节点**: 否

---

### T-B-02: 依 Phase 0 R1 结论处理 SSEHub 方法（FR-C2 前置）

- **Phase**: B
- **对应 FR/AC**: FR-C2；AC-C2
- **依赖 task**: T-B-01 + T-0-01（R1 结论必须已明确）
- **改动文件（情形 A：已有 broadcast_to_session）**: 无（直接进 T-B-03）
- **改动文件（情形 B：只有 broadcast(task_id)）**: `octoagent/apps/gateway/src/octoagent/gateway/harness/sse_hub.py`
  - 新增 `async def broadcast_to_session(self, session_id: str, payload: dict) -> None:` 方法
  - 内部通过 session_id→task_id 映射调用现有 broadcast
- **验证方式**: 若实施情形 B：单元测试 mock SSEHub.broadcast，验证 broadcast_to_session 传递正确 session_id
- **完成判据**: SSEHub 具备 per-session broadcast 能力；情形 A 则此 task 为 pass-through
- **预估改动**: 情形 A: 0 行；情形 B: 增 15-25 行
- **风险**: MED（R1）
- **Codex review 节点**: 否

---

### T-B-03: 实现 sse_push_fn 闭包（FR-C2 — 联合 Phase 第 1 步）

- **Phase**: B
- **对应 FR/AC**: FR-C2；AC-C2
- **依赖 task**: T-B-02
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py:700-703`（`_bootstrap_capability_pack`）
  - 在 ApprovalGate 构造点之前定义 sse_push_fn 闭包：
    ```python
    _sse_hub = app.state.sse_hub
    async def _sse_push(session_id: str, payload: dict) -> None:
        if _sse_hub:
            await _sse_hub.broadcast_to_session(session_id, {"type": "approval_sse", **payload})
    ```
  - 注意：`_bootstrap_runtime_services`（行 420-421）已先于 `_bootstrap_capability_pack` 执行，`app.state.sse_hub` 此时已可用
- **验证方式**: 代码审查确认闭包捕获 `_sse_hub` 而非 `app.state`（避免延迟访问问题）；函数签名 `(session_id: str, payload: dict) -> None` 与 ApprovalGate 期望的类型一致
- **完成判据**: 闭包定义正确；bootstrap 顺序约束满足
- **预估改动**: 增 6-8 行
- **风险**: MED
- **Codex review 节点**: 否（联合 Phase，统一在 T-B-12 review）

---

### T-B-04: ApprovalGate 构造注入 sse_push_fn（FR-C2 — 联合 Phase 第 2 步）

- **Phase**: B
- **对应 FR/AC**: FR-C2；AC-C2
- **依赖 task**: T-B-03
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py:700-703`
  - 修改 ApprovalGate 构造调用，将 `sse_push_fn=None` 改为 `sse_push_fn=_sse_push`
- **验证方式**: `grep -A10 'ApprovalGate(' octo_harness.py` 确认 sse_push_fn 不为 None；若可单独测试：mock ApprovalGate 构造，验证 sse_push_fn 参数非 None
- **完成判据**: production bootstrap 后 ApprovalGate.sse_push_fn 不为 None
- **预估改动**: 改 1-2 行
- **风险**: LOW
- **Codex review 节点**: 否（联合 Phase）

---

### T-B-05: 读取 ask_back_tools.py:362-444，分析 escalate_permission_handler 分支结构

- **Phase**: B
- **对应 FR/AC**: FR-C1；AC-C1
- **依赖 task**: T-B-04
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:362-444`
- **验证方式**: 确认 `approval_gate is None` 降级路径与主路径分支结构；确认 `request_approval` 和 `wait_for_decision` 的调用位置
- **完成判据**: 分支结构清晰，知道在哪里修改才能让 production 路径进入 WAITING_APPROVAL
- **预估改动**: 0 行（只读）
- **风险**: MED
- **Codex review 节点**: 否

---

### T-B-06: 修改 escalate_permission_handler，production 路径进入 WAITING_APPROVAL（FR-C1 — 联合 Phase 第 3 步）

- **Phase**: B
- **对应 FR/AC**: FR-C1；AC-C1
- **依赖 task**: T-B-05
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:362-444`
  - 在 `approval_gate is not None` 的主路径分支中：
    1. 调用 `approval_gate.request_approval(...)` → 触发 SSE push
    2. 调用 `approval_gate.wait_for_decision(timeout=300)` → task 进入 WAITING_APPROVAL
  - 保留 `approval_gate is None` 的降级路径不变（Constitution C6）
- **验证方式**: 代码审查确认主路径分支逻辑；降级路径保持 return "rejected"
- **完成判据**: production 路径（approval_gate 非 None）不再静默降级
- **预估改动**: 改/增 15-25 行
- **风险**: HIGH（R5，联合约束）
- **Codex review 节点**: 否（联合 Phase）

---

### T-B-07: 修复 task_runner.py:404-406 WAITING_APPROVAL 分支（FR-C1 联动）

- **Phase**: B
- **对应 FR/AC**: FR-C1；FR-C3；AC-C1/C3
- **依赖 task**: T-B-06
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:404-406`
  - WAITING_APPROVAL 分支从直接 `return` 改为适当处理（不再无视超时）
  - 具体方案依据 Phase 0 T-0-06 分析结论（最小侵入）
- **验证方式**: 代码审查确认 WAITING_APPROVAL 分支不再直接 return 跳过通知
- **完成判据**: WAITING_APPROVAL 分支处理正确（为 Phase C FR-B1 接入准备好调用点）
- **预估改动**: 改 5-10 行
- **风险**: HIGH（R2）
- **Codex review 节点**: 否（联合 Phase）

---

### T-B-08: 修复 task_runner.py:779 超时监控（FR-C3 — 联合 Phase 第 4 步）

- **Phase**: B
- **对应 FR/AC**: FR-C3；AC-C3
- **依赖 task**: T-B-07（WAITING_APPROVAL 分支已修复，超时修复才有意义）
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:779`（超时监控 continue 跳过）
  - 修复策略（依据 Phase 0 T-0-06 结论，最小侵入）：
    - 超时监控检测 WAITING_APPROVAL 状态下是否已超时
    - 超时后推进任务走 FAILED 终态（与 wait_for_decision 的 300s timeout 配合）
    - `continue` 改为超时感知的处理分支
- **验证方式**: 代码审查确认超时路径不再 continue 跳过；超时后 task 可走 FAILED
- **完成判据**: 超时监控能感知 WAITING_APPROVAL 超时并推进终态
- **预估改动**: 改 10-20 行
- **风险**: HIGH（R2）
- **Codex review 节点**: 否（联合 Phase）

---

### T-B-09: startup_recovery 路径修复（FR-C6 — 联合 Phase 第 5 步）

- **Phase**: B
- **对应 FR/AC**: FR-C6；AC-C6
- **依赖 task**: T-B-08
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:438-448`（startup_recovery）
  - 补充 is_caller_worker_signal 读取：从 Event Store 历史 CONTROL_METADATA_UPDATED 事件中恢复信号
  - 恢复逻辑与 attach_input 路径（`task_runner.py:613-626`）对称
- **验证方式**: 代码审查确认 startup_recovery 调用 Event Store 查询 CONTROL_METADATA_UPDATED 事件；恢复逻辑与 attach_input 路径一致
- **完成判据**: 重启后 is_caller_worker_signal 语义正确恢复
- **预估改动**: 增 15-25 行
- **风险**: MED
- **Codex review 节点**: 否（联合 Phase）

---

### T-B-10: 新建 test_f101_approval_gate.py + 单测（AC-C1/C2/C3/C6）

- **Phase**: B
- **对应 FR/AC**: AC-C1；AC-C2；AC-C3；AC-C6
- **依赖 task**: T-B-09（所有实现完成后联合验测）
- **改动文件**: `octoagent/tests/test_f101_approval_gate.py`（新建）
  - 测点 1（AC-C2）：mock octo_harness bootstrap，验证 `ApprovalGate.sse_push_fn` 不为 None，为 async callable
  - 测点 2（AC-C1）：mock approval_gate（非 None），`escalate_permission_handler` → task 状态变为 WAITING_APPROVAL（不再静默降级）
  - 测点 3（AC-C3）：mock `ApprovalGate.wait_for_decision` 超时返回 "rejected"，验证 task_runner 超时监控推进 FAILED 终态（不再无限 continue）
  - 测点 4（AC-C6）：mock startup_recovery，历史 CONTROL_METADATA_UPDATED 事件中有 is_caller_worker_signal → resume 后信号正确恢复
  - 测点 5（AC-C1 配对）：approval_gate is None（降级路径），验证仍返回 "rejected"（Constitution C6 保留）
- **验证方式**: `pytest tests/test_f101_approval_gate.py -v`，全 PASS
- **完成判据**: 5 个测点全 PASS
- **预估改动**: 新增 ~120-150 行
- **风险**: HIGH（联合 Phase 核心验收）
- **Codex review 节点**: 否（联合验收门，统一在 T-B-12）

---

### T-B-11: 联合验收门验证（AC-C1 + AC-C2 + AC-C3 + AC-C6 同时 PASS）+ 全量回归

- **Phase**: B
- **对应 FR/AC**: AC-C1；AC-C2；AC-C3；AC-C6；Phase B 出口回归门
- **依赖 task**: T-B-10（必须全部 PASS 才进入此 task）
- **改动文件**: 无（只运行测试）
- **验证方式**:
  1. `pytest tests/test_f101_approval_gate.py -v` → AC-C1/C2/C3/C6 全 PASS
  2. `pytest octoagent` ≥ 3450 passed，0 regression
  3. `pytest -m e2e_smoke` PASS（1x 循环，确认 bootstrap 未破坏）
- **完成判据**: 三个命令全通过；**三项联合验收缺一不可**，不允许部分通过进入 T-B-12
- **预估改动**: 0 行
- **风险**: HIGH
- **Codex review 节点**: 否

---

### T-B-12: Phase B commit + 触发 Codex per-Phase B review

- **Phase**: B
- **对应 FR/AC**: FR-C1/C2/C3/C6 全部验收
- **依赖 task**: T-B-11（联合验收门通过）
- **改动文件**: `sse_hub.py`（条件）+ `octo_harness.py`（闭包 + ApprovalGate 注入）+ `ask_back_tools.py`（escalate_permission 主路径）+ `task_runner.py`（WAITING_APPROVAL 分支 + 超时修复 + startup_recovery）+ `test_f101_approval_gate.py`（新建）
- **验证方式**: commit message 含 `feat(F101-Phase-B): ApprovalGate SSE 接入 + 状态机 + 超时修复（联合 Phase）`；Codex foreground review 通过
- **完成判据**: commit 完成；per-Phase B Codex review 0 HIGH 残留
- **Codex review 节点**: **是（per-Phase B，foreground，最重要的 review）**
  - 重点：FR-C1/C2/C3 三者是否真实联合（不是各自独立的半工作状态）；sse_push_fn 闭包的 session_id 来源是否正确；超时修复是否覆盖所有超时场景；startup_recovery 恢复逻辑是否与 attach_input 路径对称

---

## Phase C — Notification 主体扩展 + quiet hours + dismiss（US2/US3）

**目标**：实现 NotificationService 四级优先级模型、quiet hours 解析、dismiss 幂等、WAITING_APPROVAL 通知接入。

**User Story**: US2（通知有优先级，夜间只推关键通知，P1）+ US3（Worker 完成可靠推送一次，P1）

**独立测试**：在 USER.md 设置 active_hours，在 quiet hours 内触发不同优先级通知，验证 critical 通过、普通被过滤；同一通知两次 dismiss 不报错。

**依赖**: Phase B 完成（AC-C1/C2 完成后 FR-B1 才能接入）

---

### T-C-01: 读取 notification.py 完整（NotificationService + 两个 Channel）

- **Phase**: C
- **对应 FR/AC**: FR-B1/B2/B3/B5
- **依赖 task**: T-B-12（Phase B commit）
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/notification.py:92-485`
- **验证方式**: 确认 NotificationService 现有接口（notify_task_state_change 等）；SSENotificationChannel 和 TelegramNotificationChannel 各自 dismiss 机制；`_sent_notifications` 是否存在
- **完成判据**: 现有接口清单明确；Phase 0 T-0-03 的 dismiss 结论与实际代码对照验证
- **预估改动**: 0 行（只读）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-C-02: 新增 NotificationPriority 枚举（FR-B2）

- **Phase**: C
- **对应 FR/AC**: FR-B2；User Story 2
- **依赖 task**: T-C-01
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py:92` 附近
  - 新增 `NotificationPriority` 枚举类（或 Literal 类型别名）：
    ```python
    class NotificationPriority(str, Enum):
        CRITICAL = "approval_pending"
        HIGH = "worker_failed"
        MEDIUM = "worker_long_running"
        LOW = "worker_completed"
    ```
  - 扩展现有 `notify_task_state_change` / 新增 `notify_approval_request` 接口，加 `priority: NotificationPriority` 参数
- **验证方式**: `grep -n 'NotificationPriority' notification.py` 确认枚举存在；接口签名审查
- **完成判据**: 四级优先级枚举定义正确；现有接口向后兼容（priority 有默认值）
- **预估改动**: 增 15-20 行（枚举 + 接口扩展）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-C-03: 实现 _parse_active_hours 解析函数（FR-B3 第 1 步）

- **Phase**: C
- **对应 FR/AC**: FR-B3；FR-B4；AC-B4
- **依赖 task**: T-C-02
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py`（NotificationService 内部方法）
  - 实现 `_parse_active_hours(raw: str | None) -> tuple[time, time] | None`：
    - 解析 `"HH:MM-HH:MM"` 格式，返回 `(start_time, end_time)`
    - None 或格式非法 → 返回 None（全时段推送，AC-B4 兜底）
    - 不抛异常
- **验证方式**: 单元测试几个 case：合法格式 "09:00-23:00" → 返回 (time(9,0), time(23,0))；None → 返回 None；"invalid" → 返回 None；"25:00-26:00" → 返回 None
- **完成判据**: 解析函数完整；非法值 fallback 不抛异常
- **预估改动**: 增 20-30 行
- **风险**: MED（R4，格式解析复杂度）
- **Codex review 节点**: 否

---

### T-C-04: 实现 _is_quiet_hours 过滤函数（FR-B3 第 2 步）

- **Phase**: C
- **对应 FR/AC**: FR-B3；AC-B2；AC-B3
- **依赖 task**: T-C-03
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py`
  - 实现 `_is_quiet_hours(now: datetime, active_hours: str | None) -> bool`：
    - 调用 `_parse_active_hours` 获取 (start, end)
    - None → 返回 False（不是 quiet hours，不过滤）
    - `now.time() in [start, end)` 为 active hours → 返回 False
    - 否则（在 quiet hours 内）→ 返回 True
    - 处理跨 midnight 场景（如 active_hours "22:00-06:00"）
  - 过滤决策：`priority == CRITICAL` → 不过滤（始终推送，AC-B2）；其他 + quiet hours → 过滤（AC-B3）
- **验证方式**: 代码审查确认左闭右开语义；跨 midnight 处理逻辑（"09:00-23:00" 的 quiet hours = 23:00-09:00）
- **完成判据**: 过滤函数完整；CRITICAL 级别豁免正确；跨 midnight 场景处理
- **预估改动**: 增 25-35 行
- **风险**: MED（边界计算）
- **Codex review 节点**: 否

---

### T-C-05: NotificationService 推送前加 quiet hours 过滤（FR-B3 集成）

- **Phase**: C
- **对应 FR/AC**: FR-B3；AC-B2；AC-B3
- **依赖 task**: T-C-04
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py`（notify_task_state_change + notify_approval_request 方法）
  - 在推送到各 channel 之前调用 `_is_quiet_hours`
  - `approval_pending`（CRITICAL）→ 跳过 quiet hours 检查，直接推送
  - 其他优先级 + quiet hours → 直接 return（不推送，不报错）
  - USER.md 读取通过 `user_profile.read` 或已有机制（FR-B4，不引入独立数据存储）
- **验证方式**: 代码审查确认 quiet hours 检查在所有 channel 推送之前；CRITICAL 豁免不在 quiet hours 中
- **完成判据**: 推送前过滤完整；USER.md 是 SoT（不引入独立存储）
- **预估改动**: 改 15-25 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-C-06: USER.md 模板新增 active_hours 字段结构化注释（FR-B4）

- **Phase**: C
- **对应 FR/AC**: FR-B4；AC-B4
- **依赖 task**: T-C-05
- **改动文件**: `octoagent/packages/core/src/octoagent/core/behavior_templates/USER.md:22`（active_hours 字段注释）
  - 新增标准格式注释：`active_hours: "HH:MM-HH:MM"`（如 `"09:00-23:00"`，左闭右开）
  - 说明格式规范供 user_profile.update 工具引导用户填写
- **验证方式**: `grep -n 'active_hours' USER.md` 确认字段存在且有格式说明
- **完成判据**: USER.md 模板包含结构化 active_hours 字段定义
- **预估改动**: 增 3-5 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-C-07: WAITING_APPROVAL 通知接入（FR-B1）

- **Phase**: C
- **对应 FR/AC**: FR-B1；AC-B5
- **依赖 task**: T-C-05（NotificationService 优先级扩展完成）+ T-B-12（Phase B 完成，WAITING_APPROVAL 状态机正确）
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:404-406`（已在 Phase B T-B-07 修改的 WAITING_APPROVAL 分支）
  - 在 WAITING_APPROVAL 分支中增加 notification_service 调用：
    - 若 Phase 0 T-0-02 确认 notification_service 已注入 → 直接调用 `notification_service.notify_approval_request(..., priority=NotificationPriority.CRITICAL)`
    - 若未注入 → 需先在 task_runner 构造函数中加 notification_service 参数 + octo_harness 更新构造链
- **验证方式**: 代码审查确认 WAITING_APPROVAL 进入时通知调用存在；priority=CRITICAL 确保 quiet hours 中也能推送
- **完成判据**: WAITING_APPROVAL 进入时触发审批通知；AC-B5 满足（notify_approval_request 被调用）
- **预估改动**: 增 5-10 行（若 notification_service 已注入）；增 30-40 行（若需修改构造链）
- **风险**: MED（依赖 Phase 0 R3 结论）
- **Codex review 节点**: 否

---

### T-C-08: FR-B5 dismiss 幂等机制（跨通道共享 dismissed set）

- **Phase**: C
- **对应 FR/AC**: FR-B5；AC-B6
- **依赖 task**: T-C-07
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py`（NotificationService）
  - 依据 Phase 0 T-0-03 实测方案：
    - 若已有 `_sent_notifications` 内存 set → 扩展其 dismiss 语义（add 为幂等）；确认 Web + Telegram 通过同一 NotificationService 实例共享该 set
    - 若无 → 新建 `_dismissed_notifications: set[str]` + dismiss 方法 `dismiss(notification_id: str) -> None`
  - GATE_DESIGN 决议选 A：dismiss 跨通道同步 → Web 下次刷新反映；不做实时 SSE 推送
- **验证方式**: 代码审查确认同一 NotificationService 实例的 `_dismissed_notifications` 被 Web 和 Telegram channel 共享；dismiss 是幂等 set.add 操作
- **完成判据**: dismiss 幂等；跨通道共享；重复 dismiss 不报错（AC-B6）
- **预估改动**: 增 10-20 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-C-09: FR-B6 精确一次推送验证（_notify_completion 无重复调用路径）

- **Phase**: C
- **对应 FR/AC**: FR-B6；AC-B1
- **依赖 task**: T-C-08
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`（_notify_completion 调用路径审查）
  - 确认 `_notify_completion` 在 SUCCEEDED/FAILED 终态只被调用一次（无重复路径）
  - 若有重复调用路径 → 加 guard 或 idempotency check
- **验证方式**: grep `_notify_completion` 所有调用点，确认终态路径唯一；代码审查无 double-call 风险
- **完成判据**: SUCCEEDED/FAILED 终态 _notify_completion 精确一次调用；AC-B1 满足
- **预估改动**: 改 0-10 行（视是否有重复路径）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-C-10: FR-B7 attention_work_count 更新（SHOULD 级别）

- **Phase**: C
- **对应 FR/AC**: FR-B7（SHOULD）；通过 AC-B1 event_store 间接验证
- **依赖 task**: T-C-09
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`（dispatch 开始和终态路径）
  - dispatch 开始：`attention_work_count += 1`
  - 任务终态（SUCCEEDED/FAILED）：`attention_work_count -= 1`
  - 依据 Phase 0 T-0-04 结论：若已有更新调用则验证；若无则新建
- **验证方式**: grep `attention_work_count` 确认 +1/-1 更新存在；代码审查调用时机正确
- **完成判据**: attention_work_count 在 dispatch 开始 +1、任务终态 -1；SHOULD 级别，实施但不独立 AC
- **预估改动**: 增 8-15 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-C-11: 新建 test_f101_notification.py + 单测（AC-B1~B6）

- **Phase**: C
- **对应 FR/AC**: AC-B1；AC-B2；AC-B3；AC-B4；AC-B5；AC-B6
- **依赖 task**: T-C-10（所有实现完成后写测试）
- **改动文件**: `octoagent/tests/test_f101_notification.py`（新建）
  - 测点 1（AC-B1）：mock task_runner 终态 → NotificationService.notify_task_state_change 精确调用一次；event_store 有对应记录
  - 测点 2（AC-B2）：approval_pending 通知 + quiet hours 内 → 通过 filter，推送成功（CRITICAL 豁免）
  - 测点 3（AC-B3）：worker_completed 通知 + quiet hours 内 → filter 拦截，不推送；系统不报错
  - 测点 4（AC-B4）：USER.md active_hours 为空/None → 全时段推送，不过滤
  - 测点 5（AC-B5）：task 进入 WAITING_APPROVAL → notify_approval_request 被调用，sse_push_fn 已注入（mock AC-C2 结果）
  - 测点 6（AC-B6）：同一通知 ID 两次 dismiss → 第二次返回成功，不报错
  - 测点 7：USER.md active_hours 格式非法 → 全时段推送（fallback）
- **验证方式**: `pytest tests/test_f101_notification.py -v`，全 PASS
- **完成判据**: 7 个测点全 PASS
- **预估改动**: 新增 ~130-160 行
- **风险**: LOW
- **Codex review 节点**: 否（per-Phase C commit 后触发）

---

### T-C-12: 全量回归 + Phase C commit + 触发 Codex per-Phase C review

- **Phase**: C
- **对应 FR/AC**: AC-B1~B6 全部验收；Phase C 出口回归门
- **依赖 task**: T-C-11
- **改动文件**: `notification.py`（枚举 + 解析 + 过滤 + dismiss）+ `task_runner.py`（WAITING_APPROVAL 通知接入）+ `worker_runtime.py`（attention_work_count）+ `USER.md`（active_hours 字段）+ `test_f101_notification.py`（新建）
- **验证方式**:
  1. `pytest tests/test_f101_notification.py -v` 全 PASS
  2. `pytest octoagent` ≥ 3450 passed，0 regression
  3. `pytest -m e2e_smoke` PASS
- **完成判据**: 三个命令全通过；commit `feat(F101-Phase-C): Notification 优先级 + quiet hours + dismiss`；per-Phase C Codex review 0 HIGH 残留
- **Codex review 节点**: **是（per-Phase C，foreground）**
  - 重点：quiet hours 边界计算（左闭右开 + 跨 midnight）；USER.md fallback 合理；dismiss 幂等跨通道共享；approval_pending CRITICAL 逻辑在 quiet hours 中豁免

---

## Phase D — ask_back integration test + 顺手清（US5）

**目标**：FR-C4 integration test + M-1 broad-catch 修复 + FR-C5 guard 补全 + FR-C7 `__all__` 定义。

**User Story**: US5（ask_back 工具异常不再静默吞噬，P3）

**独立测试**：mock task_store 使 get_current_execution_context() 抛出异常，验证日志中出现 debug 级别条目，guard 异常后工具仍按原有降级策略执行。

**依赖**: Phase B 完成（FR-C4 integration test 需要真实 approval_gate 状态机工作）+ Phase C 完成（建议按顺序，但技术上 D 只依赖 Phase B）

---

### T-D-01: 修复 3 处 M-1 broad-catch（ask_back_tools.py:194/282/376）

- **Phase**: D
- **对应 FR/AC**: US5；AC-A2（降级行为保持不变）；spec §2 US5 AC
- **依赖 task**: T-C-12（Phase C commit）
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:194, 282, 376`
  - 行 194：`except Exception: pass` → `except Exception as exc: log.debug("ask_back guard failed: %s", exc)`
  - 行 282：`except Exception: pass` → `except Exception as exc: log.debug("request_input guard failed: %s", exc)`
  - 行 376：`except Exception: pass` → `except Exception as exc: log.debug("escalate_permission guard failed: %s", exc)`
  - 降级行为保持不变（guard 失败时工具仍按原降级路径执行）
- **验证方式**: `grep -n 'except Exception' ask_back_tools.py` 确认 3 处全部从 pass 改为 log.debug；功能路径不变
- **完成判据**: 3 处全部修复；降级行为不变（ask_back 仍返回 ""，escalate_permission 仍返回 "rejected"）
- **预估改动**: 改 3 行（3 处各 1 行，pass → log.debug 行）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-D-02: FR-C5 非 worker 路径 guard 补全（SHOULD 级别）

- **Phase**: D
- **对应 FR/AC**: FR-C5（SHOULD）；AC-C5
- **依赖 task**: T-D-01
- **改动文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:182-195`（ask_back_handler guard 段）
  - 当 `is_caller_worker=False`（非 worker 路径）时：
    - 方案（最小侵入）：log.debug 记录"guard skipped for non-worker path"，不修改功能逻辑
    - 或：non-worker 路径同样检查 RUNNING 状态，非 RUNNING 时 log.debug，功能降级与 worker 路径一致
    - 具体方案视 T-D-01 读取代码结构后确定
- **验证方式**: 代码审查确认非 worker 路径 guard 有日志记录或状态检查（AC-C5）
- **完成判据**: 非 worker 路径 guard 不再完全跳过（至少有 log.debug 记录）；AC-C5 满足
- **预估改动**: 增 3-8 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-D-03: source_kinds.py 新增 `__all__`（FR-C7）

- **Phase**: D
- **对应 FR/AC**: FR-C7（SHOULD）；AC-C7
- **依赖 task**: T-D-02
- **改动文件**: `octoagent/packages/core/src/octoagent/core/models/source_kinds.py`
  - 新增 `__all__` = [11 个符号列表]：5 个 SOURCE_RUNTIME_KIND_* + KNOWN_SOURCE_RUNTIME_KINDS + 5 个 CONTROL_METADATA_SOURCE_*
  - 参考 tech-research 行 154-160（spec ref-10）
- **验证方式**: `python3 -c "from octoagent.core.models.source_kinds import *; print(dir())"` 确认只导出 11 个符号；或 `grep __all__ source_kinds.py`
- **完成判据**: `__all__` 定义存在且含 11 个符号；AC-C7 满足
- **预估改动**: 增 5-8 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-D-04: 新建 test_f101_ask_back_integration.py + FR-C4 integration test（AC-C4）

- **Phase**: D
- **对应 FR/AC**: FR-C4；AC-C4
- **依赖 task**: T-D-03 + T-B-12（需要真实 approval_gate 状态机工作）
- **改动文件**: `octoagent/tests/services/test_f101_ask_back_integration.py`（新建，service layer integration test）
  - integration test 定义：不跑 LLM，使用真实 task_runner + event_store + ask_back_tools 调用链
  - 测点 1（AC-C4 主路径）：ask_back_handler 执行 → CONTROL_METADATA_UPDATED emit → task 状态 WAITING_INPUT → attach_input → resume → RUNNING 恢复
  - 测点 2（事件链完整性）：完整事件链通过 EventStore 查询验证（非纯 mock assert）
  - 测点 3（与 Phase B FR-C6 联动）：resume 后 is_caller_worker_signal 正确（通过 CONTROL_METADATA_UPDATED 历史事件恢复）
  - Given 段（修订后 AC-C4 Given：integration test 环境，Worker runtime 已 dispatch，task RUNNING，mock TaskStore + EventStore 已初始化）
- **验证方式**: `pytest tests/services/test_f101_ask_back_integration.py -v` 全 PASS；验证 EventStore 查询路径（非纯 mock）
- **完成判据**: 3 个测点全 PASS；integration test 使用真实服务调用链（service layer 真实调用，非纯 mock-based）
- **预估改动**: 新增 ~100-130 行
- **风险**: MED（integration test 复杂度）
- **Codex review 节点**: 否（per-Phase D commit 后触发）

---

### T-D-05: AC-C5 guard 单测 + AC-C7 style 验证

- **Phase**: D
- **对应 FR/AC**: AC-C5；AC-C7
- **依赖 task**: T-D-04
- **改动文件**: `octoagent/tests/services/test_f101_ask_back_integration.py`（与 T-D-04 一致，路径统一）或新增测试片段
  - AC-C5 测点：mock task_store 使 get_current_execution_context() 抛 RuntimeError → log.debug 被调用；guard 失败后工具仍按降级路径（ask_back 返回 ""）
  - AC-C7 style 验证：`from source_kinds import *` → 只导出 11 个符号（可在单独测试文件或 shell 验证）
- **验证方式**: pytest 测点全 PASS；AC-C7 可用 `python3 -c` 命令行验证
- **完成判据**: AC-C5 和 AC-C7 均满足
- **预估改动**: 增 20-35 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-D-06: 全量回归 + e2e_smoke

- **Phase**: D
- **对应 FR/AC**: Phase D 出口回归门
- **依赖 task**: T-D-05
- **改动文件**: 无（只运行测试）
- **验证方式**: `pytest octoagent` ≥ 3450 passed，0 regression；`pytest -m e2e_smoke` PASS
- **完成判据**: 两个命令均通过
- **预估改动**: 0 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-D-07: Phase D commit + 触发 Codex per-Phase D review

- **Phase**: D
- **对应 FR/AC**: FR-C4/C5/C7；US5；AC-C4/C5/C7 全部验收
- **依赖 task**: T-D-06
- **改动文件**: `ask_back_tools.py`（3 处 broad-catch + guard 补全）+ `source_kinds.py`（`__all__`）+ `test_f101_ask_back_integration.py`（新建）
- **验证方式**: commit message 含 `feat/fix(F101-Phase-D): ask_back integration test + 顺手清`；Codex foreground review 通过
- **完成判据**: commit 完成；per-Phase D Codex review 0 HIGH 残留
- **Codex review 节点**: **是（per-Phase D，foreground）**
  - 重点：FR-C4 integration test 是否真实（service layer 真实调用，非纯 mock）；3 处 M-1 修复完整性（不遗漏 request_input:282 和 escalate_permission:376）；`__all__` 符号数量是否正确（11 个）

---

## Phase E — D8 顺手清（条件实施）

**目标**：FR-E1（SHOULD）：ControlPlaneService 构造时新增 notification_service 参数。条件：若 Phase C 已通过 task_runner 路径覆盖所有通知场景，Phase E 可降级为不实施。

**不对应独立 User Story**：顺手清，AC-E1 条件验证

**依赖**: Phase D 完成（Phase C NotificationService 已就位，评估是否需要 control_plane 路径感知通知）

---

### T-E-01: 读取 _coordinator.py:93-109，评估是否需要 notification_service 参数

- **Phase**: E
- **对应 FR/AC**: FR-E1；AC-E1（条件）
- **依赖 task**: T-D-07（Phase D commit）
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/_coordinator.py:93-109`（`ControlPlaneService.__init__`）
- **验证方式**: 确认 Phase C 是否已通过 task_runner 路径覆盖所有通知场景；评估 control_plane 路径是否还需要感知通知
- **完成判据**: 明确决策：实施（需要 notification_service 参数）or 降级（不实施，AC-E1 豁免）
- **预估改动**: 0 行（只读 + 决策）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-E-02: 在 ControlPlaneService.__init__ 加 notification_service 参数（FR-E1，条件实施）

- **Phase**: E
- **对应 FR/AC**: FR-E1；AC-E1
- **依赖 task**: T-E-01（明确实施决策后才执行）
- **改动文件（若实施）**: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/_coordinator.py:93-109`
  - 新增 `notification_service: NotificationService | None = None` 参数（不破坏现有 14 参数构造）
- **改动文件（若实施）**: `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py:1017-1031`
  - 更新 ControlPlaneService 构造调用，传入 notification_service
- **验证方式**: 代码审查确认参数为 Optional 且有 None 默认值（向后兼容）；octo_harness 构造调用更新
- **完成判据**: 若实施：构造参数正确；若降级：task 跳过，commit 中显式记录"Phase E 降级，AC-E1 豁免，理由 XXX"
- **预估改动**: 若实施：增 2-5 行；若降级：0 行
- **风险**: LOW
- **Codex review 节点**: 否（若实施，与 Phase D review 合并；若降级，跳过）

---

### T-E-03: AC-E1 单测（若 E-2 实施）

- **Phase**: E
- **对应 FR/AC**: AC-E1（条件）
- **依赖 task**: T-E-02（仅在实施时）
- **改动文件**: `octoagent/tests/test_f101_control_plane.py`（新建或并入现有）
  - 验证 ControlPlaneService 构造时 notification_service 作为显式参数传入
- **验证方式**: `pytest tests/test_f101_control_plane.py -v` PASS
- **完成判据**: AC-E1 测点 PASS（若实施）
- **预估改动**: 新增 20-30 行（若实施）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-E-04: Phase E commit（条件）

- **Phase**: E
- **对应 FR/AC**: FR-E1；AC-E1
- **依赖 task**: T-E-03（若实施）or T-E-01（若降级）
- **改动文件**: `_coordinator.py`（若实施）+ `octo_harness.py`（若实施）+ 测试（若实施）
- **验证方式**: 若实施：全量回归 + commit `chore(F101-Phase-E): ControlPlaneService notification_service 参数`；若降级：commit `chore(F101-Phase-E): 降级不实施，AC-E1 豁免` + per-Phase D review 合并处理
- **完成判据**: commit 完成；Phase E 实施状态明确记录
- **Codex review 节点**: **是（per-Phase E，若实施；或合并入 Phase D review，若降级）**

---

## Phase F — AC-F1 验证 + Final 准备

**目标**：AC-F1（选 C）验证 + 全量回归门 + e2e_smoke 5x 循环 + Final review 输入文档产出。

**不对应独立 User Story**：验证 Phase，确认 F101 其他改动没有意外破坏 ask_back resume 路径。

**依赖**: 所有 Phase 完成

---

### T-F-01: 读取 runtime_control.py:106-124，确认 is_recall_planner_skip 返回逻辑

- **Phase**: F
- **对应 FR/AC**: AC-F1（选 C）
- **依赖 task**: T-E-04（或 T-D-07 如果 Phase E 降级）
- **改动文件**: 只读——`octoagent/apps/gateway/src/octoagent/gateway/services/runtime_control.py:106-124`（is_recall_planner_skip）
- **验证方式**: 确认 force_full_recall=False（默认）时 unspecified → return False 路径；与 GATE_DESIGN 选 C 决议一致
- **完成判据**: is_recall_planner_skip 在 ask_back resume 场景（force_full_recall=False，unspecified）返回 False 是预期行为
- **预估改动**: 0 行（只读）
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-F-02: 单测 AC-F1（ask_back resume → is_recall_planner_skip=False spy）

- **Phase**: F
- **对应 FR/AC**: AC-F1（选 C）
- **依赖 task**: T-F-01
- **改动文件**: `octoagent/tests/test_f101_force_full_recall.py`（append）或新建 `test_f101_acf1.py`
  - mock ask_back resume 场景（WAITING_INPUT → attach_input → RUNNING）
  - spy `is_recall_planner_skip`：验证 resume 后 turn N+1 返回 False（跑 full recall，是预期行为）
  - 验证任务从 WAITING_INPUT → resume → RUNNING，无异常
  - 不需要修改任何 production 代码（选 C = 保持 baseline）
- **验证方式**: `pytest` 测点 PASS；spy 确认 is_recall_planner_skip 返回 False
- **完成判据**: AC-F1 spy 验证通过；系统不报错，任务正常继续
- **预估改动**: 新增 ~30-40 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-F-03: 全量回归（vs F099 baseline 3450 passed）

- **Phase**: F
- **对应 FR/AC**: Phase F 出口回归门
- **依赖 task**: T-F-02
- **改动文件**: 无（只运行测试）
- **验证方式**: `pytest octoagent` ≥ 3450 + F101 新增测试数 passed，0 regression vs F099 baseline
- **完成判据**: 全量回归通过，新增测试全部计入
- **预估改动**: 0 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-F-04: e2e_smoke 5x 循环

- **Phase**: F
- **对应 FR/AC**: Phase F 出口回归门（加严）
- **依赖 task**: T-F-03
- **改动文件**: 无（只运行测试）
- **验证方式**: `pytest -m e2e_smoke --count=5` 或手动 5 轮循环，全 PASS
- **完成判据**: e2e_smoke 5 次循环全部 PASS
- **预估改动**: 0 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-F-05: 产出 Final cross-Phase review 输入文档（所有 Phase commit diff 汇总）

- **Phase**: F
- **对应 FR/AC**: Phase Final 准备
- **依赖 task**: T-F-04
- **改动文件**: `.specify/features/101-notification-attention/`（可在 phase-0-recon.md 或新建 codex-review-final-input.md 记录）
  - 汇总 Phase A/B/C/D/E/F 全部 commit hash + 主要改动范围
  - 标注联合 Phase B 的三者联合验收状态
  - 标注 Phase E 实施/降级状态
- **验证方式**: 文档完整，含 Phase A-F 所有 commit 信息
- **完成判据**: Final review 输入文档产出
- **预估改动**: 新建或追加文档 ~30-50 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-F-06: Phase F commit + 触发 Final cross-Phase Codex review

- **Phase**: F
- **对应 FR/AC**: AC-F1；全量回归；e2e_smoke 5x
- **依赖 task**: T-F-05
- **改动文件**: AC-F1 测试 + Final review 输入文档
- **验证方式**: commit `test(F101-Phase-F): AC-F1 验证 + Final 准备`；启动 Codex Final cross-Phase review（foreground）
- **完成判据**: commit 完成；Final review 触发
- **Codex review 节点**: **是（Final cross-Phase review，foreground，合并 Phase F review + Final review）**
  - 输入：spec.md + plan.md + Phase A/B/C/D/E/F 全部 commit diff
  - 范围：是否漏 Phase / 偏离计划 / Phase B 联合验收是否真实 / AC-C1/C2/C3 HIGH AC 是否真闭环

---

## Phase Final — Codex Final review + completion-report + handoff

**目标**：完成 F101 所有验收 + Final review 闭环 + 产出 F102 handoff。

---

### T-Final-01: Codex Final cross-Phase review + finding 分类处理

- **Phase**: Final
- **对应 FR/AC**: Phase Final 出口条件
- **依赖 task**: T-F-06
- **改动文件**: 视 finding 结果：可能需要修改 Phase A-F 中的任意文件（HIGH 修复）
- **验证方式**: Final review finding 分类完成：HIGH（修复 + re-review）/ MEDIUM（处理或归档 F102）/ LOW（ignored）
- **完成判据**: 0 HIGH 残留；re-review 完成（若有 HIGH 修复）
- **预估改动**: 视 finding 决定
- **风险**: MED（Final review 可能抓新 HIGH，F099 教训）
- **Codex review 节点**: **是（Final cross-Phase review + re-review 预备）**

---

### T-Final-02: re-review（预备，若 T-Final-01 抓到 HIGH）

- **Phase**: Final
- **对应 FR/AC**: Phase Final 出口条件（0 HIGH 残留）
- **依赖 task**: T-Final-01（仅在 Final review 抓到 HIGH 时触发）
- **改动文件**: HIGH finding 对应的修复文件
- **验证方式**: re-review 通过（0 HIGH 残留）；修复后全量回归 0 regression
- **完成判据**: 0 HIGH 残留；re-review PASS
- **预估改动**: 视 HIGH finding 决定
- **风险**: MED
- **Codex review 节点**: **是（re-review，foreground）**

---

### T-Final-03: 产出 completion-report.md

- **Phase**: Final
- **对应 FR/AC**: Phase Final 出口文档
- **依赖 task**: T-Final-01（0 HIGH 残留确认后）
- **改动文件**: `.specify/features/101-notification-attention/completion-report.md`（新建）
  - §1 实际 vs 计划 Phase 对照表（Phase 0/A/B/C/D/E/F/Final，含 Phase E 实施/降级记录）
  - §2 Codex finding 闭环表（per-Phase A/B/C/D + Final + re-review）
  - §3 测试通过数：F099 baseline 3450 → F101 final N，0 regression
  - §4 Phase 跳过显式归档（Phase E 若降级：理由 + 影响）
  - §5 已知 deferred 项（归档 F102/F107 的任何 medium）
- **验证方式**: completion-report.md 存在，§1-5 全部填写完整
- **完成判据**: completion-report.md 产出完整
- **预估改动**: 新建 ~80-100 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-Final-04: 产出 handoff.md（给 F102 Proactive Followup）

- **Phase**: Final
- **对应 FR/AC**: Phase Final 出口文档
- **依赖 task**: T-Final-03
- **改动文件**: `.specify/features/101-notification-attention/handoff.md`（新建）
  - §1 F101 落地状态（NotificationService 优先级模型 + quiet hours + ApprovalGate SSE 接入点）
  - §2 F102 可直接复用的接入点（NotificationService.notify_heartbeat + WorkerRuntime dispatch 信号 + attention_work_count 字段）
  - §3 已知 deferred 项（若有：dismiss 持久化 → F107；attention_work_count 完整 Attention Model 决策 → F102）
  - §4 backend 契约稳定清单（供 F102 参考无需重新实测的已稳定接口）
- **验证方式**: handoff.md 存在，§1-4 全部填写完整
- **完成判据**: handoff.md 产出完整；F102 可基于此直接启动
- **预估改动**: 新建 ~60-80 行
- **风险**: LOW
- **Codex review 节点**: 否

---

### T-Final-05: Final commit + 等待用户拍板

- **Phase**: Final
- **对应 FR/AC**: F101 Definition of Done
- **依赖 task**: T-Final-04
- **改动文件**: completion-report.md + handoff.md（若有 HIGH 修复则含修复文件）
- **验证方式**:
  - [ ] Phase A/B/C/D/E/F/Final 全部 commit 完成
  - [ ] 全量回归 ≥ 3450 passed，0 regression vs F099 baseline
  - [ ] e2e_smoke 5x 循环全 PASS
  - [ ] Codex Final cross-Phase review 0 HIGH 残留
  - [x] AC-C1/C2/C3 联合 Phase B 联合验收通过（三者同时 pass）（Phase B 22/22 tests passed）
  - [x] AC-C6 startup_recovery is_caller_worker_signal 恢复（Phase B AC-C6 2/2 tests passed）
  - [x] B-9b 竞态测试（3 场景全 PASS）
  - [x] B-9c service-layer integration test（真实 SSEHub + ApprovalGate，PASS）
  - [x] B-9d approval_timeout_seconds 配置覆盖（PASS）
  - [x] HIGH-01 production resolve 双路径接通验证（3/3 PASS）
  - [x] HIGH-02 finally 块 vs monitor 竞态测试（2/2 PASS）
  - [x] HIGH-03 monitor CAS 失败 abort side effects 测试（3/3 PASS）
  - [x] HIGH-04 startup_recovery WAITING_APPROVAL 扫描测试（3/3 PASS）
  - [x] HIGH-01 v3 CLOSED：escalate_permission 同步注册 ApprovalManager（3/3 PASS）
  - [x] HIGH-02 v3 CLOSED：finally 块按 decision 条件恢复（3/3 PASS）
  - [x] HIGH-04 v3 CLOSED：startup_recovery 重启 monitor / reason 格式统一（3/3 PASS）
  - [x] N-M-01 v3 CLOSED：双 resolve 传 operation_type + allowlist 真更新（2/2 PASS）
  - [x] N-M-02 v3 CLOSED：_run_job 终态去重 check（2/2 PASS）
  - [x] Phase B v3 全量回归 35/35 PASS，0 regression（3502 passed vs 3488 v2 baseline）
  - [x] e2e_smoke 8/8 PASS（Phase B v3 验证通过）
  - [ ] AC-F1 验证（选 C：is_recall_planner_skip spy 确认 return False）
  - [ ] completion-report.md + handoff.md 已产出
  - [ ] 不 push origin/master（等用户拍板）
- **完成判据**: commit `docs(F101): completion-report + handoff + Codex Final review`；所有 DoD 项打勾
- **Codex review 节点**: 否（Final review 在 T-Final-01 已完成）

---

## AC ↔ Task 映射表

| AC | 实施 Task | 验证 Task | Codex review 节点 |
|----|----------|----------|------------------|
| AC-D1（新对话 force_full_recall 注入）| T-A-03 | T-A-05 测点 1 | per-Phase A（T-A-07）|
| AC-D2（短消息不注入，baseline 不变）| T-A-02（常量定义）| T-A-05 测点 2 | per-Phase A（T-A-07）|
| AC-D3（续对话路径一致）| T-A-04 | T-A-05 测点 3/4 | per-Phase A（T-A-07）|
| AC-C2（ApprovalGate sse_push_fn 非 None）| T-B-03 + T-B-04 | T-B-10 测点 1 | per-Phase B（T-B-12）|
| AC-C1（escalate_permission → WAITING_APPROVAL）| T-B-06 | T-B-10 测点 2 | per-Phase B（T-B-12）|
| AC-C3（超时 → FAILED 终态）| T-B-08 | T-B-10 测点 3 | per-Phase B（T-B-12）|
| AC-C6（startup_recovery is_caller_worker 恢复）| T-B-09 | T-B-10 测点 4 | per-Phase B（T-B-12）|
| AC-B5（WAITING_APPROVAL → notify_approval_request）| T-C-07 | T-C-11 测点 5 | per-Phase C（T-C-12）|
| AC-B1（Worker 完成精确一次推送）| T-C-09 | T-C-11 测点 1 | per-Phase C（T-C-12）|
| AC-B2（approval_pending + quiet hours → 推送）| T-C-04 + T-C-05 | T-C-11 测点 2 | per-Phase C（T-C-12）|
| AC-B3（worker_completed + quiet hours → 过滤）| T-C-04 + T-C-05 | T-C-11 测点 3 | per-Phase C（T-C-12）|
| AC-B4（active_hours 为空 → 无过滤）| T-C-03 + T-C-05 | T-C-11 测点 4/7 | per-Phase C（T-C-12）|
| AC-B6（dismiss 幂等）| T-C-08 | T-C-11 测点 6 | per-Phase C（T-C-12）|
| AC-C4（ask_back integration test）| T-D-04 | T-D-04 本身 | per-Phase D（T-D-07）|
| AC-C5（非 worker 路径 guard）| T-D-02 | T-D-05 测点 1 | per-Phase D（T-D-07）|
| AC-C7（source_kinds `__all__`）| T-D-03 | T-D-05 测点 2 | per-Phase D（T-D-07）|
| AC-E1（ControlPlaneService notification_service 参数）| T-E-02（条件）| T-E-03（条件）| per-Phase E（T-E-04）|
| AC-F1（ask_back resume → is_recall_planner_skip=False，选 C）| 无代码改动（选 C）| T-F-02（spy）| Final cross-Phase（T-F-06）|

---

## FR ↔ Task 映射表（100% 覆盖）

| FR | 优先级 | 实施 Task |
|----|--------|----------|
| FR-B1（WAITING_APPROVAL 通知）| 必须 | T-C-07 |
| FR-B2（四级优先级模型）| 必须 | T-C-02 |
| FR-B3（quiet hours 解析 + 过滤）| 必须 | T-C-03 + T-C-04 + T-C-05 |
| FR-B4（USER.md SoT）| 必须 | T-C-05 + T-C-06 |
| FR-B5（dismiss 幂等）| 必须 | T-C-08 |
| FR-B6（精确一次推送）| 必须 | T-C-09 |
| FR-B7（attention_work_count，SHOULD）| 可选 | T-C-10 |
| FR-C1（escalate_permission WAITING_APPROVAL）| 必须 | T-B-06 |
| FR-C2（ApprovalGate sse_push_fn 注入）| 必须 | T-B-02 + T-B-03 + T-B-04 |
| FR-C3（超时清理修复）| 必须 | T-B-07 + T-B-08 |
| FR-C4（ask_back integration test）| 必须 | T-D-04 |
| FR-C5（非 worker guard，SHOULD）| 可选 | T-D-02 |
| FR-C6（startup_recovery is_caller_worker）| 必须 | T-B-09 |
| FR-C7（source_kinds `__all__`，SHOULD）| 可选 | T-D-03 |
| FR-D1（新对话路径 producer）| 必须 | T-A-03 |
| FR-D2（续对话路径 producer）| 必须 | T-A-04 |
| FR-D3（LONG_PROMPT_THRESHOLD 可配置）| 必须 | T-A-02 |
| FR-D4（API 显式参数，SHOULD）| 可选 | 推迟 F107（plan §19 决议）|
| FR-E1（ControlPlaneService 参数，SHOULD）| 可选 | T-E-02（条件）|
| FR-F1（AC-5 ask_back resume 处理，选 C）| 决策选 C | T-F-01 + T-F-02（验证）|

---

## 依赖关系与并行说明

### Phase 依赖关系

```
Phase 0（侦察）
  └── Phase A（force_full_recall producer，不依赖 R1/R3 结论）
  └── Phase B（ApprovalGate SSE 接入，依赖 R1/R3 结论）
        └── Phase C（Notification 主体，依赖 Phase B 状态机）
              └── Phase D（ask_back integration test + 顺手清）
                    └── Phase E（D8 顺手清，条件）
                          └── Phase F（AC-F1 验证 + Final 准备）
                                └── Phase Final（Codex Final review + 文档）
```

### 并行机会

- **Phase A 与 Phase 0 部分并行**：T-A-01（chat.py 只读）可在 T-0-09 commit 后立即启动；Phase A 不依赖 R1/R3 结论（R1 影响 Phase B，不影响 Phase A）
- **Phase 0 侦察任务内部可并行**：T-0-01、T-0-02、T-0-03、T-0-04、T-0-05 彼此独立（读不同文件）；T-0-06 依赖 T-0-05；T-0-07 依赖 T-0-03 + T-0-04
- **Phase B 内部顺序**：T-B-02（SSEHub）→ T-B-03/T-B-04（闭包注入）→ T-B-05（只读）→ T-B-06/T-B-07/T-B-08/T-B-09（联合实施）→ T-B-10（联合验收）

### Phase B 联合 Phase 特别说明

T-B-03 到 T-B-10 中涉及 FR-C1 / FR-C2 / FR-C3 / FR-C6 的实现任务构成"联合不可拆分"的实施单元。不允许仅完成其中一部分就提交验收。联合验收门（T-B-11）需要 AC-C1 + AC-C2 + AC-C3 + AC-C6 四个测试同时通过，才允许触发 T-B-12 commit。

### 推荐实施策略

**P1 MVP 优先（最小可交付单元）**：Phase 0 → Phase B（审批修复，US1 P1）→ Phase C（通知 + quiet hours，US2/US3 P1）→ 全量回归

**完整实施顺序**：Phase 0 → Phase A（与 Phase B 内部准备并行）→ Phase B → Phase C → Phase D → Phase E（条件）→ Phase F → Phase Final

---

## 附注

- **不推 origin/master 约束**：Phase Final T-Final-05 commit 完成后等用户拍板，不主动 push
- **Phase E 降级时**：必须在 commit message 和 completion-report §4 中显式记录"Phase E 降级，AC-E1 豁免，理由：Phase C 已通过 task_runner 路径覆盖所有通知场景"，不允许默认无说明跳过
- **F099 教训（re-review 必要性）**：Final review 后如抓到 HIGH（T-Final-01），必须修复后再触发 T-Final-02（re-review），不允许带 HIGH finding 进入文档阶段
- **GATE_DESIGN 4 项决议（不可推翻）**：① AC-F1 选 C；② 块 C-6 N-H1 startup_recovery 在 F101 实施；③ dismiss 选 A；④ FR-B7 不独立 AC
