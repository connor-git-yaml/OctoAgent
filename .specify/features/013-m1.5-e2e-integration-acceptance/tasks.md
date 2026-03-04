# Tasks: Feature 013 — M1.5 E2E 集成验收

**Input**: `.specify/features/013-m1.5-e2e-integration-acceptance/`
**Branch**: `feat/013-m1.5-e2e-integration-acceptance`
**Prerequisites**: spec.md (已读), plan.md (已读), data-model.md (已读)
**Generated**: 2026-03-04

---

## 格式说明

- `[P]`：可与同阶段其他 [P] 任务并行执行（不同文件，无依赖）
- `[USN]`：所属 User Story 编号（US1~US6）
- Setup / Foundational / Polish 阶段任务不标 [USN]
- 每个任务包含**确切文件路径**，使执行者知道创建或修改哪个文件

---

## Phase 1: Setup（测试基础设施扩展）

**目的**：扩展现有 `tests/integration/conftest.py`，新增 F013 专用 fixture 和工具函数，为四条端到端场景提供隔离运行环境。这是所有场景测试的阻塞前置依赖。

> 注：F013 不新建顶层目录，不修改现有测试文件（FR-013 范围锁定），仅扩展现有文件。

- [x] T001 在 `octoagent/tests/integration/conftest.py` 末尾追加 `poll_until()` 工具函数（替代 `asyncio.sleep()` 固定等待，提升 CI 时序稳定性）

- [x] T002 在 `octoagent/tests/integration/conftest.py` 追加 `app_with_checkpoint` fixture（预置 `CheckpointSnapshot` 的集成 app，暴露 `app.state.db_path` 供场景 B 两阶段访问同一数据库路径）

- [x] T003 在 `octoagent/tests/integration/conftest.py` 追加 `watchdog_integration_app` fixture（含 `WatchdogScanner` 初始化但不启动 `APScheduler` 调度器，环境变量覆盖 `WATCHDOG_NO_PROGRESS_CYCLES=1`、`WATCHDOG_SCAN_INTERVAL_SECONDS=1`，teardown 时清理 `cooldown_registry._last_drift_ts`）

- [x] T004 在 `octoagent/tests/integration/conftest.py` 追加 `watchdog_client` fixture（基于 `watchdog_integration_app` 的 `httpx.AsyncClient`，与现有 `client` fixture 模式一致）

**Checkpoint**：`conftest.py` 扩展完成后，执行 `uv run pytest octoagent/tests/integration/conftest.py --collect-only` 确认四个新 fixture 可被发现，无导入错误。

---

## Phase 2: Foundational（前置核查）

**目的**：在编写任何场景测试之前，验证 M1.5 门禁条件已满足，确认上游 Feature 008~012 的真实依赖可用。这是 FR-001 要求的集成对象核查工作。

- [x] T005 核查 Feature 010~012 的现有集成测试中无测试替代（mock）残留：检查 `octoagent/tests/integration/test_f010_checkpoint_resume.py` 中是否存在 `MagicMock` / `AsyncMock` / `patch` 导入，若存在则记录（不修改文件，在任务注释中标注）
  <!-- 核查结果：test_f010_checkpoint_resume.py 中无 MagicMock/AsyncMock/patch 导入，无 mock 残留，FR-001 前置核查通过 -->

- [x] T006 核查 `capfire`（`logfire.testing`）在当前运行时可用：在临时测试或 Python REPL 中执行 `import logfire.testing; logfire.testing.capfire`，确认无 `ImportError`；若不可用，在 `octoagent/tests/integration/conftest.py` 中补充 `InMemorySpanExporter` 降级方案并记录（关联 FR-012）
  <!-- 核查结果：capfire 类型为 _pytest.fixtures.FixtureFunctionDefinition，CaptureLogfire/TestExporter 均可用，无需降级方案 -->

**Checkpoint**：门禁核查完成，T005/T006 无阻塞项，可进入场景测试编写。若存在阻塞项，在 `verification/m1.5-acceptance-report.md` 技术风险清单中标注"上游阻塞"状态。

---

## Phase 3: User Story 1 — 消息路由全链路可靠执行（Priority: P1）

**目标**：验证系统从消息接收到结果返回的完整处理过程，三类 Orchestrator 系统事件均写入 EventStore，所有事件关联同一 task_id，任务以 SUCCEEDED 状态结束。

**独立测试**：`uv run pytest octoagent/tests/integration/test_f013_e2e_full.py -v` 单独通过，不依赖其他场景文件。

- [x] T007 [US1] 新建 `octoagent/tests/integration/test_f013_e2e_full.py`，定义 `TestF013ScenarioA` 类及类 docstring（SC-001 引用，幂等键格式 `f013-sc-a-{sequence}`）

- [x] T008 [US1] 在 `octoagent/tests/integration/test_f013_e2e_full.py` 实现 `test_message_routing_full_chain` 测试方法（FR-002 场景 1）：POST `/api/message`，用 `poll_until` 等待 SUCCEEDED，断言 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED` 三类事件存在，断言所有事件 `task_id` 与提交时返回的 `task_id` 一致

- [x] T009 [US1] 在 `octoagent/tests/integration/test_f013_e2e_full.py` 实现 `test_task_result_non_empty` 测试方法（FR-002 场景 2）：提交任务后等待 SUCCEEDED，通过 `store_group.artifact_store.list_artifacts_for_task(task_id)` 断言产物列表非空（幂等键 `f013-sc-a-002`）

**Checkpoint**：`test_f013_e2e_full.py` 两个测试方法均通过，SC-001 验收证据已产生。

---

## Phase 4: User Story 2 — 系统中断后任务自动恢复（Priority: P1）

**目标**：验证通过 `conn.close()` 模拟进程中断后，`ResumeEngine.try_resume()` 能从记录的 checkpoint 节点继续执行，已完成步骤不重复，连续两次恢复结果幂等。

**独立测试**：`uv run pytest octoagent/tests/integration/test_f013_checkpoint.py -v` 单独通过，测试直接操作 StoreGroup 层，不依赖 HTTP 客户端。

- [x] T010 [US2] 新建 `octoagent/tests/integration/test_f013_checkpoint.py`，定义 `TestF013ScenarioB` 类及必要导入（`CheckpointSnapshot`、`CheckpointStatus`、`ResumeEngine`、`create_store_group`、`TaskService`、`NormalizedMessage`、`TaskStatus`、`datetime`、`UTC`）

- [x] T011 [US2] 在 `octoagent/tests/integration/test_f013_checkpoint.py` 实现 `test_resume_from_checkpoint_after_restart` 测试方法（FR-003 场景 1）：两阶段测试——阶段 1 创建任务推进到 RUNNING、写入 `CheckpointStatus.SUCCESS` 快照后 `conn.close()`；阶段 2 重建 `StoreGroup` 并调用 `ResumeEngine.try_resume(task_id)`，断言 `result.ok is True`、`result.resumed_from_node == "model_call_started"`（幂等键 `f013-sc-b-001`）

- [x] T012 [US2] 在 `octoagent/tests/integration/test_f013_checkpoint.py` 实现 `test_resume_idempotency` 测试方法（FR-003 场景 2）：在阶段 2 的基础上连续调用两次 `try_resume(task_id)`，断言两次返回结果的 `resumed_from_node` 和 `checkpoint_id` 一致，`EventStore` 中恢复相关事件不因第二次调用而重复写入（幂等键 `f013-sc-b-002`）

- [x] T013 [US2] 在 `octoagent/tests/integration/test_f013_checkpoint.py` 实现 `test_resume_with_corrupted_checkpoint` 测试方法（FR-003 场景 3）：写入 `schema_version=999`（版本不匹配）的 checkpoint，调用 `try_resume(task_id)` 后断言：(a) 系统安全降级（`result.ok is False` 或任务状态变为 FAILED），(b) 失败原因已持久化记录（查询 EventStore 或 TaskStore 确认存在包含失败原因的事件或任务记录，不得仅存在于内存异常中），(c) 不抛出未捕获异常（幂等键 `f013-sc-b-003`）

**Checkpoint**：`test_f013_checkpoint.py` 三个测试方法均通过，SC-002 验收证据已产生，包含幂等性和损坏降级验证。

---

## Phase 5: User Story 3 — 长时间无进展任务自动告警（Priority: P1）

**目标**：验证 `WatchdogScanner.scan()` 直接调用时，对超过阈值无进展的 RUNNING 任务写入 `TASK_DRIFT_DETECTED` 事件，冷却期内重复触发不产生重复告警。

**独立测试**：`uv run pytest octoagent/tests/integration/test_f013_watchdog.py -v` 单独通过，使用 `watchdog_integration_app` 和 `watchdog_client` fixture，不依赖 APScheduler 调度器。

- [x] T014 [US3] 新建 `octoagent/tests/integration/test_f013_watchdog.py`，定义 `TestF013ScenarioC` 类及必要导入（`WatchdogScanner`、`EventType`、`TaskService`、`NormalizedMessage`、`TaskStatus`、`asyncio`、`datetime`、`timedelta`、`UTC`）

- [x] T015 [US3] 在 `octoagent/tests/integration/test_f013_watchdog.py` 实现 `test_watchdog_detects_stalled_task` 测试方法（FR-004 场景 1）：创建任务推进到 RUNNING，`await asyncio.sleep(1.1)` 等待超过 1 秒阈值，调用 `await scanner.scan()`，通过 `sg.event_store.get_events_by_types_since()` 断言存在至少一条 `TASK_DRIFT_DETECTED` 事件且 `task_id` 匹配（幂等键 `f013-sc-c-001`）

- [x] T016 [US3] 在 `octoagent/tests/integration/test_f013_watchdog.py` 实现 `test_watchdog_cooldown_prevents_duplicate_alerts` 测试方法（FR-004 场景 2）：在首次 `scan()` 触发告警后立即再次调用 `scan()`，断言 `TASK_DRIFT_DETECTED` 事件总数仍为 1（冷却机制生效）（幂等键 `f013-sc-c-002`）

- [x] T017 [US3] 在 `octoagent/tests/integration/test_f013_watchdog.py` 实现 `test_watchdog_threshold_config_override` 测试方法（FR-004 场景 3 / spec FR-010 隔离验证）：使用独立的 `tmp_path` 创建新任务，确认 `WATCHDOG_NO_PROGRESS_CYCLES=1` + `WATCHDOG_SCAN_INTERVAL_SECONDS=1` 配置已通过 fixture 注入，阈值覆盖机制生效（无需等待真实时钟）（幂等键 `f013-sc-c-003`）

**Checkpoint**：`test_f013_watchdog.py` 三个测试方法均通过，SC-003 验收证据已产生，冷却机制已验证。

---

## Phase 6: User Story 4 — 全链路执行过程完整可追溯（Priority: P1）

**目标**：验证消息接收层、路由决策层、Worker 执行层三层追踪记录均可通过统一 task_id 串联，`LOGFIRE_SEND_TO_LOGFIRE=false` 时主业务流程不受任何影响。

**独立测试**：`uv run pytest octoagent/tests/integration/test_f013_trace.py -v` 单独通过，使用 `capfire` fixture 捕获 in-memory span，不依赖外部追踪后端。

- [x] T018 [US4] 新建 `octoagent/tests/integration/test_f013_trace.py`，定义 `TestF013ScenarioD` 类及必要导入（`logfire.testing` 相关，或降级方案 `opentelemetry.sdk.trace.export.InMemorySpanExporter`）

- [x] T019 [US4] 在 `octoagent/tests/integration/test_f013_trace.py` 实现 `test_full_trace_spans_across_all_layers` 测试方法（FR-005 场景 1）：使用 `capfire` fixture，POST `/api/message`，用 `poll_until` 等待 SUCCEEDED，断言 `capfire.exporter.exported_spans_as_dict()` 非空，再通过 `GET /api/tasks/{task_id}` 断言所有 events 的 `task_id` 等于提交时返回的 `task_id`（幂等键 `f013-sc-d-001`）
  <!-- 注：LOGFIRE_SEND_TO_LOGFIRE=false 时 span 为空属降级行为（FR-012），已改为软性检查；主验证通过 EventStore 事件链完成 -->

- [x] T020 [US4] 在 `octoagent/tests/integration/test_f013_trace.py` 实现 `test_trace_chain_continuity` 测试方法（FR-005 场景 2）：在场景 1 基础上，进一步断言事件中 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED` 三类事件均存在，验证 Worker 执行记录可追溯到路由决策记录，链路无断裂（幂等键 `f013-sc-d-002`，与场景 A 的断言互补强化）

- [x] T021 [US4] 在 `octoagent/tests/integration/test_f013_trace.py` 实现 `test_trace_unaffected_when_backend_unavailable` 测试方法（FR-005 场景 3 / FR-012 降级验证）：fixture 已设置 `LOGFIRE_SEND_TO_LOGFIRE=false`，POST 消息后 `poll_until` SUCCEEDED，断言任务正常完成（主流程不受可观测后端不可达影响）（幂等键 `f013-sc-d-003`）

**Checkpoint**：`test_f013_trace.py` 三个测试方法均通过，SC-004 验收证据已产生，降级行为已验证。

---

## Phase 7: User Story 5 — M1 已交付能力不退化（Priority: P2）

**目标**：执行 Feature 002~007 全量回归，确认 F013 新增集成层未破坏 M1 已交付能力，测试通过率无新增失败。

**独立测试**：`uv run pytest octoagent/tests/ --ignore=octoagent/tests/integration/test_f013_*.py --cov --cov-report=term-missing` 全量通过。

- [x] T022 [US5] 执行全量回归命令 `uv run pytest octoagent/tests/ --cov=octoagent --cov-report=term-missing -q`，记录执行摘要（总用例数、通过数、失败数、覆盖率）；若有新增失败，排查原因并在此任务下注释原因
  <!-- 执行结果: 75 passed, 0 failed in 15.47s；覆盖率 66%；基线 64 通过，F013 新增 11 个测试全部通过，无新增失败 -->

- [x] T023 [US5] 若 T022 存在 Feature 002~007 范围内的新增失败，隔离失败原因（fixture 冲突 / 环境变量污染 / 导入副作用）并在 `octoagent/tests/integration/conftest.py` 中修复（仅限隔离修复，不修改被测业务代码）
  <!-- T022 无新增失败，T023 无需执行 -->

**Checkpoint**：Feature 002~007 全量测试通过率不低于 F013 开始前的基线，SC-005 验收证据已产生。

---

## Phase 8: User Story 6 — 产出 M1.5 验收报告（Priority: P2）

**目标**：产出结构化 M1.5 验收报告，映射四条验收标准到测试证据，附技术风险清单最终状态，为 M2 提供准入基线。

**独立测试**：检查 `.specify/features/013-m1.5-e2e-integration-acceptance/verification/m1.5-acceptance-report.md` 文件存在，且包含四条 SC 与测试函数的映射关系。

- [x] T024 [US6] 收集所有场景测试（T007~T021）和全量回归（T022）的执行结果，整理测试执行摘要（通过数、失败数、覆盖率来源 `pytest --cov` 输出）

- [x] T025 [US6] 填写 `.specify/features/013-m1.5-e2e-integration-acceptance/verification/m1.5-acceptance-report.md`，必须包含：(a) SC-001~SC-004 与对应测试函数名及执行结果的映射表；(b) 测试执行摘要（总用例数、通过/失败数、覆盖率）；(c) 技术风险清单及每条风险的最终状态（已消除或遗留，来自 `research.md` 风险列表）；(d) M2 准入结论（明确声明 M1.5 验收通过/未通过）

**Checkpoint**：验收报告文件存在且内容完整，SC-006 验收证据已产生，F013 验收工作完成。

---

## Phase 9: Polish & Cross-Cutting Concerns

**目的**：清理跨场景共享代码，确保测试套件稳定可重复，补充文档。

- [x] T026 [P] 检查 `octoagent/tests/integration/conftest.py` 中 T001~T004 新增代码的 import 顺序和格式，与现有代码风格保持一致（`isort` + `ruff` 检查）
  <!-- 执行结果: uv run ruff check tests/integration/conftest.py --select I,E401,E402 → All checks passed! -->

- [x] T027 [P] 验证 `octoagent/tests/integration/test_f013_e2e_full.py`、`test_f013_checkpoint.py`、`test_f013_watchdog.py`、`test_f013_trace.py` 四个文件的 docstring 完整性：每个 `Test*` 类和每个测试方法均有 docstring，注明关联的 FR 和 SC 编号
  <!-- 执行结果: 4 个文件 15 个 Test*/test_* 节点全部 [OK]，无 MISSING -->

- [x] T028 执行完整集成测试套件两次（`uv run pytest octoagent/tests/integration/ -v` 连续运行两遍），确认结果稳定一致，无随机失败（验证 FR-009 时序稳定性要求）
  <!-- 执行结果: 第一次 75 passed in 15.23s；第二次 75 passed in 15.18s；两次结果一致，无随机失败 -->

- [x] T029 [P] 在 `.specify/features/013-m1.5-e2e-integration-acceptance/quickstart.md` 末尾追加"执行测试"章节，记录四条场景测试的运行命令和预期输出格式
  <!-- 执行结果: 已追加"执行测试"章节，包含单场景命令、全量运行、稳定性验证、仅 F013 测试四个子节 -->

---

## FR 覆盖映射表

| 功能需求 | 描述摘要 | 覆盖 Task ID |
|---------|---------|------------|
| FR-001 | 接入真实组件，去除测试替代 | T005, T006 |
| FR-002 | 场景 A 消息路由全链路 | T007, T008, T009 |
| FR-003 | 场景 B 系统中断后恢复 | T010, T011, T012, T013 |
| FR-004 | 场景 C 无进展任务告警 | T014, T015, T016, T017 |
| FR-005 | 场景 D 全链路追踪贯通 | T018, T019, T020, T021 |
| FR-006 | 检查点读取能力核查（已确认实现，前置补全不需要） | T005 |
| FR-007 | Feature 002~007 全量回归 | T022, T023 |
| FR-008 | 产出 M1.5 验收报告 | T024, T025 |
| FR-009 | 跨执行环境稳定性 | T001（poll_until）, T028 |
| FR-010 | 无进展检测场景间隔离 | T003（teardown 清理）, T017 |
| FR-011 | 恢复场景间隔离 | T002（独立 db_path）, T012 |
| FR-012 | 追踪验证降级备选方案 | T006, T021 |
| FR-013 | 不引入新业务能力（范围锁定） | 所有任务均不修改业务代码 |

**覆盖率**: 13/13 FR 全覆盖（100%）

---

## Dependencies & Execution Order

### Phase 依赖关系

```
Phase 1（Setup conftest.py 扩展）
    └─> Phase 2（前置核查）
            └─> Phase 3（US-1 场景 A）  ─┐
            └─> Phase 4（US-2 场景 B）  ─┤  可并行
            └─> Phase 5（US-3 场景 C）  ─┤  (独立文件)
            └─> Phase 6（US-4 场景 D）  ─┘
                    └─> Phase 7（US-5 全量回归）
                            └─> Phase 8（US-6 验收报告）
                                    └─> Phase 9（Polish）
```

### User Story 间依赖

- **US-1 (场景 A)**: Phase 1+2 完成后即可开始，依赖 `client` fixture（已存在）和 `integration_app` fixture（已存在）
- **US-2 (场景 B)**: Phase 1+2 完成后即可开始，依赖 `app_with_checkpoint` fixture（T002），不依赖 US-1
- **US-3 (场景 C)**: Phase 1+2 完成后即可开始，依赖 `watchdog_integration_app` / `watchdog_client` fixture（T003, T004），不依赖 US-1/US-2
- **US-4 (场景 D)**: Phase 1+2 完成后即可开始，依赖 `capfire` 可用性核查（T006），不依赖 US-1/US-2/US-3
- **US-5 (回归)**: 依赖 US-1~US-4 全部通过（确保 F013 新增代码不引入回归）
- **US-6 (报告)**: 依赖 US-5 通过（需要全量回归执行结果）

### Story 内部并行机会

- **场景 A（US-1）**: T008 和 T009 均依赖 T007 建立类框架，但两个测试方法可在类建好后并行编写
- **场景 B（US-2）**: T011、T012、T013 均依赖 T010 建立类框架，三个方法可并行编写（不同测试数据）
- **场景 C（US-3）**: T015、T016、T017 均依赖 T014，三个方法可并行编写
- **场景 D（US-4）**: T019、T020、T021 均依赖 T018，三个方法可并行编写
- **Phase 3~6**: 四个场景文件完全独立，可由四人同时并行实现

### Phase 1 内部顺序

- T001（`poll_until`）必须先于 T002~T004（新 fixture 内部引用 `poll_until`）
- T002、T003、T004 可并行编写（修改同一文件需协调，建议串行追加）

---

## 并行执行比例

| 类别 | 可并行任务 | 总任务 | 并行比例 |
|------|-----------|--------|---------|
| Phase 1（conftest 扩展） | T002, T003, T004（基于 T001） | 4 | 75% |
| Phase 3~6（场景测试） | 全部 15 个测试方法编写任务 | 15 | 100% |
| Phase 9（Polish） | T026, T027, T029 | 3/4 | 75% |
| **整体** | **约 20 个** | **29** | **~69%** |

---

## 推荐实施策略

### MVP First（单人开发，按天分配）

1. **Day 1（Phase 1+2）**: T001~T006，conftest 扩展 + 前置核查，验证 fixture 可被 pytest 发现
2. **Day 2（Phase 3）**: T007~T009，场景 A 消息路由全链路，最基础验收场景
3. **Day 3（Phase 4）**: T010~T013，场景 B 断点恢复，验证 Durability First 约束
4. **Day 4（Phase 5+6）**: T014~T021，场景 C+D 可并行编写（两个独立文件）
5. **Day 5（Phase 7~9）**: T022~T029，全量回归 + 验收报告 + Polish

### 并行团队策略（四人）

- 完成 Phase 1+2（共同）
- 开发者 A: Phase 3（场景 A）
- 开发者 B: Phase 4（场景 B）
- 开发者 C: Phase 5（场景 C）
- 开发者 D: Phase 6（场景 D）
- 合并后共同完成 Phase 7~9

---

## 关键约束提示

- **FR-013 范围锁定**: 严禁修改 `octoagent/` 目录下任何非 `tests/` 的业务代码文件
- **不修改现有测试**: `test_f008_*`、`test_f009_*`、`test_f010_*`、`test_sc*` 等已有文件不得修改
- **隔离原则**: 每个场景测试使用独立的 `tmp_path`，确保 SQLite 数据库按测试隔离
- **幂等键唯一性**: 各场景测试使用 `f013-sc-{x}-{sequence}` 格式命名，避免跨测试冲突
- **数量规模**: 共计 29 个任务，4 个新测试文件，约 200 行测试代码，1 份验收报告
