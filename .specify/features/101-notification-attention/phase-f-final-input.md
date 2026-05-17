# F101 Phase F — Final cross-Phase Codex Review 输入文档

**生成时间**: 2026-05-17
**生成者**: spec-driver:implement 子代理（Phase F 执行后）
**用途**: 供 Final cross-Phase Codex review 子代理参考

---

## 1. 所有 Phase Commit 汇总

| Phase | Commit ID | 简述 | 文件数 | 净变更行 |
|-------|-----------|------|--------|---------|
| Phase 0 | `641bfb9` | 侦察报告 + M3 decision table 实测填写 | 1 | +N/A（文档） |
| Phase A | `3eba8a7` | force_full_recall producer + per-Phase A Codex review 闭环（含 bonus bug 修复） | 4 | +809 |
| Phase B | `7a40471` | WAITING_APPROVAL 状态机 + ApprovalGate SSE + 超时 + startup_recovery 联合（4 轮 Codex review） | 18 | +3973/-51 |
| Phase C | `ec2886f` | Notification 主体 + quiet hours + dismiss + Telegram callback + Web API（v1→v2→v3 三轮 Codex review） | 15 | +1777/-23 |
| Phase D | `98e658a` | ask_back integration test + 顺手清（FR-C4/C5/C7 + M-1 broad-catch + 2 轮 Codex review） | 5 | +1233/-6 |
| Phase E | ~~跳过~~ | D8 顺手清（条件降级）：Phase C 已通过 task_runner 路径覆盖所有通知场景，FR-E1 SHOULD 级别不实施 | — | — |
| Phase F | **未 commit** | AC-F1 验证 + 多轮 ask_back loop 测试（F-2 + F-2b，8 个测试） | 1 | +~350 |

**基础 baseline**: `182e9ed`（F100 Phase H 完成，origin/master）
**当前 HEAD**: `98e658a`（Phase D commit，Phase F 测试文件未 commit）

---

## 2. 各 Phase Codex Review 轮次摘要

### Phase A（per-Phase A，1 轮）

| Finding | Severity | 处理结论 |
|---------|----------|---------|
| H-A1: 标准 task_runner 路径丢弃 force_full_recall | HIGH | ✅ 已修（bonus bug fix：写入 USER_MESSAGE 时同时写 force_full_recall 到 control_metadata） |
| H-A2: LONG_PROMPT_THRESHOLD hardcode | HIGH | ✅ 已修（改为 env 可配置） |
| M-A1: AC-D1 orchestrator 处理点测试覆盖缺失 | MED | ✅ 已修（补 orchestrator 路径测试） |
| L-A1: 跨语言矩阵死字段 | LOW | ✅ 已修（清理 should_trigger 死字段） |

**结论**: 4 finding 全闭环（2 HIGH + 1 MED + 1 LOW = 0 残留）

---

### Phase B（4 轮 review：v1 → 修复 → v2 → 修复 → v3 → 修复 → v4 收敛）

| Round | Finding 数 | 主要议题 |
|-------|-----------|---------|
| v1 | 4 HIGH + 6 MED + 1 LOW | HIGH-01 ApprovalGate resolve 路径永不唤醒；HIGH-02/03/04 竞态/分裂/restart |
| v2（修复后） | 3 HIGH PARTIAL + 2 N-MED | HIGH-01 部分闭环；HIGH-02/04 PARTIAL；N-M-01/02 新 MED |
| v3（再修复后） | 3 HIGH + 8 CANNOT_VERIFY | NEW-HIGH-01 timeout 不一致；HIGH-02/04 PARTIAL 继续 |
| v4（最终）| **0 HIGH 残留** | HIGH-02/04 PARTIAL + NEW-HIGH-01 已修；MED 归档 |

**结论**: v4 收敛到 0 HIGH，3 HIGH PARTIAL + 1 NEW-HIGH + 6 MED + 1 LOW 全处理

---

### Phase C（3 轮 review：v1 → v2 → v3 收敛）

| Round | Finding 数 | 主要议题 |
|-------|-----------|---------|
| v1 | 7 HIGH + 2 MED | H3 Telegram dismiss / Web list API 缺失；FR-B8 sha256 未实现；H4 event_store audit；H7 USER.md SoT |
| v2（7 HIGH 修复后） | 2 wiring issues（Codex streaming 识别）| task_runner session_id 取法；ask_back_tools session_id |
| v3（2 wiring 修复后） | **0 HIGH 残留** | 38 tests + 全量 3549 passed |

**结论**: v3 收敛到 0 HIGH，7 HIGH + 2 MED 全处理（Final review 兜底）

---

### Phase D（2 轮 review：v1 → 修复 → v2 收敛）

| Round | Finding 数 | 主要议题 |
|-------|-----------|---------|
| v1 | 1 HIGH + 0 MED + 1 LOW | D-H1 AC-C4 USER_MESSAGE 事件链缺失；D-L1 三工具覆盖缺口 |
| v2（修复后）| **0 HIGH 残留** | D-H1 已修（显式写入 USER_MESSAGE + 相对顺序验证）；D-L1 归档（非生产 bug，覆盖缺口可接受） |

**结论**: v2 收敛到 0 HIGH，1 HIGH 处理 + 1 LOW 归档

---

### Phase E — 降级记录（显式）

**决策**：Phase E 降级，不实施。
**原因**：FR-E1 是 SHOULD 级别；Phase C 已通过 task_runner.py 内的 notification_service 调用路径覆盖所有通知场景（WAITING_APPROVAL 分支直接调用 `notification_service.notify_approval_request`）。ControlPlaneService 不直接参与通知触发链路，新增构造参数无实质性价值。
**影响**：AC-E1 豁免（条件 AC，依 plan §5 降级条件）。
**后续**：若 D8（ControlPlaneService DI 重构）在 F107 实施时一并处理。

---

### Phase F — Codex review（待 Final cross-Phase 评估）

Phase F 不单独做 per-Phase review（per-Phase F 合并进 Final cross-Phase review，见 plan §7.3）。

Phase F 产出：
- `test_f101_phase_f_acceptance.py`（8 个测试，全通过）
- `phase-f-final-input.md`（本文档）

---

## 3. 验证通过状态

| 验证项 | 状态 | 具体值 |
|--------|------|--------|
| F-1: is_recall_planner_skip 路径验证 | ✅ | unspecified + False → return False；None → return False |
| F-2: AC-F1 单测（spy is_recall_planner_skip） | ✅ | 2 集成测试 PASS |
| F-2b: 多轮 ask_back loop 测试 | ✅ | 3 测试 PASS（2 轮并发 + 独立性 + 性能基准） |
| F-3: 全量回归 | ✅ | 3571 passed（vs 3563 baseline，+8 Phase F 测试，0 regression） |
| F-4: e2e_smoke 5x 循环 | ✅ | 8/8 × 5 轮 = 40 次全 PASS（2.5s/轮） |

---

## 4. Final cross-Phase Review 重点检查项

Final review 需重点审视以下潜在盲点：

### 4.1 Phase B 联合验收门（plan §3.7 6 项）

Phase B 引入了 WAITING_APPROVAL 状态机。Final review 应确认：
1. **AC-C1**（task 进 WAITING_APPROVAL）：`test_f101_phase_b.py` 是否真实覆盖
2. **AC-C2**（SSE 推送审批请求）：approval_gate sse_push_fn 注入路径是否真实 wired
3. **AC-C3**（超时 FAILED）：`task_runner.py` 监控循环是否真实处理 timeout → FAILED（不再 continue 跳过）
4. **B-9c service-layer integration test**：是否有不纯 mock 的 integration test 覆盖三者联动
5. **B-9b 竞态测试**：WAITING_APPROVAL 双 owner 竞态是否有测试覆盖
6. **B-9d 配置覆盖测试**：approval_timeout_seconds 配置是否有测试

### 4.2 Phase C 跨通道 dismiss 一致性（选 A）

- Telegram dismiss → Web 下次刷新不返回该通知：H3-test 是否真实 wired
- notification_id sha256 生成规则：FR-B8 去重 key 是否真实工作（不同 transition → 不同 id；同 transition 重试 → 同 id）

### 4.3 Phase E 降级合理性

- FR-E1 SHOULD 级别降级是否合规（plan §5 降级条件是否满足）
- ControlPlaneService 是否真的不参与通知触发链路

### 4.4 AC-F1 选 C 稳定性

- F-2b 多轮 loop 测试覆盖是否足够（2 轮 ask_back + 2 轮 request_input）
- full recall 耗时基准是否合理（< 100μs 上限）
- resume 后 task 状态是否始终处于活跃态（非非法终态）

### 4.5 Phase A force_full_recall producer 完整性

- `chat.py` 两处 dispatch_metadata 构造点是否都注入了 force_full_recall
- 跨语言矩阵（中/英/代码/JSON）是否通过 A-5b 测试

---

## 5. 已知 deferred 项（Final review 前已归档）

| ID | 内容 | 归档至 |
|----|------|--------|
| D-L1 | AC-C5 三工具非 worker guard 覆盖不对称（request_input/escalate_permission 缺测试）| F107 或下游 Feature |
| Phase E | FR-E1 ControlPlaneService notification_service 参数 | F107 D8 |
| Phase E frontend | F096 Phase E frontend UI | 独立 Feature |

---

## 6. 文件变更汇总（F101 全部 Phase）

### Production 代码（`octoagent/` 目录下）

| 文件 | Phase | 主要变更 |
|------|-------|---------|
| `gateway/routes/chat.py` | A | force_full_recall producer 两处注入点 |
| `gateway/services/connection_metadata.py` | A | force_full_recall 透传到 USER_MESSAGE control_metadata |
| `gateway/harness/approval_gate.py` | B | sse_push_fn 真实接入；wait_for_decision timeout 配置 |
| `gateway/harness/octo_harness.py` | B, C | ApprovalGate 构造时注入 sse_push_fn 闭包；NotificationService 注入 |
| `gateway/routes/approvals.py` | B | approval 决策双 resolve（ApprovalManager + ApprovalGate） |
| `gateway/services/builtin_tools/_deps.py` | B, C | ToolDeps approval_gate 真实注入 |
| `gateway/services/builtin_tools/ask_back_tools.py` | B, C, D | escalate_permission 真实 ApprovalGate 路径；M-1 broad-catch → log.debug |
| `gateway/services/execution_console.py` | B | WAITING_INPUT → WAITING_APPROVAL 状态分支 |
| `gateway/services/execution_context.py` | B | ExecutionRuntimeContext 新字段 |
| `gateway/services/operator_actions.py` | B | OperatorActionService approval_gate 参数 |
| `gateway/services/task_runner.py` | B, C | WAITING_APPROVAL monitor 修复；notify_approval_request 接入；startup_recovery |
| `gateway/deps.py` | B | FastAPI DI approval_gate 注入 |
| `policy/approval_manager.py` | B | ApprovalManager 超时配置 |
| `policy/models.py` | B | 新增超时相关模型字段 |
| `gateway/main.py` | C | notifications 路由挂载 |
| `gateway/routes/notifications.py` | C | GET /api/notifications list/refresh API |
| `gateway/services/notification.py` | C | NotificationPriority 枚举 + quiet hours + dismiss 幂等 + sha256 id |
| `gateway/services/telegram.py` | C | Telegram callback dismiss ingress |
| `core/behavior_templates/USER.md` | C | active_hours 字段结构化注释 |
| `core/models/enums.py` | C | NOTIFICATION_* 事件类型新增 |
| `core/models/source_kinds.py` | D | FR-C7 `__all__` 导出列表 |

### 测试文件

| 文件 | Phase | 覆盖 AC |
|------|-------|---------|
| `tests/test_chat_force_full_recall.py` | A | AC-D1/D2/D3 + 跨语言矩阵 |
| `tests/test_f101_phase_b.py` | B | AC-C1/C2/C3/C6 |
| `tests/test_f101_notification.py` | C | AC-B1~B6 + M4 三场景 + H3-test |
| `tests/test_notification.py` | C | 已有测试回归 |
| `tests/services/test_f101_ask_back_integration.py` | D | AC-C4（完整事件链）+ AC-C5 + AC-C7 |
| `tests/services/test_f101_phase_f_acceptance.py` | F | AC-F1（is_recall_planner_skip spy）+ 多轮 ask_back loop（M2） |

---

## 7. Final cross-Phase review 命令参考

```bash
# 生成完整 diff（供 review 输入）
git diff 182e9ed HEAD -- octoagent/

# 各 Phase 单独 diff
git diff 182e9ed 641bfb9  # Phase 0
git diff 641bfb9 3eba8a7  # Phase A
git diff 3eba8a7 7a40471  # Phase B
git diff 7a40471 ec2886f  # Phase C
git diff ec2886f 98e658a  # Phase D
git diff 98e658a HEAD     # Phase F（未 commit 状态）

# 全量回归（Phase F 后）
cd octoagent && uv run python -m pytest -q --ignore=apps/gateway/tests/e2e_live
# 预期: 3571 passed, 0 regression

# e2e_smoke 循环
cd octoagent && uv run python -m pytest -m e2e_smoke -p no:cacheprovider apps/gateway/tests/e2e_live
# 预期: 8/8 PASS
```
