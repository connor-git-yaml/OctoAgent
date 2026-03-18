# Tasks: Feature 061 — 统一工具注入 + 权限 Preset 模型

**Feature ID**: 061
**Generated**: 2026-03-17
**Status**: Ready for Implementation
**Source**: spec.md, plan.md, data-model.md, contracts/

---

## 任务总览

| Phase | User Story | 优先级 | 任务数 | 预计复杂度 |
|-------|-----------|--------|--------|-----------|
| Phase 1 | US-001 权限 Preset 基础设施 | P1 | T-001 ~ T-010 | L |
| Phase 2 | US-005 二级审批运行时覆盖 | P1 | T-011 ~ T-017 | M |
| Phase 3 | US-002 Deferred Tools 懒加载 | P1 | T-018 ~ T-027 | L |
| Phase 4 | US-003 Bootstrap 简化 + 统一 | P2 | T-028 ~ T-033 | M |
| Phase 5 | US-004 Skill-Tool 注入优化 | P3 | T-034 ~ T-039 | M |
| Phase 6 | 集成测试 + 前端适配 + 文档 | — | T-040 ~ T-046 | M |

**总计**: 46 个任务

---

## 并行机会标注

```
Phase 1:
  [T-001, T-002]         可并行（枚举 + 数据模型，无依赖）
  [T-003, T-004]         可并行（两个 Hook 实现，仅依赖 T-001）
  [T-005, T-006]         顺序（T-005 broker 改造 → T-006 测试）
  [T-007, T-008]         顺序（migration → AgentRuntime 字段）
  [T-009, T-010]         顺序（集成连通 → 集成测试）

Phase 2:
  [T-011, T-012]         可并行（SQLite store + 内存缓存）
  [T-014, T-015]         可并行（SSE 事件 + Web 端管理 UI）

Phase 3:
  [T-018, T-019]         可并行（tier 参数 + DeferredToolEntry/CoreToolSet 已在 Phase 1 就绪）
  [T-020, T-021]         可并行（tool_search 工具 + ToolIndex facade）

Phase 4:
  [T-028, T-029]         顺序（砍模板 → 简化 bootstrap）

Phase 5:
  [T-034, T-035]         顺序（解析 → 提升逻辑）

Phase 6:
  [T-040, T-041, T-042]  可并行（端到端测试、前端适配、文档更新）
```

---

## Phase 1: 权限 Preset 基础设施（P1 — US-001）

### [x] T-001: 新增 PermissionPreset / PresetDecision 枚举 + PRESET_POLICY 矩阵

**所属 User Story**: US-001
**描述**: 在 tooling/models.py 中新增 `PermissionPreset`、`PresetDecision` 枚举和 `PRESET_POLICY` 矩阵，以及 `preset_decision()` 查表函数。同时新增 `ToolTier` 枚举（Phase 3 使用，但数据模型统一在此引入）。标记 `ToolProfile` 为废弃，新增 `TOOL_PROFILE_TO_PRESET` 映射和 `migrate_tool_profile_to_preset()` 兼容函数。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/models.py`

**依赖**: 无

**验收标准**:
- `PermissionPreset` 有三个值: MINIMAL, NORMAL, FULL
- `PresetDecision` 有两个值: ALLOW, ASK（无 DENY）
- `PRESET_POLICY` 矩阵 9 个组合全部正确（对齐 contracts/permission_preset.py）
- `preset_decision()` 对所有 9 个组合返回正确结果
- `ToolProfile` 标记 deprecated，`TOOL_PROFILE_TO_PRESET` 映射正确
- `ToolTier` 有两个值: CORE, DEFERRED
- 现有代码引用 `ToolProfile` 不报错（兼容）

**预估复杂度**: S

---

### [x] T-002: 新增 PresetCheckResult / ApprovalOverride / DeferredToolEntry 数据模型

**所属 User Story**: US-001 + US-002 + US-005
**描述**: 在相应模块中新增 Feature 061 所需的数据模型。`PresetCheckResult` 和 `DeferredToolEntry`/`CoreToolSet`/`ToolSearchResult`/`ToolSearchHit` 在 tooling/models.py；`ApprovalOverride` 在 policy/models.py。对齐 data-model.md 和 contracts/ 中的定义。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/models.py`
- `octoagent/packages/policy/src/octoagent/policy/models.py`

**依赖**: T-001（依赖 PermissionPreset、PresetDecision、ToolTier 枚举）

**验收标准**:
- `PresetCheckResult` 字段与 data-model.md §6.1 一致
- `ApprovalOverride` 字段与 contracts/approval_override.py 一致，含 `create()` 工厂方法
- `DeferredToolEntry` 含 name、one_line_desc（max_length=80）、tool_group、side_effect_level
- `CoreToolSet` 含 `default()` 类方法返回 10 个默认 Core 工具
- `ToolSearchHit` 和 `ToolSearchResult` 字段与 contracts/deferred_tools.py 一致
- 所有模型可序列化/反序列化（Pydantic BaseModel）

**预估复杂度**: S

---

### [x] T-002a: T-001 + T-002 单元测试

**所属 User Story**: US-001
**描述**: 为 T-001 和 T-002 引入的枚举和数据模型编写单元测试。

**涉及文件**:
- `octoagent/packages/tooling/tests/test_models.py`（追加）
- `octoagent/packages/policy/tests/test_models.py`（新增或追加）

**依赖**: T-001, T-002

**验收标准**:
- `preset_decision()` 9 个组合的测试全部通过
- `migrate_tool_profile_to_preset()` 正确映射 + 未知值回退到 MINIMAL
- `CoreToolSet.default()` 包含 tool_search
- `ApprovalOverride.create()` 正确生成 ISO 时间戳
- `ToolPromotionState.promote()` / `demote()` 引用计数逻辑正确

**预估复杂度**: S

---

### [x] T-003: 实现 PresetBeforeHook

**所属 User Story**: US-001
**描述**: 新增 `PresetBeforeHook`（BeforeHook 实现，priority=20）。从 `ExecutionContext` 读取 `permission_preset`，从 `ToolMeta` 读取 `side_effect_level`，查询 PRESET_POLICY 矩阵。ALLOW → `BeforeHookResult(proceed=True)`；ASK → `BeforeHookResult(proceed=False, rejection_reason="ask:preset_denied:{tool_name}:{side_effect_level}")`。同时生成 `PRESET_CHECK` 事件。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/hooks.py`（追加，或新建 hooks/ 子目录）

**依赖**: T-001（PermissionPreset、PRESET_POLICY）

**验收标准**:
- Hook priority=20
- fail_mode=CLOSED
- MINIMAL + NONE → proceed=True
- MINIMAL + REVERSIBLE → proceed=False, rejection_reason 以 "ask:" 开头
- NORMAL + IRREVERSIBLE → proceed=False
- FULL + IRREVERSIBLE → proceed=True
- 每次检查生成 PRESET_CHECK 事件

**预估复杂度**: M

---

### [x] T-004: 实现 ApprovalOverrideHook

**所属 User Story**: US-001 + US-005
**描述**: 新增 `ApprovalOverrideHook`（BeforeHook 实现，priority=10，高于 PresetBeforeHook）。查询 ApprovalOverrideCache（内存缓存），命中 always → `BeforeHookResult(proceed=True)` 并设置 `override_hit=True`；未命中 → `BeforeHookResult(proceed=True)`（交给后续 Hook 决策）。命中时生成 `APPROVAL_OVERRIDE_HIT` 事件。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/hooks.py`（追加）

**依赖**: T-001, T-002（ApprovalOverride 模型 + ApprovalOverrideCache 接口）

**验收标准**:
- Hook priority=10（先于 PresetBeforeHook 执行）
- 缓存命中 always → proceed=True，跳过后续 Preset 检查
- 缓存未命中 → proceed=True（不拦截，后续 Hook 继续）
- 命中时生成 APPROVAL_OVERRIDE_HIT 事件
- 不同 agent_runtime_id 的覆盖互相隔离

**预估复杂度**: M

---

### [x] T-004a: PresetBeforeHook + ApprovalOverrideHook 单元测试

**所属 User Story**: US-001
**描述**: 为两个新 Hook 编写单元测试，覆盖 PRESET_POLICY 矩阵全部 9 个组合、always 覆盖命中/未命中、不同 Agent 实例隔离。

**涉及文件**:
- `octoagent/packages/tooling/tests/test_hooks.py`（追加）

**依赖**: T-003, T-004

**验收标准**:
- PresetBeforeHook: 9 个 Preset × SideEffectLevel 组合全部覆盖
- ApprovalOverrideHook: always 命中放行、未命中透传
- Agent 实例隔离: Worker A 的 always 不影响 Worker B
- Hook Chain 顺序: ApprovalOverrideHook(10) → PresetBeforeHook(20) 集成场景

**预估复杂度**: M

---

### [x] T-005: ToolBroker 权限检查改造（硬拒绝 → Hook Chain）

**所属 User Story**: US-001
**描述**: 修改 `broker.py`，移除 L272-283 硬编码 `profile_allows()` 检查和 L286-309 FR-010a `PolicyCheckpoint` 强制拒绝逻辑。权限检查完全由 Hook Chain 驱动（ApprovalOverrideHook + PresetBeforeHook）。更新 `ExecutionContext` 的 `profile` 字段使用，新增 `permission_preset` 字段读取。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/broker.py`
- `octoagent/packages/tooling/src/octoagent/tooling/models.py`（ExecutionContext 新增 permission_preset 字段）

**依赖**: T-003, T-004（两个 Hook 就绪）

**验收标准**:
- broker.execute() 不再包含硬编码的 profile_allows() 调用
- broker.execute() 不再包含 FR-010a PolicyCheckpoint 强制拒绝
- proceed=False + rejection_reason 以 "ask:" 开头 → 返回 ToolResult(is_error=True, error=reason)
- 现有 Hook Chain（日志、审计等）不受影响
- ExecutionContext.permission_preset 字段可用，profile 字段保留兼容

**预估复杂度**: M

---

### [x] T-006: ToolBroker 改造单元测试

**所属 User Story**: US-001
**描述**: 更新 broker 测试，验证新的 Hook Chain 权限检查行为，确保旧的硬拒绝逻辑被正确移除。

**涉及文件**:
- `octoagent/packages/tooling/tests/test_broker.py`（修改）
- `octoagent/packages/tooling/tests/test_integration.py`（修改）

**依赖**: T-005

**验收标准**:
- 原有 profile_allows 硬拒绝的测试用例更新为 soft deny (ask) 行为
- ToolResult.error 以 "ask:" 开头的 soft deny 场景覆盖
- FULL Preset + IRREVERSIBLE 工具 → allow（不再被 FR-010a 硬拒绝）
- 旧 ToolProfile 兼容路径测试（profile → permission_preset 自动映射）

**预估复杂度**: M

---

### [x] T-007: SQLite migration — approval_overrides 表 + agent_runtimes 新增列

**所属 User Story**: US-001 + US-005
**描述**: 在 SQLite 初始化脚本中新增 `approval_overrides` 表 DDL（含索引），并为 `agent_runtimes` 表添加 `permission_preset` 和 `role_card` 列。

**涉及文件**:
- `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`

**依赖**: 无（纯 DDL）

**验收标准**:
- `approval_overrides` 表结构与 data-model.md §4.1 一致
- UNIQUE(agent_runtime_id, tool_name) 约束存在
- idx_overrides_agent 和 idx_overrides_tool 索引存在
- agent_runtimes 表新增 permission_preset（默认 'normal'）和 role_card（默认 ''）列
- CREATE TABLE IF NOT EXISTS / ALTER TABLE ADD COLUMN 幂等执行不报错

**预估复杂度**: S

---

### [x] T-008: AgentRuntime 模型新增 permission_preset + role_card 字段  ✓

**所属 User Story**: US-001 + US-003
**描述**: 在 `AgentRuntime`（或对应的 agent_context 模型）中新增 `permission_preset` 和 `role_card` 字段。Butler 默认 FULL，Worker 默认 NORMAL，Subagent 继承 Worker。更新 agent_context_store 的读写逻辑。

**涉及文件**:
- `octoagent/packages/core/src/octoagent/core/models/agent_context.py`
- `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py`

**依赖**: T-007（数据库列就绪）

**验收标准**:
- AgentRuntime / AgentContext 模型含 permission_preset 字段（默认 "normal"）
- AgentRuntime / AgentContext 模型含 role_card 字段（默认 ""）
- Store 的 INSERT/SELECT 正确处理新字段
- Butler 创建时 permission_preset = "full"
- Worker 创建时 permission_preset = "normal"（可覆盖）
- Subagent 继承 Worker 的 permission_preset

**预估复杂度**: M

---

### [x] T-009: 权限 Preset 端到端连通 — CapabilityPack + ExecutionContext 串联

**所属 User Story**: US-001
**描述**: 将 PermissionPreset 从 AgentRuntime 一路传递到 ToolBroker 的 ExecutionContext。CapabilityPackService 构建工具上下文时读取 Agent 的 preset 并设置 ExecutionContext.permission_preset。注册 PresetBeforeHook 和 ApprovalOverrideHook 到 ToolBroker 的 Hook Chain。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

**依赖**: T-003, T-004, T-005, T-008

**验收标准**:
- ExecutionContext.permission_preset 正确反映 Agent 实例的 Preset
- PresetBeforeHook 和 ApprovalOverrideHook 注册到 ToolBroker Hook Chain
- minimal Worker 调用 reversible 工具 → ask
- normal Worker 调用 reversible 工具 → allow
- full Agent 调用 irreversible 工具 → allow

**预估复杂度**: M

---

### [x] T-010: Phase 1 集成测试 — 权限 Preset 完整链路

**所属 User Story**: US-001
**描述**: 编写集成测试覆盖 US-001 的全部 10 个验收场景。测试从 Agent 创建、工具调用、Preset 检查、ask 触发到事件记录的完整链路。

**涉及文件**:
- `octoagent/packages/tooling/tests/test_integration.py`（追加）
- `octoagent/apps/gateway/tests/test_capability_pack_tools.py`（追加）

**依赖**: T-009

**验收标准**:
- US-001 场景 1-5（三级 Preset 的 allow/ask 行为）全部通过
- US-001 场景 8（Subagent 继承 Worker Preset）通过
- US-001 场景 9-10（默认 Preset 分配）通过
- SC-002: PresetBeforeHook 延迟 <1ms（性能断言）
- SC-003: 所有工具调用 100% 经过 Preset 检查（事件审计）

**预估复杂度**: L

---

## Phase 2: 二级审批运行时覆盖（P1 — US-005）

### [x] T-011: ApprovalOverrideRepository 实现（SQLite 持久化）

**所属 User Story**: US-005
**描述**: 实现 `ApprovalOverrideRepository`，基于 SQLite `approval_overrides` 表。提供 save_override / remove_override / has_override / load_overrides / load_all_overrides / remove_overrides_for_tool / remove_overrides_for_agent 方法。对齐 contracts/approval_override.py 中的 Protocol 定义。

**涉及文件**:
- `octoagent/packages/policy/src/octoagent/policy/approval_override_store.py`（新增）

**依赖**: T-007（approval_overrides 表就绪）

**验收标准**:
- save_override: INSERT OR REPLACE 语义（幂等）
- load_overrides: 按 agent_runtime_id 查询
- load_all_overrides: 全量查询
- remove_override: 删除单条记录
- remove_overrides_for_tool: 按 tool_name 批量删除
- remove_overrides_for_agent: 按 agent_runtime_id 批量删除
- 所有写入操作同时生成 Event Store 事件

**预估复杂度**: M

---

### [x] T-012: ApprovalOverrideCache 实现

**所属 User Story**: US-005
**描述**: 实现 `ApprovalOverrideCache` 内存缓存，对齐 contracts/approval_override.py 中的类定义。支持 has / set / remove / load_from_records / clear_agent / clear_tool / list_for_agent。

**涉及文件**:
- `octoagent/packages/policy/src/octoagent/policy/approval_override_store.py`（追加到同文件，或独立文件）

**依赖**: T-002（ApprovalOverride 模型）

**验收标准**:
- has() 返回 O(1) 查询结果
- load_from_records() 从 ApprovalOverride 列表批量加载
- set/remove 保持缓存与 Repository 一致
- clear_agent/clear_tool 批量清理正确
- key = (agent_runtime_id, tool_name)，不同 Agent 隔离

**预估复杂度**: S

---

### [x] T-012a: ApprovalOverrideRepository + Cache 单元测试

**所属 User Story**: US-005
**描述**: 为 Repository 和 Cache 编写单元测试。

**涉及文件**:
- `octoagent/packages/policy/tests/test_approval_override_store.py`（新增）

**依赖**: T-011, T-012

**验收标准**:
- Repository CRUD 操作正确
- save_override 幂等（重复调用不报错）
- Cache has/set/remove 与 Repository 一致
- Agent 实例隔离（CLR-002）
- 工具移除时批量清理（Edge Case）

**预估复杂度**: M

---

### [x] T-013: ApprovalManager 改造 — always 持久化 + Agent 实例隔离

**所属 User Story**: US-005
**描述**: 改造 `ApprovalManager`：注入 `ApprovalOverrideRepository` 和 `ApprovalOverrideCache`。`_allow_always` 从全局 `dict[str, bool]` 改为委托 Cache 查询。resolve() ALLOW_ALWAYS 决策同时写入 Cache + Repository + Event Store。进程启动时 `recover_from_store()` 从 SQLite 恢复。

**涉及文件**:
- `octoagent/packages/policy/src/octoagent/policy/approval_manager.py`

**依赖**: T-011, T-012

**验收标准**:
- `_allow_always` 不再是全局 dict，改为 Cache 委托
- resolve(ALLOW_ALWAYS) 同时写入内存缓存 + SQLite
- recover_from_store() 从 SQLite 批量加载
- Agent 实例间 always 隔离（CLR-002）
- deny 仅作用于本次调用（FR-012），不写入持久化
- 审批超时默认 600s（CLR-004）

**预估复杂度**: M

---

### [x] T-013a: ApprovalManager 改造单元测试

**所属 User Story**: US-005
**描述**: 更新 ApprovalManager 测试覆盖新的 always 持久化行为。

**涉及文件**:
- `octoagent/packages/policy/tests/test_approval_manager.py`（修改或新增）

**依赖**: T-013

**验收标准**:
- approve → 本次允许，下次仍触发审批（US-005 场景 1）
- always → 本次允许 + 持久化 + 下次直接放行（US-005 场景 2）
- 进程重启后 always 仍有效（US-005 场景 3）— 模拟 recover_from_store()
- deny → 本次拒绝，不永久封禁（US-005 场景 4）
- 超时 → 默认 deny（US-005 场景 5）

**预估复杂度**: M

---

### [x] T-014: 审批 ask 信号桥接 — ToolBroker → ApprovalManager

**所属 User Story**: US-005
**描述**: 在 LLM 执行层（llm_service / task_runner）中识别 ToolResult.error 的 "ask:" 前缀，桥接到 ApprovalManager 审批流。approve 后重新执行工具调用；always 后写入 override + 重新执行；deny 返回拒绝信息给 LLM。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`（如需）

**依赖**: T-005（ask: 前缀），T-013（ApprovalManager 改造）

**验收标准**:
- "ask:" 前缀被正确识别为 soft deny
- 触发 ApprovalManager.register() 创建审批请求
- approve → 重新执行工具调用
- always → 写入 override + 重新执行
- deny → 返回拒绝信息给 LLM
- 非 "ask:" 前缀的 rejection 保持原行为（硬拒绝）

**预估复杂度**: M

---

### [x] T-015: 审批 SSE 事件推送 + 前端审批覆盖管理

**所属 User Story**: US-005
**描述**: 确保 ask 触发的审批请求通过 SSE 推送到前端（复用现有 approval:requested 事件）。扩展审批响应支持 always 选项。在 Web 端已有审批 UI 基础上适配三选项（approve/always/deny）。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/sse/approval_events.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/approvals.py`
- `octoagent/frontend/src/`（审批相关组件）

**依赖**: T-014

**验收标准**:
- SSE 事件 approval:requested 正确推送 soft deny 审批
- 前端审批 UI 展示三个选项: 本次允许(approve)、永久允许(always)、拒绝(deny)
- always 选项的 UI 提示用户该授权将持久化
- 审批响应 API 正确处理三种决策

**预估复杂度**: M

---

### [x] T-016: Web 端审批覆盖列表管理

**所属 User Story**: US-005
**描述**: 在 Web UI（ControlPlane 或 Advanced 区域）新增 always 授权管理面板。展示当前所有 Agent 的 always 授权列表，支持手动撤销。调用 GET/DELETE /api/approval-overrides 端点。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/routes/approvals.py`（新增 API 端点）
- `octoagent/frontend/src/domains/advanced/` 或 `octoagent/frontend/src/pages/`（新增组件）

**依赖**: T-011（Repository API）

**验收标准**:
- GET /api/approval-overrides 返回所有 always 授权列表
- GET /api/approval-overrides?agent_runtime_id=xxx 按 Agent 过滤
- DELETE /api/approval-overrides/{id} 撤销单条授权
- Web 面板按 Agent 分组展示，每条显示工具名和创建时间
- 撤销后同步清除内存缓存

**预估复杂度**: M

---

### [x] T-017: Phase 2 集成测试 — 二级审批完整链路

**所属 User Story**: US-005
**描述**: 编写集成测试覆盖 US-005 全部 5 个验收场景 + always 持久化跨重启验证。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_approval_override_e2e.py`（新增）

**依赖**: T-014, T-015

**验收标准**:
- US-005 场景 1-5 全部通过
- SC-005: always 授权跨进程重启后仍有效
- Edge Case: 并发审批互不阻塞
- Edge Case: always 授权的工具被移除后不影响其他工具
- 所有审批决策事件可在 Event Store 查询

**预估复杂度**: L

---

## Phase 3: Deferred Tools 懒加载（P1 — US-002）

### [x] T-018: @tool_contract 新增 tier 参数

**所属 User Story**: US-002
**描述**: 扩展 `@tool_contract` 装饰器，新增可选 `tier: ToolTier = ToolTier.DEFERRED` 参数。Core 工具通过配置指定（非装饰器硬编码）。更新 `ToolMeta` 新增 `tier` 字段。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/decorators.py`
- `octoagent/packages/tooling/src/octoagent/tooling/models.py`（ToolMeta 新增 tier 字段）

**依赖**: T-001（ToolTier 枚举）

**验收标准**:
- `@tool_contract` 接受 `tier` 参数，默认 DEFERRED
- ToolMeta 含 tier 字段，默认 DEFERRED
- 现有工具注册不受影响（tier 可选，默认 DEFERRED）
- FR-039: 向后兼容

**预估复杂度**: S

---

### [x] T-018a: @tool_contract tier 参数单元测试

**所属 User Story**: US-002
**描述**: 测试 @tool_contract 新增 tier 参数的行为。

**涉及文件**:
- `octoagent/packages/tooling/tests/test_decorators.py`（追加）

**依赖**: T-018

**验收标准**:
- 未指定 tier → 默认 DEFERRED
- 显式指定 tier=CORE → ToolMeta.tier == CORE
- 现有测试不受影响

**预估复杂度**: S

---

### [x] T-019: ToolIndex 新增 search_for_deferred() facade

**所属 User Story**: US-002
**描述**: 在 `tool_index.py` 中新增 `search_for_deferred()` 方法，接收自然语言查询，返回匹配工具的完整 ToolMeta（含 schema）。复用现有 cosine + BM25 混合打分。新增降级逻辑：ToolIndex 不可用时回退全量 Deferred 名称列表。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/tool_index.py`

**依赖**: T-001（ToolTier 枚举）

**验收标准**:
- search_for_deferred(query) 返回 ToolSearchResult
- 复用现有 select_tools() 基础设施（FR-017）
- ToolIndex 不可用时 → is_fallback=True + 返回全量 Deferred 名称列表（FR-022）
- 降级时生成 TOOL_INDEX_DEGRADED 事件
- SC-004: 检索延迟 <10ms

**预估复杂度**: M

---

### [x] T-019a: search_for_deferred() 单元测试

**所属 User Story**: US-002
**描述**: 测试 search_for_deferred() 的正常检索、降级、空结果场景。

**涉及文件**:
- `octoagent/packages/tooling/tests/test_tool_index.py`（追加）

**依赖**: T-019

**验收标准**:
- 正常检索返回匹配结果
- ToolIndex 不可用 → fallback 模式
- 空查询返回空结果 + 提示信息（Edge Case: tool_search 零命中）
- 性能断言 <10ms

**预估复杂度**: S

---

### [x] T-020: tool_search 核心工具实现

**所属 User Story**: US-002
**描述**: 新增 `tool_search` 工具，使用 `@tool_contract` 注册，tier=CORE。接收自然语言查询参数，调用 ToolIndex.search_for_deferred()，返回 ToolSearchResult。每次调用生成 TOOL_SEARCH_EXECUTED 事件。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/tool_search_tool.py`（新增）

**依赖**: T-019（search_for_deferred facade）

**验收标准**:
- 注册为 Core 工具（tier=CORE）
- side_effect_level=NONE
- 接收 query: str 参数
- 返回 ToolSearchResult（含 results, is_fallback, backend, latency_ms）
- 每次调用生成 TOOL_SEARCH_EXECUTED 事件（FR-034）
- FR-018: tool_search 自身必须在 Core Tools 清单中

**预估复杂度**: M

---

### [x] T-020a: tool_search 工具单元测试

**所属 User Story**: US-002
**描述**: 测试 tool_search 工具的正常调用、降级、事件记录。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_tool_search.py`（新增）

**依赖**: T-020

**验收标准**:
- 正常查询返回匹配工具的完整 schema
- 降级场景返回 is_fallback=True
- 事件记录正确（TOOL_SEARCH_EXECUTED）
- 空查询处理正确

**预估复杂度**: S

---

### [x] T-021: CapabilityPackService 工具上下文分区 — Core + Deferred

**所属 User Story**: US-002
**描述**: 重构 `CapabilityPackService.build_tool_context()`，按 ToolTier 将工具分为 Core 和 Deferred 两组。Core Tools → 完整 JSON Schema（FunctionToolset）；Deferred Tools → `{name, one_line_desc}` 列表注入 system prompt。使用 `CoreToolSet.default()` 确定初始 Core 清单。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`

**依赖**: T-018（ToolMeta.tier 字段），T-002（DeferredToolEntry、CoreToolSet 模型）

**验收标准**:
- build_tool_context() 返回 Core Tools schema + Deferred Tools 列表
- Deferred Tools 列表格式为 `{name, one_line_desc}`，注入 system prompt
- MCP 工具默认以 Deferred 状态纳入（FR-021）
- Core Tools 至少包含 tool_search（FR-018）
- 对比全量注入模式，Deferred 模式 token 减少 ≥60%（SC-001）

**预估复杂度**: L

---

### [x] T-022: DynamicToolset 集成 — 运行时工具注入

**所属 User Story**: US-002
**描述**: 在 `llm_service.py` 中集成 DynamicToolset（Pydantic AI），在每个 run_step 前评估是否有 tool_search 返回的工具需要注入活跃工具集。维护 `ToolPromotionState`，追踪提升/回退状态。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

**依赖**: T-020（tool_search 工具），T-021（分区逻辑）

**验收标准**:
- tool_search 返回的工具在下一个 run_step 中以完整 schema 注入
- 工具提升生成 TOOL_PROMOTED 事件（FR-036）
- ToolPromotionState 正确追踪提升来源
- 已提升的工具仍经过 Preset 权限检查（与 Phase 1 联动）

**预估复杂度**: L

---

### [x] T-022a: DynamicToolset 集成单元测试

**所属 User Story**: US-002
**描述**: 测试运行时工具注入的正确性。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_llm_service_tools.py`（追加）

**依赖**: T-022

**验收标准**:
- tool_search 结果注入活跃集合
- 提升的工具 schema 完整（FR-040）
- TOOL_PROMOTED 事件正确记录
- ToolPromotionState 引用计数正确

**预估复杂度**: M

---

### [x] T-023: Deferred Tools system prompt 注入

**所属 User Story**: US-002
**描述**: 实现 Deferred Tools 列表的 system prompt 注入。使用 contracts/deferred_tools.py 中的 `DEFERRED_TOOLS_PROMPT_TEMPLATE` 和 `format_deferred_tools_list()`。在 agent_context 组装时注入到 system prompt。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`

**依赖**: T-021（分区逻辑提供 Deferred 列表）

**验收标准**:
- system prompt 中包含 Deferred Tools 列表
- 列表格式: `- {name}: {one_line_desc}`
- 包含总数提示
- LLM 收到的 prompt 引导"不确定时先用 tool_search 搜索"

**预估复杂度**: S

---

### [x] T-024: Phase 3 集成测试 — Deferred Tools 端到端

**所属 User Story**: US-002
**描述**: 编写集成测试覆盖 US-002 全部 6 个验收场景。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_deferred_tools_e2e.py`（新增）

**依赖**: T-022, T-023

**验收标准**:
- US-002 场景 1: 初始 context 仅 Core Tools 完整 schema
- US-002 场景 2: tool_search 返回完整 schema，后续可调用
- US-002 场景 3: SC-001 token 减少 ≥60%
- US-002 场景 4: tool_search 加载的工具仍经过 Preset 检查
- US-002 场景 5: ToolIndex 降级 → 全量名称列表
- US-002 场景 6: MCP 工具默认 Deferred

**预估复杂度**: L

---

### [x] T-025: ToolPromotionState 实现 + 事件记录

**所属 User Story**: US-002
**描述**: 实现 `ToolPromotionState`（对齐 contracts/deferred_tools.py），追踪工具提升来源的引用计数。promote/demote 操作生成 TOOL_PROMOTED/TOOL_DEMOTED 事件。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`（或独立模块）

**依赖**: T-002（ToolPromotionState 模型）

**验收标准**:
- promote() 首次提升返回 True，重复来源不重复计数
- demote() 最后来源移除返回 True（应回退 Deferred）
- is_promoted() 正确判断
- active_tool_names 返回当前所有 Active 工具
- 事件记录含 tool_name, direction, source, source_id

**预估复杂度**: S

---

### [x] T-025a: ToolPromotionState 单元测试

**所属 User Story**: US-002
**描述**: 测试引用计数逻辑的正确性。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_tool_promotion_state.py`（新增）

**依赖**: T-025

**验收标准**:
- 单来源 promote → demote → 回退
- 多来源 promote → 部分 demote → 不回退 → 全部 demote → 回退
- 重复 promote 同一来源 → 不重复计数

**预估复杂度**: S

---

## Phase 4: Bootstrap 简化 + 统一（P2 — US-003）

### [x] T-028: 砍掉 WorkerType 多模板 + _build_worker_profiles()

**所属 User Story**: US-003
**描述**: 从 `capability_pack.py` 中移除 `_build_worker_profiles()` 和 `_build_bootstrap_templates()` 中 4 个 Worker Type 模板（bootstrap:general/ops/research/dev）。`resolve_profile_first_tools()` 不再按 `default_tool_groups` 过滤。WorkerType 枚举保留为分类标签但不再作为工具过滤维度。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/packages/core/src/octoagent/core/models/capability.py`

**依赖**: T-009（统一工具集已就位，Preset 替代 Profile）

**验收标准**:
- _build_worker_profiles() 移除或返回单一统一 profile
- bootstrap:general/ops/research/dev 4 个模板文件/配置移除
- default_tool_groups 矩阵不再用于工具过滤
- WorkerType 枚举保留但语义变化（分类标签，非过滤维度）
- SC-007: 多模板系统完全移除

**预估复杂度**: M

---

### [x] T-029: Bootstrap 简化为 shared + 角色卡片

**所属 User Story**: US-003
**描述**: 重构 `agent_context.py` 中的 bootstrap 组装逻辑。bootstrap 由 `bootstrap:shared`（~50 tokens 核心元信息: project/workspace/datetime/preset）+ 角色卡片（Agent 实例级自定义描述 ~100-150 tokens）组成。角色卡片从 AgentRuntime.role_card 读取。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`

**依赖**: T-028, T-008（role_card 字段就绪）

**验收标准**:
- bootstrap:shared 仅含 project/workspace/datetime/preset 元信息（~50 tokens）
- 角色卡片从 AgentRuntime.role_card 读取（~100-150 tokens）
- 冗余字段（重复治理警告、已由 behavior pack 覆盖的内容）移除
- SC-006: bootstrap 总量 ≤200 tokens
- 角色卡片支持创建时自定义（FR-027）

**预估复杂度**: M

---

### [x] T-030: Worker 创建 API 适配 — permission_preset + role_card 参数

**所属 User Story**: US-003
**描述**: 更新 Worker 创建的 API 端点和 control_plane 服务，支持 permission_preset 和 role_card 参数。Butler 创建 Worker 时可指定这两个参数，默认值: preset=NORMAL, role_card=""。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/control_plane.py`
- `octoagent/packages/core/src/octoagent/core/models/control_plane.py`

**依赖**: T-008, T-029

**验收标准**:
- Worker 创建 API 接受 permission_preset 参数（minimal/normal/full）
- Worker 创建 API 接受 role_card 参数（字符串）
- 未指定 preset → 默认 normal（FR-005）
- Butler 工具 spawn_worker 传递 preset 和 role_card
- 前端 Worker 创建 UI 适配（T-042 中处理）

**预估复杂度**: M

---

### [x] T-031: Bootstrap 简化单元测试

**所属 User Story**: US-003
**描述**: 测试新的 bootstrap 组装逻辑。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_capability_pack_tools.py`（追加）
- `octoagent/apps/gateway/tests/test_worker_runtime.py`（修改）

**依赖**: T-029, T-030

**验收标准**:
- US-003 场景 1: bootstrap = shared + 角色卡片
- US-003 场景 2: 4 个独立模板不再存在
- US-003 场景 3: 角色卡片是引导而非硬约束
- US-003 场景 4: shared 模板无冗余字段
- SC-006: bootstrap token ≤200

**预估复杂度**: M

---

### [x] T-032: WorkerType 依赖清理

**所属 User Story**: US-003
**描述**: 清理代码库中依赖 WorkerType 作为工具过滤维度的引用。WorkerType 保留为分类标签（用于 UI 显示、统计），但不再影响工具可见性或权限。更新 orchestrator、delegation_plane、worker_runtime 等文件。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`
- `octoagent/packages/core/src/octoagent/core/models/delegation.py`

**依赖**: T-028

**验收标准**:
- WorkerType 不再用于 default_tool_groups 查找
- WorkerType 不再用于 WorkerCapabilityProfile 构建
- 保留 WorkerType 作为分类标签（UI、日志、统计）
- 所有受影响的测试更新通过

**预估复杂度**: M

---

### [x] T-033: Phase 4 集成测试 — Bootstrap 简化验证

**所属 User Story**: US-003
**描述**: 端到端测试验证 Bootstrap 简化后的行为。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_bootstrap_simplification.py`（新增）

**依赖**: T-031, T-032

**验收标准**:
- Worker 创建后 bootstrap 内容符合预期（shared + role_card）
- 不同角色卡片的 Worker 行为差异符合预期
- SC-007: 代码库中无 Worker Type 多模板遗留

**预估复杂度**: M

---

## Phase 5: Skill-Tool 注入优化（P3 — US-004）

### [x] T-034: SKILL.md tools_required 字段解析

**所属 User Story**: US-004
**描述**: 在 Skill 解析逻辑中支持 `tools_required` 字段。从 SKILL.md 的 frontmatter 中解析 `tools_required: [tool_name1, tool_name2]` 列表。更新 SkillMdEntry 模型。

**涉及文件**:
- `octoagent/packages/skills/src/octoagent/skills/skill_models.py`
- `octoagent/packages/skills/src/octoagent/skills/discovery.py`

**依赖**: 无（可在 Phase 1-3 完成前开始）

**验收标准**:
- SkillMdEntry 新增 tools_required: list[str] 字段（默认空列表）
- SKILL.md frontmatter 中 `tools_required: [docker.run, terminal.exec]` 正确解析
- 未声明 tools_required 的 Skill 不受影响
- 声明不存在的工具 → 记录警告（Edge Case）

**预估复杂度**: S

---

### [x] T-034a: tools_required 解析单元测试

**所属 User Story**: US-004
**描述**: 测试 tools_required 字段的解析行为。

**涉及文件**:
- `octoagent/packages/skills/tests/test_skill_models.py`（追加）
- `octoagent/packages/skills/tests/test_skill_discovery.py`（追加）

**依赖**: T-034

**验收标准**:
- 正常解析 tools_required 列表
- 空列表或缺失字段 → 默认空列表
- 不存在的工具名 → 仍可解析，记录警告

**预估复杂度**: S

---

### [x] T-035: Skill 加载时工具提升 — Deferred → Active

**所属 User Story**: US-004
**描述**: 在 DynamicToolset 的 per_run_step 评估中，检查当前 session 已加载 Skill 的 tools_required，将这些工具从 Deferred 提升到 Active（完整 schema 注入）。使用 ToolPromotionState 追踪来源为 `skill:{skill_name}`。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

**依赖**: T-022（DynamicToolset 集成），T-025（ToolPromotionState），T-034（tools_required 解析）

**验收标准**:
- Skill 加载后其 tools_required 工具自动提升到 Active
- 生成 TOOL_PROMOTED 事件（source="skill", source_id=skill_name）
- 提升的工具仍受 Preset 权限检查（FR-031）
- 超出 Preset 的工具 schema 可见但调用触发 ask

**预估复杂度**: M

---

### [x] T-036: Skill 卸载时工具回退 — Active → Deferred

**所属 User Story**: US-004
**描述**: Skill 卸载时，从 ToolPromotionState 移除 `skill:{skill_name}` 来源。仅因该 Skill 提升的工具（无其他来源）回退到 Deferred。

**涉及文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

**依赖**: T-035

**验收标准**:
- Skill 卸载后独占提升的工具回退到 Deferred
- 生成 TOOL_DEMOTED 事件
- 多 Skill 共同依赖的工具：单个 Skill 卸载不回退（FR-032）
- tool_search 提升的工具不受 Skill 卸载影响

**预估复杂度**: M

---

### [x] T-037: Skill-Tool 注入单元测试

**所属 User Story**: US-004
**描述**: 测试 Skill 加载/卸载时工具提升/回退的完整逻辑。

**涉及文件**:
- `octoagent/apps/gateway/tests/test_skill_tool_injection.py`（新增）

**依赖**: T-035, T-036

**验收标准**:
- US-004 场景 1: Skill 加载 → tools_required 工具提升
- US-004 场景 2: 超出 Preset 的工具仍提升但调用触发 ask
- US-004 场景 3: Skill 卸载 → 独占工具回退
- US-004 场景 4: 多 Skill 共享工具 → 单 Skill 卸载不回退

**预估复杂度**: M

---

### [x] T-038: 内置 Skill 添加 tools_required 声明

**所属 User Story**: US-004
**描述**: 为 `skills/` 目录下的内置 Skill 的 SKILL.md 添加 `tools_required` 声明。例如 coding-agent Skill 声明依赖 `filesystem.write_text`、`terminal.exec` 等。

**涉及文件**:
- `skills/*/SKILL.md`（8 个内置 Skill 的 SKILL.md）

**依赖**: T-034（解析支持就绪）

**验收标准**:
- 每个内置 Skill 的 SKILL.md 含 tools_required 字段
- tools_required 中的工具名与实际注册名一致
- 未使用特殊工具的 Skill 可声明空列表

**预估复杂度**: S

---

### [x] T-039: Phase 5 集成测试 — Skill-Tool 注入端到端

**所属 User Story**: US-004
**描述**: 端到端测试 Skill 加载→工具提升→Preset 检查→Skill 卸载→工具回退的完整链路。

**涉及文件**:
- `octoagent/apps/gateway/tests/e2e/test_skill_tool_injection_e2e.py`（新增）

**依赖**: T-037, T-038

**验收标准**:
- 加载带 tools_required 的 Skill → 对应工具从 Deferred 变为 Active
- Active 工具可直接调用（无需 tool_search）
- 超出 Preset 的工具触发 ask（与 Phase 1 联动）
- Skill 卸载后独占工具回退

**预估复杂度**: M

---

## Phase 6: 集成测试 + 前端适配 + 文档

### [x] T-040: 全功能端到端集成测试

**所属 User Story**: 全部
**描述**: 综合集成测试覆盖 Feature 061 的核心跨 Phase 场景。包括: Agent 创建（Preset+RoleCard）→ 对话启动（Core+Deferred 分区）→ tool_search → Deferred 工具加载 → Preset 权限检查 → ask 审批 → always 持久化 → 进程重启恢复。

**涉及文件**:
- `octoagent/apps/gateway/tests/e2e/test_061_unified_tool_permission.py`（新增）

**依赖**: T-017, T-024, T-033（各 Phase 集成测试通过）

**验收标准**:
- 完整链路端到端通过
- SC-001: token 减少 ≥60%
- SC-005: always 跨重启持久化
- SC-009: 所有事件可在 Event Store 查看
- 所有 Edge Cases 有覆盖

**预估复杂度**: L

---

### [x] T-041: 前端适配 — Worker 创建 UI + Preset 展示

**所属 User Story**: US-001 + US-003
**描述**: 更新前端 Worker 创建 UI，支持 permission_preset 选择（minimal/normal/full）和 role_card 输入。更新 ControlPlane / AgentCenter 页面展示 Agent 的 Preset 信息。移除 WorkerType 多模板相关 UI 元素。

**涉及文件**:
- `octoagent/frontend/src/domains/agents/AgentTemplatePicker.tsx`
- `octoagent/frontend/src/domains/agents/AgentEditorSection.tsx`
- `octoagent/frontend/src/domains/agents/agentManagementData.ts`
- `octoagent/frontend/src/pages/ControlPlane.tsx`
- `octoagent/frontend/src/types/index.ts`

**依赖**: T-030（API 就绪）

**验收标准**:
- Worker 创建对话框包含 Preset 选择器（下拉框，默认 normal）
- Worker 创建对话框包含 Role Card 文本输入
- ControlPlane 页面显示每个 Agent 的 Preset
- WorkerType 多模板选择 UI 移除
- 用户友好的 Preset 说明文案（minimal=保守/normal=标准/full=完全信任）

**预估复杂度**: M

---

### [x] T-042: 前端适配 — Deferred Tools 状态展示

**所属 User Story**: US-002
**描述**: 在 Advanced/诊断区域展示当前 Agent 对话的工具状态：Core Tools 清单、Deferred Tools 清单、已提升工具列表。

**涉及文件**:
- `octoagent/frontend/src/domains/advanced/CapabilitySection.tsx`
- `octoagent/frontend/src/domains/advanced/DashboardSection.tsx`

**依赖**: T-021（分区逻辑，后端数据就绪）

**验收标准**:
- Advanced 页面可查看 Core/Deferred/Promoted 三组工具
- 每个工具显示名称、描述、tier、side_effect_level
- 工具状态变更（提升/回退）实时更新（SSE）

**预估复杂度**: M

---

### [x] T-043: 可观测性事件类型注册

**所属 User Story**: 全部
**描述**: 在 Event Store 的事件类型枚举中注册 Feature 061 新增的事件类型: PRESET_CHECK, APPROVAL_OVERRIDE_HIT, TOOL_SEARCH_EXECUTED, TOOL_PROMOTED, TOOL_DEMOTED。定义对应的 payload schema。

**涉及文件**:
- `octoagent/packages/core/src/octoagent/core/models/event.py`（或 enums.py / payloads.py）

**依赖**: 无（可在 Phase 1 开始前完成）

**验收标准**:
- 5 个新事件类型注册到 EventType 枚举
- 每个事件的 payload schema 与 data-model.md §6 一致
- 现有事件类型不受影响

**预估复杂度**: S

---

### [x] T-044: ToolProfile 兼容层 + 废弃警告

**所属 User Story**: US-001
**描述**: 确保 `ToolProfile` 的所有现有引用通过兼容层正常工作。`profile_allows()` 内部委托到 `preset_decision()`。所有废弃调用生成 DeprecationWarning。更新 `@tool_contract` 的 `tool_profile` 参数为可选废弃参数。

**涉及文件**:
- `octoagent/packages/tooling/src/octoagent/tooling/models.py`
- `octoagent/packages/tooling/src/octoagent/tooling/decorators.py`
- `octoagent/packages/tooling/src/octoagent/tooling/protocols.py`

**依赖**: T-001, T-005

**验收标准**:
- `profile_allows()` 内部使用 TOOL_PROFILE_TO_PRESET 映射 + preset_decision()
- 调用 `profile_allows()` 生成 DeprecationWarning
- `@tool_contract(tool_profile=...)` 仍可工作，生成 DeprecationWarning
- 所有现有测试通过（兼容性）

**预估复杂度**: S

---

### [x] T-045: 性能验证测试

**所属 User Story**: 全部
**描述**: 专项性能测试验证 Feature 061 的核心性能指标。

**涉及文件**:
- `octoagent/packages/tooling/tests/test_performance_061.py`（新增）

**依赖**: T-024, T-040

**验收标准**:
- SC-001: Deferred 模式 token 占用减少 ≥60%（token 计数对比）
- SC-002: PresetBeforeHook 延迟 <1ms
- SC-004: tool_search 延迟 <10ms
- SC-006: bootstrap token ≤200

**预估复杂度**: M

---

### [x] T-046: 前端测试更新

**所属 User Story**: 全部
**描述**: 更新前端测试以覆盖 Feature 061 的 UI 变更。

**涉及文件**:
- `octoagent/frontend/src/pages/ControlPlane.test.tsx`（修改）
- `octoagent/frontend/src/pages/AgentCenter.test.tsx`（修改）

**依赖**: T-041, T-042

**验收标准**:
- Worker 创建 UI 测试包含 Preset 选择
- ControlPlane 测试包含 Preset 展示
- WorkerType 多模板相关测试移除或更新
- 审批 UI 测试包含 always 选项

**预估复杂度**: S

---

## 任务依赖关系图

```
Phase 1 (权限 Preset):
  T-001 ──┬──→ T-003 ──┐
           │            ├──→ T-005 ──→ T-006
  T-002 ──┼──→ T-004 ──┘
           │
  T-002a ←─┤
           │
  T-004a ←─┼── T-003, T-004
           │
  T-007 ──→ T-008 ──┐
                     ├──→ T-009 ──→ T-010
  T-005 ────────────┘

Phase 2 (二级审批):
  T-007 ──→ T-011 ──┐
  T-002 ──→ T-012 ──┼──→ T-013 ──→ T-013a
             T-012a ←┘    │
                          ├──→ T-014 ──→ T-015 ──→ T-017
                          │
  T-011 ─────────────────→ T-016

Phase 3 (Deferred Tools):
  T-001 ──→ T-018 ──→ T-018a
  T-001 ──→ T-019 ──→ T-019a
  T-019 ──→ T-020 ──→ T-020a
  T-018, T-002 ──→ T-021 ──→ T-022 ──→ T-022a
  T-021 ──→ T-023
  T-002 ──→ T-025 ──→ T-025a
  T-022, T-023 ──→ T-024

Phase 4 (Bootstrap 简化):
  T-009 ──→ T-028 ──→ T-029 ──→ T-030 ──→ T-031
  T-028 ──→ T-032
  T-031, T-032 ──→ T-033

Phase 5 (Skill-Tool 注入):
  T-034 ──→ T-034a
  T-022, T-025, T-034 ──→ T-035 ──→ T-036 ──→ T-037
  T-034 ──→ T-038
  T-037, T-038 ──→ T-039

Phase 6 (集成 + 前端):
  T-017, T-024, T-033 ──→ T-040
  T-030 ──→ T-041
  T-021 ──→ T-042
  (无前置) ──→ T-043
  T-001, T-005 ──→ T-044
  T-024, T-040 ──→ T-045
  T-041, T-042 ──→ T-046
```

---

## 关键路径

```
T-001 → T-003 → T-005 → T-009 → T-028 → T-029 → T-033 → T-040
  ↓
T-002 → T-004 → T-005
  ↓
T-007 → T-011 → T-013 → T-014 → T-017 → T-040
  ↓
T-018 → T-021 → T-022 → T-024 → T-040
```

最长路径约 12 个串行任务（~2-3 周，单人开发）。Phase 1 和 Phase 3 的早期任务可大量并行。
