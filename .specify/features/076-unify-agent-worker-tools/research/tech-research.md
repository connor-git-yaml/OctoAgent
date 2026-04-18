# 技术调研：统一主 Agent 与 Worker 工具集

## 核心结论

`runtime_kinds` 的工具过滤功能**从未真正生效**。`BundledToolDefinition.runtime_kinds` 字段仅作为 metadata 传递给前端展示，**不被 `select_tools()` 或 ToolIndex 用于过滤决策**。删除过滤逻辑 = 清理死代码。

## RuntimeKind 使用分类

### A. 工具过滤相关（可安全删除）
- `_resolve_tool_runtime_kinds()`（capability_pack.py:1715-1736）— 硬编码映射
- `BundledToolDefinition.runtime_kinds` 字段赋值（capability_pack.py:300）
- `@tool_contract` metadata 中 `runtime_kinds` 声明（12 个文件，75+ 处）
- `ToolAvailabilityExplanation.metadata.runtime_kinds` 输出（capability_pack.py:654,700）

### B. Worker Profile 配置（可保留）
- `WorkerProfile.runtime_kinds` DB 字段（sqlite_init.py:314）
- `WorkerCapabilityProfile.runtime_kinds` 模型字段
- 前端 AgentEditor 的"运行形态"多选 UI

### C. 运行时角色标识（必须保留）
- `ExecutionRuntimeContext.runtime_kind` — 运行时上下文
- `DelegationEnvelope.runtime_kind` — dispatch 路由
- `_enforce_child_target_kind_policy()` — 防止 Worker 嵌套 Worker（安全边界）
- `RuntimeKind` 枚举本身（仍被 DelegationEnvelope 使用）

## 推荐方案：A（保留字段结构，清除过滤语义）

1. 删除 `_resolve_tool_runtime_kinds()` 方法
2. 清理所有 `@tool_contract` metadata 中的 `runtime_kinds`
3. 删除 bootstrap 模板 `{{runtime_kinds}}` 占位符
4. 前端移除"运行形态"编辑 UI
5. 保留 `WorkerProfile.runtime_kinds` DB 字段（不做 schema 迁移）
6. **不删除** RuntimeKind 枚举、DelegationEnvelope.runtime_kind、_enforce_child_target_kind_policy

## 受影响文件（25 个）

### 后端（21 个）
- `capability_pack.py` — 删除方法 + 字段赋值 + 模板占位符
- `builtin_tools/*.py`（12 个文件）— 删除 metadata 中 `runtime_kinds`
- `mcp_registry.py` — 删除 metadata
- `tool_search_tool.py` — 删除 metadata

### 前端（4 个）
- `AgentEditorSection.tsx` — 删除"运行形态"多选框
- `agentManagementData.ts` — 删除 runtimeKinds draft/payload
- `AgentCenter.tsx` — 删除 onToggleRuntimeKind 传递
- `types/index.ts` — 可选删除 RuntimeKind type

## 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| _enforce_child_target_kind_policy 误解 | 低 | 高 | 明确标注独立于工具过滤 |
| bootstrap 模板占位符残留 | 低 | 低 | 同步删除 |
| 前端快照兼容性 | 中 | 低 | dict[str,Any] 自动忽略多余字段 |
| ToolIndex 语义倒退 | 低 | 中 | 记录删除决策 |

**零 schema 迁移，零安全降级，核心改动集中在 capability_pack.py。**
