# Feature Specification: 统一主 Agent 与 Worker 工具集 — 清除 runtime_kinds 工具过滤死代码

**Feature Branch**: `076-unify-agent-worker-tools`  
**Created**: 2026-04-18  
**Status**: Draft  
**Input**: 清除 `BundledToolDefinition.runtime_kinds` 工具过滤死代码，统一主 Agent 与 Worker 的工具集视图

---

## User Scenarios & Testing

### User Story 1 - 消除工具过滤死代码（Priority: P1）

作为系统维护者，当我阅读工具注册相关代码（`capability_pack.py`、`builtin_tools/*.py`）时，我不希望看到 `runtime_kinds` 过滤逻辑——因为该逻辑从未生效，只会增加认知负担和误导性预期。清除后，代码读者能直接看到"工具对所有运行时上下文统一可见"这一实际行为。

**Why this priority**: 这是 MVP 核心目标。`_resolve_tool_runtime_kinds()` 方法及其相关字段赋值是确认无效的死代码，清除后可立即降低维护成本，且无任何功能回退风险。其他 User Story 都依赖此清理完成后的干净状态。

**Independent Test**: 可以通过以下方式独立验证：在清理完成后，搜索代码库中对 `_resolve_tool_runtime_kinds` 的所有调用，若零结果则通过；同时运行现有工具调用集成测试，确认主 Agent 和 Worker 均能正常获取工具列表。

**Acceptance Scenarios**:

1. **Given** `capability_pack.py` 中存在 `_resolve_tool_runtime_kinds()` 方法，**When** 该特性实现完成，**Then** 该方法不再存在于代码库中
2. **Given** `BundledToolDefinition` 模型含有 `runtime_kinds` 字段，**When** 该特性实现完成，**Then** 该字段的赋值逻辑（`capability_pack.py:300`）不再存在
3. **Given** 现有工具调用功能正常，**When** 死代码被移除，**Then** 主 Agent 与 Worker 的工具列表内容与移除前完全一致

---

### User Story 2 - 清理工具合约中的 runtime_kinds 声明（Priority: P1）

作为维护工具合约（`@tool_contract`）的开发者，当我新增或修改一个工具时，我不需要在 metadata 中声明 `runtime_kinds`——因为该字段不影响任何运行时行为。清理后，工具合约只需声明有实际意义的字段。

**Why this priority**: 与 User Story 1 同等优先级，属于同一批死代码清理范围。12 个文件中的 75+ 处声明若不同步清理，会在将来引发"这个字段是否有用"的混淆。此清理对功能无副作用。

**Independent Test**: 在 12 个 `builtin_tools/*.py` 文件及 `mcp_registry.py`、`tool_search_tool.py` 中，通过代码搜索确认 `runtime_kinds` 不再出现在任何 `@tool_contract` metadata 声明内。

**Acceptance Scenarios**:

1. **Given** 12 个工具文件的 `@tool_contract` metadata 中含有 `runtime_kinds` 字段，**When** 清理完成，**Then** 这些文件中不再有 `runtime_kinds` 字段声明
2. **Given** `ToolAvailabilityExplanation` 的 metadata 输出含有 `runtime_kinds`，**When** 清理完成，**Then** 该输出不再包含 `runtime_kinds` 字段（`capability_pack.py:654, 700`）

---

### User Story 3 - 移除前端"运行形态"工具过滤 UI（Priority: P2）

作为使用 AgentEditor 配置 Worker 的用户，当我编辑 Worker 时，我不会看到"运行形态"多选框——因为该 UI 对应的工具过滤功能从未生效，显示它会误导用户以为工具集会因此变化。

**Why this priority**: 前端 UI 的误导性低于后端死代码的危害，但仍需清理以保持 UI 与系统实际行为一致。可在后端清理完成后独立执行。

**Independent Test**: 在 AgentEditor 页面渲染后，确认不存在"运行形态"多选组件；检查 `agentManagementData.ts` 确认不再有 `runtimeKinds` draft/payload 字段。

**Acceptance Scenarios**:

1. **Given** `AgentEditorSection.tsx` 含有运行形态多选框，**When** 清理完成，**Then** 该组件不再渲染
2. **Given** `AgentCenter.tsx` 传递了 `onToggleRuntimeKind` 回调，**When** 清理完成，**Then** 该回调不再被传递或定义
3. **Given** `agentManagementData.ts` 含有 `runtimeKinds` 相关 draft 和 payload 字段，**When** 清理完成，**Then** 这些字段不再存在

---

### Edge Cases

- **`WorkerProfile.runtime_kinds` DB 字段**：该字段存在于数据库 schema（`sqlite_init.py:314`）和 `WorkerCapabilityProfile` 模型中，但属于 Worker 配置元数据，与工具过滤逻辑无关。本次清理 MUST NOT 删除该字段，也不执行任何数据库 schema 迁移，以避免现有数据丢失。
- **`RuntimeKind` 枚举**：枚举本身被 `DelegationEnvelope.runtime_kind` 和 `_enforce_child_target_kind_policy()` 使用，属于路由与安全边界逻辑，MUST NOT 删除。若前端 `types/index.ts` 中的 `RuntimeKind` type 仍被其他组件引用，则保留；若已无引用，可选删除。
- **`ExecutionRuntimeContext.runtime_kind` 和 `DelegationEnvelope.runtime_kind`**：这两个字段用于运行时角色标识和 dispatch 路由，与工具过滤死代码无关，MUST NOT 触碰。
- **`_enforce_child_target_kind_policy()`**：该方法是防止 Worker 嵌套 Worker 的安全边界，MUST NOT 删除。
- **bootstrap 模板 `{{runtime_kinds}}` 占位符**：若存在，应删除该占位符及其相关的模板替换逻辑，以避免残留占位符出现在 Agent 上下文中。

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 从 `capability_pack.py` 中删除 `_resolve_tool_runtime_kinds()` 方法及其全部调用点 `[必须]`
- **FR-002**: 系统 MUST 删除 `BundledToolDefinition` 中 `runtime_kinds` 字段的赋值逻辑（`capability_pack.py:300`）`[必须]`
- **FR-003**: 系统 MUST 从所有 `@tool_contract` metadata 声明中删除 `runtime_kinds` 字段，覆盖 12 个 builtin_tools 文件、`mcp_registry.py`、`tool_search_tool.py` `[必须]`
- **FR-004**: 系统 MUST 从 `ToolAvailabilityExplanation` 的 metadata 输出中移除 `runtime_kinds` 字段（`capability_pack.py:654, 700`）`[必须]`
- **FR-005**: 系统 MUST 从前端 `AgentEditorSection.tsx` 中删除"运行形态"多选框组件 `[必须]`
- **FR-006**: 系统 MUST 从 `AgentCenter.tsx` 中删除 `onToggleRuntimeKind` 回调的传递 `[必须]`
- **FR-007**: 系统 MUST 从 `agentManagementData.ts` 中删除 `runtimeKinds` 相关的 draft 字段和 payload 字段 `[必须]`
- **FR-008**: 系统 MUST NOT 删除 `WorkerProfile.runtime_kinds` DB 字段，不执行任何数据库 schema 迁移 `[必须]`
- **FR-009**: 系统 MUST NOT 删除 `RuntimeKind` 枚举、`ExecutionRuntimeContext.runtime_kind`、`DelegationEnvelope.runtime_kind`、`_enforce_child_target_kind_policy()` `[必须]`
- **FR-010**: 系统 SHOULD 删除 bootstrap 模板中的 `{{runtime_kinds}}` 占位符（若存在）`[必须]` [AUTO-RESOLVED: 调研结论中已明确列出此项，属于清理范围]
- **FR-011**: 前端 `types/index.ts` 中的 `RuntimeKind` type MAY 被删除，前提是确认无其他组件引用 `[可选]`

### Key Entities

- **`BundledToolDefinition`**：工具定义的运行时包装结构，含 `runtime_kinds` 字段（metadata passthrough，无过滤语义）。本次清理移除该字段的赋值逻辑，但不删除字段本身（若字段本身已无任何赋值和消费则可删除）
- **`WorkerCapabilityProfile`**：Worker 能力配置模型，含 `runtime_kinds` 字段，代表该 Worker 被设计运行的形态。本次清理不触碰此模型
- **`RuntimeKind` 枚举**：表示运行时角色（如 Agent、Worker），被路由与安全边界逻辑使用，本次清理不删除

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 清理完成后，在代码库全文搜索 `_resolve_tool_runtime_kinds`，结果为零
- **SC-002**: 清理完成后，在所有 `@tool_contract` metadata 声明中搜索 `runtime_kinds`，结果为零
- **SC-003**: 清理完成后，现有工具调用集成测试全部通过，主 Agent 和 Worker 的可用工具列表与清理前一致
- **SC-004**: 清理完成后，AgentEditor 页面中不再渲染"运行形态"多选框
- **SC-005**: 清理完成后，受影响的 25 个文件中不再有任何因死代码产生的 lint 警告或 dead variable 警告
- **SC-006**: 无任何数据库 schema 迁移被引入，现有数据库可直接升级使用

---

## 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 值 | 说明 |
|------|-----|------|
| **组件总数** | 0 | 纯删除操作，无新增组件/模块 |
| **接口数量** | 0 | 无新增或修改接口/契约 |
| **依赖新引入数** | 0 | 无新引入外部依赖 |
| **跨模块耦合** | 是 | 涉及后端 21 个文件 + 前端 4 个文件，但均为删除操作，不引入新耦合 |
| **复杂度信号** | 无 | 无递归结构、状态机、并发控制、数据迁移 |
| **总体复杂度** | **LOW** | 纯清理操作，改动集中在删除死代码，无逻辑变更 |

**复杂度判定依据**：组件数 = 0（< 3），接口数 = 0（< 4），无复杂度信号，判定为 LOW。
受影响文件数量虽多（25 个），但所有改动类型为"删除字段/方法/组件"，属于机械性清理，无需设计决策。

**GATE_DESIGN 建议**：本特性复杂度为 LOW，可跳过人工架构审查，直接进入实现阶段。需在实现前确认 `_enforce_child_target_kind_policy()` 及 `RuntimeKind` 枚举的引用链完整，避免误删。
