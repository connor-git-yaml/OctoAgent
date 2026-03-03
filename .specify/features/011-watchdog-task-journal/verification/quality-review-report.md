# 代码质量审查报告 — Feature 011: Watchdog + Task Journal + Drift Detector

**审查日期**: 2026-03-03
**审查员**: 代码质量审查子代理
**审查范围**: Feature 011 本次新增/修改的 12 个源码文件
**测试结果参考**: 308 个测试全部通过（180 gateway + 128 core）

---

## 四维度评估

| 维度 | 评级 | 关键发现 |
|------|------|---------|
| 设计模式合理性 | GOOD | Strategy 模式清晰可插拔；存在两处常量定义重复（NON_TERMINAL_STATES / _progress_types）未提取到共享位置 |
| 安全性 | GOOD | SQL 构建均使用参数化占位符，无注入风险；trace_id 降级为空字符串存在可观测性盲区（非安全漏洞） |
| 性能 | NEEDS_IMPROVEMENT | TaskJournalService.get_journal() 存在 N+2 查询模式（每个任务 2-3 次 DB 查询），活跃任务 200 时最坏 400-600 次查询 |
| 可维护性 | GOOD | 代码组织清晰，注释规范；`_new_event_id()` 延迟 import 在热路径中轻微影响可读性；`_progress_types` 在循环内重复构建 |

---

## 问题清单

| 严重程度 | 维度 | 位置 | 描述 | 修复建议 |
|---------|------|------|------|---------|
| WARNING | 性能 | `task_journal.py:124-202`（`get_journal` 循环体） | **N+2 查询模式**：每个非终态任务触发 2-3 次独立 DB 查询（`get_latest_event_ts` + `get_events_by_types_since` DRIFT + 条件下再次 `get_events_by_types_since` PROGRESS）。活跃任务 200 时最坏 600 次查询，与 SC-007 "< 2s" 目标存在风险 | 优化路径：① 将 `get_latest_event_ts` 合并到 `get_events_by_types_since` 的一次查询中；② 使用单次聚合 SQL（`GROUP BY task_id` + `MAX(ts)` + 事件类型过滤）批量获取所有任务的最新事件时间戳；③ plan.md 已提及物化视图升级路径，可作为 P2 提前收益 |
| WARNING | 可维护性 | `task_journal.py:30-37` 与 `scanner.py:30-37` | **NON_TERMINAL_STATES 常量重复定义**：两处均定义了相同的 6 个非终态状态列表（`NON_TERMINAL_STATUSES` / `NON_TERMINAL_STATES`），注释明确说明"保持一致"但未强制约束。若 spec FR-011 新增一个非终态状态，需同步修改两处，存在漂移风险 | 将常量提取到 `core/models/enums.py` 或 `watchdog/scanner.py` 中，`task_journal.py` 直接导入复用 |
| WARNING | 可维护性 | `task_journal.py:147-155`（循环体内） | **`_progress_types` 在循环内重复构建**：该 7 元素列表在每次 `for task in tasks` 迭代时都重新构建，且未复用 `detectors.py` 中已定义的 `PROGRESS_EVENT_TYPES` 常量（注释中承认是"复用"但实际独立构建了新列表）。对 200 个任务的循环造成 200 次无效列表构建 | 将 `_progress_types` 提取到循环体外（方法级别常量），或直接 `from .watchdog.detectors import PROGRESS_EVENT_TYPES` 导入复用 |
| WARNING | 设计模式 | `scanner.py:41-43`（`_new_event_id()` 函数） | **延迟导入 `ulid` 在热路径中**：`_new_event_id()` 每次调用都执行 `import ulid`（虽然 Python 有模块缓存机制，开销极小，但属于不规范写法）。APScheduler 每 15s 扫描触发，每次漂移写事件都调用此函数 | 将 `import ulid` 提升到模块级别，与其他导入保持一致 |
| WARNING | 安全性 | `scanner.py:165`（`_emit_drift_event` 中） | **trace_id 透传降级为空字符串**：代码注释 "F012 接入后将从请求上下文中透传真实 trace_id"，当前 `getattr(task, "trace_id", "")` 导致 DRIFT 事件的 `trace_id` 字段始终为空字符串。SC-008 要求"所有 DRIFT 事件携带 task_id 和 trace_id"，当前实现不满足该验收标准（SC-008 是 GATE-M15-WATCHDOG 的门禁条件） | 临时方案：从 Task 关联的 TASK_CREATED 事件中读取 trace_id（`event_store.get_events_by_types_since(TASK_CREATED)` 取第一条）；或 Task 模型增加可选 `trace_id` 字段；长期方案等待 F012 |
| WARNING | 性能 | `event_store.py:154-159`（`_get_task_lock` 方法） | **`_task_locks` 字典无上限增长**：每个新 task_id 创建一个 Lock 并永久存储，TaskService 已有对应清理机制（`_task_locks.pop(task_id, None)`），但 EventStore 的 `_task_locks` 未实现清理。MVP 量级（数十任务）无影响，但长期运行累积 | 参考 `TaskService` 在任务完成后清理 Lock 的模式，或设置 LRU 上限；MVP 阶段可 INFO 级记录 |
| INFO | 可维护性 | `task_journal.py:112`（`drift_since_ts` 计算） | **DRIFT 事件查询窗口选择语义不明确**：使用 `failure_window_seconds`（默认 300s）作为 DRIFT 事件历史查询窗口，但 DRIFT 检测本身与 `failure_window_seconds` 无直接语义关联。注释未解释此选择 | 添加注释说明此窗口选择的理由（"使用 failure_window_seconds 作为 DRIFT 历史回溯窗口，确保 Journal 中能看到最近 5 分钟内的漂移历史"），或独立引入 `journal_drift_window_seconds` 配置项 |
| INFO | 可维护性 | `payloads.py:269`（文件末尾 `from typing import Literal` 导入） | **非标准导入位置**：`from typing import Literal` 放在文件中间（第 269 行），而非标准的文件头部导入区。违反 PEP 8 导入顺序约定，影响可读性 | 将 `from typing import Literal` 与 `from pydantic import BaseModel, Field` 一起移至文件顶部 |
| INFO | 可维护性 | `config.py:61`（`_positive_integer` validator） | **`# type: ignore[override]` 注释说明不足**：使用了 `type: ignore[override]` 但无注释解释为何需要绕过类型检查（实际原因是 Pydantic v2 field_validator 对 `info` 参数的类型推导限制）| 添加内联注释说明原因，如 `# Pydantic v2 FieldValidationInfo 类型推导限制，无法精确类型注解` |
| INFO | 可维护性 | `detectors.py:253`（`failure_type_counts` 变量） | **计算结果 `failure_type_counts` 未被使用**：在第 251-253 行构建了 `failure_type_counts: dict[str, int]` 统计各失败类型计数，但该变量在后续代码中未被 `DriftResult` 使用，仅 `failure_event_types` 列表被使用。死代码存在 | 删除未使用的 `failure_type_counts` 变量，或将其纳入 `DriftResult.failure_event_types` 携带（改为 `dict` 而非 `list`，提升信息密度）|
| INFO | 可维护性 | `models.py:63`（`TaskJournalEntry.task_status`） | **`task_status` 字段类型为 `str` 而非 `TaskStatus`**：注释说明使用内部 TaskStatus 值（Constitution 原则 14），但类型标注为 `str`，运行时无法通过类型系统强制约束。后续代码可能意外传入非 TaskStatus 字符串 | 将类型改为 `TaskStatus`，或添加 Pydantic validator 校验（若改用 Pydantic BaseModel 替代 dataclass） |

---

## Constitution 原则符合性专项核查

| Constitution 原则 | 核查结论 | 证据 |
|-----------------|---------|------|
| C1: Durability First | PASS | `CooldownRegistry.rebuild_from_store()` 在 `startup()` 时从 EventStore 重建；扫描失败降级不丢失检测基准；E2E 场景 4 验证跨重启一致性 |
| C2: Everything is an Event | PASS | 三种 EventType 新增（`TASK_HEARTBEAT` / `TASK_MILESTONE` / `TASK_DRIFT_DETECTED`）；Payload 有强类型定义；DRIFT 事件通过 `append_event_committed` 持久化 |
| C4: Side-effect Must be Two-Phase | PASS | `WatchdogScanner` 绝不调用 task cancel/pause；仅写 `TASK_DRIFT_DETECTED` 信号事件；扫描器头部注释明确标注此硬约束 |
| C7: User-in-Control | PASS | 阈值通过 `WATCHDOG_{KEY}` 环境变量配置；无效值回退默认值不崩溃；P1 Policy Engine 消费 DRIFT 事件前须用户/Policy 门控 |
| C8: Observability is a Feature | PARTIAL | 每次扫描产生结构化日志（FR-008）；DRIFT 事件携带 `task_id`；但 `trace_id` 当前为空字符串（SC-008 未满足，见 WARNING 级问题） |

---

## APScheduler 集成审查

| 检查项 | 结果 | 说明 |
|-------|------|------|
| lifespan 注册方式 | PASS | `AsyncIOScheduler` 在 `lifespan()` 中初始化，`yield` 前启动，`yield` 后 `shutdown(wait=False)` 清理 |
| APScheduler 版本锁定 | PASS | plan.md 明确锁定 `apscheduler<4.0`，避免 4.x API 不兼容 |
| misfire_grace_time 配置 | PASS | 设置为 5s，允许系统繁忙时的执行延迟 |
| scheduler 关闭时序 | PASS | 关闭顺序：scheduler -> task_runner -> store_group.conn，顺序合理 |
| scheduler 暴露到 app.state | PASS | `app.state.watchdog_scheduler` 保存引用，测试/健康检查可访问 |
| event loop 兼容性 | PASS | `AsyncIOScheduler` 与 FastAPI 共享 event loop，无独立线程池冲突 |

---

## 安全性专项核查（OWASP Top 10）

| 风险类别 | 核查结论 | 说明 |
|---------|---------|------|
| SQL 注入 | PASS | `get_events_by_types_since` 和 `list_tasks_by_statuses` 均使用 `","join("?" * N)` 构建参数化 IN 子句，event_type 值来自枚举 `.value`，task_id 为 ULID 字符串——均通过参数绑定，无字符串拼接用户输入的路径 |
| XSS | N/A | 纯后端 API，无直接 HTML 渲染 |
| 硬编码密钥 | PASS | 无硬编码 API Key/密码；WatchdogConfig 仅含数值阈值 |
| 不安全反序列化 | PASS | payload 通过 Pydantic model_dump() 序列化，TaskDriftDetectedPayload 有强类型约束 |
| 路径遍历 | N/A | Watchdog 模块不涉及文件路径操作 |
| 敏感信息泄露 | INFO | DRIFT 事件 payload 携带 `suggested_actions`（字符串列表），无敏感数据；`trace_id` 为空字符串，可观测性受限但非安全风险 |

---

## 测试覆盖率评估

### 测试结构分层

| 测试层级 | 文件 | 覆盖场景 |
|---------|------|---------|
| 单元测试 | `tests/unit/watchdog/test_config.py` | WatchdogConfig 校验、env 加载、无效值降级 |
| 单元测试 | `tests/unit/watchdog/test_cooldown.py` | CooldownRegistry 重建与防抖 |
| 单元测试 | `tests/unit/watchdog/test_no_progress.py` | NoProgressDetector 核心逻辑 + LLM 豁免 |
| 单元测试 | `tests/unit/watchdog/test_state_drift.py` | StateMachineDriftDetector |
| 单元测试 | `tests/unit/watchdog/test_repeated_failure.py` | RepeatedFailureDetector |
| 单元测试 | `tests/unit/watchdog/test_task_journal_service.py` | TaskJournalService 分组规则 |
| 集成测试 | `tests/integration/watchdog/test_scanner.py` | Scanner 全链路：漂移写事件、cooldown 防抖、扫描失败恢复、进程重启重建 |
| 集成测试 | `tests/integration/watchdog/test_journal_api.py` | API 端点集成测试 |
| E2E 测试 | `tests/e2e/test_watchdog_e2e.py` | 4 个场景：卡死/重复失败/状态漂移/进程重启 cooldown 恢复 |
| Core 单元测试 | `core/tests/unit/models/test_watchdog_payloads.py` | Payload 序列化/反序列化 |
| Core 单元测试 | `core/tests/unit/store/test_event_store_extensions.py` | 新增查询接口 |
| Core 单元测试 | `core/tests/unit/store/test_task_store_extensions.py` | list_tasks_by_statuses |

### 覆盖评估

- **优点**: 单元/集成/E2E 三层测试结构完整；E2E 使用 in-memory SQLite + 时间注入，无外部依赖；`scanner.py` 的 FR-007（扫描失败不抛出）通过 mock patch 验证；跨重启 cooldown 重建（SC-006）有专项场景覆盖
- **缺口**: `test_watchdog_e2e.py:414` 的 `asyncio.sleep(2)` 为真实时间等待，CI 环境可能产生偶发性超时失败（建议改用时间注入或 mock）；TaskJournalService 中"任务从漂移恢复到 running"的 User Story 2.3 场景未见专项测试

---

## 总体质量评级

**GOOD**

评级依据：
- **CRITICAL**: 0 个
- **WARNING**: 5 个（N+2 查询模式、两处常量重复、延迟 import、trace_id 空值、_task_locks 无限增长）
- **INFO**: 4 个（DRIFT 查询窗口语义、非标准导入位置、type:ignore 说明、死代码）

总体设计严格遵守 Constitution 四项核心原则（C1/C2/C4/C7）；代码结构清晰，测试分层合理，308 个测试全部通过。主要需关注的是 Task Journal API 的 N+2 查询性能问题（在 MVP 量级下尚可接受，但已处于 SC-007 性能目标的边界区域），以及 trace_id 空值导致的 SC-008 可观测性门禁条件未能完全满足。

---

## 问题分级汇总

- **CRITICAL**: 0 个
- **WARNING**: 5 个
- **INFO**: 4 个
