# F099 Ask-Back Channel + Source Generalization — Spec（v0.3 Post-Analyze）

**Feature Branch**: `feature/099-ask-back-source-generalization`
**Created**: 2026-05-11
**Status**: Draft
**M5 Stage**: 阶段 2 第 3 个 Feature（继 F098 H3-B 后）
**Upstream**: F098（已合入 master c2e97d5）/ F097（已合入 master）
**Downstream**: F100 Decision Loop Alignment
**Baseline passed count**: ≥ F098 c2e97d5（实际数取决于 Phase-0 实测）

---

## 0. GATE_DESIGN 锁定决策（2026-05-11 用户拍板）

GATE_DESIGN 已通过，下列 7 项 OD 在 plan / implement 阶段**不得偏离**。
GATE_DESIGN 用户决议汇总（4 项）：

| 决议 | 锁定结果 |
|------|----------|
| **G-1** OD-F099-1 ~ OD-F099-7 | 全部按推荐执行（B/B/B/B/A/B/A）|
| **G-2** FR-C1 (automation/user_channel 派生)| **保留 MUST**：完整 role/session_kind/agent_uri 派生（**用户 override YAGNI 推荐**——理由：F101 Notification Model 已明确依赖此基础设施，前置一并落地比 F101 再做切换便宜；§YAGNI 表 + §5 Non-Goals 已对应修订）|
| **G-3** ApprovalGate 超时 + compaction tool_call/tool_result 保护 | **前置到 plan 阶段 grep 验证**（plan agent 必须先 grep 确认假设，假设不成立时调整 spec 后重过 GATE_DESIGN）|
| **G-4** 跨 OD 命名混淆 | **plan 阶段必须写 §命名约定 章节 + 常量化**：`source_runtime_kind`（caller 身份枚举，扩 5 值）vs `control_metadata_source`（事件来源操作字符串，自由）；常量集中在 enums.py，三工具 handler 通过常量引用 |

| 决策点 | 候选 | 推荐 | 理由 |
|--------|------|------|------|
| **OD-F099-1** ask_back 事件承载方式 | A=新增 `EventType.ASK_BACK_REQUESTED` / B=复用 `CONTROL_METADATA_UPDATED` + source 字段 `"worker_ask_back"` | **B** | CONTROL_METADATA_UPDATED 已稳定，新增 EventType 增加复杂度；B 的 source 字段足以区分来源，merge_control_metadata 无需修改；且 F099 不需要"纯审计 trace"（已有 task 状态 WAITING_INPUT 审计路径） |
| **OD-F099-2** 三工具抽象层次 | A=共用 BaseAskBackDelegation（继承 BaseDelegation） / B=三工具各自独立 handler，无共享抽象 | **B** | phase-0-recon 实测：ask_back 工具的核心是调用 `execution_context.request_input()`，不走 spawn 路径，不需要 delegation_id / child_task_id 等 BaseDelegation 字段；引入抽象层反而增加无意义的复杂度（YAGNI）。BaseDelegation 适合 spawn-and-die 场景，ask_back 是"工具调用挂起"场景，语义不同 |
| **OD-F099-3** source 语义扩展方式 | A=在 A2AConversation 新增 source_type 字段（Literal 枚举）/ B=扩展 `envelope.metadata.source_runtime_kind` 枚举值 + 在 `_resolve_a2a_source_role()` 加新判断分支 | **B** | phase-0-recon 实测：A2AConversation 完全无 source_type 字段（F098 handoff 描述的是未来规划）；B 改动最小、向后兼容，仅扩展 dispatch_service.py 的一个函数；加新字段到 A2AConversation 需修改所有构造点，风险大 |
| **OD-F099-4** 三工具适用 agent kind | A=仅 kind=worker 可调用 / B=所有 agent（worker + subagent + main）均可调用 | **B** | phase-0-recon 实测：当前 broker 不区分 agent kind 做工具注册过滤；强制 kind 区分需新建 policy 控制机制（超 F099 范围）；ask_back 对主 Agent 也有意义（主 Agent 处理用户请求时可请求澄清）；kind 限制留 F107 策略层 |
| **OD-F099-5** escalate_permission 接入 Policy Engine 方式 | A=直接调用 `PolicyAction` 决策 + 现有 ApprovalGate SSE 路径 / B=新建专用 PermissionEscalationGate | **A** | ApprovalGate（`harness/approval_gate.py`）已实现完整 Plan → Gate → Execute 两阶段流程（Constitution C4 合规），符合 C10 Policy-Driven Access；B 重复造轮子；escalate_permission 本质是"请求用户批准高风险操作"，与 ApprovalGate 语义完全对齐 |
| **OD-F099-6** spawn 路径 source_runtime_kind 注入位置 | A=在 `delegation_plane.spawn_child()` 内部注入（依赖 spawned_by 字段判断）/ B=在 `delegate_task_tool` 调用 `spawn_child()` 前注入到 envelope.metadata | **B** | F098 dispatch_service 注释明确说"task_runner / capability_pack 在 spawn 阶段显式注入"——B 是 F098 已设计的扩展点；A 在 plane 层注入会让 plane 层感知 source 身份，违反职责分离；B 在工具层注入更干净 |
| **OD-F099-7** ask_back 唤醒后上下文恢复机制 | A=工具调用返回值路径（`request_input()` 返回用户文本 → broker 作为 tool_result）/ B=新增 `CONTROL_METADATA_UPDATED` 事件承载唤醒后输入 / C=新增 user_message 注入 | **A** | phase-0-recon 实测：`execution_context.request_input()` 已通过 asyncio.Queue 实现阻塞等待，返回值就是用户输入文本；broker 将此作为 tool_result 返回 LLM，turn N+1 天然看到"原工具调用 + 用户回答"——这是最自然的上下文恢复路径；B/C 增加不必要的间接层 |

---

## 1. 目标（Why）

F099 是 M5 阶段 2 第 3 个 Feature，**主责 H3-B 上行通道（Worker 主动发起交互）+ source 泛化**，同时承接 F098 已知 LOW §3（spawn 路径 source_runtime_kind 注入缺失）。

**核心问题**（来自 CLAUDE.local.md §M5 战略规划 §三条核心设计哲学）：

1. **Worker 缺乏上行通道**：Worker 处理任务时若遇到歧义、缺乏权限或需要额外输入，当前只能 fail 或 complete——无法中途暂停等待澄清，导致任务上下文丢失
2. **A2A source 语义不完整**：worker→worker dispatch 目前缺少 source_runtime_kind 注入（F098 已知 LOW §3），audit 记录中 source 始终是 main→worker，不反映真实调用链
3. **source 类型硬编码**：source 只区分 main/worker/subagent，无法表达 automation（APScheduler 调度）或 user_channel（用户直接发消息给 Worker）场景

**预期收益**：
- Worker 可以中途向 request 来源提问，等待答复后继续执行，任务上下文不丢失
- Worker 可以请求权限提升，走现有 Policy Engine + ApprovalGate 审批路径
- worker→worker dispatch audit 记录的 source 反映真实调用方，而非默认 main
- source 语义可表达 automation / user_channel 场景，为 F101（Notification + Attention Model）铺路

---

## 2. 已通项（Baseline-Already-Passed）

phase-0-recon 实测发现以下 baseline 已部分覆盖 F099 场景：

| 已通项 | 实测证据 | F099 影响 |
|--------|----------|-----------|
| **WAITING_INPUT 状态机完整** | `execution_console.py:294-310`：RUNNING → WAITING_INPUT 写入；`task_runner.py:577-622`：attach_input 唤醒路径（live + 重启两路）| 块 E 唤醒路径直接复用，无需新建 |
| **上下文恢复天然支持** | `execution_console.request_input()` 通过 asyncio.Queue 阻塞，返回值作为 tool_result | OD-F099-7 选 A，无需新建恢复框架 |
| **CONTROL_METADATA_UPDATED 稳定** | `payloads.py:40-67`：source 字段自由字符串；`connection_metadata.py:141`：merge 函数已支持两类事件 | 块 D 直接复用，无 schema 变更 |
| **BaseDelegation 已就位** | `delegation.py:329`（F098 Phase J）| F099 经 phase-0-recon 判断不需要继承（OD-F099-2 选 B） |
| **Policy Engine 已存在** | `packages/policy/src/.../models.py:29`（PolicyAction.ALLOW/ASK/DENY）；`harness/approval_gate.py`（ApprovalGate SSE 路径）| escalate_permission 直接接入，无需新建门禁层 |
| **A2A source 派生函数已有扩展点** | `dispatch_service.py:858-876`（`_resolve_a2a_source_role`）：已有注释"F099+ ask-back / source 泛化时一并补齐 spawn 路径注入逻辑"| 块 C 仅扩展此函数 |
| **subagents.steer 工具已存在** | `delegation_tools.py:282-314`：`attach_input` 已被工具层封装 | 块 E 端到端验证可借用 steer 工具路径 |

**结论**：F099 实际工作量集中在：
- 块 B：三工具 handler 新建（主要工作量）
- 块 C：`_resolve_a2a_source_role()` 扩展 + spawn 路径注入（中等）
- 块 D：CONTROL_METADATA_UPDATED 扩展 source 候选值描述 + ask_back emit 点（轻量）
- 块 E：端到端验证 + 单测（验证为主）

---

## 3. 范围（What）

按依赖关系组织 4 块（块 A 已在 phase-0-recon.md 完成）。每块对应一个 implementation Phase。

### 块 B：三工具引入（worker.ask_back / worker.request_input / worker.escalate_permission）

**目标**：让 Worker 在决策环中途暂停，向 request 来源或用户请求澄清/输入/权限，任务进入 WAITING_INPUT 后等待 attach_input 唤醒，继续执行不丢上下文。

**三工具定义**：

| 工具名 | 参数 | 语义 | 状态变化 |
|--------|------|------|----------|
| `worker.ask_back` | `question: str`（要问的问题）, `context: str = ""`（背景） | 向 request 来源提问，等待答复 | RUNNING → WAITING_INPUT → RUNNING |
| `worker.request_input` | `prompt: str`（请求描述）, `expected_format: str = ""`（期望格式）| 请求额外结构化输入 | RUNNING → WAITING_INPUT → RUNNING |
| `worker.escalate_permission` | `action: str`（请求的操作）, `scope: str`（作用范围）, `reason: str`（必要性说明）| 请求权限提升，走 Policy Engine + ApprovalGate | RUNNING → WAITING_APPROVAL → RUNNING/FAILED |

**实现路径**：
- `worker.ask_back` / `worker.request_input`：在 supervision_tools.py（或新建 ask_back_tools.py）中注册工具；handler 内部调用 `deps.execution_context.request_input(prompt=..., actor="worker:ask_back")`；emit CONTROL_METADATA_UPDATED（source="worker_ask_back"）
- `worker.escalate_permission`：handler 构建权限请求 → 调用 `deps.approval_gate.request_approval(...)` → 进入 WAITING_APPROVAL；ApprovalGate SSE 路径用户审批后继续（Constitution C4/C10 合规）
- **注册位置**：新建 `builtin_tools/ask_back_tools.py`，entrypoints = `{"agent_runtime", "web"}`，tool_group = `"interaction"`

**FR-B1**（MUST）：`worker.ask_back` 调用后 task 状态变为 WAITING_INPUT，不得 raise，不得 complete/fail 任务。
**FR-B2**（MUST）：`worker.request_input` 调用后 task 状态变为 WAITING_INPUT，返回用户输入文本作为工具调用结果。
**FR-B3**（MUST）：`worker.escalate_permission` 调用后走 ApprovalGate 路径（WAITING_APPROVAL），审批通过返回 `"approved"` / 审批拒绝返回 `"rejected"`，**均不 raise**（由 LLM 根据返回值决策后续行为）。
**FR-B4**（MUST）：三工具 handler 调用 `execution_context.request_input()` 时必须 emit CONTROL_METADATA_UPDATED（source="worker_ask_back" / "worker_request_input" / "worker_escalate_permission"）用于审计。[必须]
**FR-B5**（SHOULD）：`worker.ask_back` 的工具描述向 LLM 提示"当前工作来源（caller）" 信息，帮助 LLM 判断应向谁提问。[必须]
**FR-B6**（MAY）：三工具加入 ToolRegistry 的 `entrypoints={"agent_runtime", "web"}`，允许从 Web UI 触发。[可选]

### 块 C：source_runtime_kind 扩展 + spawn 路径注入

**目标**：
1. 扩展 `_resolve_a2a_source_role()` 支持 `"automation"` / `"user_channel"` 两个新值
2. 在 `delegate_task_tool.py` 和 `delegation_tools.py`（subagents.spawn 路径）中注入 `source_runtime_kind=worker`，修复 F098 已知 LOW §3

**FR-C1**（MUST）：`dispatch_service._resolve_a2a_source_role()` 扩展：`source_runtime_kind == "automation"` → 派生独立 AUTOMATION 路径（role/session_kind/agent_uri）；`source_runtime_kind == "user_channel"` → 派生 USER_CHANNEL 路径。[必须]
**FR-C2**（MUST）：worker 在 `delegate_task_tool` 内调用 `spawn_child()` 时，在 envelope.metadata 中注入 `source_runtime_kind="worker"`（F098 已知 LOW §3 修复）。[必须]
**FR-C3**（MUST）：subagents.spawn 路径（delegation_tools.py:150）同样注入 `source_runtime_kind="worker"`（当 target_kind=worker 且 caller 是 worker 时）。[必须]
**FR-C4**（SHOULD）：`source_runtime_kind` 无效值（非 worker/subagent/main/automation/user_channel）时，降级为 main 路径并 emit 结构化 warning log。[必须]

**[YAGNI 检验 - GATE_DESIGN G-2 锁定为 MUST]**：
- FR-C1 automation/user_channel 两个新路径：v0.1 标注 `[可选]` 建议留 F101，但 GATE_DESIGN 用户 override → **保留 MUST**。理由：F101 Notification + Attention Model 已明确依赖此 source 派生基础设施，前置在 F099 一并落地比 F101 再做切换便宜（避免 F099/F101 两次改 _resolve_a2a_source_role）。
- 实施约束：automation 派生 → role=AUTOMATION, session_kind=AUTOMATION_INTERNAL（如不存在则新建枚举值），agent_uri=`automation.<source_id>`；user_channel 派生 → role=USER, session_kind=USER_CHANNEL（同），agent_uri=`user.<channel_id>`
- plan 阶段必须给出新增 enum 值清单 + role/session_kind 枚举扩展位置

### 块 D：CONTROL_METADATA_UPDATED 承载 ask_back metadata

**目标**：ask_back 触发时 emit CONTROL_METADATA_UPDATED，承载 ask_back 语义信息，复用现有 merge_control_metadata 读取路径。

**FR-D1**（MUST）：ask_back / request_input 触发时 emit CONTROL_METADATA_UPDATED，payload 含：`source="worker_ask_back"` / `"worker_request_input"`，control_metadata 含 `ask_back_question` / `ask_back_context` / `created_at`（ISO 格式）。[必须]
**FR-D2**（MUST）：emit 的 CONTROL_METADATA_UPDATED **不污染对话历史**（即不出现在 `_load_conversation_turns()` 的 turns 列表中），保持 F098 OD-1 的修复成果。[必须]
**FR-D3**（SHOULD）：`escalate_permission` 触发时 emit CONTROL_METADATA_UPDATED，source="worker_escalate_permission"，control_metadata 含 `escalate_action` / `escalate_scope` / `escalate_reason`。[必须]
**FR-D4**（MAY）：`ControlMetadataUpdatedPayload.source` 字段的描述字符串更新，补充 F099 新增候选值列表（文档变更，无 schema 变更）。[可选]

### 块 E：唤醒路径验证（attach_input → ask_back resume）

**目标**：端到端验证 Worker turn N 调 ask_back → WAITING_INPUT → attach_input → turn N+1 看到原问题 + 用户回答。

**FR-E1**（MUST）：Worker ask_back 后 WAITING_INPUT 状态可通过 `subagents.steer`（delegation_tools.py:282）或 Web UI attach_input 唤醒，task 状态回 RUNNING。[必须]
**FR-E2**（MUST）：唤醒后 LLM turn N+1 的工具调用结果（tool_result）包含用户 attach_input 的文本，与 turn N 的 ask_back 调用关联正确（tool_call_id 匹配）。[必须]
**FR-E3**（MUST）：整个 RUNNING → WAITING_INPUT → RUNNING 转换在 Event Store 中有完整 audit trace：TASK_STATE_CHANGED（RUNNING→WAITING_INPUT）+ CONTROL_METADATA_UPDATED（ask_back）+ TASK_STATE_CHANGED（WAITING_INPUT→RUNNING）。[必须]
**FR-E4**（SHOULD）：Worker escalate_permission 走 ApprovalGate 路径时，WAITING_APPROVAL 状态下用户可通过 SSE 审批；审批通过后 task 状态回 RUNNING，拒绝后 LLM 收到 "rejected" 工具结果可自主决定是否继续。[必须]

---

## 4. Acceptance Criteria

### AC-B1（三工具工具名注册）
- Given: 系统启动完成
- When: 工具 broker 注册完毕
- Then: `worker.ask_back` / `worker.request_input` / `worker.escalate_permission` 均可在 broker 中查到，entrypoints 包含 `agent_runtime`

### AC-B2（ask_back 状态变化）
- Given: Worker 正在 RUNNING 状态处理任务
- When: LLM 调用 `worker.ask_back(question="请说明...")`
- Then: task.status = WAITING_INPUT；session.can_attach_input = True；工具调用未 raise；Event Store 中有 TASK_STATE_CHANGED(RUNNING→WAITING_INPUT)

### AC-B3（ask_back 上下文恢复）
- Given: Worker 进入 WAITING_INPUT（因 ask_back）
- When: 用户通过 attach_input 提交回答文本
- Then: Worker LLM turn N+1 的 tool_result 包含用户回答文本；task.status 回 RUNNING

### AC-B4（escalate_permission 走审批路径）
- Given: Worker 调用 `worker.escalate_permission(action="delete_file", scope="project", reason="...")`
- When: ApprovalGate 注册请求
- Then: task.status = WAITING_APPROVAL；approval_id 不为空；SSE 推送审批卡片

### AC-B5（escalate_permission 审批返回值 + 状态回归）（Analyze F-001 修订追加）
- Given: Worker 已调用 escalate_permission 进入 WAITING_APPROVAL
- When: 用户审批通过 / 用户审批拒绝 / ApprovalGate 超时（默认 300s）
- Then:
  - 工具返回 `"approved"` / `"rejected"` / `"rejected"`（超时按 plan §-1 P-VAL-1 实测路径返回 rejected，非 raise）
  - 工具调用未 raise（FR-B3 不 raise 约束）
  - task.status 从 WAITING_APPROVAL 回 RUNNING
  - LLM turn N+1 的 tool_result 包含返回字符串，由 LLM 自主决定后续行为

### AC-C1（source 注入修复）
- Given: Worker A 调用 `delegate_task` 委托给 Worker B
- When: `_resolve_a2a_source_role()` 执行
- Then: source role = WORKER（而非 MAIN）；A2AConversation.source_agent = `"worker.<capability>"` 格式

### AC-C2（源派生后向兼容）
- Given: 主 Agent 调用 `delegate_task`（无 source_runtime_kind 注入）
- When: `_resolve_a2a_source_role()` 执行
- Then: source role = MAIN（baseline 行为不变，0 regression）

### AC-C3（无效 source_runtime_kind 降级）（Analyze F-006 修订追加）
- Given: envelope.metadata.source_runtime_kind 是无效值（不在 {main/worker/subagent/automation/user_channel} 五值中）
- When: `_resolve_a2a_source_role()` 执行
- Then:
  - 派生 source role = MAIN（降级路径，FR-C4 SHOULD）
  - emit 一条结构化 warning log（含 invalid 值 + caller context）
  - 工具调用未 raise（0 exception）

### AC-D1（ask_back audit trace）
- Given: ask_back 工具被调用
- When: Event Store 查询该 task 的事件列表
- Then: 存在 CONTROL_METADATA_UPDATED 事件，source="worker_ask_back"，control_metadata 含 ask_back_question

### AC-D2（不污染对话历史）
- Given: ask_back emit CONTROL_METADATA_UPDATED
- When: 对话历史加载（`_load_conversation_turns()`）
- Then: CONTROL_METADATA_UPDATED 事件不出现在 turns 列表中

### AC-E1（端到端 ask_back 流程）
- Given: Worker 处于 RUNNING，调用 ask_back
- When: 用户 attach_input → Worker turn N+1 继续执行
- Then: Event Store 有三条连续事件：TASK_STATE_CHANGED(RUNNING→WAITING_INPUT) + CONTROL_METADATA_UPDATED(ask_back) + TASK_STATE_CHANGED(WAITING_INPUT→RUNNING)；Worker 最终能完成任务（不 fail）

### 全局 AC

**AC-G1**（0 regression）：全量回归 ≥ F098 baseline c2e97d5 passed 数（实测取值）；e2e_smoke 8/8 通过。

**AC-G2**（OD-1~OD-9 不偏离）：F098 spec.md §0 的 9 项 OD 在 F099 实施后仍全部成立（行为不变）。

**AC-G3**（Constitution 合规）：escalate_permission 走 ApprovalGate（C4 两阶段 + C7 User-in-Control + C10 Policy-Driven Access）。

**AC-G4**（audit trace 完整）：所有三工具调用均在 Event Store 有 CONTROL_METADATA_UPDATED 审计记录，task_id 关联正确。

---

## 5. Non-Goals（不在范围）

- ❌ **Decision Loop Alignment**（F100 范围）：去掉 single_loop_executor 跳过 recall planner 的 hack + 启用 `RecallPlannerMode="auto"` 实际语义
- ❌ **main direct 路径走 AGENT_PRIVATE**（F107 完整对等）
- ❌ **WorkerProfile 完全合并**（F107）
- ❌ **F096 Phase E frontend agent 视角 UI**（独立 Feature）
- ❌ **F098 已稳定的 A2A receiver 主路径 + Worker→Worker 解禁主体**（已锁，不动）
- ❌ **atomic single-transaction**（F098 OD-3 推迟 F107）
- ❌ **AC-H3 task_runner 手动 cleanup 残留**（F098 已知 LOW，F107 顺手清）
- ~~❌ automation/user_channel source 完整派生路径~~ **GATE_DESIGN G-2 修订**：F099 实施完整派生（role=AUTOMATION/USER, session_kind=AUTOMATION_INTERNAL/USER_CHANNEL, agent_uri）。F101 仅消费此基础设施，不再修改 dispatch_service。
- ❌ **ask_back 工具 WebSocket 实时推送**（当前 SSE 路径已足够，WebSocket 是 M6 范围）
- ❌ **kind=worker 专属工具集过滤**（需新建 policy 控制机制，超 F099 范围，留 F107）

---

## 6. Risks & Mitigations

| 风险 | 严重度 | 缓解策略 |
|------|--------|----------|
| **spawn 路径 source 注入破 baseline**（F098 已知 LOW §3）：注入 source_runtime_kind=worker 到 subagents.spawn 路径时，若 caller 判断逻辑不准确，可能误将主 Agent 发出的 spawn 记为 worker→worker | HIGH | 仅在 caller 有明确 work_id + target_kind=worker 时注入；无明确信号时保持不注入（default main）；Phase C 做 `AC-C2` 后向兼容验证 |
| **escalate_permission ApprovalGate 超时**（plan §-1 P-VAL-1 已实测）：ApprovalGate baseline 含 `wait_for_decision(timeout_seconds=300.0)` 超时机制 | LOW | 直接复用 baseline 超时；超时路径 `handle.decision='rejected'`（**非 'timeout'**——P-VAL-1 实测修正 v0.1 措辞，Analyze F-002 闭环）；工具不 raise；LLM 收到 "rejected" tool_result 自主决策 |
| **ask_back tool_result 上下文丢失**：LLM 对话历史被 compaction 压缩后，turn N 的 ask_back tool_call 和 turn N+1 的 tool_result 可能被分割，导致上下文关联断裂 | MEDIUM | 验证现有 compaction 机制是否保留 tool_call / tool_result 对；若不保留则记录为已知 risk（compaction 改造是 F100+ 范围）|
| **A2AConversation source_type 字段缺失**（phase-0-recon 发现）：F098 handoff 描述的 source_type 扩展是规划，不是已存在的字段 | LOW | OD-F099-3 选 B（扩展 source_runtime_kind 枚举），绕开需要修改 A2AConversation 模型的风险；source_type 字段留 F107 或 M6 评估 |
| **ask_back 后 exec_console 重启丢失 waiter**：进程重启后 asyncio.Queue waiter 消失，但 task 仍是 WAITING_INPUT | LOW | task_runner.attach_input() 已有"无 live waiter 则重启 job"路径（`_spawn_job(resume_from_node="state_running")`），F099 无需额外处理 |

---

## 7. Test Strategy

### 单测覆盖（按块）

| 测试文件 | 覆盖范围 | 预期测试数 |
|----------|----------|-----------|
| `tests/services/test_ask_back_tools.py`（新建）| 块 B：三工具 handler 单测（ask_back 状态变化 / request_input 返回值 / escalate_permission ApprovalGate 路径）| 12-15 |
| `tests/services/test_phase_c_source_injection.py`（新建）| 块 C：`_resolve_a2a_source_role()` 扩展（新值 + 后向兼容）+ spawn 路径注入验证 | 8-10 |
| `tests/services/test_phase_d_ask_back_audit.py`（新建）| 块 D：CONTROL_METADATA_UPDATED emit + merge_control_metadata 读取 + 不污染对话历史 | 6-8 |
| `tests/services/test_phase_e_ask_back_e2e.py`（新建）| 块 E：端到端 RUNNING → WAITING_INPUT → RUNNING + Event Store audit | 4-6 |

### 集成测

- `test_task_runner.py` 扩展：ask_back 工具在 task_runner 层的端到端流程（类似已有的 `ctx.request_input()` 测试模式）
- `test_capability_pack_tools.py` 扩展：新工具在 broker 中的注册验证

### e2e_smoke 影响评估

**F099 不新增 e2e_smoke 能力域**（现有 smoke 5 域 + full 8 域不变）：
- ask_back 是 Worker 行为扩展，现有 `delegate_task` smoke 域已覆盖 Worker 基础能力
- 若发现 e2e_smoke 中 `delegate_task` 域需更新断言（如验证 source audit），作为 existing 测试更新

### 回归要求

- 全量回归 ≥ F098 baseline c2e97d5 passed 数
- Phase A（实测侦察）完成后确认实际 baseline 数值
- e2e_smoke 8/8 通过（pre-commit hook 强制）

---

## 8. Phase 顺序建议（供 plan 阶段参考）

按"先简后难，先基础设施后主行为"原则：

```
Phase A（已完成）: 实测侦察 → phase-0-recon.md
Phase C:  source_runtime_kind 扩展 + spawn 路径注入（数据模型 + 纯函数扩展，影响面可控）
Phase D:  CONTROL_METADATA_UPDATED 扩展（轻量，完善 audit 基础设施）
Phase B:  三工具引入（主行为，依赖 D 的 audit emit 路径）
Phase E:  端到端验证 + 单测补全（验证块 B-D 联合正确性）
Verify:   Codex adversarial review（pre-impl + final cross-Phase）
```

---

## 9. 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 值 | 说明 |
|------|-----|------|
| **组件总数** | 2 | 新建：`ask_back_tools.py`（工具注册模块）；`test_ask_back_tools.py` 等测试文件不计入组件 |
| **接口数量** | 4 | 新增：`worker.ask_back` / `worker.request_input` / `worker.escalate_permission` 三个工具接口 + `_resolve_a2a_source_role()` 修改（扩展，非新增） |
| **依赖新引入数** | 0 | Policy Engine / ApprovalGate / execution_console.request_input / BaseDelegation 均已存在 |
| **跨模块耦合** | 1 处 | `dispatch_service.py`（source 派生扩展）修改；`delegate_task_tool.py` + `delegation_tools.py` 各加注入逻辑（共 3 处改动，但属于同一 delegation 域） |
| **复杂度信号** | 0 | 无递归结构 / 无状态机新建（复用已有状态机）/ 无并发控制新建（复用 asyncio.Queue）/ 无数据迁移 |
| **总体复杂度** | **LOW** | 组件 2 < 3，接口 4 == 4（边界值），无复杂度信号 |

**GATE_DESIGN 建议**：LOW 复杂度，OD-F099-1~OD-F099-7 共 7 项决议点是 GATE_DESIGN 的核心审查对象，尤其是 OD-F099-2（不引入 BaseDelegation 继承）和 OD-F099-3（不新增 A2AConversation.source_type 字段）两个"简化决策"需要用户确认接受。

---

## §YAGNI 最小必要性检验汇总

| FR / 组件 | 标注 | 理由 |
|-----------|------|------|
| ask_back_tools.py（三工具注册模块）| [必须] | 核心功能载体，去掉后 F099 无法实现 |
| CONTROL_METADATA_UPDATED emit（ask_back audit）| [必须] | Constitution C2（Everything is an Event），无审计不符合宪法 |
| source_runtime_kind 注入（spawn 路径）| [必须] | F098 已知 LOW §3，worker→worker audit 修正，主责之一 |
| escalate_permission → ApprovalGate | [必须] | Constitution C10，不走 Policy Engine 则违反宪法 |
| automation/user_channel 完整派生路径 | **[必须 - GATE_DESIGN G-2 override]** | v0.1 标 [可选]，GATE_DESIGN 用户 override 为 [必须]。F101 Notification Model 已确定依赖此基础设施，前置在 F099 落地避免两次改 _resolve_a2a_source_role |
| BaseAskBackDelegation 抽象类 | [YAGNI-移除] | OD-F099-2 选 B，ask_back 工具不走 spawn 路径，BaseDelegation 语义不对齐；去掉后功能完整实现。**移除理由**：ask_back 是"工具调用挂起"而非"任务委托"，引入继承层增加错误语义 |
| A2AConversation.source_type 新字段 | [YAGNI-移除] | OD-F099-3 选 B，扩展 source_runtime_kind 枚举值已足够表达 source 语义，新增模型字段会破坏所有构造点。**移除理由**：当前无消费方需要从 A2AConversation 读取 source_type 的场景 |
| ToolRegistry kind 过滤机制 | [YAGNI-移除] | 需新建 policy 控制机制（超 F099 范围）；ask_back 对所有 agent kind 均有意义（OD-F099-4 选 B）。**移除理由**：当前 baseline 无此机制，新建超出 F099 范围且不影响核心功能 |

---

---

## 版本历史

- **v0.1**（2026-05-11）：specify agent 初版，7 OD 候选，FR-C1 标 [可选]
- **v0.2**（2026-05-11）：GATE_DESIGN 通过，4 项决议锁定（G-1~G-4）。**FR-C1 升级为 MUST**（用户 override），§YAGNI / §5 Non-Goals 同步修订。plan 阶段不得偏离。
- **v0.3**（2026-05-11）：Analyze 阶段 4 项修订闭环（F-001 追加 AC-B5 / F-002 修正 §6 超时返回值文字 / F-006 追加 AC-C3 / F-009 在 tasks 修正）。FR 18/18 全 AC 覆盖（FR-B5 / FR-D4 文档类除外）。

v0.3 - Analyze 闭环，可进入 GATE_TASKS
