# F097 → F098 Handoff

**Source**: F097 Subagent Mode Cleanup (feature/097-subagent-mode-cleanup, 7 commits + 1 fix)
**Target**: F098 A2A Mode + Worker↔Worker（M5 阶段 2 第 2 个 Feature）

## 必须接管的推迟项

### 1. F096 H2 推迟项 AC-F1 worker_capability 路径（来自 F096）

F096 Final review 已显式归档：worker_capability 路径完整 audit chain 集成测推迟到 F098 — 等 delegate_task fixture 完备。

### 2. Phase E P2-3 事务边界（Codex Phase E medium 推迟）

`session.save + event.append_event_committed` 跨事务，单点失败可能留下 closed session 但无审计事件。F097 用 EventStore.idempotency_key 缓解（重复 emit 短路 + 仍尝试 close session），未根治。

**F098 修复方向**：
- session.save 和 SUBAGENT_COMPLETED event 放同一事务
- 或：先写事件后改 session（已 cleanup 缓解但未根治）
- 涉及 spawn 路径联合设计（Phase B / Phase E 协同）

### 3. Phase B P2-4 终态统一层（Codex Phase B medium 推迟）

cleanup 仅在 _notify_completion 触发；shutdown / dispatch exception 兜底标 FAILED 但**不一定全调** _notify_completion → 部分终态路径不触发 cleanup → ACTIVE session 残留。

F097 已在 dispatch exception / non-terminal mark_failed 路径手动补 `_close_subagent_session_if_needed` 调用，但未挪到 task state machine 终态层。

**F098 修复方向**：
- cleanup hook 挪到 `task_service._write_state_transition`（task state 终态触发）
- 移除 task_runner 各处手动调用（避免重复，但有 EventStore idempotency 兜底）
- 涉及 task state machine 改造，影响面较大

### 4. Final P1-1 USER_MESSAGE event 复用污染（Codex Final high 归档）

F097 在 task_runner._emit_subagent_delegation_init_if_needed 和 agent_context._ensure_agent_session B-3 backfill 用 USER_MESSAGE 承载 control_metadata 更新（marker text "[subagent delegation metadata]"）。`ContextCompactionService._load_conversation_turns` 把所有 USER_MESSAGE 当用户 turn → 首轮 latest_user_text 错误。

**F098 修复方向**（3 选项）：
- A. 引入新 event type `CONTROL_METADATA_UPDATED`，only carries control_metadata（推荐）
- B. USER_MESSAGE 加 `is_synthetic_marker` 标记，consumer 跳过
- C. 重构 SubagentDelegation 持久化路径走 task store metadata（CL#16 原始决策回归）

工作量 ~3-5h。

### 5. Final P1-2 ephemeral subagent runtime 复用 caller worker runtime（Codex Final high 归档）

`_ensure_agent_runtime` 用 `(project_id, role, worker_profile_id)` 三元组复用 active runtime。subagent ephemeral profile 没有 `source_worker_profile_id` → 复用 caller worker runtime → audit 数据混在一起。

F097 测试覆盖 spawn → SUBAGENT_INTERNAL session 创建 + parent_worker_runtime_id 填充，但 audit chain 实际混叠到 caller worker runtime 的影响在测试中未直接验证。

**F098 修复方向**：
- subagent runtime 用独立 query key（如用 SubagentDelegation.delegation_id 派生）
- 或：跳过 _ensure_agent_runtime 复用逻辑，每次 spawn 创建新 runtime
- 影响 _ensure_agent_runtime 函数签名 + 调用方

工作量 ~3-5h。F098 A2A 模式实施时一并收口（F098 spec 应明确 receiver runtime 独立路径）。

## F097 与 F098 概念分离边界

F097 SubagentDelegation 与 F098 A2A WorkerDelegation 应保持概念分离：

| 维度 | F097 SubagentDelegation | F098 A2A WorkerDelegation |
|------|-------------------------|---------------------------|
| 生命周期 | spawn-and-die（短生命周期）| 长生命周期 |
| Project | 共享 caller project（scope_id 继承）| receiver 在自己 project |
| Memory namespace | α 共享（caller AGENT_PRIVATE）| receiver 独立 namespace |
| RuntimeHintBundle | surface 拷贝 + 其他默认（架构限制）| receiver 重建 |
| AgentRuntime | ephemeral 应独立（Final P1-2 修复）| 独立 |
| AgentSession | SUBAGENT_INTERNAL kind | A2A receiver session |
| audit chain | 4 层一致 + parent_worker_runtime_id | 4 层一致 + parent reference |

**BaseDelegation 公共抽象**：F097 当前 SubagentDelegation 是独立 model，F098 评估时考虑提取 BaseDelegation 父类（共享字段：delegation_id / parent_task_id / parent_work_id / child_task_id / spawned_by / created_at / closed_at）。

## agent_kind enum 演化

F097 当前枚举：`Literal["main", "worker", "subagent"]`

F098 可能扩展：
- `worker_a2a` (A2A receiver worker)
- 或保持 `worker` 通过 delegation_mode 区分（"main_delegate" vs "subagent" vs "a2a"）

F098 spec 阶段需决定。BehaviorPackLoadedPayload.agent_kind 字段已是 str（不是 Literal），新增值无需 schema bump。

## 测试基础设施可借鉴

F097 7 测试文件可借鉴的模式：
- `test_agent_context_phase_b.py`：spawn → SUBAGENT_INTERNAL session 端到端 + Codex P1 闭环 deep copy 守护
- `test_agent_context_phase_f.py`：α 语义端到端 + caller scope 传递验证
- `test_task_runner_subagent_cleanup.py`：cleanup + idempotency + RecallFrame 保留

## 关键引用

- 完整 spec：[spec.md](spec.md) v0.2
- 实施计划：[plan.md](plan.md)
- 任务清单：[tasks.md](tasks.md) (46 任务)
- 完成报告：[completion-report.md](completion-report.md)
- Final Codex：[codex-review-final.md](codex-review-final.md)
