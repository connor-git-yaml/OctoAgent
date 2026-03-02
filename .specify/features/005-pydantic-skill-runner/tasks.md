# Tasks: Feature 005 — Pydantic Skill Runner

**Input**: `.specify/features/005-pydantic-skill-runner/` (spec.md, plan.md, data-model.md, contracts/skills-api.md)
**Prerequisites**: Feature 004 已交付（ToolBrokerProtocol）
**Branch**: `codex/feat-005-pydantic-skill-runner`

---

## Phase 1: Setup（Shared Infrastructure）

- [x] T001 创建 `packages/skills/` 包结构与 `pyproject.toml`。
  - `octoagent/packages/skills/pyproject.toml`
  - `octoagent/packages/skills/src/octoagent/skills/__init__.py`
  - `octoagent/packages/skills/tests/__init__.py`
- [x] T002 更新 workspace 配置，纳入 `octoagent-skills`。
  - `octoagent/pyproject.toml`

## Phase 2: Foundational（Blocking）

- [x] T003 实现异常与协议层。
  - `octoagent/packages/skills/src/octoagent/skills/exceptions.py`
  - `octoagent/packages/skills/src/octoagent/skills/protocols.py`
- [x] T004 实现数据模型层。
  - `octoagent/packages/skills/src/octoagent/skills/models.py`
- [x] T005 扩展 core EventType（Skill 级事件）。
  - `octoagent/packages/core/src/octoagent/core/models/enums.py`
- [x] T006 为新增 EventType 编写基础测试。
  - `octoagent/packages/core/tests/test_models.py`

## Phase 3: User Story 1（P1）结构化 Skill 执行闭环

### Tests first

- [x] T007 [P] 编写模型层测试（策略、Envelope、Result）。
  - `octoagent/packages/skills/tests/test_models.py`
- [x] T008 [P] 编写 Registry 基础测试（注册/查询/重复注册）。
  - `octoagent/packages/skills/tests/test_registry.py`

### Implementation

- [x] T009 实现 Manifest 与 Registry。
  - `octoagent/packages/skills/src/octoagent/skills/manifest.py`
  - `octoagent/packages/skills/src/octoagent/skills/registry.py`
- [x] T010 实现 Runner 基础主链（input/output 校验 + complete 终止）。
  - `octoagent/packages/skills/src/octoagent/skills/runner.py`
- [x] T011 实现 hooks 接口与默认 no-op hook。
  - `octoagent/packages/skills/src/octoagent/skills/hooks.py`

## Phase 4: User Story 2（P1）tool_calls 执行与回灌

### Tests first

- [x] T012 [P] 编写 Runner 工具执行测试（成功/工具错误回灌）。
  - `octoagent/packages/skills/tests/test_runner.py`

### Implementation

- [x] T013 在 Runner 中集成 ToolBrokerProtocol.execute。
  - `octoagent/packages/skills/src/octoagent/skills/runner.py`
- [x] T014 实现 ToolFeedbackMessage 生成与回灌拼接。
  - `octoagent/packages/skills/src/octoagent/skills/runner.py`

## Phase 5: User Story 3（P1）失败重试与异常分流

### Tests first

- [x] T015 [P] 编写重试与异常分流测试（validation/repeat/tool_error）。
  - `octoagent/packages/skills/tests/test_runner.py`

### Implementation

- [x] T016 实现 retry_policy 与错误分类输出。
  - `octoagent/packages/skills/src/octoagent/skills/runner.py`

## Phase 6: User Story 4（P2）循环检测与终止保护

### Tests first

- [x] T017 [P] 编写循环检测与 max_steps 测试。
  - `octoagent/packages/skills/tests/test_runner.py`

### Implementation

- [x] T018 实现 tool_calls 签名重复检测与 step 限制。
  - `octoagent/packages/skills/src/octoagent/skills/runner.py`

## Phase 7: User Story 5（P2）可观测与审计

### Tests first

- [x] T019 [P] 编写 Skill 级事件测试（started/completed/failed）。
  - `octoagent/packages/skills/tests/test_runner.py`

### Implementation

- [x] T020 实现 Skill 级事件写入与 trace/task 绑定。
  - `octoagent/packages/skills/src/octoagent/skills/runner.py`

## Phase 8: Cross-Cutting（预算防护 + 示例）

- [x] T021 实现 context budget guard（超限摘要/artifact_ref 优先）。
  - `octoagent/packages/skills/src/octoagent/skills/runner.py`
- [x] T022 编写 `test_integration.py`，覆盖 echo/file_summary 双示例流程。
  - `octoagent/packages/skills/tests/test_integration.py`
- [x] T023 完善 `__init__.py` 导出。
  - `octoagent/packages/skills/src/octoagent/skills/__init__.py`

## Phase 9: Polish

- [x] T024 运行 `ruff check` 并修复告警。
- [x] T025 运行 `pytest packages/skills/tests -v`。
- [x] T026 运行 `pytest packages/core/tests -v` 回归新增 EventType 影响。
- [x] T027 更新 `tasks.md` 勾选状态与执行记录。

---

## FR Coverage（摘要）

- FR-001~FR-003 -> T009
- FR-004~FR-008 -> T010/T013/T014
- FR-009~FR-010 -> T016
- FR-011~FR-012 -> T018
- FR-013 -> T021
- FR-014~FR-015 -> T020
- FR-016~FR-017 -> T022
