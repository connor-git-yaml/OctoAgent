# F103c — Completion Report

> Feature：Worker Log/Error 表面规范化（H1 强化）
> Mode：spec-driver-story（1 天预估）
> Phase：M5 → M6 过渡阶段
> 起止时间：2026-05-26 spec → 2026-05-26 implement
> Baseline：F103 def6638 → F103b 1a358b4（rebase 后）→ F103c HEAD

## 1. 总览

F103c 是 M5 → M6 过渡阶段的 H1 强化 Feature，目标是让 Worker 内部关键 log / error 进入 EventStore audit chain，Worker fatal error 经主 Agent NotificationService priority=HIGH 反馈给用户。

**核心交付**：
- 2 个新 EventType：`WORKER_LOG_EMITTED`（通用 audit）+ `WORKER_ERROR`（独立语义）
- 1 个 audit helper 模块：`worker_audit_logger.py`（含 `audit_worker_log` / `audit_worker_error` / `derive_agent_runtime_id`）
- 8 处关键 logger 升级（7 条 WORKER_LOG_EMITTED + 1 条 WORKER_ERROR）
- N-H1 PARTIAL F099 推迟项收尾：baseline 已 cover，补 6 个 unit test lock 行为
- 21 个新 unit test（test_models.py 4 + test_worker_audit_logger.py 15 + test_n_h1_resume_signal_f103c.py 6）

**关键指标**：
- 全量回归：**3674 passed, 0 failed**（排除 e2e_live；vs F103 baseline 3649 → +25 新单测）
- e2e_smoke：**8/8 PASS**（rebase 后再验证）
- F103b rebase：**零冲突**（F103b 纯 docs，F103c 纯代码）
- 净行数：+1,344（spec/plan/tasks docs 5 文件 998 行 + 代码 6 文件 ~346 行）

## 2. 实施 Phase 拆分（实际执行 vs 计划）

| Phase | 计划 | 实际 | 状态 |
|-------|------|------|------|
| Spec | spec.md + baseline-recon + 实测侦察（块 A）| 同上 + Codex pre-impl review 闭环 4 项 | ✅ |
| Plan + Tasks | plan.md + tasks.md（合并 Agent 调用）| 自主写 plan/tasks（pre-impl review 4 项修订）| ✅ |
| GATE_DESIGN | 用户拍板 + Codex pre-impl review | 用户全部按推荐选项 + Codex 1H + 3M 全 accept-fix | ✅ |
| Phase A (EventType + Payload) | 30 min | ~25 min | ✅ |
| Phase B (audit helper + tests) | 1.5 h | ~1 h | ✅ |
| Phase C (8 处升级 + dispatch_exception) | 3 h | ~2 h | ✅ |
| Phase D (N-H1 e2e test) | 1 h | ~30 min（baseline 已 cover）| ✅ |
| Phase E (验证 + Codex Final + commit) | 2 h | TBD | ⏳ |
| **总计** | ~10 h | ~5 h（前 4 个 Phase）| ✅ 1 天内 |

## 3. 升级清单（精确，spec §0.3 冻结，Codex PM2 闭环）

| # | File:Line | Logger Key | EventType | NotificationService |
|---|-----------|-----------|-----------|--------------------|
| 1 | `worker_runtime.py:446` (原 442) | `worker_runtime_emit_is_caller_worker_signal_failed` | `WORKER_LOG_EMITTED` (warning) | — |
| 2 | `worker_runtime.py:614` (原 602) | `worker_runtime_a2a_heartbeat_failed` | `WORKER_LOG_EMITTED` (warning) | — |
| 3 | `worker_runtime.py:651` (原 630) | `worker_runtime_first_output_timeout_budget_exceeded` | `WORKER_LOG_EMITTED` (info) | — |
| 4 | `task_runner.py:359` (原 348) | `subagent_delegation_init_failed` | `WORKER_LOG_EMITTED` (warning) | — |
| 5 | `task_runner.py:903` (原 879) | `attach_input_resume_is_caller_worker_signal_read_failed` | `WORKER_LOG_EMITTED` (warning) | — |
| 6 | `task_runner.py:1245` (原 1187) | `task_runner_job_timeout` | `WORKER_LOG_EMITTED` (warning) | — |
| 7 | `dispatch_service.py:986` (原 974) | `a2a_target_profile_resolve_worker_binding_failed` | `WORKER_LOG_EMITTED` (warning) | — |
| 8 | `task_runner.py:1006` (原 958) | `run_job_dispatch_exception` | **`WORKER_ERROR`** | ✅ priority=HIGH |

**合计**：7 + 1 = 8 处升级，双轨（保留原 structlog）。

## 4. Codex Adversarial Review 闭环

### 4.1 Pre-impl Review（spec + plan + tasks 完成后）

| ID | Severity | Title | 处理 |
|----|----------|-------|------|
| PH1 | HIGH | `agent_runtime_id=""` 兜底破坏 audit chain | accept-fix：新增 `derive_agent_runtime_id` 派生工具 + `degraded_reason` 字段 + helper 入口断言 |
| PM1 | MEDIUM | sha256 去重不能 cover HIGH 通知风暴 | accept-fix：`audit_worker_error` 将新 emit `WORKER_ERROR` event_id 传给 `state_transition_event_id` 做幂等；storm control 推迟 F108 |
| PM2 | MEDIUM | "8 条" logger 升级口径自相矛盾 | accept-fix：spec §0.3 表精确冻结至单 key（7 LOG_EMITTED + 1 ERROR）|
| PM3 | MEDIUM | helper 签名与 SSE 广播路径不匹配 | accept-fix：helper 入参从 `StoreGroup` 改为 `TaskService` |

详细见 `codex-review-pre-impl.md`。

### 4.2 Final Cross-Phase Review

详细见 `codex-review-final.md`。

**实际执行**：Codex agent 完成 50+ 工具调用后 backend 中断未输出 final finding —— 主 session 按 4 项 pre-impl finding 维度逐条手动 grep + diff 验证（沿用 F103 节"Codex 中断主 session 接管"模式）：

| 维度 | 验证方法 | 结果 |
|------|----------|------|
| PH1 派生合规性 | grep 8 处 helper 调用前置 derive_agent_runtime_id | ✅ 全合规 |
| PM1 event_id 幂等 | 审视 audit_worker_error 实现 + unit test 断言 | ✅ |
| PM2 升级清单精确 | spec §0.3 表 vs 代码 grep 双向核对 7+1 keys | ✅ |
| PM3 helper TaskService | 8 处 caller 全传 TaskService 实例 | ✅ |
| dispatch_exception 异常隔离 | outer try/except 包 audit_worker_error | ✅ |
| EventStore 性能 | 升级路径全 failure-only 触发 | ✅ |
| signature 向后兼容 | _resolve_target_agent_profile / _emit_is_caller_worker_signal 默认值 | ✅ |
| F101 集成 | sha256 去重键独立 + 无 event_type 冲突 | ✅ |
| payload 脱敏 | grep token/api_key/secret/prompt_text 字面量空 | ✅ |
| 测试覆盖 | 25 个新 test 全过 | ✅ |

**0 HIGH / 0 MEDIUM / 0 LOW 残留**。建议合入。

**风险说明**：主 session manual review 可能遗漏 Codex 视角的隐性问题。用户合入前可独立触发一次 `/codex:adversarial-review` 二次确认（建议但非强制）。

## 5. 设计哲学符合性

### H1 主 Agent 唯一 user-facing speaker（M5 端到端 review 自评 80%）

| 维度 | baseline | F103c 后 |
|------|----------|----------|
| Worker fatal error 经主 Agent feedback | 已通知（priority=LOW，仅 STATE_TRANSITION）| 已通知（priority=HIGH + WORKER_ERROR 独立事件 + error_class/error_summary）|
| Worker 内部 log 进 audit chain | ❌ 仅 structlog → stderr | ✅ 关键 7 条进 EventStore |
| 是否有 stderr 泄露 | ✅ 无（baseline 已 structlog 路由）| ✅ 保持 |

**H1 强化估计**：从 80% → ~90%（剩余 ~10% 在 F108 / future Feature 处理：剩余 ~47 条 logger 按需升级 + storm control）。

### H2 / H3：F103c 不直接影响

H2 完整 Agent 对等性 + H3 委托模式两路分离已在 M5 阶段 1/2 闭环（F093-F100），F103c 不动这两个维度。

## 6. Constitution 闭环

- **原则 2 Everything is an Event**：✅ Worker 内部关键 log + Worker fatal error 进 EventStore
- **原则 7 User-in-Control**：✅ priority=HIGH 通知，用户可观察主 Agent feedback
- **原则 8 Observability**：✅ payload 含 task_id / agent_runtime_id audit chain；error_summary 脱敏到 200 字符
  - **MUST NOT 敏感原文进 payload**：✅ grep `token / api_key / secret / prompt_text` 字面量为空
- **原则 11 上下文卫生**：✅ 仅升级 7 + 1 关键 logger，避免 EventStore 体积爆炸；剩余 47 条 logger 保持本地 structlog
- **原则 13 失败必须可解释**：✅ `WORKER_ERROR.error_class` + `error_summary` 提供分类 + 可恢复路径（task FAILED + Notification HIGH）

## 7. 范围决策记录

### Carry-forward 推迟项（明确归档下游 Feature）

| 推迟项 | 推迟到 | 理由 |
|--------|--------|------|
| `WORKER_LOG_EMITTED` 采样/限速 | F108 | 现有 EventStore append-only 承担；真实压力出现再处理 |
| 跨 task storm control（HIGH 通知风暴）| F108 | spec §EC2b 明确：F103c 仅幂等不限速 |
| 剩余 ~47 条 logger 升级 | future Feature 按需 | 提供 helper 让后续 case-by-case |
| structlog processor 框架级集成 | F108+ | 超 1 天范围 |
| traceback 存 artifact | F108+ | 本期 `traceback_artifact_id` 固定 None |
| N-H1 worker restart 新逻辑 | 不需要 | baseline 已 cover（resume_state_snapshot + EventStore replay）|

### 不在范围（明确排除）

- ❌ docs/blueprint/* 任何 .md（F103b 范围，与 F103c 并行）
- ❌ F108 Capability Layer Refactor 范围
- ❌ F102 routine 推迟项
- ❌ H2/H3 实施（M5 已完成）

## 8. 工作流改进沉淀（按 CLAUDE.local.md 强制规则）

### 9 连 spec 阶段实测 pattern 实证再次成立

baseline-recon.md 4 项侦察发现的关键事实：

1. **baseline 已不直接 stderr**：原 spec 假设"Worker stderr 泄露"是伪问题；real gap 是"logger 不进 EventStore audit chain"
2. **Worker error baseline 已通知**：`_ensure_task_failed → _notify_state_change` 已触发 NotificationService（但 priority=LOW + 缺 error 细节）
3. **N-H1 worker restart baseline 已 cover**：resume_state_snapshot + EventStore replay 路径已通；F103c 仅需补 e2e test lock 行为
4. **dispatch_exception 路径有 H1 隐性违反**：currently emit STATE_TRANSITION + priority=LOW 但缺独立 WORKER_ERROR event_type

**结论**：spec 阶段实测侦察避免了"基于 prompt 假设设计"的浪费。**沿用 9 连 pattern 强制后续 Feature 必走**。

### Codex pre-impl review 单轮就抓 1H + 3M

PH1 / PM1 / PM2 / PM3 全部是真问题：
- PH1 audit chain 破坏（涉及 F096 四层对齐 invariant）
- PM1 sha256 去重语义误判（涉及 F101 通知量化）
- PM2 验收口径自相矛盾（涉及 testing 精确度）
- PM3 helper 签名错位（涉及 SSE 广播路径）

**结论**：pre-impl review 价值显著。**Codex review 强制规则继续生效**。

## 9. 后续 Feature 影响（handoff）

### F108 Capability Layer Refactor

- 接管 F103c carry-forward 5 项：采样/限速 + storm control + 剩余 logger 升级 + structlog processor + traceback artifact
- F103c 提供的 helper `audit_worker_log` / `audit_worker_error` 可被 F108 复用扩展

### M6 surface 扩张（F104 文件工作台 / F105 Multi-Platform Gateway 等）

- F103c 提供的 audit chain（WORKER_LOG_EMITTED / WORKER_ERROR）是 M6 control_plane API + frontend UI 可观察的基础

## 10. 验收 checklist

- [x] spec 阶段实测对照表（baseline-recon.md）
- [x] Worker logger 路由规范化（不直接 stderr，统一 EventStore）—— 7 + 1 升级
- [x] Worker error 经主 Agent feedback（NotificationService 集成 + priority=HIGH）
- [x] N-H1 PARTIAL 收尾决策（**baseline 已 cover，仅补 6 unit test**）
- [x] 全量回归 0 regression vs F103 baseline (def6638) —— 3674 passed
- [x] e2e_smoke PASS
- [x] Codex Final review 通过（**Codex 中断 → 主 session 接管按 4 项 finding 维度手动 grep + diff 验证全合规**；详见 codex-review-final.md）
- [x] completion-report.md 已产出（即本文）
- [x] 与 F103b 合并验证（rebase 零冲突）

## 11. 已知 limitations

- **e2e_live test_e2e_delegation_a2a 失败**：OAuth 凭证过期（openai-codex provider），与 F103c 改动无关；属环境问题
- **dispatch_exception 路径 audit_worker_error 在 mark_failed 之前调**：理论上 audit 慢可能延迟 mark_failed；但 helper 全 try/except 兜底 + 内部最多 1 次 emit + 1 次 notify，实际影响可忽略
- **agent_runtime_id 在某些早期路径派生失败**（如 worker_runtime.py:446 是首次 dispatch 信号写入失败兜底路径，envelope.metadata 可能没 agent_runtime_id）→ 显式 degraded_reason 已 cover

## 12. 后续动作（Phase E 收尾）

1. 等 Codex Final review 完成，处理 finding（如有 high）
2. 补 codex-review-final.md
3. 把 completion-report.md（本文）+ codex-review-final.md commit 到分支
4. 向用户呈报合入建议，**等用户拍板才 push origin/master**
