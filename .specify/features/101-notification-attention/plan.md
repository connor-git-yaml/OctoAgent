# F101 Notification + Attention Model — Plan

**Spec**: [spec.md](spec.md)（GATE_DESIGN 通过）
**Tech Research**: [research/tech-research.md](research/tech-research.md)
**Baseline**: `182e9ed`（F100 Phase H 完成，origin/master）
**Passed count**: 1469（F100 mock-based subset）/ 3450（F099 实测基线）
**Plan date**: 2026-05-16

---

## 0. 总览

### 0.1 背景上下文

F101 是 M5 阶段 3 起点，由两部分合并：

- **块 B（主路径）**：Notification + 优先级模型 + quiet hours 进 USER.md SoT + dismiss 同步
- **块 C-G（扩大承接）**：F099 7 项推迟（F3 HIGH 状态机 + ApprovalGate SSE production 接入）+ F100 force_full_recall producer + D8 顺手清

### 0.2 GATE_DESIGN 用户决议（plan 固化，不可推翻）

| 决议 | 内容 |
|------|------|
| **AC-F1 → 选 C** | ask_back resume 后跑 full recall 是合理行为；runtime_context 不修；AC-F1 验收仅确认系统不报错、任务正常继续，is_recall_planner_skip 返回 False 是预期行为 |
| **块 C-6 N-H1 startup_recovery → F101 实施** | startup_recovery 与 C-1 状态机同源（task_runner.py），顺手修复 |
| **dismiss 跨通道同步 → 选 A** | Telegram dismiss 后 Web 下次刷新反映；并发 last-write-wins 幂等；不做实时 SSE 推送 |
| **FR-B7 attention_work_count 独立 AC** | 不增加 AC-B7；FR-B7 为 SHOULD 级别，plan 决定实施，间接通过 AC-B1 event_store 验证 |

### 0.3 clarify 自动澄清（plan 直接采纳）

| 项目 | 采纳内容 |
|------|---------|
| FR-B7 attention_work_count | 不增加 AC-B7，SHOULD 级别实施 |
| FR-D4 API 显式参数 | 推迟 follow-up，F101 只做自动检测 |
| LONG_PROMPT_THRESHOLD 单位 | Unicode 字符数 `len(message)` |
| active_hours 时间边界 | 左闭右开 `[start, end)`，23:00 属 quiet hours |

### 0.4 checklist WARN 修复（plan Phase 0 完成）

| WARN | 修复内容 |
|------|---------|
| **WARN-1**（AC-C4 缺 Given / AC-F1 Then 可量化性弱）| Phase 0 改写 AC-C4 Given 段；AC-F1 Then 补充 is_recall_planner_skip spy 验证 |
| **WARN-2**（ref-5 行号混淆）| Phase 0 在 spec §12 引用索引表头部加注"行号列指 tech-research.md 文档行，括号内为源码文件行" |
| **WARN-3**（FR-B7 attention_work_count 更新路径未定义）| Phase 0 补充：WorkerRuntime dispatch 开始 +1 / 任务终态 -1 |
| **WARN-4**（FR-B5 dismiss 持久化机制未定义）| Phase 0 实测 NotificationService 内部 `_sent_notifications` set 是否已有 dismiss 语义，补充 spec §7 |

### 0.5 复杂度：HIGH

5 个组件修改（notification.py + approval_gate.py + task_runner.py + octo_harness.py + chat.py）、跨包耦合、WAITING_APPROVAL 状态机 + dismiss 并发控制、两处 HIGH 风险（R2 / R5）——HIGH 复杂度按约 7 Phase（含 Phase 0）设计。

---

## 1. Phase 0 — 侦察 + spec 修订（入口 commit: 182e9ed）

### 1.1 目标

在写一行 production 代码之前，先实测 Phase A/B/C 全部依赖的 2 个已知风险点（R1 / R3）和 4 个 WARN 修复项，产出侦察报告，修订 spec.md。

### 1.2 侦察任务清单

**R1 — SSEHub.broadcast per-session_id 能力确认**

- 读 `harness/sse_hub.py` 完整实现（目标文件：`octoagent/apps/gateway/src/octoagent/gateway/harness/sse_hub.py`）
- 确认 SSEHub 方法签名：是否存在 `broadcast_to_session(session_id, payload)` 或等价接口
- 若只有 `broadcast(task_id, event)` 形态 → 需要新增 `broadcast_to_session` 方法或 session_id→task_id 映射层
- 结论写入 phase-0-recon.md：`SSEHub_BROADCAST_CAPABILITY = PER_SESSION | TASK_ONLY | NEEDS_NEW_METHOD`

**R3 — task_runner.`__init__` notification_service 注入状态**

- 读 `task_runner.py` 构造函数（`octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`，重点读 `__init__` 和 `_notify_completion` 方法）
- 确认：`notification_service` 是否已作为构造参数注入
- 若未注入 → FR-B1 接入需修改 task_runner 构造函数 + octo_harness 构造链（复杂度升级 MED→HIGH）
- 若已注入 → FR-B1 接入简单，只需在 WAITING_APPROVAL 分支补调用
- 结论写入 phase-0-recon.md：`NOTIFICATION_SERVICE_INJECTED = YES | NO`

**dismiss 持久化实测（WARN-4）**

- 读 `notification.py:92-229`（NotificationService 全实现）
- 确认是否有 `_sent_notifications`（内存 set）或等价 dismiss 语义状态
- 确认跨通道 dismiss 是否有共享 set（或各 channel 独立）
- 结论：FR-B5 幂等实现方案（内存 set 共享 or 持久化 or 无需新建）

**attention_work_count 更新路径确认（WARN-3）**

- 读 `models/control_plane/agent.py:55`（`WorkerProfileDynamicContext`）
- 读 `worker_runtime.py`：Worker dispatch 开始和终态时是否有 `attention_work_count` +1/-1 的更新调用
- 结论：FR-B7 更新路径是否已存在，或需新建

**ApprovalGate wait_for_decision 超时配置确认（R2 前置）**

- 读 `approval_gate.py:88-105`（`__init__`）+ `wait_for_decision` 方法完整签名
- 确认：超时参数名称 + 默认值 + 超时后返回值（`"rejected"` or raise）
- 结论：FR-C3 超时修复的实现入口

**task_runner.py:779 超时监控 WAITING_APPROVAL continue 分析**

- 读 `task_runner.py:770-800`（超时监控循环全文）
- 确认：`continue` 跳过的条件和相邻代码结构
- 设计：修复 FR-C3 的最小侵入方案（是否需要从 `continue` 改为超时转 FAILED，还是通过 ApprovalGate callback 机制）

### 1.3 Phase 0 修订输出

- 产出 `phase-0-recon.md`（实测结论 + 6 项确认结果 + **M3 decision table**）
- 修订 spec.md：
  - AC-C4 Given 段改写（WARN-1）
  - AC-F1 Then 补 is_recall_planner_skip spy（WARN-1）
  - §12 引用索引表头部加注（WARN-2）
  - FR-B7 attention_work_count 更新路径补充（WARN-3）
  - §7 依赖表末尾补 dismiss 存储机制（WARN-4）

### 1.3b M3 Decision Table（Codex M3 修订 — Phase 0 出口强制产出）

| 实测结果 | 后续路径 | 额外 task | 是否需更新 spec/plan/tasks | 是否允许进 Phase B/C |
|---------|---------|----------|--------------------------|-----------------|
| `SSEHub_BROADCAST_CAPABILITY=PER_SESSION` | T-B-03 走选项 1（直接闭包调用 broadcast_to_session）| 无 | 否 | 是 |
| `SSEHub_BROADCAST_CAPABILITY=TASK_ONLY` | T-B-03 走选项 1b（加 session_id→task_id 映射层）| 新增 T-B-03b 映射层实施 + 测试 | tasks.md 加 T-B-03b | 是 |
| `SSEHub_BROADCAST_CAPABILITY=NEEDS_NEW_METHOD` | T-B-02 新增 SSEHub.broadcast_to_session 方法 | T-B-02 实施 + 单测 | tasks.md T-B-02 范围扩大 | 是 |
| `NOTIFICATION_SERVICE_INJECTED=YES` | Phase C 直接接入 | 无 | 否 | 是 |
| `NOTIFICATION_SERVICE_INJECTED=NO` | Phase C 先加 task_runner 构造参数 | 新增 T-C-00 task_runner.__init__ 加 notification_service 参数 + octo_harness 构造链更新 | tasks.md 加 T-C-00 | 是（Phase C 顺序调整） |
| `APPROVAL_TIMEOUT_DEFAULT != 300s` | spec FR-C3b 调整默认值 | 更新 spec FR-C3b + plan §3 | 是 | 是 |
| `TELEGRAM_CALLBACK_HANDLER` 缺失（R7）| Phase C 必须新建 callback handler | 新增 T-C-07b1 Telegram callback handler 框架（若 baseline 完全没有）| tasks.md 加 T-C-07b1 | 是（Phase C 范围扩大） |
| `WEB_NOTIFICATION_LIST_API` 缺失（H3）| Phase C 必须新建 API endpoint | 新增 T-C-07c1 Web API endpoint（若 baseline 完全没有）| tasks.md 加 T-C-07c1 | 是（Phase C 范围扩大） |

**Decision table 强制原则**：Phase 0 出口 commit 必须含 decision table 实测填写，每行"实测结果"列必须明确（不允许 unknown）；进 Phase B/C 前主编排器必须读 decision table 决定是否调整后续 Phase 任务。

### 1.4 退出条件

- phase-0-recon.md 产出，6 项结论明确 + decision table 实测填写完毕
- spec.md 已修订（4 WARN 修复 commit）
- R1 / R3 / R7（Telegram callback）/ R8（WAITING_APPROVAL timeout 来源）结论已明确，不允许 Phase A/B/C 实施时再现"未知"

### 1.5 Codex review 时机

不需要（侦察 + spec 修订，无 production 代码改动）

### 1.6 回归门

不运行全量回归（无代码改动），只运行 `pytest -m e2e_smoke` 确认 baseline 稳定

---

## 2. Phase A — force_full_recall Producer 实现（块 D）

### 2.1 目标

在 chat.py 两处 dispatch_metadata 构造点注入 force_full_recall producer，让长 prompt 场景真正触发完整决策环（FR-D1/D2/D3）。此块独立，不依赖块 B/C，优先实施。

### 2.2 接入点（来自 tech-research §A-4，spec ref-11）

**新对话路径（tech-research 行 220-262，`chat.py:422-444`）**

- 在 `encode_runtime_context(RuntimeControlContext(...))` 调用之前（`chat.py:433` 行附近）
- 判断 `len(body.message) > LONG_PROMPT_THRESHOLD` → 写入 `dispatch_metadata["force_full_recall"] = True`
- LONG_PROMPT_THRESHOLD 定义为模块级常量：`LONG_PROMPT_THRESHOLD: int = 2000`（Unicode 字符数）
- 常量需放在可配置位置（建议 chat.py 顶部或 settings 模块）

**续对话路径（`chat.py:479-493`，tech-research 行 251-262）**

- 相同逻辑（FR-D2），与新对话路径行为完全一致

### 2.3 任务清单

- **A-1**：读 `chat.py` 完整（新对话路径 + 续对话路径），确认两处 dispatch_metadata 构造上下文
- **A-2**：定义 `LONG_PROMPT_THRESHOLD = 2000`（选择放置位置：chat.py 顶部 or settings.py）
- **A-3**：新对话路径注入（FR-D1）：在 `dispatch_metadata` dict 构造后、`encode_runtime_context` 之前加条件写入
- **A-4**：续对话路径注入（FR-D2）：同样位置，相同逻辑
- **A-5**：单测（AC-D1/D2/D3）：
  - 新对话路径 len(message) > 2000 → dispatch_metadata["force_full_recall"] = True
  - 新对话路径 len(message) <= 2000 → dispatch_metadata 不含 force_full_recall
  - 续对话路径同等两 case
  - 验证 orchestrator._with_delegation_mode 后 runtime_context.force_full_recall = True（mock orchestrator，不跑 LLM）
  - 验证 is_recall_planner_skip 返回 False（spec ref AC-D1：`runtime_control.py:106-124`）
- **A-5b（Codex M1 修订）**：跨语言测试矩阵 — len(message) 是 Unicode 字符数（GATE_DESIGN 决议），但中/英/代码密度不同。
  - 测试 case：2000 中文字符 / 2000 英文字符 / 2000 字符代码块 / 2000 字符 JSON / 短中文+长 stack trace
  - 验证：均触发 force_full_recall=True；记录单元测试中各类输入的"假阳/假阴"边界情况，写入 phase-0-recon.md 阈值局限段
  - **不修改 baseline 行为**——本测试仅证明阈值合理性 + 留下后续调参（F102 或 attention model）数据基础
- **A-6**：commit（feat: F101-Phase-A）

### 2.4 验证策略

- 单测：mock ChatSendRequest 构造，spy dispatch_metadata 写入
- mock-based：不运行 LLM；验证 orchestrator._with_delegation_mode 路径接收 force_full_recall hint
- 全量回归：≥ 1469（F100 subset baseline），0 regression

### 2.5 Codex review 时机

Phase A commit 后 foreground review，重点：
- 两路径注入是否对称（新对话 vs 续对话）
- LONG_PROMPT_THRESHOLD 常量位置是否合理
- 长度计算是否正确（Unicode 字符数 `len(message)` 而非字节数）
- 是否影响 baseline 短 prompt 行为

---

## 3. Phase B — ApprovalGate SSE 接入 + escalate_permission 状态机 + 超时修复（块 C-1/C-2/C-3）联合 Phase

### 3.1 为什么联合（不可拆分）

spec §10 第 2 条（spec 行 461）：**FR-C1 + FR-C2 + FR-C3 必须在同一 Phase 联合实施，不允许各自独立验收**。

理由（spec §9 风险 R5，tech-research 风险 5，tech-research 行 340-342）：
- C-2（sse_push_fn 注入）完成后若 C-1（状态机）未完成 → production 路径 SSE 能推但状态机不正确 → 危险的半工作状态
- C-3（超时修复）依赖 C-1 状态机正确后才能验证超时路径
- 三者共同构成 WAITING_APPROVAL 状态机完整性的联合组件

### 3.2 目标

1. **FR-C2**：ApprovalGate.sse_push_fn 在 `octo_harness._bootstrap_capability_pack` 构造时注入真实推送函数（非 None）
2. **FR-C1**：escalate_permission_handler 在 production 中真正进入 WAITING_APPROVAL 状态（不再静默降级 "rejected"）
3. **FR-C3**：`task_runner.py:779` 超时监控修复，ApprovalGate 超时后 task_runner 能感知并走 FAILED 终态

### 3.3 FR-C2 实现方案（依赖 Phase 0 R1 结论）

**前提确认**（Phase 0 必须完成 R1）：

若 Phase 0 确认 SSEHub 已有 `broadcast_to_session(session_id, payload)` → 使用选项 1（就地闭包，tech-research 行 190-212，spec ref-6）：

```
# octo_harness.py:700-703 修改点
_sse_hub = app.state.sse_hub  # _bootstrap_runtime_services 已完成，此时已可用（tech-research 行 104）
async def _sse_push(session_id: str, payload: dict) -> None:
    if _sse_hub:
        await _sse_hub.broadcast_to_session(session_id, {"type": "approval_sse", **payload})
_approval_gate = ApprovalGate(event_store=..., task_store=..., sse_push_fn=_sse_push)
```

若 Phase 0 确认 SSEHub 只有 `broadcast(task_id, event)` → 需在 SSEHub 新增 `broadcast_to_session` 方法（增量改动，不影响 Phase B 整体策略）。

**注意 bootstrap 顺序约束**（spec §3 Edge Cases 行 136，tech-research 行 104）：`_bootstrap_runtime_services`（SSEHub 初始化，`octo_harness.py:420-421`）先于 `_bootstrap_capability_pack`（ApprovalGate 构造，`octo_harness.py:694-709`）执行，因此 `app.state.sse_hub` 在 ApprovalGate 构造时已可用。

### 3.4 FR-C1 实现方案

接入点：`ask_back_tools.py:362-444`（`escalate_permission_handler`，spec ref-3，tech-research 行 50-64）

当 `approval_gate is not None`（FR-C2 完成后 production 路径不为 None）：
- 调用 `approval_gate.request_approval(...)` → 触发 SSE push（sse_push_fn 已注入）
- 调用 `approval_gate.wait_for_decision(timeout=300)` → task 进入 WAITING_APPROVAL（状态机转移）
- 不修改 `approval_gate is None` 的降级路径（Constitution C6，spec FR-C1 前提行 173）

task_runner.py 层面：
- `task_runner.py:404-406`（spec ref-2，tech-research 行 39-41）：WAITING_APPROVAL 分支需要从 `return` 改为适当处理（配合 FR-C3 超时修复）

### 3.5 FR-C3 超时修复方案（依赖 Phase 0 超时分析）

接入点：`task_runner.py:779`（超时监控 continue 跳过，tech-research 行 15，spec ref-2）

修复策略（最小侵入）：
- 超时监控检测 WAITING_APPROVAL 状态下是否已超时
- 超时后：调用 ApprovalGate 或直接 task_runner 推进任务走 FAILED 终态
- 与 `wait_for_decision` 的 300s timeout 配合（具体机制 Phase 0 实测 `approval_gate.py:wait_for_decision` 后确定）

### 3.6 任务清单

- **B-1**：读 `approval_gate.py` 完整（确认 `request_approval` + `wait_for_decision` 完整签名）
- **B-2**：依 Phase 0 R1 结论，实现 sse_push_fn 闭包（FR-C2）；若需新增 SSEHub 方法先新增
- **B-3**：修改 `octo_harness.py:700-703`，ApprovalGate 构造时注入 sse_push_fn（FR-C2）
- **B-4**：读 `ask_back_tools.py:362-444`，分析 `approval_gate is None` 降级路径与主路径分支结构
- **B-5**：修改 `escalate_permission_handler`，production 路径（approval_gate 非 None）真正进入 WAITING_APPROVAL（FR-C1）
- **B-6**：依 Phase 0 超时分析，修复 `task_runner.py:779` 超时监控（FR-C3）
- **B-7**：同步处理 `task_runner.py:404-406` WAITING_APPROVAL 分支（与 FR-C3 联动）
- **B-8**：startup_recovery 路径修复（FR-C6，N-H1 PARTIAL）：`task_runner.py:438-448` 补充 is_caller_worker_signal 读取（从 CONTROL_METADATA_UPDATED 历史事件中恢复）
- **B-9**：单测（AC-C1/C2/C3/C6）：
  - AC-C2：mock octo_harness bootstrap，验证 ApprovalGate.sse_push_fn 不为 None
  - AC-C1：mock approval_gate（非 None），escalate_permission_handler → task 状态变为 WAITING_APPROVAL
  - AC-C3：mock ApprovalGate.wait_for_decision 超时返回 "rejected"，验证 task_runner 走 FAILED 终态 + reason 字段（FR-C3b）
  - AC-C6：mock startup_recovery，验证 is_caller_worker_signal 从 CONTROL_METADATA_UPDATED 事件恢复
- **B-9b（Codex H2 修订）**：竞态测试（task_runner 是状态机 owner）：
  - 场景 1：approve callback 与 wait_for_decision timeout 并发触发 → 仅 1 个终态 event 写入（compare-and-set）
  - 场景 2：task_runner monitor 与 wait_for_decision 同时触发 timeout → 仅 1 个 FAILED 终态
  - 场景 3：FAILED 终态后到达 late approve callback → callback 被忽略（无状态机重入）
- **B-9c（Codex H1 修订）**：**service-layer integration test**（联合验收门核心，**不是 mock**）：
  - 真实构造 SSEHub + 真实 ApprovalGate（含 sse_push_fn 闭包）+ 真实 task/event store
  - 触发 escalate_permission_handler → 断言 SSEHub 对应 session 收到 approval SSE event
  - 若 R1 实测 SSEHub 仅 task_id 广播 → 测试覆盖 session_id ↔ task_id 映射路径
- **B-9d（FR-C3b 修订）**：approval_timeout_seconds 配置覆盖测试：
  - USER.md 写 `approval_timeout_seconds: 60` → wait_for_decision 使用 60s 超时
  - USER.md 无该字段 → fallback 300s
- **B-10**：e2e_smoke 验证（1x 循环，确认 bootstrap 未破坏）
- **B-11**：commit（feat: F101-Phase-B）

### 3.7 联合验收门（六者必须同时通过）

1. ApprovalGate.sse_push_fn 在 bootstrap 后不为 None（AC-C2 pass）
2. mock approval_gate 路径下 escalate_permission_handler → task WAITING_APPROVAL（AC-C1 pass）
3. mock 超时场景下 task_runner 走 FAILED 终态 + reason 字段（AC-C3 pass，含 FR-C3b reason）
4. **service-layer integration test（H1 修订）**：真实 SSEHub + 真实 ApprovalGate + 真实 task store 链路下 escalate_permission → SSE event 推送成功（B-9c）
5. **竞态测试（H2 修订）**：approve-vs-timeout / late approve / monitor + wait_for_decision 三场景下任务终态唯一性（B-9b）
6. startup_recovery 路径 is_caller_worker_signal 正确恢复（AC-C6 pass）

六者均通过后才允许 commit，不允许部分通过。**B-9c integration test 是核心 gate**——mock-only 验收（仅 1-3）不足以证明 production 链路工作。

### 3.8 Codex review 时机

Phase B commit 后 foreground review，重点：
- FR-C1 + FR-C2 + FR-C3 三者是否真实联合（不是各自独立的半工作状态）
- sse_push_fn 闭包的 session_id 来源是否正确（approval_gate.request_approval 如何传入 session_id）
- 超时修复是否覆盖所有超时场景（wait_for_decision timeout + task_runner monitor）
- startup_recovery 恢复逻辑是否与 attach_input 路径对称

---

## 4. Phase C — Notification 主体扩展 + quiet hours（块 B）

### 4.1 目标

1. **FR-B2**：NotificationService 新增四级优先级模型（approval_pending > worker_failed > worker_long_running > worker_completed）
2. **FR-B3/B4**：quiet hours 解析（USER.md `active_hours` 字段）+ 过滤决策
3. **FR-B1**：WAITING_APPROVAL 通知触发（依赖 Phase B FR-C1/C2/C3 完成）
4. **FR-B5/B6**：dismiss 幂等 + Worker 完成精确一次推送

### 4.2 依赖说明

FR-B1（WAITING_APPROVAL 通知）必须在 Phase B 完成后实施（spec §10 第 3 条，行 461）：状态机正确后，WAITING_APPROVAL 进入才有意义触发通知。

### 4.3 优先级模型设计

接入点：`notification.py:92`（NotificationService 类，spec ref-1，tech-research 行 20-32）

扩展方案：
- 新增 `NotificationPriority` 枚举：`CRITICAL = "approval_pending"` / `HIGH = "worker_failed"` / `MEDIUM = "worker_long_running"` / `LOW = "worker_completed"`
- 或在现有 notify 方法 signature 中加 `priority: str` 参数
- quiet hours filter：`_is_quiet_hours(now: datetime, active_hours: str | None) -> bool`

### 4.4 quiet hours 解析设计

SoT：USER.md `active_hours` 字段（`core/behavior_templates/USER.md:22`，tech-research 行 369）

格式规范：`"HH:MM-HH:MM"`（如 `"09:00-23:00"`），左闭右开区间 `[start, end)`（clarify 自动澄清）

解析函数：
- `_parse_active_hours(raw: str | None) -> tuple[time, time] | None`
- None 或格式非法 → 返回 None → 全时段推送（AC-B4，spec FR-B3）
- 解析成功 → 返回 `(start_time, end_time)` → quiet hours 为 `[end_time, start_time + 24h)` 的补集

读取机制：
- NotificationService 通过 user_profile 读 USER.md（F084 user_profile.update/read 机制，spec §7 依赖表）
- 不引入独立数据存储（FR-B4）

过滤决策：
- `priority == "approval_pending"` → 不过滤（始终推送，spec AC-B2）
- 其他优先级 + quiet hours 内 → 过滤，不发送（spec AC-B3）

### 4.5 dismiss 幂等实现

依据 Phase 0 实测（WARN-4 修复）：

若 NotificationService 已有 `_sent_notifications` 内存 set → 复用该 set 实现 dismiss 幂等：
- dismiss 操作：将通知 ID 加入已处理 set
- 重复 dismiss：set.add 是幂等操作，不报错（AC-B6）
- GATE_DESIGN 决议选 A（dismiss 跨通道同步 → Web 下次刷新反映，不做实时 SSE 推送）

跨通道共享：NotificationService 维护单一共享 dismissed set（Web + Telegram 均通过 NotificationService 处理）

### 4.6 WAITING_APPROVAL 通知接入（FR-B1）

接入点：`task_runner.py:404-406`（spec ref-2，tech-research 行 39-41）

Phase B 已修复此处（WAITING_APPROVAL 分支从直接 return 改为适当处理），Phase C 在此基础上增加：
- `notification_service.notify_approval_request(...)` 调用（若 Phase 0 确认 notification_service 已注入）
- 通知类型：`priority="approval_pending"`，触发 quiet hours 检查（always pass，critical 级别）

### 4.7 任务清单

- **C-1**：读 `notification.py` 完整（SSENotificationChannel + TelegramNotificationChannel + NotificationService）
- **C-2**：新增 `NotificationPriority` 枚举（或 priority 字段），扩展 `notify_task_state_change` / `notify_approval_request` 接口（FR-B2）
- **C-3**：实现 `_parse_active_hours` + `_is_quiet_hours` 方法（FR-B3）
- **C-4**：NotificationService 通知推送前增加 quiet hours 过滤（FR-B3），approval_pending 强制通过
- **C-5**：USER.md `active_hours` 字段结构化（在 USER.md 模板中新增标准格式注释，用于 user_profile.update 写入引导）
- **C-6**：FR-B1 WAITING_APPROVAL 通知接入（依赖 Phase B 完成）：在 task_runner WAITING_APPROVAL 分支调用 notification_service
- **C-7**：dismiss 幂等机制（FR-B5）：依 Phase 0 实测方案，在 NotificationService 维护共享 dismissed set（**by notification_id**，FR-B8）
- **C-7b（Codex H3 修订）**：Telegram callback ingress 接入：
  - 定位现有 Telegram bot callback handler（或在 Phase C 新建）
  - 接入 `notification_service.dismiss(notification_id, source="telegram")`
  - inline keyboard "dismiss" 按钮 callback 真实工作
- **C-7c（Codex H3 修订）**：Web notification list/refresh API：
  - 定位现有 Web notification API（或在 Phase C 新建 `GET /api/notifications?session_id=...`）
  - 实现 `notification_service.list_active(session_id)` 自动过滤 dismissed notification_id
  - integration test 覆盖："Telegram dismiss → Web refresh 不返回该 notification"
- **C-8（FR-B8 修订）**：实现 `generate_notification_id(task_id, notification_type, state_transition_event_id) -> str`（SHA256 前 16 位），所有 notify 调用必须使用该函数生成 id
- **C-9（H4 修订）**：quiet hours discard 语义：被过滤通知**仍写 event_store**（保留审计链），channel push 丢弃，不补发
- **C-10**：FR-B6 精确一次推送验证（**event_store 按 notification_id 去重一次** + active hours 内 channel push 一次）
- **C-11**：FR-B7 attention_work_count 更新：WorkerRuntime dispatch 开始 +1 / 任务终态 -1（SHOULD 级别，**含独立 spy assert，Codex HIGH-02 修订**）
- **C-12**：单测（AC-B1/B2/B3/B4/B5/B6 + 新增 M4 三场景）：
  - AC-B1：mock task_runner 终态 → event_store 写入通知事件一次（按 notification_id 去重）+ channel push 一次（active hours）
  - AC-B2：approval_pending + quiet hours → 通过 filter，channel push 成功；event_store 写入一次
  - AC-B3：worker_completed + quiet hours → channel push 拦截（**discard 不补发**），event_store 仍写入一次
  - AC-B4：USER.md active_hours 为空 → 无过滤，全时段推送
  - AC-B5：task 进入 WAITING_APPROVAL → notify_approval_request 被调用，AC-C2 已注入 sse_push_fn
  - AC-B6：同一 notification_id 两次 dismiss → 第二次返回成功，不报错
  - **M4-1**：同一 task 不同 transition（WAITING_APPROVAL 进入 vs FAILED 终态）→ 不同 notification_id
  - **M4-2**：同一 transition 重试 → 同 notification_id，event_store 去重为一条
  - **M4-3**：dismiss 一个 approval notification → 后续 completion notification 不受影响（不同 id）
  - **H3-test**：Telegram callback dismiss → 同 session 的 list_active 不返回该 id
- **C-13**：commit（feat: F101-Phase-C）

### 4.8 Codex review 时机

Phase C commit 后 foreground review，重点：
- quiet hours 边界计算是否正确（左闭右开 + 跨 midnight 场景）
- USER.md 解析是否 fallback 合理（格式非法 → 全时段，不抛异常）
- dismiss 幂等是否真正跨通道共享（Web + Telegram 同一 set）
- approval_pending critical 逻辑是否在 quiet hours 中豁免

---

## 5. Phase D — ask_back integration test + 顺手清（块 C-4/C-5/C-7/G + US5）

### 5.1 目标

1. **FR-C4**：ask_back 完整链路 integration test
2. **FR-C5**（SHOULD）：非 worker 路径 guard 补全（F5 PARTIAL）
3. **FR-C7**（SHOULD）：source_kinds.py `__all__` 定义
4. **US5 / User Story 5**：M-1 broad-catch 改为 log.debug（3 处：`ask_back_tools.py:194, 282, 376`，spec ref-9，tech-research 行 141-144）

### 5.2 FR-C4 integration test 规格

依据 clarify.md S-2 建议：integration test 定义为 **service layer integration test**（不跑 LLM，真实 task_runner + event_store + ask_back_tools 调用链），非 HTTP 端到端。

给定（修订后 AC-C4 Given 段，WARN-1 修复）：
- integration test 环境，Worker runtime 已 dispatch，task 处于 RUNNING 状态
- mock TaskStore 和 EventStore 已初始化
- ask_back_handler 通过真实 service 调用（不 mock ask_back_tools 内部）

验收路径：
- ask_back_handler 执行 → `CONTROL_METADATA_UPDATED` emit → task 状态 WAITING_INPUT → attach_input → resume → RUNNING 恢复
- 完整事件链通过 EventStore 查询验证（非纯 mock assert）

### 5.3 M-1 broad-catch 修复（3 处）

接入点（tech-research 行 141-144，spec ref-9）：
- `ask_back_tools.py:194`：`except Exception: pass` → `except Exception as exc: log.debug("guard failed: %s", exc)`
- `ask_back_tools.py:282`：同上
- `ask_back_tools.py:376`：同上

降级行为保持不变（修复前后：guard 失败时工具仍按原降级路径执行，spec §2 US5 AC-A2）

### 5.4 FR-C5 非 worker 路径 guard（F5 PARTIAL 补全）

接入点：`ask_back_tools.py:182-195`（spec ref-4，tech-research 行 68-78）

当前逻辑：`if getattr(_guard_ctx, "is_caller_worker", False):` → 非 worker 路径 guard 完全跳过

修复方案（最小侵入）：
- 选项 A（实施）：non-worker 路径同样检查 RUNNING 状态，非 RUNNING 时 `log.debug("non-worker guard: task not RUNNING, skipping")`，功能降级与 worker 路径一致
- 或 AC-C5 选项 B（显式记录 skipped）：log.debug 记录"guard skipped for non-worker path"，不修改功能逻辑

具体实现方案在 Phase D 实测后确定（SHOULD 级别）

### 5.5 任务清单

- **D-1**：修复 3 处 M-1 broad-catch（ask_back_tools.py:194/282/376）→ log.debug
- **D-2**：FR-C5 非 worker 路径 guard 补全（SHOULD，选方案后实施）
- **D-3**：source_kinds.py 新增 `__all__`（FR-C7，11 个符号，tech-research 行 154-160，spec ref-10）
- **D-4**：FR-C4 integration test（新文件 `tests/services/test_f101_ask_back_integration.py`）：
  - 测点 1：ask_back 完整链路（CONTROL_METADATA_UPDATED → WAITING_INPUT → attach_input → RUNNING）
  - 测点 2：CONTROL_METADATA_UPDATED 事件链完整（can query from EventStore）
  - 测点 3：resume 后 is_caller_worker_signal 正确（与 Phase B FR-C6 联动验证）
- **D-5**：AC-C5 guard 单测（验证非 worker 路径 guard 新行为）
- **D-6**：AC-C7 style 验证（`from source_kinds import *` → 只导出 11 个符号）
- **D-7**：commit（feat/fix: F101-Phase-D）

### 5.6 Codex review 时机

Phase D commit 后 foreground review，重点：
- FR-C4 integration test 是否真实（service layer 真实调用，非纯 mock）
- 3 处 M-1 修复是否完整（不遗漏 request_input:282 和 escalate_permission:376）
- `__all__` 符号数量是否正确（11 个）

---

## 6. Phase E — D8 顺手清（块 E）

### 6.1 目标

**FR-E1**（SHOULD）：ControlPlaneService 构造时新增 `notification_service` 参数，支持 Notification 集成到 control_plane 路径（D8 实测结论：显式 DI 最佳实践，tech-research 行 288，spec ref-12）

### 6.2 评估

依据 tech-research §A-5（行 266-310，spec ref-12）：D8 实测发现 ControlPlaneService 已是显式 DI 构造模式（14 参数），并非"隐性耦合"——问题仅是参数数量多。F101 如需 Notification/Attention Model 集成到 control_plane，最简路径是在现有参数中加 `notification_service`。

**实施前提**：Phase C NotificationService 已就位，评估是否需要 control_plane 路径感知通知。若 Phase C 已通过 task_runner 路径覆盖所有通知场景，Phase E 可 downgrade 为"不实施"（AC-E1 豁免）。

### 6.3 任务清单

- **E-1**：读 `_coordinator.py:93-109`（ControlPlaneService.__init__），确认是否需要 notification_service 参数
- **E-2**（条件实施）：在 `ControlPlaneService.__init__` 加 `notification_service: NotificationService | None = None` 参数；更新 `octo_harness.py:1017-1031` 构造调用
- **E-3**：AC-E1 单测（若实施）：验证 ControlPlaneService 构造时 notification_service 作为显式参数传入
- **E-4**：commit（chore: F101-Phase-E）

### 6.4 Codex review 时机

Phase E commit 后 foreground review（若 E-2 实施）或与 Phase D review 合并（若 Phase E 降级为不实施）

---

## 7. Phase F — AC-F1 验证 + Final 准备（块 F）

### 7.1 目标

**AC-F1（选 C）**：验证 ask_back resume 后 turn N+1 是 baseline 行为（full recall），系统不报错，任务正常继续。重点是验证 F101 其他改动没有意外破坏此路径。

**AC-F1 可量化验证**（WARN-1 修复后 Then 段）：
- spy `is_recall_planner_skip` 返回值 → resume 后 return False（跑 full recall）是预期结果
- 验证 task 从 WAITING_INPUT → resume → RUNNING，无异常
- 不需要修改任何代码（选 C = 保持 baseline）

### 7.2 任务清单

- **F-1**：读 `runtime_control.py:106-124`（is_recall_planner_skip，spec ref AC-D1），确认 force_full_recall=False 时 unspecified → return False 路径
- **F-2**：单测 AC-F1：mock ask_back resume 场景，spy is_recall_planner_skip → 验证 return False（full recall），任务正常继续
- **F-2b（Codex M2 修订）**：多轮 ask_back loop 测试：
  - 2-3 轮连续 ask_back → resume → ask_back，验证不重复执行已完成的 ask_back/request_input/escalate_permission 工具意图
  - trace 中含 `resume_after_user_input_full_recall_expected` 标记（或类似 explicit reason），明确表达"非 bug"
  - 记录 full recall 耗时指标对比 baseline（避免性能回退被伪装成 baseline 行为）
- **F-3**：全量回归（vs F099 baseline 3450 passed），确认 0 regression
- **F-4**：e2e_smoke 5x 循环
- **F-5**：产出 Final cross-Phase review 输入文档（所有 Phase commit diff 汇总）
- **F-6**：commit（test: F101-Phase-F）

### 7.3 Codex review 时机

Phase F commit 后进行 **Final cross-Phase Codex review**（合并 Phase F review + Final review，foreground）

---

## 8. Phase Final — Codex Final review + completion-report + handoff

### 8.1 目标

完成 F101 所有验收 + Final review 闭环 + 产出 F102 handoff

### 8.2 任务清单

- **Final-1**：**Final cross-Phase Codex review**
  - 输入：spec.md + plan.md + Phase A/B/C/D/E/F 全部 commit diff
  - 范围：是否漏 Phase / 偏离计划 / Phase B 联合验收是否真实 / 3 个 HIGH AC（AC-C1/C2/C3）是否真闭环
  - 预留 re-review 时间（F099 教训：Final review 可能抓新 HIGH）
- **Final-2**：处理 Codex finding
  - HIGH：当 Phase 修复 + re-review
  - MEDIUM：处理或归档 F102
  - LOW：ignored，commit message 列出
- **Final-3**：产出 `completion-report.md`：
  - 实际 vs 计划 Phase 对照表（Phase 0/A/B/C/D/E/F/Final）
  - Codex finding 闭环表（per-Phase + Final + re-review）
  - 测试通过数：F099 baseline 3450 → F101 final N，0 regression
  - Phase 跳过显式归档（如 Phase E 降级）
- **Final-4**：产出 `handoff.md`（给 F102 Proactive Followup）：
  - §1 F101 落地状态（NotificationService 优先级模型 + quiet hours + ApprovalGate SSE）
  - §2 F102 可直接复用的接入点（NotificationService.notify_heartbeat + WorkerRuntime dispatch 信号）
  - §3 已知 deferred 项（若有）
- **Final-5**：commit（docs: F101 completion-report + handoff + Codex Final review）

### 8.3 退出条件（Definition of Done）

- [ ] Phase A/B/C/D/E/F/Final 全部 commit + 回归门通过
- [ ] 全量回归 ≥ 3450 passed，0 regression vs F099 baseline
- [ ] e2e_smoke 5x 循环全 PASS
- [ ] Codex Final cross-Phase review 0 HIGH 残留
- [ ] AC-C1/C2/C3 联合 Phase B 联合验收通过（三者同时 pass）
- [ ] AC-F1 验证（选 C：is_recall_planner_skip spy 确认 return False）
- [ ] completion-report.md + handoff.md 已产出
- [ ] 不 push origin/master（等用户拍板）

---

## 9. Phase 顺序总表

| Phase | ID | 主要 FR/AC | 入口 commit | 依赖 | Codex review 时机 |
|-------|----|-----------|------------|------|-------------------|
| 侦察 + spec 修订 | **Phase 0** | 风险 R1/R3 实测 + 4 WARN 修复 | 182e9ed | — | 不需要 |
| force_full_recall Producer | **Phase A** | FR-D1/D2/D3；AC-D1/D2/D3 | Phase 0 commit | Phase 0 R1 结论不阻塞 Phase A | per-Phase A，foreground |
| ApprovalGate SSE + 状态机 + 超时（联合） | **Phase B** | FR-C1/C2/C3/C6；AC-C1/C2/C3/C6 | Phase A commit | Phase 0 R1/R3 必须完成 | per-Phase B，foreground |
| Notification 主体 + quiet hours + dismiss | **Phase C** | FR-B1/B2/B3/B4/B5/B6/B7；AC-B1~B6 | Phase B commit | Phase B（AC-C1/C2 完成后 FR-B1 才接入）| per-Phase C，foreground |
| ask_back integration test + 顺手清 | **Phase D** | FR-C4/C5/C7；US5；AC-C4/C5/C7 | Phase C commit | Phase B（AC-C4 需 WAITING_INPUT 路径工作）+ Phase C（建议按顺序，技术上非强制）| per-Phase D，foreground |
| D8 顺手清（条件实施）| **Phase E** | FR-E1；AC-E1（条件）| Phase D commit | Phase C | per-Phase E or 合并 Phase D review |
| AC-F1 验证 + Final 准备 | **Phase F** | AC-F1；全量回归；e2e_smoke 5x | Phase E commit | 所有 Phase 完成 | 合并 Final review |
| Final review + 文档 | **Phase Final** | Codex Final + completion-report + handoff | Phase F commit | Phase F | Final cross-Phase review（foreground）+ re-review |

**关键约束**：
- Phase B（联合 Phase）内 FR-C1 + FR-C2 + FR-C3 + FR-C6 不可拆分（spec §10 行 461 已更新含 FR-C6，risk R5；FR-C6 startup_recovery 与 C-1 task_runner.py 状态机同源，GATE_DESIGN 决议 F101 实施时并入联合范围）
- Phase C（FR-B1）必须在 Phase B 之后（spec §10 第 3 条，行 462）
- Phase D（FR-C4 integration test）必须在 Phase B 之后（需真实 approval_gate 状态机工作）
- Phase A 独立，可与 Phase 0 实测结论不依赖时先行（Phase 0 R1 不影响 chat.py producer）

---

## 10. Codex Review 计划

| Review 类型 | 时机 | 范围 | 模式 |
|-------------|------|------|------|
| **pre-impl review** | **plan 完成后立即**（本 plan.md commit 后）| spec.md + plan.md 整体；重点：Phase B 联合合理性 + Phase 顺序依赖 | foreground |
| per-Phase A review | Phase A commit 后 | chat.py 两路径注入 + threshold 常量 | foreground |
| per-Phase B review | Phase B commit 后 | FR-C1/C2/C3 联合验收真实性 + 超时机制 + FR-C6 实现 | foreground |
| per-Phase C review | Phase C commit 后 | quiet hours 边界 + dismiss 幂等 + approval_pending 豁免逻辑 | foreground |
| per-Phase D review | Phase D commit 后 | FR-C4 integration test 真实性 + 3 处 M-1 修复完整性 | foreground |
| per-Phase E review | Phase E commit 后（若实施）| ControlPlaneService 参数扩展 + octo_harness 构造调用 | foreground（或合并 Phase D）|
| **Final cross-Phase review** | Phase F commit 后 | 全 Phase commit diff + 联合 Phase B 三者真实闭环验证 + 漏 Phase 检查 | foreground |
| re-review（预备）| Final review 抓 HIGH 修复后 | 修复范围 | foreground |

**强制规则来源**：CLAUDE.local.md §Codex Adversarial Review 强制规则——spec/plan 大改后必走 pre-impl review；每 Phase implement 完成后必走；重大架构变更（Phase B 状态机改造）commit 前必走；Final cross-Phase review 最后一 Phase 前必走。

---

## 11. 风险联合控制

### 11.1 Phase B 联合 Phase 风险（最高优先）

| 风险 | 严重度 | 来源 | 缓解 |
|------|--------|------|------|
| C-1 和 C-2 强耦合（spec §9 R5，tech-research 行 340-342）| HIGH | 分 Phase 独立验证危险 | Phase B 联合实施，三者同时验收（AC-C1 + AC-C2 + AC-C3 同时 pass 才 commit）|
| WAITING_APPROVAL 超时修复复杂度超预期（spec §9 R2，tech-research 行 324-326）| HIGH | task_runner.py:779 continue 跳过 + ApprovalGate 回调联动 | Phase 0 提前实测超时机制；Phase B 设计最小侵入方案；预留额外时间 |
| SSEHub.broadcast per-session_id 能力缺失（spec §9 R1，tech-research 行 316-320）| MED | 需 Phase 0 确认 | Phase 0 R1 侦察必须在 Phase B 开始前完成；若需新增方法，Phase B 内先新增 SSEHub 方法 |

### 11.2 其他风险

| 风险 | 严重度 | 来源 | 缓解 |
|------|--------|------|------|
| NotificationService 未绑定到 task_runner（spec §9 R3）| MED | tech-research 风险 3 明确未确认 | Phase 0 R3 实测；若未注入，Phase C 增加构造函数修改（复杂度可接受，octo_harness 一处构造）|
| USER.md active_hours 格式解析复杂度（spec §9 R4，tech-research 行 334-337）| MED | 自由文本字段 | Phase C 只处理 `"HH:MM-HH:MM"` 格式，非法值 fallback 全时段推送（AC-B4 兜底）|
| LONG_PROMPT_THRESHOLD 阈值不合理（spec §9 R6）| LOW | 过低 → full recall 频繁 | 默认 2000 Unicode 字符（clarify 自动澄清）；F100 perf 基准（phase-g-perf-report.md）确认 recall planner 入口延迟在可接受范围 |

---

## 12. 技术上下文

**语言/版本**：Python 3.12+
**主要依赖**：FastAPI + Pydantic + Uvicorn + SSE（无新增外部依赖，spec §11）
**存储**：SQLite WAL（EventStore + TaskStore）；dismiss 幂等内存 set（重启后不保留，已知限制）
**测试**：pytest（unit + service layer integration test；不含 Telegram 真实 e2e）
**新增/修改组件**：5 个（notification.py + approval_gate.py + task_runner.py + octo_harness.py + chat.py）
**目标平台**：Linux server（OctoAgent gateway 进程）

---

## 13. 项目结构

### 制品目录

```text
.specify/features/101-notification-attention/
├── plan.md                    # 本文件
├── research/
│   └── tech-research.md       # 块 A 实测侦察（182e9ed baseline）
├── clarify.md                 # GATE_DESIGN 决议
├── checklist.md               # 质量检查表
├── phase-0-recon.md           # Phase 0 产出（6 项实测结论）
├── completion-report.md       # Phase Final 产出
└── handoff.md                 # Phase Final 产出（给 F102）
```

### 核心改动文件

```text
octoagent/apps/gateway/src/octoagent/gateway/
├── routes/
│   └── chat.py                        # Phase A：force_full_recall producer（行 422-444, 479-493）
├── harness/
│   ├── octo_harness.py                # Phase B：ApprovalGate sse_push_fn 注入（行 700-703）
│   └── approval_gate.py               # Phase B：（可能）sse_push_fn 相关方法
│   └── sse_hub.py                     # Phase B：（可能）新增 broadcast_to_session 方法
├── services/
│   ├── notification.py                # Phase C：优先级模型 + quiet hours + dismiss（行 92-485）
│   ├── task_runner.py                 # Phase B：超时修复（行 779）+ startup_recovery（行 438-448）
│   │                                  # Phase C：WAITING_APPROVAL 通知（行 404-406）
│   ├── builtin_tools/
│   │   └── ask_back_tools.py          # Phase D：M-1 broad-catch（行 194, 282, 376）+ FR-C5 guard
│   └── control_plane/
│       └── _coordinator.py            # Phase E（条件）：notification_service 参数

octoagent/packages/core/src/octoagent/core/
├── behavior_templates/
│   └── USER.md                        # Phase C：active_hours 字段结构化注释（行 22）
└── models/
    └── source_kinds.py                # Phase D：新增 __all__（FR-C7）

tests/
├── services/
│   └── test_f101_ask_back_integration.py  # Phase D 新建（FR-C4 integration test）
├── test_f101_notification.py              # Phase C 新建（AC-B1~B6）
├── test_f101_force_full_recall.py         # Phase A 新建（AC-D1~D3）
└── test_f101_approval_gate.py             # Phase B 新建（AC-C1~C3/C6）
```

---

## 14. Constitution Check

| 宪法原则 | 适用性 | 评估 | 说明 |
|---------|--------|------|------|
| C1 Durability First | 适用 | PASS | dismiss 状态内存 set（重启丢失）是已知限制，不阻塞 F101；持久化 dismiss 可 F107 补强 |
| C2 Everything is an Event | 适用 | PASS | notification 触发需记录 EventStore 事件（AC-B1 已要求 event_store 记录）|
| C3 Tools are Contracts | 适用 | PASS | approve_gate 工具接口不变；NotificationService 新增方法但不修改现有接口 |
| C4 Side-effect Must be Two-Phase | 适用 | PASS | escalate_permission → ApprovalGate → 用户审批 = Plan→Gate→Execute 三阶段合规 |
| C5 Least Privilege by Default | 适用 | PASS | sse_push_fn 闭包内只调用 SSEHub，不暴露 secrets |
| C6 Degrade Gracefully | 适用 | PASS | approval_gate is None 时 escalate_permission 降级路径保留（spec FR-C1 前提）|
| C7 User-in-Control | 适用 | PASS | F101 主责：修复 escalate_permission 审批门禁，让 C7 真正生效（F3 HIGH 修复核心动机）|
| C8 Observability is a Feature | 适用 | PASS | M-1 broad-catch 修复（Phase D）+ notification EventStore 记录（Phase C）|
| C9 Agent Autonomy | N/A | PASS | NotificationService 是基础设施层，不涉及 LLM 决策路径 |
| C10 Policy-Driven Access | 适用 | PASS | approval_gate 权限判断走现有 ApprovalGate 路径，不在 escalate_permission 工具层自行做权限拦截 |

无 VIOLATION，无豁免需求。

---

## 15. Impact Assessment

| 维度 | 值 |
|------|-----|
| **直接修改文件数** | 8（chat.py / octo_harness.py / approval_gate.py / sse_hub.py(可能) / notification.py / task_runner.py / ask_back_tools.py / source_kinds.py）|
| **新增测试文件数** | 4（test_f101_force_full_recall.py / test_f101_approval_gate.py / test_f101_notification.py / test_f101_ask_back_integration.py）|
| **间接受影响文件** | _coordinator.py（条件）/ USER.md 模板 / worker_runtime.py（FR-B7 attention_work_count）|
| **跨包影响** | 是：`apps/gateway`（services/harness/routes）+ `packages/core`（models/behavior_templates）|
| **数据迁移** | 否（无 schema 变更；dismiss 内存 set 无需迁移）|
| **API/契约变更** | 是：ApprovalGate sse_push_fn 注入（从 None → 真实函数）；NotificationService 新增优先级字段；chat.py dispatch_metadata 条件写入 force_full_recall |
| **影响文件数** | ~11-12 文件 |
| **风险等级** | **MEDIUM**（影响文件 10-12，跨包 2 处，但无数据迁移，主要是现有组件扩展而非新建）|

**HIGH 复杂度说明**：虽然 Impact Assessment 为 MEDIUM，但 spec §11 评估总体复杂度为 HIGH——原因是 WAITING_APPROVAL 状态机改造（Phase B）和跨模块耦合（5 个服务层文件）的实施难度超出单纯的影响文件数评估。按 HIGH 复杂度执行 7 Phase 计划。

---

## 16. Codebase Reality Check

### 目标文件基线状态（Phase 0 侦察前预估，以 spec/tech-research 引用为依据）

| 文件 | 估计 LOC | 关键方法/接口 | 已知 debt | Phase |
|------|---------|-------------|---------|-------|
| `chat.py` | ~500+ | 新对话路径（行 422-444）/ 续对话路径（行 479-493）| 无 TODO/FIXME（tech-research 引用精准）| Phase A |
| `octo_harness.py` | ~1100+ | `_bootstrap_capability_pack`（行 694-709）/ `_bootstrap_runtime_services`（行 420-421）| ApprovalGate sse_push_fn=None 注释"后续阶段绑定"（已知 debt）| Phase B |
| `approval_gate.py` | ~300+ | `__init__`（行 88-105）/ `request_approval`（行 222-244）/ `wait_for_decision` | sse_push_fn=None（已知 debt，F101 修复目标）| Phase B |
| `task_runner.py` | ~900+ | `_notify_completion`（行 404-406）/ `startup_recovery`（行 438-448）/ 超时监控（行 770-782）| 2 处已知 debt：WAITING_APPROVAL 无通知 + 超时监控 continue 跳过 | Phase B/C |
| `notification.py` | ~500+ | `NotificationService`（行 92-229）/ `SSENotificationChannel`（行 237-300）/ `TelegramNotificationChannel`（行 318-485）| 无优先级模型 + quiet hours 未实现（已知 debt）| Phase C |
| `ask_back_tools.py` | ~450+ | `ask_back_handler`（行 179-226）/ `request_input_handler`（行 268-283）/ `escalate_permission_handler`（行 362-444）| 3 处 M-1 broad-catch（行 194/282/376）| Phase D |
| `source_kinds.py` | ~72 | 11 个符号 | 无 `__all__`（style debt）| Phase D |
| `_coordinator.py` | ~500+ | `ControlPlaneService.__init__`（行 93-109）| 14 参数构造（复杂度 debt，非隐性耦合）| Phase E |

**前置清理规则评估**：
- `task_runner.py`：LOC 估计 > 500 且 Phase B 新增 > 50 行，且有 2 处相关 debt → **需要 Phase 0 确认实际 LOC，若触发规则则 Phase B 前置 cleanup task**
- `octo_harness.py`：LOC 估计 > 500 且 Phase B 有少量新增 → Phase 0 实测决定是否需要 cleanup

---

## 17. AC ↔ Phase 映射表

| AC | 块 | Phase 实施 | Phase 验证 | Codex review 节点 |
|----|-----|-----------|-----------|------------------|
| AC-D1（新对话 force_full_recall 注入）| D | Phase A | Phase A 单测 | per-Phase A |
| AC-D2（短消息不注入，baseline 不变）| D | Phase A | Phase A 单测 | per-Phase A |
| AC-D3（续对话路径一致）| D | Phase A | Phase A 单测 | per-Phase A |
| AC-C2（ApprovalGate sse_push_fn 非 None）| C | Phase B | Phase B 单测 | per-Phase B |
| AC-C1（escalate_permission → WAITING_APPROVAL）| C | Phase B | Phase B 单测 | per-Phase B |
| AC-C3（超时 → FAILED 终态）| C | Phase B | Phase B 单测 | per-Phase B |
| AC-C6（startup_recovery is_caller_worker 恢复）| C | Phase B | Phase B 单测 | per-Phase B |
| AC-B5（WAITING_APPROVAL → notify_approval_request）| B | Phase C | Phase C 单测 | per-Phase C |
| AC-B1（Worker 完成精确一次推送）| B | Phase C | Phase C 单测 | per-Phase C |
| AC-B2（approval_pending + quiet hours → 推送）| B | Phase C | Phase C 单测 | per-Phase C |
| AC-B3（worker_completed + quiet hours → 过滤）| B | Phase C | Phase C 单测 | per-Phase C |
| AC-B4（active_hours 为空 → 无过滤）| B | Phase C | Phase C 单测 | per-Phase C |
| AC-B6（dismiss 幂等）| B | Phase C | Phase C 单测 | per-Phase C |
| AC-C4（ask_back integration test）| C | Phase D | Phase D integration test | per-Phase D |
| AC-C5（非 worker 路径 guard）| C | Phase D | Phase D 单测 | per-Phase D |
| AC-C7（source_kinds `__all__`）| C | Phase D | Phase D style 验证 | per-Phase D |
| AC-E1（ControlPlaneService notification_service 参数）| E | Phase E（条件）| Phase E 单测（条件）| per-Phase E |
| AC-F1（ask_back resume → is_recall_planner_skip=False，选 C）| F | 无代码改动（选 C）| Phase F is_recall_planner_skip spy 单测 | Final cross-Phase |

---

## 18. 回归 / 验证策略

| 节点 | 命令 | 通过门 |
|------|------|-------|
| 每 Phase commit 后 | `pytest octoagent` | ≥ 3450 passed（vs F099 baseline），0 regression |
| 每 Phase commit 后 | `pytest -m e2e_smoke octoagent/tests/e2e/ -v` | 全 PASS |
| Phase F | `pytest octoagent` | ≥ 3450 + F101 新增测试数 |
| Phase F | e2e_smoke 5x 循环 | 5x 全 PASS |
| Final | Codex cross-Phase review 0 HIGH 残留 | — |

**注**：F100 完成后实际 passed count 为 1469（mock-based subset）；但 spec 行 9 记录 F099 实测 3450 passed 为可靠全量 baseline，F101 以 3450 为回归门。

---

## 19. 推迟项 / 已知 known issue

| 项目 | 推迟到 | 理由 |
|------|--------|------|
| FR-D4 API 显式 force_full_recall 参数 | F107 或独立 Feature | SHOULD 级别；自动检测已覆盖主要场景（clarify 自动澄清）|
| dismiss 状态持久化（重启后丢失）| F107 | F101 内存 set 是已知限制；持久化需 TaskStore 或独立表，超 F101 范围 |
| F096 Phase E frontend UI（agent 视角审计）| 独立 Feature | backend 契约已稳定（F096 已归档），UI 不阻 F101 |
| attention_work_count 完整 Attention Model 决策逻辑 | F102 | spec §6 Out of Scope 第 7 条；F101 只维护计数字段 |
| ApprovalManager SSEApprovalBroadcaster 重构 | N/A（不实施）| spec §6 Out of Scope 第 8 条；F101 使用选项 1 闭包注入 |

---

**Status**: v1.0 Draft，准备进入 pre-impl Codex review。

**Phase 数**: 7（Phase 0 / A / B / C / D / E / F / Final，其中 Phase E 条件实施）
**联合 Phase**: Phase B（FR-C1 + FR-C2 + FR-C3 + FR-C6）
**Phase 0 实测项**: 6 项（R1 SSEHub + R3 task_runner 注入 + dismiss 持久化 + attention_work_count 路径 + ApprovalGate 超时 + task_runner:779 分析）
