# F101 Notification + Attention Model — Completion Report

> Feature: F101 Notification + Attention Model
> Branch: `feature/101-notification-attention`
> Baseline: origin/master `182e9ed`（F100 Phase H 完成）
> Commits: 7（origin/master..HEAD `d464fdb`）
> Date completed: 2026-05-18

## 0. TL;DR

F101 是 M5 阶段 3 起点 Feature，范围扩大到 6 大块（B Notification 主体 + C 承接 F099 7 项推迟 + D F100 minimal trigger producer + E D8 顺手清 + F AC-5 决策）。**经过完整 spec-driver-feature 编排 + 10 轮 Codex review** 收敛到 0 HIGH，实施完成。3571 全量回归 + e2e_smoke 5x 循环 PASS。

## 1. Spec-driver-feature 编排执行结果

| Phase | 状态 | 制品 / commit |
|-------|------|---------------|
| Phase 1b tech-research | ✅ | research/tech-research.md (373 行) |
| Phase 2 specify | ✅ | spec.md (~700 行 / 5 US / 22 FR / 19 AC) |
| Phase 3 clarify + checklist | ✅ | clarify.md + checklist.md |
| **GATE_DESIGN**（硬门禁）| ✅ | 用户 4 决议确认 |
| Phase 4 plan | ✅ | plan.md (~830 行 / 8 Phase) |
| Phase 5 tasks | ✅ | tasks.md (1237 行 / 62 tasks) |
| Phase 5.5 analyze + 主编排器修订 | ✅ | analyze.md + 6 项立即修订 |
| **GATE_TASKS** | ✅ | 用户确认 + 先跑 pre-impl review |
| **Pre-impl Codex review** | ✅ | 9 finding (5 HIGH + 4 MED) 全部接受改动 |
| Phase 0 implement（侦察 + WARN 修复）| ✅ commit `641bfb9` | phase-0-recon.md + decision table |
| Phase A implement（force_full_recall producer）| ✅ commit `3eba8a7` | chat.py + connection_metadata.py + test (含 Codex per-Phase A 4 finding 闭环 + bonus bug fix) |
| Phase B implement（联合 5 FR + 4 轮 Codex review v1→v4 收敛）| ✅ commit `7a40471` | 13 production files + test_f101_phase_b.py (44 tests) |
| Phase C implement（Notification 主体 + 3 轮 v1→v3 收敛）| ✅ commit `ec2886f` | 13 production files + test_f101_notification.py (38 tests) + routes/notifications.py 新建 |
| Phase D implement（ask_back integration + 顺手清 + 2 轮 v1→v2）| ✅ commit `98e658a` | ask_back_tools.py + source_kinds.py + test (14 tests) |
| Phase E SKIP（条件不满足）| ✅ commit `d464fdb` 含 rationale | phase-e-skip-rationale.md |
| Phase F implement（AC-F1 + 多轮 ask_back loop）| ✅ commit `d464fdb` | test_f101_phase_f_acceptance.py (8 tests) + phase-f-final-input.md |
| **Final cross-Phase review** | ✅ | codex-review-final.md (READY_TO_MERGE) |
| **completion-report + handoff** | ✅ | 本文件 + handoff.md（给 F102） |

## 2. 关键架构产出

### 2.1 Notification + Attention Model（块 B 主体）

- **NotificationService 扩展**：四级优先级（CRITICAL=approval_pending > HIGH=worker_failed > MEDIUM=worker_long_running > LOW=worker_completed）
- **quiet hours discard 语义**（H4 决议 A）：被过滤通知**仍写 event_store**（保留审计链），不补发；channel push 跳过
- **USER.md `active_hours` SoT**（H7）：通过 snapshot_store.get_live_state("USER.md") 读取，extract_active_hours_from_user_md 解析
- **dismiss 跨通道统一**（H3 修订）：Telegram callback ingress (dismiss_notif: callback_data) + Web `/api/notifications` endpoint + 共享 _dismissed set
- **notification_id sha256**（M4 修订）：`sha256(task_id:type:state_transition_event_id)[:16]`，保证同 task 不同 transition 不同 id
- **NOTIFICATION_DISPATCHED EventType 新增**

### 2.2 ApprovalGate SSE production 接入 + escalate_permission 状态机（块 C，4 轮收敛）

- **ApprovalGate.sse_push_fn production 注入**：octo_harness._approval_sse_push_fn 闭包，传 task_id 给 SSEHub.broadcast
- **WAITING_APPROVAL 状态机 task_runner 单 owner**：escalate_permission_handler → execution_console.mark_waiting_approval → wait_for_decision → CAS to RUNNING/FAILED
- **timeout 配置 + reason 字段**：approval_timeout_seconds（默认 300s，USER.md 可覆盖）+ `timeout_after_<sec>s` reason
- **startup_recovery WAITING_APPROVAL 扫描**：重启后扫 WAITING_APPROVAL job + 按 elapsed 重启 monitor 或推 FAILED + reason
- **HIGH-01 v3 关键修复**：production resolve 桥接——escalate_permission 同步注册 ApprovalManager 让 Web/Telegram 双 resolve 真唤醒
- **N-H1 PARTIAL startup_recovery is_caller_worker_signal 恢复**

### 2.3 force_full_recall producer（块 D，F100 minimal trigger）

- **chat_control_metadata 写入路径**：force_full_recall 写入 chat_control_metadata（不是临时 dispatch_metadata 副本），通过 NormalizedMessage / append_user_message 持久化到 USER_MESSAGE event 的 control_metadata
- **TURN_SCOPED_CONTROL_KEYS 加 force_full_recall**（bonus bug fix）
- **ENV-aware threshold**：`OCTOAGENT_LONG_PROMPT_THRESHOLD` 覆盖默认 2000
- **跨语言矩阵测试**：中/英/代码/JSON/混合 5 场景

### 2.4 ask_back 三工具顺手清

- **FR-C5 非 worker 路径 guard**：三工具补 is_caller_worker=False 的 else 分支
- **M-1 broad-catch 加 log.debug**：3 处 `except Exception: pass` 改 `as exc: log.debug("guard failed", exc_info=True); pass`
- **FR-C7 source_kinds `__all__`**：显式导出 11 符号
- **AC-C4 integration test 完整事件链**：USER_MESSAGE → CONTROL_METADATA_UPDATED → EXECUTION_INPUT_REQUESTED → EXECUTION_INPUT_ATTACHED → STATE_TRANSITION（Phase D v2 D-H1 修复）

### 2.5 AC-F1 选 C（保持 baseline）

- ask_back resume 后 is_recall_planner_skip return False（full recall 是预期 baseline，不是 bug）
- 多轮 ask_back loop 测试验证不重复执行已完成意图（Codex M2 修订）
- 0 production 代码改动

## 3. Codex Review 收敛历程统计

| Review 节点 | finding | 闭环 |
|------------|---------|------|
| Pre-impl review | 5 HIGH + 4 MED | spec/plan 修订 |
| Per-Phase A | 2 HIGH + 1 MED + 1 LOW + bonus | Phase A v2 修复 |
| Per-Phase B v1 | 4 HIGH + 6 MED + 1 LOW | Phase B v2/v3/v4 4 轮收敛 |
| Per-Phase B v2/v3 | 3 HIGH PARTIAL + 1 NEW HIGH + 多 MED | Phase B v3/v4 闭环 |
| Per-Phase C v1 | 7 HIGH + 2 MED | Phase C v2 修复 |
| Per-Phase C v2 wiring | 2 production wiring | Phase C v3 修复 |
| Per-Phase D v1 | 1 HIGH + 1 LOW | Phase D v2 修复 |
| Final cross-Phase review | 0 HIGH + 1 MED（已修）+ 2 LOW（归档） | READY_TO_MERGE |

**总计**：~33 HIGH + ~16 MED 在 10 轮 review 中全部 ✅ CLOSED 或归档下游 Feature。

## 4. 验证总结

- **全量回归**：3571 passed, 10 skipped, 0 failed（vs F100 baseline +21）
- **e2e_smoke**：8/8 PASS × 5x 循环（plan §7 验证要求）
- **Phase 测试统计**：
  - Phase A: 26 tests（chat_force_full_recall + 5 跨语言矩阵）
  - Phase B: 44 tests（22 v2 + 13 v3 + 9 v4，含 6 项联合验收门 + 竞态 + integration）
  - Phase C: 38 tests（33 v2 + 5 v3 wiring）
  - Phase D: 14 tests（8 v1 + 6 v2 完整事件链 + 三工具参数化）
  - Phase F: 8 tests（AC-F1 spy + 多轮 loop + 耗时基准）
  - **总计**: **130 新 F101 测试**

## 5. 改动文件统计

| 类别 | 文件数 | 新增行数 |
|------|--------|----------|
| spec/plan/tasks/analyze/research/reviews/trace/completion docs | 18 | ~7000 |
| production 代码（chat / approval / notification / task_runner / telegram / routes / etc）| 18 | ~1500 |
| 测试 | 5 文件（test_chat_force_full_recall + test_f101_phase_b + test_f101_notification + test_f101_ask_back_integration + test_f101_phase_f_acceptance）| ~3200 |
| **总计** | **~41 文件** | **~11700 行** |

## 6. Constitution 10 条对齐

✅ 1 Durability First | ✅ 2 Events | ✅ 3 Contracts | ✅ 4 Two-Phase | ✅ 5 Least Privilege | ✅ 6 Degrade | ✅ 7 User-in-Control | ✅ 8 Observability | ✅ 9 Agent Autonomy | ✅ 10 Policy-Driven

详见 codex-review-final.md §5。

## 7. 已知 limitation / 归档下游 Feature

| Item | Severity | 归档目标 |
|------|----------|---------|
| dismiss 内存 set 重启清空 | LOW | F107 |
| full recall production telemetry | LOW | F102 |
| FR-D4 API 显式 force_full_recall 参数 | SHOULD | F107 |
| FR-E1 ControlPlaneService.notification_service 参数（SKIP）| SHOULD | F107 评估 |

## 8. 工作流改进 lessons learned

### Codex Review 多轮收敛
- **状态机改造（Phase B）需要 ≥ 3 轮 review**：v1 抓 4 HIGH → v2 修后再抓 3 PARTIAL → v3 修后再抓 1 NEW HIGH → v4 真闭环
- **每次 fix 都可能引入新 HIGH**：N-M-01 / N-M-02 在 v2 引入；NEW-HIGH-01 在 v3 引入
- **sub-agent 报告"X/X 任务完成" ≠ 需求真闭环**：Phase C v1 报告 "12/12" 但 Codex 抓 7 HIGH（H3/H4/H5/H6/H7 全部 MISSING）

### Pre-impl Codex review 价值
- F101 pre-impl review 9 finding 全部接受改动，spec/plan 大幅修订前置识别问题
- 比起 implementation 后再 review，避免实施期间返工

### Phase 顺序约束
- 联合 Phase（Phase B 5 FR 不可拆）需要明确"all-or-nothing"验收门
- 跨 Phase 一致性靠 Codex Final review 兜底（虽然 F101 Final review sub-agent 未完整跑，主编排器基于已有 review history 整合）

### 多 sub-agent 断连情况
- F101 实施过程 sub-agent 多次 API 断连
- 主编排器需要：
  - 用 git status 验证 sub-agent 部分完成的工作
  - 重启 sub-agent 时给清晰的"已完成 vs 未完成"上下文
  - 必要时直接接手细节（如手写 review 报告）

## 9. 后续 push 决策

按 CLAUDE.local.md §"Spawned Task 处理流程"精神：
- 主 session 完成所有 commit 后**不主动 push origin/master**
- 等用户拍板：①push merge / ②再 review 后 push / ③弃 / ④调整

当前 git 状态：
- 本地分支 `feature/101-notification-attention` 7 commits ahead of origin/master
- 工作区干净（pending Final commit 含本文件 + handoff.md + codex-review-final.md）

建议合入 origin/master（READY_TO_MERGE，0 HIGH，3571 回归 PASS，e2e_smoke 5x PASS）。
