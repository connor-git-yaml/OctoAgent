# F103c — Worker Log/Error 表面规范化（H1 强化）

**Feature Branch**: `feature/103c-worker-log-error`
**Created**: 2026-05-26
**Status**: Draft
**Mode**: spec-driver-story（1 天预估，跳调研，5 阶段）
**Phase**: M5 → M6 过渡阶段

**Input**：CLAUDE.local.md §"M5 → M6 过渡阶段" F103c。F099 推迟项 N-H1 收尾 + 端到端 review 发现 H1 仅 80%（Worker stderr / error 表面 feedback 未规范化）。

**Baseline 侦察依据**：见 `research/baseline-recon.md`。

---

## 0. 范围澄清（核心，先于 User Stories）

### 0.1 baseline 侦察结论改写了原 prompt 假设

| 原 prompt 假设 | baseline 实测 | F103c 实际范围 |
|---|---|---|
| Worker stderr 泄露 | ✅ **不存在** — 全部走 `structlog → logging.StreamHandler`，无 `print()` / `sys.stderr.write()` | 不做 stderr 改造 |
| Worker logger 不进 EventStore | ✅ **确认** — 54 条 logger 全部 stderr，无 EventStore audit | **核心范围**：升级关键 8 条 logger 走 EventStore |
| Worker error 直接 raise 给用户 | ✅ **不存在** — dispatch exception → log.error + STATE_TRANSITION → NotificationService | 加 `WORKER_ERROR` 独立 EventType + Worker fatal error 走 `priority=high` Notification |
| N-H1 PARTIAL worker restart 需新逻辑 | ✅ **baseline 已 cover** — `resume_state_snapshot` + `EventStore replay` 路径已通 | 仅补 e2e 测试验证，不写新逻辑 |

### 0.2 范围决策表（spec 阶段拍板）

| 决策 | 选择 | 理由 |
|------|------|------|
| 新 EventType | 2 个：`WORKER_LOG_EMITTED` + `WORKER_ERROR` | `WORKER_LOG_EMITTED` 通用 audit，`WORKER_ERROR` 独立语义（对 H1 主 Agent 重要）|
| logger 升级条数 | **8 条**关键升级（见 0.3）| 全升级 28-30 条违反原则 11 + 滚雪球 |
| 是否提供 audit helper | ✅ 提供 `worker_logger.audit(level, key, **payload)` | 让后续按需升级，不锁死本次 8 条 |
| stderr 路由改造 | ❌ 不做 | baseline 无泄露 |
| N-H1 收尾 | 补 e2e test 1-2 个 | baseline 已 cover，无新逻辑 |
| `WORKER_HEALTH` EventType | ❌ 不做 | 用 `WORKER_LOG_EMITTED.payload.level=warning` 区分 |
| NotificationService 集成范围 | 仅 Worker `dispatch_exception`（priority=high）| 其他 logger 走 EventStore 即可，避免通知噪音 |

### 0.3 升级清单（冻结版：7 条 WORKER_LOG_EMITTED + 1 条 WORKER_ERROR）

> **Codex pre-impl review PM2 闭环**：精确冻结至 key 级别。所有清单**不允许 implement 阶段裁剪或新增**。

| # | File:Line | Logger Key（精确）| EventType | NotificationService |
|---|-----------|-----------|-----------|--------------------|
| 1 | `worker_runtime.py:442` | `worker_runtime_emit_is_caller_worker_signal_failed` | `WORKER_LOG_EMITTED` (level=warning) | — |
| 2 | `worker_runtime.py:602` | `worker_runtime_a2a_heartbeat_failed` | `WORKER_LOG_EMITTED` (level=warning) | — |
| 3 | `worker_runtime.py:630` | `worker_runtime_first_output_timeout_budget_exceeded` | `WORKER_LOG_EMITTED` (level=info) | — |
| 4 | `task_runner.py:348` | `subagent_delegation_init_failed` | `WORKER_LOG_EMITTED` (level=warning) | — |
| 5 | `task_runner.py:879` | `attach_input_resume_is_caller_worker_signal_read_failed` | `WORKER_LOG_EMITTED` (level=warning) | — |
| 6 | `task_runner.py:1187` | `task_runner_job_timeout` | `WORKER_LOG_EMITTED` (level=warning) | — |
| 7 | `dispatch_service.py:974` | `a2a_target_profile_resolve_worker_binding_failed` | `WORKER_LOG_EMITTED` (level=warning) | — |
| 8 | `task_runner.py:958` | `run_job_dispatch_exception` | **`WORKER_ERROR`**（独立 EventType）| ✅ priority=HIGH |

**合计**：7 条 `WORKER_LOG_EMITTED` + 1 条 `WORKER_ERROR`。dispatch_service.py 的 `a2a_target_profile_explicit_id_not_found`(988) / `a2a_target_profile_fallback_to_source`(994) 保留 structlog only（不在范围）。

---

## 1. User Scenarios & Testing

### User Story 1 — Worker fatal error 经主 Agent feedback（Priority: P1）

**As** 用户（Connor），**I want** 当 Worker 任务因 exception 失败时，收到主 Agent 的明确解释（"我派出去的 worker 失败了，原因是 X"），**so that** 不会出现 H1 违反（Worker 直接发声或静默失败）。

**Why P1**：H1 是 OctoAgent 三条核心设计哲学之一。M5 端到端 review 自评 H1 仅 80%，Worker fatal error 路径是最显眼的剩余 20%。

**Independent Test**：
1. 手动触发 Worker `dispatch_exception`（模拟 LLM API 5xx 或工具崩溃）
2. 观察事件流：必须看到 `WORKER_ERROR` 事件 + NotificationService `priority=high` 推送
3. NotificationService 推送的消息内容：包含 task_id / error_class / error_summary，**不能**包含完整 stack trace（脱敏，原则 8 + 11）

**Acceptance Scenarios**:

- **AC1-1**: **Given** 用户派单给 Worker A，**When** Worker A 在主 loop 中抛 `RuntimeError`，**Then** EventStore 写入 1 条 `WORKER_ERROR` 事件 + NotificationService 推送 1 条 `priority=high` 通知 + task 状态 = FAILED
- **AC1-2**: **Given** AC1-1 发生，**When** 主 Agent 查询事件流（control_plane API 或 SSE），**Then** `WORKER_ERROR` 事件可见，含 task_id / error_class / error_summary（≤ 200 字符摘要）
- **AC1-3**: **Given** AC1-1 发生，**When** Notification 推送到用户渠道（Web/Telegram），**Then** 文案以主 Agent 口吻输出（"派单 Worker 在执行 X 时失败：Y"），**禁止** Worker 自报式发言

### User Story 2 — Worker 内部 log 走 EventStore audit chain（Priority: P1）

**As** 主 Agent 或用户 advanced 视图，**I want** Worker 关键内部 log（A2A heartbeat 失败、首次输出超时、资源恢复信号失败等）通过 EventStore 可查，**so that** 排障时无需翻 stderr 日志文件。

**Why P1**：原则 2 Everything is an Event + 原则 8 Observability。当前 54 条 worker logger 完全脱离 audit chain。

**Independent Test**：
1. 触发某个升级清单中的 logger（如断网模拟 A2A heartbeat 失败）
2. 查询 EventStore：必须有 `WORKER_LOG_EMITTED` 事件（level=warning, key=`worker_runtime_a2a_heartbeat_failed`）
3. 同时本地 structlog 仍有输出（双轨：本地 debug + EventStore audit）

**Acceptance Scenarios**:

- **AC2-1**: **Given** Worker A 在 A2A 心跳失败时，**When** `worker_runtime.py:602` 触发，**Then** EventStore 写入 1 条 `WORKER_LOG_EMITTED`（task_id, agent_runtime_id, level=warning, key, payload）
- **AC2-2**: **Given** AC2-1，**When** 同时 structlog 输出，**Then** 本地 stderr 仍有原 log（双轨，不破坏现有 debug 流程）
- **AC2-3**: **Given** §0.3 冻结清单 8 条全部触发各一次，**When** 查询 EventStore，**Then** 7 条 `WORKER_LOG_EMITTED` + 1 条 `WORKER_ERROR` 全部可见，**且**每条事件的 `agent_runtime_id` 字段满足：
  - **派生成功路径**：`agent_runtime_id` 为非空字符串（从 `envelope.metadata.get("agent_runtime_id")` 或 `target_agent_runtime_id` 或 `AgentRuntime.agent_runtime_id` 派生）
  - **派生失败路径**：`agent_runtime_id == ""` **且** payload 含 `degraded_reason: "agent_runtime_id_unavailable"`（不允许静默空串）
  - 测试按 key 逐条断言（不允许仅 grep 数量验收）

### User Story 3 — N-H1 resume 路径 e2e 验证（Priority: P2）

**As** 框架可靠性 owner，**I want** worker restart / dispatcher recovery 路径中 `is_caller_worker_signal` 能正确从 `resume_state_snapshot` 恢复，**so that** F099 推迟项有 e2e 测试守护。

**Why P2**：baseline 已 cover（侦察 4），但 0 e2e 测试。补测试避免回归。

**Independent Test**：
1. 启动 worker，发送 caller_worker dispatch
2. 杀掉 worker 进程，从 EventStore replay 恢复
3. 验证 resume 后 `is_caller_worker_signal` 状态正确

**Acceptance Scenarios**:

- **AC3-1**: **Given** Worker A 处理 caller_worker dispatch（CONTROL_METADATA_UPDATED 已写入），**When** 模拟进程重启 + resume，**Then** `worker_runtime.py:543` 从 `resume_state_snapshot.is_caller_worker_signal` 读到正确值
- **AC3-2**: **Given** AC3-1 发生，**When** Worker resume 后继续主 loop，**Then** 行为与重启前一致（is_caller_worker 决策无差异）

### Edge Cases

- **EC1**: Worker 同一 task 短时间内多次 emit `WORKER_LOG_EMITTED`（如 100 次/秒 A2A 失败）→ EventStore 不做限速，由现有 EventStore append-only 写入承担；本次不引入采样（避免范围爆炸；如出现真实压力 → F108 处理）
- **EC2**: Worker `dispatch_exception` 触发时 NotificationService 自身失败 → 必须落 `WORKER_ERROR` 事件（EventStore audit chain 不依赖 Notification）+ log.error 兜底
- **EC2b** (**Codex PM1 闭环**): NotificationService `priority=HIGH` 去重语义：F103c 仅追求**幂等**——`audit_worker_error` 将新 emit 的 `WORKER_ERROR` event_id 作为 `state_transition_event_id` 传给 `notify_task_state_change`（防止同一 event 重复推送）。**不做跨 task / 跨 worker 通知限速**（storm control 推迟 F108）
- **EC3**: 8 条升级 logger 中任一调用点的 store / event_store 实例不可用（如启动期）→ try/except 兜底，回退 structlog only，不抛
- **EC4**: 升级 logger 时 payload 含 secrets / 凭证 → 原则 8 MUST NOT。所有 payload 必须脱敏（不含 token / api_key / 完整 prompt）

---

## 2. Functional Requirements

### FR-A：新 EventType 定义

- **FR-A1**: 在 `packages/core/src/octoagent/core/models/enums.py` `EventType` 枚举新增 `WORKER_LOG_EMITTED` + `WORKER_ERROR` 两个值，遵循过去式命名约定
- **FR-A2**: `WORKER_LOG_EMITTED` payload schema MUST 包含：`task_id`, `agent_runtime_id`（**必填** str，空串仅当 `degraded_reason` 同时设置）, `level`（"info"/"warning"/"error"）, `key`（logger 标识符）, `payload`（dict, 脱敏后字段）, 可选 `agent_session_id`, **可选 `degraded_reason: str | None`**（**Codex PH1 闭环**：用于显式标记 audit chain 降级原因，如 `"agent_runtime_id_unavailable"`）
- **FR-A3**: `WORKER_ERROR` payload schema MUST 包含：`task_id`, `agent_runtime_id`（同 FR-A2 必填语义）, `error_class`（exception 类名）, `error_summary`（≤ 200 字符脱敏摘要）, `traceback_artifact_id`（可选, 完整 traceback 存 artifact 引用，本期固定 None）, 可选 `agent_session_id`, 可选 `degraded_reason`
- **FR-A4**: 两个新 EventType MUST 通过 EventType replay compatibility test（宪法 §IV）

### FR-B：Audit helper

- **FR-B1**: 新增 helper（位置：建议 `apps/gateway/src/octoagent/gateway/services/worker_audit_logger.py`）暴露函数 `audit_worker_log(store, *, task_id, agent_runtime_id, level, key, payload, agent_session_id=None)` → 同时 emit `WORKER_LOG_EMITTED` event + 调 structlog
- **FR-B2**: helper MUST 是 idempotent-safe（payload 含 task_id + 时间戳，重复调用产生独立事件）
- **FR-B3**: helper 当 `store` 为 None 或 EventStore 写入失败时，MUST fallback 到 structlog only + 不抛异常（EC3）

### FR-C：8 条 logger 升级

- **FR-C1**: 上表第 1-8 条（除第 6 条）的 logger 调用点改造为：`audit_worker_log(...) + 保留原 log.{level}(...)` 双轨
- **FR-C2**: 第 6 条（`task_runner.py:958` `run_job_dispatch_exception`）改造：
  - emit `WORKER_ERROR` 事件（含 traceback_artifact_id 引用）
  - 调 `NotificationService.notify_task_state_change(priority=NotificationPriority.HIGH, event_type="WORKER_ERROR", payload={...脱敏})`
  - 保留原 log.error + 现有 STATE_TRANSITION 路径
- **FR-C3**: 升级清单（FR-C1 + FR-C2）MUST 不破坏现有行为：task 仍 FAILED、structlog 仍输出、STATE_TRANSITION 仍 emit（双轨叠加，无替换）

### FR-D：N-H1 resume e2e 测试

- **FR-D1**: 新增 1 个 pytest 测试用例验证：CONTROL_METADATA_UPDATED 写入 → 模拟进程重启 / `_resume_engine.try_resume()` → resume_state_snapshot 含 `is_caller_worker_signal` → worker_runtime 正确读取
- **FR-D2**: 测试 MUST 直调 baseline 路径（不引入新代码），用以 lock 当前行为防止回归

### FR-E：脱敏 + 范围保护

- **FR-E1**: `WORKER_LOG_EMITTED` / `WORKER_ERROR` payload 字段 MUST 脱敏：禁止包含 `api_key` / `token` / 完整 prompt / Vault 内容（原则 8 + 5）
- **FR-E2**: `error_summary` MUST ≤ 200 字符；完整 traceback 通过 artifact 引用（原则 11 上下文卫生）
- **FR-E3**: 不对 54 条 logger 全升级；不引入采样限速；不改造 stderr 路由（范围严格 1 天）

---

## 3. Key Entities

### Entity 1 — `WORKER_LOG_EMITTED` event

| 字段 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `task_id` | str | ✅ | audit chain key |
| `agent_runtime_id` | str | ✅ | 配合 RecallFrame audit；空串**仅当** `degraded_reason` 同时填写 |
| `level` | str enum | ✅ | "info" / "warning" / "error" |
| `key` | str | ✅ | logger 标识符（原 structlog key 名）|
| `payload` | dict | ✅ | 脱敏后的字段，原 structlog kwargs |
| `agent_session_id` | str \| None | ❌ | 可选会话关联 |
| `degraded_reason` | str \| None | ❌ | 显式 audit chain 降级原因（Codex PH1 闭环）|

### Entity 2 — `WORKER_ERROR` event

| 字段 | 类型 | 必选 | 说明 |
|------|------|------|------|
| `task_id` | str | ✅ | audit chain key |
| `agent_runtime_id` | str | ✅ | 同 Entity 1 语义 |
| `error_class` | str | ✅ | exception `__class__.__name__` |
| `error_summary` | str | ✅ | ≤ 200 字符脱敏摘要 |
| `traceback_artifact_id` | str \| None | ❌ | 本期固定 None；artifact 路径留 F108 |
| `agent_session_id` | str \| None | ❌ | |
| `degraded_reason` | str \| None | ❌ | 同 Entity 1 |

### Entity 3 — `audit_worker_log` helper

```python
async def audit_worker_log(
    store: StoreGroup,
    *,
    task_id: str,
    agent_runtime_id: str,
    level: Literal["info", "warning", "error"],
    key: str,
    payload: dict[str, Any],
    agent_session_id: str | None = None,
) -> None:
    """同时 emit WORKER_LOG_EMITTED + structlog 双轨"""
```

---

## 4. 不在范围（明确排除）

| 排除项 | 理由 |
|--------|------|
| 54 条 logger 全升级 | 违反原则 11 + 范围爆炸；提供 helper 让后续按需 |
| WORKER_HEALTH / WORKER_HEARTBEAT EventType | 用 WORKER_LOG_EMITTED.level 区分 |
| stderr 路由改造 / sink 切换 | baseline 无泄露 |
| structlog → EventStore 框架级集成（如 structlog processor）| 范围太大，与 helper 选择正交 |
| Worker log 采样 / 限速 | 现有 EventStore append-only 承担，真实压力出现再处理 |
| N-H1 worker restart 新逻辑 | baseline 已 cover |
| docs/blueprint/* 任何 .md 文件 | F103b 范围（并行硬约束）|
| F108 范围（D9/D11/D12 + F090 D2 + F101 4 项推迟）| F108 独立 |
| F102 routine 推迟项（WeeklyRoutine 等）| F102 独立 |

---

## 5. Success Criteria

### SC-1: H1 强化可见证据

- **SC1-1**: AC1-1 / AC1-2 / AC1-3 全部 PASS（Worker fatal error 经主 Agent feedback）
- **SC1-2**: AC2-1 / AC2-2 / AC2-3 全部 PASS（关键 8 条 logger 走 EventStore）
- **SC1-3**: AC3-1 / AC3-2 全部 PASS（N-H1 resume e2e 验证）

### SC-2: 测试基线（宪法 §IV）

- **SC2-1**: WORKER_LOG_EMITTED + WORKER_ERROR event schema 通过 replay compatibility test
- **SC2-2**: 新增 unit test 覆盖 helper + WORKER_ERROR notification 集成（≥ 5 cases）
- **SC2-3**: 全量回归 0 regression vs F103 baseline (def6638)，e2e_smoke PASS

### SC-3: 安全 + 上下文卫生

- **SC3-1**: WORKER_LOG_EMITTED / WORKER_ERROR payload 脱敏审查（grep 测试 payload 不含 `token` / `api_key` / 完整 prompt 字段名）
- **SC3-2**: `error_summary` ≤ 200 字符（assert in test）

### SC-4: H1 表面规范化端到端审计

- **SC4-1**: grep 验证 `apps/gateway/.../services/` 下无新增 `print()` / `sys.stderr.write()`
- **SC4-2**: 升级清单 8 条 logger 全部双轨（grep 验证既调 `audit_worker_log` 又有原 `log.warning/error`）

---

## 6. 与 F103b 并行隔离

| 维度 | F103b | F103c |
|------|------|------|
| 范围 | docs/blueprint/*.md 三个文件 | 代码 + 测试，**严禁动 .md** |
| 分支 | feature/103b-blueprint-limitations | feature/103c-worker-log-error |
| 合并顺序 | 先合 F103b（纯文档无冲突）| 后合 F103c（Phase 5 前 rebase F103b 完成的 master）|
| 冲突风险 | 零（文件无交集）| 仅 rebase 时验证 |

---

## 7. Codex Adversarial Review 安排

- **pre-impl review**（spec + plan 完成后，implement 前）：1 轮 Codex review
- **per-Phase review**：每 Phase 完成后 1 轮（FR-A / FR-B / FR-C / FR-D 任一节点）
- **Final cross-Phase review**：所有 Phase 完成后 1 轮（必走）
- **总预期 review**：3-5 轮（spec 阶段拍板可能调整）

---

## 8. Carry-forward 决策（不阻 F103c）

- **WORKER_LOG_EMITTED 采样/限速**：如出现真实 EventStore 压力 → F108 处理
- **54 条 logger 中剩余 ~20 条升级**：后续按需 case-by-case，通过 helper 实现
- **structlog processor 框架级集成**：超 1 天范围，留 F108+

---

## 9. 风险点 + 缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| EventStore append 写入失败 → 影响 worker 主 loop | 低 | FR-B3 兜底（fallback structlog only，不抛）|
| Notification.HIGH 通知风暴（同一 worker 频繁 dispatch_exception）| 中 | F103c 通过传 event_id 至 `state_transition_event_id` **做幂等**（防同事件重复）；**不做跨 task storm 限速**（推迟 F108）。Codex PM1 闭环 |
| 8 条升级清单选错（应升级而未升级 / 不应升级而升级）| 中 | spec 阶段已 freeze，Codex pre-impl review 把关 |
| N-H1 e2e test 涉及进程重启难写 | 中 | 直调 `_resume_engine.try_resume()` 路径，不真重启进程 |
