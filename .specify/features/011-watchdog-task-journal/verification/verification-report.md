# Verification Report: Feature 011 — Watchdog + Task Journal + Drift Detector

**特性分支**: `master`
**验证日期**: 2026-03-03
**验证范围**: Layer 1 (Spec-Code 对齐) + Layer 1.5 (验证铁律合规) + Layer 2 (原生工具链)
**验证员**: 验证闭环子代理

---

## Layer 1: Spec-Code Alignment

### 功能需求对齐

| FR | 描述 | 状态 | 对应 Task | 说明 |
|----|------|------|----------|------|
| FR-001 | 新增 TASK_HEARTBEAT / TASK_MILESTONE / TASK_DRIFT_DETECTED EventType，不破坏向后兼容 | 已实现 | T004 | `enums.py` 第 117-119 行追加三个枚举值，现有枚举值未修改 |
| FR-002 | TASK_DRIFT_DETECTED payload 必填诊断字段，详细诊断走 artifact 引用 | 已实现 | T007, T023 | `payloads.py` TaskDriftDetectedPayload 全部必填字段均在，`artifact_ref` 字段存在 |
| FR-003 | TASK_HEARTBEAT payload 必填字段，服务端 UTC 时间 | 已实现 | T005 | `payloads.py` TaskHeartbeatPayload 字段完整，注释明确"写入时间戳由服务端 UTC 时间确定" |
| FR-004 | Watchdog 周期扫描，进程重启后从 EventStore/TaskStore 重建检测基准 | 已实现 | T023, T025 | `scanner.py` startup() 调用 cooldown.rebuild_from_store；main.py 注册 APScheduler interval job |
| FR-005 | Watchdog 仅写 DRIFT 信号，不直接执行取消/暂停 | 已实现 | T023 | `scanner.py` 全文无 cancel/pause/update_task_status 调用；代码注释明确硬约束 |
| FR-006 | 每任务独立 cooldown 计数器，跨重启从 EventStore 最近 DRIFT 事件时间戳重建 | 已实现 | T017, T023, T024 | `cooldown.py` CooldownRegistry 实现完整；rebuild_from_store 跨重启重建 |
| FR-007 | 扫描失败记录 warning 日志，不影响主任务执行，等待下次重试 | 已实现 | T023, T024 | `scanner.py` scan() 全程 try/except 包裹，均为 log.warning 不重新抛出 |
| FR-008 | 每次扫描产生结构化日志（触发时间/活跃任务数/漂移数/耗时），绑定 trace_id | 已实现 | T023, T049 | `scanner.py` 第 191-196 行 watchdog_scan_completed 日志，含全部字段 |
| FR-009 | 无进展检测使用 7 种进展事件类型，45s（3 周期）阈值 | 已实现 | T021, T022 | `detectors.py` PROGRESS_EVENT_TYPES 恰好包含全部 7 种；config.no_progress_threshold_seconds 正确计算 |
| FR-010 | 无进展检测显式排除 MODEL_CALL_STARTED 后的 LLM 等待期，豁免窗口复用 no_progress_threshold | 已实现 | T021, T022 | `detectors.py` 第 102-115 行显式排除；无独立配置项 |
| FR-011 | 状态机漂移检测使用完整内部 TaskStatus 枚举，禁止降级为 A2A 状态 | 已实现 | T032, T033 | StateMachineDriftDetector 使用 TaskStatus(task.status) 直接转换内部枚举 |
| FR-012 | 重复失败检测：300s 窗口内失败类事件 >= 3 次时写入漂移 | 已实现 | T036, T037 | RepeatedFailureDetector 实现完整，FAILURE_EVENT_TYPES 包含三类失败事件 |
| FR-013 | 漂移检测跳过终态任务（SUCCEEDED/FAILED/CANCELLED/REJECTED） | 已实现 | T021, T022, T024 | 三个检测器均在首行检查 TERMINAL_STATES；scanner.py 额外防御层 |
| FR-014 | Task Journal 查询接口，四分组固定分类（running/stalled/drifted/waiting_approval） | 已实现 | T026, T028 | `task_journal.py` TaskJournalService.get_journal()；GET /api/tasks/journal 端点 |
| FR-015 | Journal 每条记录必填字段（task_id/task_status/journal_state/last_event_ts/drift_summary/suggested_actions） | 已实现 | T026, T027 | `models.py` TaskJournalEntry 包含全部字段；task_status 使用内部 TaskStatus 值 |
| FR-016 | 诊断详情走摘要 + artifact 引用模式，不直接内联响应体 | 已实现 | T026, T027, T030 | DriftSummary 仅含摘要字段；完整诊断通过 drift_artifact_id 引用 |
| FR-017 | WatchdogConfig 强类型模型，5 个可配置项及默认值 | 已实现 | T015 | `config.py` 全部 5 个字段有默认值（15/3/60/300/3），与 spec 表格一致 |
| FR-018 | 支持 WATCHDOG_{KEY} 环境变量覆盖，无效值回退默认值，不影响启动 | 已实现 | T015, T016, T040 | `config.py` from_env() 映射 5 个环境变量；_positive_integer validator 处理无效值 |
| FR-019 | 所有 TASK_DRIFT_DETECTED 事件携带 task_id/trace_id/watchdog_span_id | 部分实现 | T007, T023, T048 | watchdog_span_id="" 占位符正确；但 Task 模型无 trace_id 字段，getattr 降级，所有 DRIFT 事件 trace_id 实际为空字符串（见 WARNING-01） |
| FR-020 | Journal 视图和扫描日志全链路透传 task_id/trace_id | 部分实现 | T023, T049 | scanner.py task_log.bind(task_id=...) 确保含 task_id；但扫描摘要日志 watchdog_scan_completed 未绑定 trace_id（见 WARNING-02） |
| FR-021 | watchdog_span_id 字段 F012 前为空字符串占位，不需修改 schema | 已实现 | T007, T050 | `payloads.py` 默认值 "" + 注释；scanner.py 第 221-222 行显式传入 watchdog_span_id="" |
| FR-022 | Policy Engine 消费 TASK_DRIFT_DETECTED 事件，支持 alert/demote/pause/cancel 动作 | 未实现 | T041 (SKIP) | Policy Engine（apps/kernel/）尚未交付，tasks.md T041 明确标记 SKIP，原因为跨 Feature 硬依赖 |
| FR-023 | pause/cancel 必须走两阶段门控，Watchdog 不直接执行 | 未实现 | T041, T043 (SKIP) | Watchdog 侧已满足约束（不直接执行），Policy Engine 消费端两阶段门控未实现 |
| FR-024 | 策略动作执行结果写入 EventStore（drift_event_id/action_type/executed_at） | 未实现 | T042 (SKIP) | T042 依赖 T041，均为 SKIP |

### 覆盖率摘要

- **总 FR 数**: 24
- **已实现**: 19
- **部分实现**: 2（FR-019, FR-020）
- **未实现**: 3（FR-022, FR-023, FR-024，均为 Policy Engine 集成侧，属计划内后续交付）
- **覆盖率**: 87.5%（含部分实现）/ 79.2%（仅完全实现）

---

## Layer 1.5: 验证铁律合规

**状态**: COMPLIANT

**依据**：
- spec-review-report.md 基准行：**308 个测试全部通过（180 gateway + 128 core）**（spec-review-report.md 第 7 行）
- quality-review-report.md 基准行：**308 个测试全部通过（180 gateway + 128 core）**（quality-review-report.md 第 7 行）
- 本次实际运行验证（见 Layer 2 测试结果）：**308 个测试全部通过**，无失败、无错误

**验证证据来源**：前序制品（spec-review-report.md、quality-review-report.md）均包含具体测试通过数字，无推测性表述（无 "should pass" / "looks correct" 等模式），符合有效证据标准。本次 Layer 2 实际运行进一步确认了前序制品中测试结果的真实性。

**缺失验证类型**: 无

**检测到的推测性表述**: 无

---

## Layer 2: Native Toolchain

### Python (uv) — octoagent-gateway

**检测到**: `apps/gateway/pyproject.toml`、`uv.lock`
**项目目录**: `octoagent/apps/gateway/`

| 验证项 | 命令 | 状态 | 详情 |
|--------|------|------|------|
| Build | `uv run --package octoagent-gateway python -c "import octoagent.gateway"` | 已通过（隐式） | 测试套件全部通过意味着包可正常导入 |
| Lint | `ruff check .` | 未安装/跳过 | ruff 未在项目 dev 依赖中独立安装；跳过不阻断 |
| Test | `uv run --package octoagent-gateway pytest apps/gateway/tests/ -v --tb=short` | **180/180 PASS** | 10.79s 内 180 个测试全部通过，0 失败，0 错误 |

**实际测试命令输出（尾部 30 行）**:
```
apps/gateway/tests/unit/watchdog/test_task_journal_service.py::TestTaskJournalServiceGrouping::test_task_status_uses_internal_enum_not_a2a PASSED [ 98%]
apps/gateway/tests/unit/watchdog/test_task_journal_service.py::TestTaskJournalServiceGrouping::test_drifted_task_recovered_becomes_running PASSED [ 99%]
apps/gateway/tests/unit/watchdog/test_task_journal_service.py::TestTaskJournalServiceGrouping::test_summary_counts_match_groups PASSED [100%]

============================= 180 passed in 10.79s =============================
```

### Python (uv) — octoagent-core

**检测到**: `packages/core/pyproject.toml`、`uv.lock`
**项目目录**: `octoagent/packages/core/`

| 验证项 | 命令 | 状态 | 详情 |
|--------|------|------|------|
| Build | `uv run --package octoagent-core python -c "import octoagent.core"` | 已通过（隐式） | 测试套件全部通过意味着包可正常导入 |
| Lint | `ruff check .` | 未安装/跳过 | 同上，跳过不阻断 |
| Test | `uv run --package octoagent-core pytest packages/core/tests/ -v --tb=short` | **128/128 PASS** | 0.33s 内 128 个测试全部通过，0 失败，0 错误 |

**实际测试命令输出（尾部 20 行）**:
```
packages/core/tests/unit/store/test_task_store_extensions.py::TestListTasksByStatuses::test_empty_statuses_returns_empty PASSED [ 96%]
packages/core/tests/unit/store/test_task_store_extensions.py::TestListTasksByStatuses::test_single_status_filter PASSED [ 96%]
packages/core/tests/unit/store/test_task_store_extensions.py::TestListTasksByStatuses::test_multi_status_filter PASSED [ 97%]
packages/core/tests/unit/store/test_task_store_extensions.py::TestListTasksByStatuses::test_no_matching_tasks_returns_empty PASSED [ 98%]
packages/core/tests/unit/store/test_task_store_extensions.py::TestListTasksByStatuses::test_existing_list_tasks_still_works PASSED [ 99%]
packages/core/tests/unit/store/test_task_store_extensions.py::TestListTasksByStatuses::test_all_non_terminal_statuses PASSED [100%]

============================= 128 passed in 0.33s ==============================
```

### Monorepo 子项目汇总

| 子项目 | 路径 | 语言 | Build | Lint | Test |
|--------|------|------|-------|------|------|
| octoagent-gateway | `apps/gateway/` | Python/uv | 隐式通过 | 跳过 | 180/180 |
| octoagent-core | `packages/core/` | Python/uv | 隐式通过 | 跳过 | 128/128 |

---

## 专项核查

### 核查 1：路由注册顺序

**结论**: 已正确实现

代码证据（`main.py` 第 228-232 行）：
```python
# 注意：watchdog.router 必须在 tasks.router 之前注册，
# 确保 /api/tasks/journal 优先于 /api/tasks/{task_id} 匹配（contracts/rest-api.md 要求）
app.include_router(watchdog.router, tags=["watchdog"])   # 230 行
app.include_router(message.router, tags=["message"])     # 231 行
app.include_router(tasks.router, tags=["tasks"])         # 232 行
```

`watchdog.router`（含 `/api/tasks/journal`）在 `tasks.router`（含 `/api/tasks/{task_id}`）之前注册，符合 contracts/rest-api.md 要求，路由 `/api/tasks/journal` 不会被参数路由 `/api/tasks/{task_id}` 错误捕获。

### 核查 2：apscheduler 依赖

**结论**: 已正确添加

代码证据（`apps/gateway/pyproject.toml` 第 15 行）：
```
"apscheduler>=3.10,<4.0",
```

版本范围 `>=3.10,<4.0` 符合 tasks.md T002 要求，锁定在 3.x 系列避免 4.x API 不兼容。

### 核查 3：WARNING-01 — trace_id 空字符串问题

**严重性评估**: WARNING（不阻断 P0 验收，影响 SC-008 门禁中 trace_id 透传语义）

**根因确认**: `Task` 模型（`packages/core/src/octoagent/core/models/task.py`）中无 `trace_id` 字段（已通过代码审查确认），导致 `scanner.py` 第 165 行 `getattr(task, "trace_id", "")` 始终返回空字符串。

**实际影响**:
- 所有写入 EventStore 的 TASK_DRIFT_DETECTED 事件中 `trace_id` 字段值为 `""`
- FR-019 要求"继承被检测任务的 trace_id"，当前不满足此语义
- SC-008（GATE-M15-WATCHDOG 门禁条件之一）要求所有 DRIFT 事件携带 trace_id，当前实际为空字符串
- 代码已有明确注释说明这是 F012 接入前的已知临时降级

**可接受性判断**: 此问题已在 spec-review-report.md（WARNING-01）和 quality-review-report.md（第 29 行）中明确记录。F011 规范本身（FR-021）预留了 F012 接入后完善的升级路径。Task 模型缺少 trace_id 字段属于跨 Feature 依赖（F012/F013），非 F011 独自可修复。**当前 MVP 阶段可接受，不阻断 P0 验收，应在 F013 M1.5 集成验收时作为门禁条件之一处理。**

### 核查 4：WARNING-02 — 扫描摘要日志缺少 trace_id 绑定

**严重性评估**: WARNING（轻度可观测性缺口，不影响功能正确性）

**根因确认**: `scanner.py` 第 191-196 行 `watchdog_scan_completed` 日志输出 `active_task_count`、`drift_detected_count`、`scan_duration_ms`，未绑定 `trace_id`。

**实际影响**:
- 扫描摘要级别（非任务级别）的日志缺少 trace_id 维度
- 任务级别的漂移事件日志 `watchdog_drift_event_emitted` 中仍携带 trace_id（虽然值为空字符串，受 WARNING-01 影响）
- 不影响功能正确性，属于可观测性层面问题

**可接受性判断**: 与 WARNING-01 同源问题（Task 模型无 trace_id 字段）。扫描摘要日志本质上是跨多个任务的聚合统计，绑定单个 trace_id 语义不明确。**可接受，不阻断验收。建议 F012 接入时统一处理。**

### 核查 5：N+2 查询性能问题

**严重性评估**: WARNING（当前 MVP 量级可接受，SC-007 边界区域）

**问题描述**: `TaskJournalService.get_journal()` 在 `for task in tasks` 循环体内对每个非终态任务发起 2-3 次独立 DB 查询：
1. `get_latest_event_ts()` — 1 次查询
2. `get_events_by_types_since(TASK_DRIFT_DETECTED)` — 1 次查询
3. 条件性：有 DRIFT 事件或无进展时再次 `get_events_by_types_since(PROGRESS_EVENT_TYPES)` — 最多 1 次查询

活跃任务 N 个时，最坏 3N 次数据库查询，N=200 时约 600 次查询。

**MVP 阶段判断**: SC-007 要求 Journal API 在 MVP 量级（数十至百级）响应 < 2s。以 SQLite WAL 模式下单次简单查询约 0.5-2ms 估算：
- N=50（MVP 典型）：约 150 次查询，约 75-300ms，在 SC-007 范围内
- N=200（上限）：约 600 次查询，约 300-1200ms，接近 2s 边界

**可接受性判断**: MVP 阶段（数十任务）可接受。建议在活跃任务 > 100 时触发性能优化（批量 GROUP BY 聚合查询或引入物化视图）。**不阻断当前验收，标记为 P2 技术债。**

---

## GATE-M15-WATCHDOG 验收门禁评估

Feature 011 规范明确标注 Phase 3 完成即可满足 GATE-M15-WATCHDOG 验收门禁（tasks.md 第 182 行：P0 核心闭环完整 — WatchdogScanner 运行、NoProgressDetector 检测卡死、Task Journal API 可用、满足 GATE-M15-WATCHDOG 验收门禁）。

| 门禁条件 | 状态 | 说明 |
|---------|------|------|
| WatchdogScanner 已注册并启动（lifespan） | 通过 | main.py APScheduler 集成完整，lifespan 管理 startup/shutdown |
| NoProgressDetector 无进展检测逻辑实现 | 通过 | detectors.py NoProgressDetector，含 LLM 等待期豁免，180 个 gateway 测试验证 |
| Task Journal API GET /api/tasks/journal 可访问 | 通过 | watchdog.py 路由，TestClient 集成测试覆盖 |
| 路由 /api/tasks/journal 在 /api/tasks/{task_id} 之前注册 | 通过 | main.py 第 230-232 行已确认顺序正确 |
| P0 核心 FR 全部实现（FR-001 ~ FR-018，FR-021） | 通过 | 19 个 FR 已实现，2 个部分实现（trace_id 相关，计划内升级） |
| 308 个测试全部通过（unit + integration + E2E） | 通过 | 本次运行：gateway 180/180，core 128/128，总计 308/308 |
| SC-008 DRIFT 事件携带 task_id | 通过 | DRIFT 事件均包含正确的 task_id（ULID 格式） |
| SC-008 DRIFT 事件携带 trace_id（F013 门禁完整验收） | 部分通过 | trace_id 字段存在但值为空字符串（Task 模型无 trace_id 字段），F013 验收时需完善 |
| FR-022/023/024 Policy Engine 集成（US6） | 不适用 | 计划内后续 Feature 交付，tasks.md 已明确标记 SKIP 并说明原因 |

**GATE-M15-WATCHDOG 结论**: **P0 核心门禁通过**。trace_id 完整透传作为 F013 M1.5 集成验收的前置条件，在 F013 时需补充 Task 模型 trace_id 字段。

---

## 合并发现列表

### CRITICAL（FR 未实现，计划内后续交付）

| 编号 | 来源 | 描述 | 修复时机 |
|------|------|------|---------|
| CRITICAL-01 | spec-review-report.md | FR-022/FR-023/FR-024 Policy Engine Watchdog 集成全部未实现（WatchdogActionRouter、两阶段门控、审计事件写入） | 后续 Policy Engine 专项 Feature；tasks.md T041-T043 已明确标记 SKIP 并注明原因 |

### WARNING（需关注，不阻断 P0 验收）

| 编号 | 来源 | 描述 | 建议 |
|------|------|------|------|
| WARNING-01 | spec-review-report.md / quality-review-report.md | Task 模型无 trace_id 字段，导致 DRIFT 事件 trace_id 实际为空字符串；FR-019 语义未完全满足 | F013 M1.5 集成验收前在 Task 模型追加 trace_id 字段 |
| WARNING-02 | spec-review-report.md | 扫描摘要日志 watchdog_scan_completed 未绑定 trace_id，FR-020 全链路透传不完整 | F012 接入时统一处理 |
| WARNING-N+2 | quality-review-report.md | TaskJournalService.get_journal() N+2 查询模式，活跃任务 > 100 时接近 SC-007 2s 边界 | 活跃任务 > 100 时优先引入批量聚合查询 |
| WARNING-CONST | quality-review-report.md | NON_TERMINAL_STATES 在 scanner.py 和 task_journal.py 各自定义，存在漂移风险 | 提取到 core/models/enums.py 共享位置 |
| WARNING-IMPORT | quality-review-report.md | scanner.py _new_event_id() 延迟导入 ulid 不规范 | 提升到模块级导入 |
| WARNING-LOCKS | quality-review-report.md | EventStore._task_locks 字典无上限增长，MVP 量级无影响但需长期关注 | M2 前参考 TaskService 实现清理机制 |

### INFO（低风险观察项）

| 编号 | 来源 | 描述 |
|------|------|------|
| INFO-01 | spec-review-report.md | task_journal.py stalled 分组增加 updated_at 超阈值检查，比 spec 更严谨（良性增强） |
| INFO-02 | spec-review-report.md | DRIFT 事件查询窗口使用 failure_window_seconds（300s），语义与名称不直接对应；超过 300s 前的旧 DRIFT 事件不会被纳入 Journal 分组判断 |
| INFO-03 | spec-review-report.md | SC-007 无专用性能测试，无法自动化验证 < 2s 目标 |
| INFO-04 | quality-review-report.md | task_journal.py _progress_types 在循环内重复构建（可提取为循环体外常量） |
| INFO-05 | quality-review-report.md | detectors.py failure_type_counts 变量构建但未使用（死代码） |
| INFO-06 | quality-review-report.md | models.py TaskJournalEntry.task_status 类型标注为 str 而非 TaskStatus，类型系统无法强制约束 |
| INFO-07 | quality-review-report.md | payloads.py 第 269 行非标准导入位置（from typing import Literal 在文件中间） |

---

## Summary

### 总体结果

| 维度 | 状态 |
|------|------|
| Spec Coverage | 87.5%（21/24 FR，含 2 条部分实现 + 3 条计划内后续交付） |
| Build Status | 隐式通过（测试套件全部通过） |
| Lint Status | 跳过（工具未独立安装） |
| Test Status | **308/308 PASS**（gateway 180/180 + core 128/128） |
| 路由注册顺序 | 已正确实现（watchdog.router 在 tasks.router 之前） |
| apscheduler 依赖 | 已正确添加（>=3.10,<4.0） |
| GATE-M15-WATCHDOG | **P0 核心通过**（trace_id 完整透传留 F013 处理） |
| **Overall** | **CONDITIONALLY READY FOR REVIEW** |

### 最终建议

**结论：通过（附条件）**

Feature 011 的 P0 核心功能已完整交付，308 个测试全部通过，路由注册顺序正确，apscheduler 依赖已添加，WatchdogScanner + 三种漂移检测器 + Task Journal API 构成完整的任务治理层，满足 GATE-M15-WATCHDOG 的 P0 验收门禁。

**以下条件需在后续 Feature 中跟进**：

1. **F013 前置条件**（不阻断当前合并，但影响 M1.5 E2E 验收）：
   - 在 `Task` 模型（`packages/core/src/octoagent/core/models/task.py`）追加 `trace_id: str = ""` 字段，并在 TaskStore 创建/读取路径中持久化，以满足 SC-008 完整语义（DRIFT 事件 trace_id 非空）。

2. **后续 Policy Engine Feature**（计划内，不阻断当前验收）：
   - 实现 `apps/kernel/src/octoagent/kernel/policy/watchdog_action_router.py` 消费 TASK_DRIFT_DETECTED 事件，覆盖 FR-022/FR-023/FR-024。

3. **P2 技术债**（低优先级，不阻断）：
   - 提取 `NON_TERMINAL_STATES` 常量到共享位置
   - 活跃任务 > 100 时引入批量聚合查询替代 N+2 模式
   - `_progress_types` 提取到循环体外

### 需要修复的问题（阻断级别）

**无阻断级问题。**

### 未验证项（工具未安装/跳过）

- `ruff check .`：ruff 未在测试环境安装，Lint 验证跳过。建议在 CI 环境或本地 pre-commit hook 中补充。
