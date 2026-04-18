# Tasks: 统一主 Agent 与 Worker 工具集 — 清除 runtime_kinds 工具过滤死代码

**Feature**: `076-unify-agent-worker-tools`  
**Generated**: 2026-04-18  
**总任务数**: 18（Batch 1: 14 个，Batch 2: 3 个，验证: 1 个）

---

## Batch 1 — 后端清理

### task-001: 删除 capability_pack.py 中的核心死代码

- [ ] task-001: [BATCH-1] 删除 `_resolve_tool_runtime_kinds()` 方法及所有直接引用
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
      操作:
        1. 删除 `_resolve_tool_runtime_kinds()` 方法整体（第 1714-1736 行，约 22 行）
        2. 删除第 300 行：`runtime_kinds=self._resolve_tool_runtime_kinds(meta.name),`（BundledToolDefinition 构造时的赋值）
        3. 删除第 654 行：`"runtime_kinds": [item.value for item in bundled.runtime_kinds],`（ToolAvailabilityExplanation.metadata 输出）
        4. 删除第 700 行：`"runtime_kinds": [item.value for item in bundled.runtime_kinds],`（同上，另一处输出）
        5. 删除第 797-798 行的 `"{{runtime_kinds}}": ...` bootstrap 模板替换映射条目
        6. 删除第 1080 行：`"Runtime Kinds: {{runtime_kinds}}\n"` bootstrap 模板内容行
      验证: `grep "_resolve_tool_runtime_kinds" <文件路径>` 返回零结果；文件可正常被 Python 导入（无语法错误）

---

### task-002 至 task-013: builtin_tools 各文件删除 runtime_kinds metadata key

- [ ] task-002: [BATCH-1] 删除 `browser_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/browser_tools.py`
      操作: 删除 6 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-003: [BATCH-1] 删除 `config_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/config_tools.py`
      操作: 删除 6 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-004: [BATCH-1] 删除 `delegation_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py`
      操作: 删除 6 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-005: [BATCH-1] 删除 `filesystem_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/filesystem_tools.py`
      操作: 删除 3 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-006: [BATCH-1] 删除 `mcp_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/mcp_tools.py`
      操作: 删除 6 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-007: [BATCH-1] 删除 `memory_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/memory_tools.py`
      操作: 删除 6 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-008: [BATCH-1] 删除 `misc_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/misc_tools.py`
      操作: 删除 6 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-009: [BATCH-1] 删除 `network_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/network_tools.py`
      操作: 删除 2 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-010: [BATCH-1] 删除 `runtime_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/runtime_tools.py`
      操作: 删除 6 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-011: [BATCH-1] 删除 `session_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/session_tools.py`
      操作: 删除 5 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-012: [BATCH-1] 删除 `supervision_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/supervision_tools.py`
      操作: 删除 2 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-013: [BATCH-1] 删除 `terminal_tools.py` 中所有 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/terminal_tools.py`
      操作: 删除 1 处 `@tool_contract` metadata dict 中的 `"runtime_kinds": [...]` 行（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

---

### task-014 至 task-015: 其他后端服务文件

- [ ] task-014: [BATCH-1] 删除 `mcp_registry.py` 中的 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
      操作: 删除第 432 行：`"runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],`（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

- [ ] task-015: [BATCH-1] 删除 `tool_search_tool.py` 中的 `runtime_kinds` metadata key
      文件: `octoagent/apps/gateway/src/octoagent/gateway/services/tool_search_tool.py`
      操作: 删除第 86 行：`"runtime_kinds": ["worker", "subagent", "graph_agent"],`（含行末逗号）
      验证: `grep '"runtime_kinds"' <文件路径>` 返回零结果；文件可正常导入

---

## Batch 2 — 前端清理

- [ ] task-016: [BATCH-2] 删除 `AgentEditorSection.tsx` 中的运行形态 UI 区块及相关 prop
      文件: `octoagent/frontend/src/domains/agents/AgentEditorSection.tsx`
      操作:
        1. 删除 `RUNTIME_KIND_OPTIONS` 常量定义（第 10-15 行）
        2. 删除 props 接口中的 `onToggleRuntimeKind: (value: string) => void;` 声明（第 30 行附近）
        3. 删除函数参数解构中的 `onToggleRuntimeKind`（第 64 行附近）
        4. 删除运行形态 UI 区块（包含 `RUNTIME_KIND_OPTIONS.map(...)` 的整段 JSX，约第 250-267 行）
      验证: `grep -E "RUNTIME_KIND_OPTIONS|onToggleRuntimeKind|runtime_kinds" <文件路径>` 返回零结果；TypeScript 编译无报错

- [ ] task-017: [BATCH-2] 删除 `AgentCenter.tsx` 中的 `onToggleRuntimeKind` 传递及相关调用
      文件: `octoagent/frontend/src/pages/AgentCenter.tsx`
      操作:
        1. 删除传递给 `AgentEditorSection` 的 `onToggleRuntimeKind` prop（第 788 行附近）
        2. 删除 `updateDraftList` 函数中 `"runtimeKinds"` key 类型约束（第 540 行附近）；若 `updateDraftList` 仅服务于 `runtimeKinds` 一种用途，则整个函数一并删除；若有其他用途，则仅收窄类型移除 `"runtimeKinds"` 分支
      验证: `grep -E "onToggleRuntimeKind|runtimeKinds" <文件路径>` 返回零结果；TypeScript 编译无报错

- [ ] task-018: [BATCH-2] 删除 `agentManagementData.ts` 中的 `runtimeKinds` draft/payload 字段
      文件: `octoagent/frontend/src/domains/agents/agentManagementData.ts`
      操作:
        1. 删除 `AgentEditorDraft.runtimeKinds: string[]` 字段声明（第 93 行附近）
        2. 删除 `runtimeKinds` 初始化逻辑（第 450-451 行附近）
        3. 删除 `DEFAULT_RUNTIME_KINDS` 常量（若存在且无其他引用）
        4. 删除 `buildAgentPayload` 中的 `runtime_kinds: uniqueStrings(draft.runtimeKinds),` 输出行（第 526 行附近）
      验证: `grep -E "runtimeKinds|DEFAULT_RUNTIME_KINDS" <文件路径>` 返回零结果；TypeScript 编译无报错

---

## 集成验证

- [ ] task-019: [验证] 全量验证 Batch 1 + Batch 2 清理结果
      文件: N/A（验证任务，不修改文件）
      操作（按序执行）:
        1. `grep -r "_resolve_tool_runtime_kinds" octoagent/` — 期望零结果（SC-001）
        2. `grep -r '"runtime_kinds"' octoagent/apps/gateway/src/octoagent/gateway/services/` — 期望零结果，仅允许 `BundledToolDefinition` Pydantic 字段定义（SC-002）
        3. `grep -r "runtimeKinds\|onToggleRuntimeKind\|RUNTIME_KIND_OPTIONS" octoagent/frontend/src/` -- 期望零结果（测试文件 mock 数据中的 `runtime_kinds` 属于 `WorkerCapabilityProfile`，不在此检查范围）（SC-004）
        4. 后端 pytest 全量运行，确认全部通过（SC-003）
        5. 前端 `pnpm build` 通过，无 TypeScript 编译错误（SC-005）
        6. `grep "runtime_kinds" octoagent/apps/gateway/src/octoagent/gateway/db/sqlite_init.py` — 期望仍有结果（确认 DB 字段未被误删）（SC-006）
        7. `grep "_enforce_child_target_kind_policy\|class RuntimeKind" octoagent/` — 期望仍有结果（确认安全边界未被误删）（FR-009）
      验证: 以上 7 项全部通过则清理完成

---

## FR 覆盖映射

| FR | 描述（简） | 覆盖任务 |
|----|-----------|---------|
| FR-001 | 删除 `_resolve_tool_runtime_kinds()` 方法及调用点 | task-001 |
| FR-002 | 删除 `BundledToolDefinition.runtime_kinds` 赋值逻辑 | task-001 |
| FR-003 | 删除所有 `@tool_contract` metadata 中的 `runtime_kinds` 字段 | task-002 ~ task-015 |
| FR-004 | 删除 `ToolAvailabilityExplanation.metadata.runtime_kinds` 输出 | task-001 |
| FR-005 | 删除前端"运行形态"多选框 | task-016 |
| FR-006 | 删除 `onToggleRuntimeKind` 回调传递 | task-016, task-017 |
| FR-007 | 删除 `agentManagementData.ts` 中 `runtimeKinds` draft/payload | task-018 |
| FR-008 | 不删除 `WorkerProfile.runtime_kinds` DB 字段 | task-019（验证步骤 6） |
| FR-009 | 不删除 `RuntimeKind` 枚举、路由/安全边界方法 | task-019（验证步骤 7） |
| FR-010 | 删除 bootstrap 模板 `{{runtime_kinds}}` 占位符 | task-001 |
| FR-011 | `types/index.ts` 的 `RuntimeKind` type 保留（前提不满足） | 不触碰，无对应任务 |

---

## 并行说明

- task-002 至 task-015（12 个 builtin_tools + mcp_registry + tool_search_tool）可与 task-001 **并行执行**，彼此无依赖
- task-016、task-017、task-018 可与 Batch 1 任意任务**并行执行**（前后端独立）
- task-019 必须在所有前序任务完成后执行

## 注意事项

- 删除 dict key 时注意行末逗号：如果被删行是 dict 最后一个 key，还需检查上一行末尾是否留有多余逗号
- `types/index.ts` 不修改（FR-011 前提不满足）
- 测试文件（`App.test.tsx`、`AgentCenter.test.tsx`）中 mock 数据的 `runtime_kinds` 字段属于 `WorkerCapabilityProfile` 数据，不在清理范围内，不修改
