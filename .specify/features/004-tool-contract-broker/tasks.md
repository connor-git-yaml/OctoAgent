# Tasks: Feature 004 — Tool Contract + ToolBroker

**Input**: `.specify/features/004-tool-contract-broker/` (spec.md, plan.md, data-model.md, contracts/tooling-api.md)
**Prerequisites**: spec.md (29 FR, 7 User Stories), plan.md (技术计划), data-model.md (数据模型), contracts/tooling-api.md (接口契约)
**Branch**: `feat/004-tool-contract-broker`
**Tests**: 测试先行 -- 每个 User Story Phase 中测试任务排在实现之前，确认测试失败后再实现

**Organization**: 任务按 Phase 组织。Phase 1-2 为 Setup/Foundational（无 US 标记），Phase 3-9 按 User Story 优先级排列，Phase 10 为 Polish。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[USN]**: 所属 User Story（US1-US7）
- 包含精确文件路径

---

## Phase 1: Setup（包/目录初始化、monorepo 配置）

**Purpose**: 创建 `packages/tooling` 目录结构，配置 monorepo workspace，确保包可安装和导入。

- [x] T001 创建 `packages/tooling/` 目录结构（src/octoagent/tooling/、tests/、_examples/）`octoagent/packages/tooling/`
- [x] T002 创建 `packages/tooling/pyproject.toml`（依赖 octoagent-core、pydantic、pydantic-ai-slim、structlog、python-ulid）`octoagent/packages/tooling/pyproject.toml`
- [x] T003 更新 monorepo 根 `pyproject.toml`（workspace members + dev deps + uv sources + testpaths）`octoagent/pyproject.toml`
- [x] T004 [P] 创建 `packages/tooling/src/octoagent/tooling/__init__.py`（空占位，Phase 9 完善导出）`octoagent/packages/tooling/src/octoagent/tooling/__init__.py`
- [x] T005 [P] 创建 `packages/tooling/tests/__init__.py`（空）`octoagent/packages/tooling/tests/__init__.py`
- [x] T006 [P] 创建 `packages/tooling/tests/conftest.py`（共享 fixtures：mock EventStore、mock ArtifactStore、mock ExecutionContext）`octoagent/packages/tooling/tests/conftest.py`
- [x] T007 运行 `uv sync` 验证包可安装 `octoagent/`

**Checkpoint**: `packages/tooling` 可被 monorepo 识别，`import octoagent.tooling` 不报错。

---

## Phase 2: Foundational（core 扩展 + tooling 基础类型）

**Purpose**: 建立所有 User Story 依赖的基础类型——枚举、数据模型、异常、Protocol 接口。此阶段完成后所有 User Story 可并行实施。

**CRITICAL**: 所有 User Story 的实现任务均依赖此阶段完成。

### 2A: core 包扩展（EventType + Payload）

- [x] T008 扩展 `EventType` 枚举：新增 `TOOL_CALL_STARTED` / `TOOL_CALL_COMPLETED` / `TOOL_CALL_FAILED` `octoagent/packages/core/src/octoagent/core/models/enums.py`
- [x] T009 [P] 新增 `ToolCallStartedPayload` / `ToolCallCompletedPayload` / `ToolCallFailedPayload` Payload 类型 `octoagent/packages/core/src/octoagent/core/models/payloads.py`
- [x] T010 [P] 更新 core `__init__.py` 导出新增的 3 个 Payload 类型和 EventType 扩展值 `octoagent/packages/core/src/octoagent/core/models/__init__.py`

### 2B: tooling 包枚举 + 数据模型

- [x] T011 实现 `models.py` — 枚举（SideEffectLevel、ToolProfile、HookType、FailMode）+ 数据模型（ToolMeta、ToolResult、ToolCall、ExecutionContext、BeforeHookResult、CheckResult）`octoagent/packages/tooling/src/octoagent/tooling/models.py`
- [x] T012 [P] 实现 `exceptions.py` — 异常类型（ToolRegistrationError、ToolNotFoundError、ToolExecutionError、ToolProfileViolationError、PolicyCheckpointMissingError、SchemaReflectionError）`octoagent/packages/tooling/src/octoagent/tooling/exceptions.py`

### 2C: Protocol 接口定义

- [x] T013 实现 `protocols.py` — Protocol 定义（ToolBrokerProtocol、ToolHandler、BeforeHook、AfterHook、PolicyCheckpoint）`octoagent/packages/tooling/src/octoagent/tooling/protocols.py`

### 2D: Foundational 测试

- [x] T014 [P] 编写 `test_models.py` — 枚举值验证、ToolMeta 构建/序列化、ToolResult 必含字段验证、ToolProfile 层级比较、CheckResult 默认值 `octoagent/packages/tooling/tests/test_models.py`
- [x] T015 [P] 编写 core 扩展测试 — EventType 新值可用、Payload 类型可实例化 `octoagent/packages/core/tests/test_enums_payloads_004.py`

**Checkpoint**: 基础类型全部就绪。`from octoagent.tooling.models import ToolMeta, SideEffectLevel, ToolProfile` 可导入，所有模型可实例化和序列化。Protocol 类型检查通过。

---

## Phase 3: User Story 1 — 工具契约声明（Priority: P1）

**Goal**: 工具开发者通过 `@tool_contract` 装饰器声明元数据，系统自动从函数签名生成 JSON Schema，确保 code = schema 单一事实源。

**Independent Test**: 编写带类型注解和 docstring 的函数，附加装饰器，验证生成的 ToolMeta 与函数签名完全一致。

### Tests for US1

> **Write tests FIRST, ensure they FAIL before implementation**

- [x] T016 [P] [US1] 编写 `test_decorators.py` — @tool_contract 元数据附加、side_effect_level 必填校验、默认 name 取 func.__name__、各可选参数传递 `octoagent/packages/tooling/tests/test_decorators.py`
- [x] T017 [P] [US1] 编写 `test_schema.py` — Schema Reflection 测试：基础类型（str/int/float/bool）、Optional/Union、list/dict、嵌套 BaseModel、docstring 解析（Google 格式）、async/sync 检测、EC-1（无类型注解拒绝）、EC-5（零参数函数）`octoagent/packages/tooling/tests/test_schema.py`

### Implementation for US1

- [x] T018 [US1] 实现 `decorators.py` — @tool_contract 装饰器（side_effect_level 必填、元数据附加到 func._tool_meta）`octoagent/packages/tooling/src/octoagent/tooling/decorators.py`
- [x] T019 [US1] 实现 `schema.py` — reflect_tool_schema()：检查装饰器元数据 -> 检查类型注解完整性 -> 调用 pydantic_ai._function_schema.function_schema() -> 合并元数据 -> 返回 ToolMeta `octoagent/packages/tooling/src/octoagent/tooling/schema.py`

**Checkpoint**: @tool_contract + reflect_tool_schema() 可生成完整 ToolMeta，JSON Schema 与函数签名一致。所有 US1 测试绿色。

---

## Phase 4: User Story 2 — 工具注册与发现（Priority: P1）

**Goal**: 通过 ToolBroker 注册、发现和查询可用工具，按 ToolProfile 和 tool_group 过滤工具集。

**Independent Test**: 注册若干不同 Profile/Group 的工具，以不同 Profile 查询，验证过滤规则。

### Tests for US2

> **Write tests FIRST, ensure they FAIL before implementation**

- [x] T020 [US2] 编写 `test_broker.py`（注册部分）— 注册成功、名称冲突拒绝（EC-7）、discover 按 profile 过滤（minimal/standard/privileged 三级）、按 group 过滤、空注册表返回空列表 `octoagent/packages/tooling/tests/test_broker.py`

### Implementation for US2

- [x] T021 [US2] 实现 `broker.py`（注册 + 发现部分）— ToolBroker 类：__init__（注册表 + hook 列表）、register()（名称唯一性 + ToolMeta/Handler 存储）、discover()（Profile 层级过滤 + Group 过滤）、unregister() `octoagent/packages/tooling/src/octoagent/tooling/broker.py`

**Checkpoint**: 工具注册/发现/注销通过，Profile 层级过滤和 Group 过滤正确。所有 US2 测试绿色。

---

## Phase 5: User Story 3 — 工具执行与事件追踪（Priority: P1）

**Goal**: 通过 ToolBroker 执行工具调用，自动生成事件链（STARTED/COMPLETED/FAILED），支持声明式超时和 sync->async 包装。

**Independent Test**: 通过 ToolBroker 执行已注册工具，验证返回结构化 ToolResult 且 EventStore 中生成完整事件链。

**Dependencies**: Phase 4 (broker.py 注册部分)

### Tests for US3

> **Write tests FIRST, ensure they FAIL before implementation**

- [x] T022 [US3] 扩展 `test_broker.py`（执行部分）— 正常执行返回 ToolResult、超时控制（timeout_seconds）、异常捕获（is_error + 错误分类）、sync 函数自动 async 包装、Profile 权限检查拒绝、FR-010a irreversible 无 PolicyCheckpoint 拒绝 `octoagent/packages/tooling/tests/test_broker.py`

### Implementation for US3

- [x] T023 [US3] 实现 `broker.py`（执行部分）— execute()：查找工具 -> Profile 权限检查 -> FR-010a 强制拒绝逻辑 -> 生成 TOOL_CALL_STARTED 事件 -> before hook 链 -> 工具执行（含超时 asyncio.wait_for + sync asyncio.to_thread 包装）-> after hook 链 -> 生成 COMPLETED/FAILED 事件 -> 返回 ToolResult `octoagent/packages/tooling/src/octoagent/tooling/broker.py`

**Checkpoint**: 工具执行全链路通过，事件自动生成，超时/异常/sync 包装均正确。FR-010a irreversible 安全保障验证通过。所有 US3 测试绿色。

---

## Phase 6: User Story 5 — Hook 扩展机制（Priority: P1）

**Goal**: ToolBroker 提供 before/after Hook 扩展点，支持优先级排序和 fail_mode 双模式（closed/open）。

**Independent Test**: 注册自定义 before/after hook，执行工具后验证 hook 按优先级执行，拒绝/降级策略正确。

**Dependencies**: Phase 5 (broker.py 执行部分)

**Note**: US5 提前到 US4 之前，因为 US4（大输出裁切）依赖 after hook 机制。

### Tests for US5

> **Write tests FIRST, ensure they FAIL before implementation**

- [x] T024 [US5] 编写 `test_hooks.py` — before hook 优先级排序（10->20->30）、before hook 拒绝执行、before hook fail_mode=closed 异常时拒绝、before hook fail_mode=open 异常时继续、after hook 异常 log-and-continue（FR-022）、after hook 修改 ToolResult、add_hook 自动分类 `octoagent/packages/tooling/tests/test_hooks.py`

### Implementation for US5

- [x] T025 [US5] 实现 `broker.py`（Hook 管理部分）— add_hook()：自动检测 BeforeHook/AfterHook 类型分类 + 按 priority 排序插入；execute() 集成 hook 链调用（before hooks 按优先级执行 + fail_mode 处理、after hooks 按优先级执行 + fail_mode 处理）`octoagent/packages/tooling/src/octoagent/tooling/broker.py`

**Checkpoint**: Hook 链按优先级执行，fail_mode=closed/open 策略正确，before hook 可拒绝执行，after hook 异常不影响结果。所有 US5 测试绿色。

---

## Phase 7: User Story 4 — 大输出自动裁切（Priority: P1）

**Goal**: 工具输出超过阈值时自动存入 ArtifactStore，上下文保留精简引用摘要（对齐 C11 Context Hygiene）。

**Independent Test**: 执行返回超长字符串的工具，验证 ToolResult 含 artifact 引用，ArtifactStore 中可检索完整内容。

**Dependencies**: Phase 6 (Hook 机制就绪)

### Tests for US4

> **Write tests FIRST, ensure they FAIL before implementation**

- [x] T026 [US4] 编写 `test_large_output.py` — 超阈值裁切（800 > 500）、未超阈值不裁切（300 < 500）、工具级自定义阈值覆盖全局（FR-017）、ArtifactStore 不可用降级保留原文（FR-018 / EC-3）、超大输出 >100KB（EC-6）、裁切后 artifact_ref 有值 + truncated=True `octoagent/packages/tooling/tests/test_large_output.py`

### Implementation for US4

- [x] T027 [US4] 实现 `hooks.py` — LargeOutputHandler（after hook）：阈值检测 -> 超阈值时 ArtifactStore 存储完整输出 -> 替换为引用摘要（前 200 字符前缀 + artifact ID）-> 降级处理（ArtifactStore 不可用时 log-and-continue 保留原文）`octoagent/packages/tooling/src/octoagent/tooling/hooks.py`

**Checkpoint**: 大输出自动裁切工作正常，降级策略生效。所有 US4 测试绿色。

---

## Phase 8: 事件生成 + 脱敏（跨 US3/US4/US5）

**Purpose**: 实现 EventGenerationHook（after hook）和 Sanitizer（脱敏），完成可观测性基础设施。此 Phase 横跨多个 User Story，不标记 USN。

### Tests

> **Write tests FIRST, ensure they FAIL before implementation**

- [x] T028 [P] 编写 `test_sanitizer.py` — 文件路径 $HOME 替换为 ~、环境变量值替换为 [ENV:VAR_NAME]、凭证模式（token/password/secret/key）替换为 [REDACTED]、嵌套 dict 递归脱敏、无敏感数据不变 `octoagent/packages/tooling/tests/test_sanitizer.py`

### Implementation

- [x] T029 实现 `sanitizer.py` — sanitize_for_event()：三条脱敏规则（路径 -> ~、环境变量 -> [ENV:*]、凭证 -> [REDACTED]）+ 递归处理嵌套 dict `octoagent/packages/tooling/src/octoagent/tooling/sanitizer.py`
- [x] T030 实现 `hooks.py`（EventGenerationHook 部分）— EventGenerationHook（after hook, priority=0）：集成 Sanitizer 对 payload 脱敏 -> 生成 TOOL_CALL_COMPLETED / TOOL_CALL_FAILED 事件写入 EventStore `octoagent/packages/tooling/src/octoagent/tooling/hooks.py`

**Checkpoint**: 事件 payload 中无敏感原文，EventStore 可查询到完整事件链。所有脱敏/事件生成测试绿色。

---

## Phase 9: User Story 6 — 接口契约输出 + User Story 7 — 示例工具（Priority: P1/P2）

**Goal**: 输出稳定接口契约供 005/006 并行开发；提供示例工具作为端到端验证 fixture 和最佳实践参考。

**Independent Test**: 基于 Protocol 定义编写 mock ToolBroker 验证类型检查通过；运行示例工具端到端测试。

**Dependencies**: Phase 7-8（Hook + 大输出裁切 + 事件生成全部就绪）

### Tests

> **Write tests FIRST, ensure they FAIL before implementation**

- [x] T031 [P] [US7] 编写 `test_examples.py` — echo_tool 端到端（声明 -> 注册 -> 发现 -> 执行 -> 事件 -> 结果）、file_write_tool 端到端、irreversible 工具无 PolicyCheckpoint 被拒绝 `octoagent/packages/tooling/tests/test_examples.py`
- [x] T032 [P] [US6] 编写 Protocol mock 验证测试 — MockToolBroker 满足 ToolBrokerProtocol 类型检查、MockPolicyCheckpoint 满足 PolicyCheckpoint Protocol `octoagent/packages/tooling/tests/test_protocols_mock.py`

### Implementation

- [x] T033 [P] [US7] 实现 `_examples/echo_tool.py` — @tool_contract(side_effect_level=none, tool_profile=minimal, tool_group="system") `octoagent/packages/tooling/src/octoagent/tooling/_examples/echo_tool.py`
- [x] T034 [P] [US7] 实现 `_examples/file_write_tool.py` — @tool_contract(side_effect_level=irreversible, tool_profile=standard, tool_group="filesystem") `octoagent/packages/tooling/src/octoagent/tooling/_examples/file_write_tool.py`
- [x] T035 [P] [US7] 创建 `_examples/__init__.py` `octoagent/packages/tooling/src/octoagent/tooling/_examples/__init__.py`

**Checkpoint**: 示例工具端到端通过，Mock ToolBroker 类型检查通过。所有 US6/US7 测试绿色。

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: 完善公共导出、契约文档验证、全量测试、lint 清理。

- [x] T036 完善 `__init__.py` 公共导出清单（所有公共类型：枚举、模型、Protocol、装饰器、Broker、Hook、异常）`octoagent/packages/tooling/src/octoagent/tooling/__init__.py`
- [x] T037 [P] 验证 `contracts/tooling-api.md` 与代码实现一致 — 逐条对比 Protocol 方法签名、枚举值、默认值、ToolResult 必含字段 `.specify/features/004-tool-contract-broker/contracts/tooling-api.md`
- [x] T038 [P] 运行全量测试 `pytest packages/tooling/tests/ packages/core/tests/ -v` 确保所有测试绿色
- [x] T039 [P] 运行 `ruff check` + `ruff format --check` 确保 lint 清洁 `octoagent/`
- [x] T040 编写集成测试 — 完整链路：工具声明 -> @tool_contract -> reflect_tool_schema -> broker.register -> broker.discover -> broker.execute -> Hook 链 -> 大输出裁切 -> 事件生成 -> 结果返回 `octoagent/packages/tooling/tests/test_integration.py`

**Checkpoint**: 所有测试绿色，lint 清洁，接口契约与实现一致。Feature 004 交付就绪。

---

## FR Coverage Map（FR -> Task 映射，100% 覆盖）

| FR | 描述 | 覆盖任务 |
|----|------|---------|
| FR-001 | 声明性标注定义工具元数据（name/description/side_effect_level/tool_profile/tool_group） | T011, T018, T016 |
| FR-002 | 强制声明 side_effect_level，缺失则拒绝注册 | T018, T019, T016 |
| FR-003 | 从函数签名 + docstring 自动生成 JSON Schema | T019, T017 |
| FR-004 | 可选元数据字段（version, timeout_seconds） | T011, T018, T016 |
| FR-005 | 缺少类型注解的参数拒绝注册 | T019, T017 |
| FR-006 | 集中注册 + 名称唯一性检查 | T021, T020 |
| FR-007 | 按 tool_profile 层级过滤工具集 | T021, T020 |
| FR-008 | 按 tool_group 过滤工具集 | T021, T020 |
| FR-009 | 工具注销（unregister） | T021, T020 |
| FR-010 | ToolBroker 统一执行，禁止绕过 | T023, T022 |
| FR-010a | irreversible 无 PolicyCheckpoint 时强制拒绝（safe by default） | T023, T022 |
| FR-011 | ToolResult 结构化结果（output/is_error/error/duration/artifact_ref） | T011, T014 |
| FR-012 | 声明式超时控制 | T023, T022 |
| FR-013 | 同步函数自动 async 包装 | T023, T022 |
| FR-014 | 事件生成（TOOL_CALL_STARTED/COMPLETED/FAILED） | T008, T009, T010, T030, T023 |
| FR-015 | 敏感数据脱敏（路径/$HOME/凭证） | T029, T028, T030 |
| FR-016 | 大输出超阈值自动裁切 + ArtifactStore 存储 | T027, T026 |
| FR-017 | 裁切阈值可配置（全局 + 工具级） | T027, T026 |
| FR-018 | ArtifactStore 不可用时降级保留原文 | T027, T026 |
| FR-019 | before/after hook + fail_mode 双模式 | T025, T024, T013 |
| FR-020 | Hook 优先级排序 | T025, T024 |
| FR-021 | before hook 拒绝执行信号 | T025, T024 |
| FR-022 | after hook 异常 log-and-continue 降级 | T025, T024 |
| FR-023 | ToolBrokerProtocol 接口定义 | T013, T032 |
| FR-024 | PolicyCheckpoint Protocol 接口定义 | T013, T032 |
| FR-025 | 接口契约文档 contracts/tooling-api.md | T037 |
| FR-025a | 契约锁定项（枚举值/默认值/Protocol 签名/ToolResult 字段） | T011, T013, T037 |
| FR-026 | 至少 2 个示例工具（none + irreversible） | T033, T034, T031 |
| FR-027 | 示例工具使用标准声明方式 | T033, T034, T031 |

**Coverage**: 29/29 FR = 100%

---

## Edge Case Coverage Map

| EC | 描述 | 覆盖任务 |
|----|------|---------|
| EC-1 | 无类型注解参数拒绝注册 | T017, T019 |
| EC-2 | 同一工具并发调用独立执行 | T040 |
| EC-3 | ArtifactStore 不可用降级 | T026, T027 |
| EC-4 | Hook 执行超时按 fail_mode 处理 | T024, T025 |
| EC-5 | 零参数工具正常注册执行 | T017, T019 |
| EC-6 | 超大输出 >100KB | T026, T027 |
| EC-7 | 重复注册拒绝 | T020, T021 |

**Coverage**: 7/7 EC = 100%

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
  |
  v
Phase 2 (Foundational) -- BLOCKS all User Stories
  |
  +---> Phase 3 (US1: 契约声明)
  |       |
  |       v
  +---> Phase 4 (US2: 注册与发现)
  |       |
  |       v
  +---> Phase 5 (US3: 执行与事件追踪) -- 依赖 Phase 4
  |       |
  |       v
  +---> Phase 6 (US5: Hook 扩展) -- 依赖 Phase 5
  |       |
  |       v
  +---> Phase 7 (US4: 大输出裁切) -- 依赖 Phase 6
  |       |
  |       v
  +---> Phase 8 (脱敏 + 事件生成) -- 依赖 Phase 5 + Phase 6
  |       |
  |       v
  +---> Phase 9 (US6/US7: 契约输出 + 示例工具) -- 依赖 Phase 7 + Phase 8
          |
          v
        Phase 10 (Polish)
```

### User Story Dependencies

| User Story | 依赖 | 说明 |
|-----------|------|------|
| US1（契约声明） | Phase 2 only | models.py 就绪即可开始 |
| US2（注册发现） | Phase 2 only | 可与 US1 并行（但实践中建议 US1 先完成） |
| US3（执行追踪） | US2 | 需要 broker.py 注册部分 |
| US5（Hook 扩展） | US3 | 需要 broker.py 执行部分 |
| US4（大输出裁切） | US5 | 作为 after hook 实现，依赖 hook 机制 |
| US6（接口契约） | US1-US5 | 需要所有 Protocol 定义稳定 |
| US7（示例工具） | US4, US5 | 需要完整执行链路 |

### Story 内部并行机会

- **Phase 2**: T008/T009/T010 可并行（不同文件）；T011/T012 可并行
- **Phase 3**: T016/T017 测试可并行
- **Phase 9**: T031/T032 测试可并行；T033/T034/T035 实现可并行
- **Phase 10**: T037/T038/T039 可并行

### 跨 Phase 并行机会

- **Phase 3 (US1)** 和 **Phase 4 (US2)** 可并行（US1 操作 decorators.py + schema.py，US2 操作 broker.py 注册部分，文件无冲突）
- **Phase 8 (脱敏)** 中 T028/T029 可在 Phase 6 完成后立即开始（不依赖 Phase 7）

---

## Implementation Strategy

### Recommended: Incremental Delivery（推荐）

1. **Phase 1 + 2**: Setup + Foundational -> 基础就绪（~0.5 天）
2. **Phase 3**: US1 契约声明 -> 验证 code=schema 单一事实源（~0.5 天）
3. **Phase 4**: US2 注册发现 -> 验证 Profile 过滤（~0.5 天）
4. **Phase 5**: US3 执行追踪 -> 验证事件链 + 超时 + FR-010a（~1 天）
5. **Phase 6**: US5 Hook 扩展 -> 验证 fail_mode 双模式（~0.5 天）
6. **Phase 7 + 8**: US4 大输出裁切 + 脱敏 -> 验证 Context Hygiene（~0.5 天）
7. **Phase 9**: US6/US7 契约输出 + 示例工具 -> 端到端验证（~0.5 天）
8. **Phase 10**: Polish -> 全量测试 + lint（~0.5 天）

**Total**: ~40 个任务，~4 天

### MVP First（最小可用范围）

如需最快交付 MVP，完成到 Phase 5（US1+US2+US3）即可提供 "工具声明 -> 注册 -> 发现 -> 执行" 的核心链路。

### Key Risk: FR-010a

FR-010a（irreversible 安全保障）是 Constitution C4/C7 的强制要求，必须在 Phase 5 中实现和验证，不可延后。

---

## Task Summary

| 统计项 | 数值 |
|--------|------|
| 总任务数 | 40 |
| User Stories 覆盖 | 7/7 (100%) |
| FR 覆盖 | 29/29 (100%) |
| EC 覆盖 | 7/7 (100%) |
| 可并行任务 | 17/40 (42.5%) |
| 测试任务 | 12 |
| 实现任务 | 20 |
| Setup/配置任务 | 8 |
