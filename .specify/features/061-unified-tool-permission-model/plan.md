# 技术规划: 统一工具注入 + 权限 Preset 模型

**Feature ID**: 061
**Feature Branch**: `claude/festive-meitner`
**Status**: Plan
**Date**: 2026-03-17

---

## 1. 架构概览

### 1.1 变更前（当前架构）

```
WorkerType(GENERAL/OPS/RESEARCH/DEV)
    ├── default_tool_groups 矩阵（每类 Worker 看到的工具组不同）
    ├── WorkerCapabilityProfile（4 个实例）
    ├── ToolProfile(MINIMAL/STANDARD/PRIVILEGED)  ← 硬拒绝
    ├── bootstrap:shared + bootstrap:{type}（5 个模板文件）
    └── capability_pack._build_worker_profiles() 静态映射

工具上下文构建:
    所有可见工具完整 JSON Schema → 全量注入 LLM context（~10k-25k tokens）

权限检查:
    broker.execute() L272-283: profile_allows() → 硬拒绝 ToolResult(is_error=True)
    broker.execute() L286-309: FR-010a 无 PolicyCheckpoint → 硬拒绝 irreversible

审批:
    ApprovalManager._allow_always: dict[str, bool]（纯内存，全局共享，不持久化）
```

### 1.2 变更后（目标架构）

```
PermissionPreset(MINIMAL/NORMAL/FULL)  ← soft deny (ask)
    ├── 每个 Agent 实例独立配置（AgentRuntime.permission_preset）
    ├── Butler = FULL, Worker = NORMAL(默认), Subagent = 继承 Worker
    └── 工具侧：SideEffectLevel(NONE/REVERSIBLE/IRREVERSIBLE) 不变

统一工具集:
    所有 Agent 共享全量工具注册表（砍掉 WorkerType 多模板 + default_tool_groups 矩阵）

工具上下文构建（Deferred Tools 双层）:
    Core Tools（~10 个高频工具）→ 完整 JSON Schema 常驻
    Deferred Tools（其余全部）→ {name, one_line_desc} 列表 + tool_search 按需加载

权限检查（Hook Chain 驱动）:
    ApprovalOverrideHook(priority=10) → 查 always 覆盖表 → allow
    PresetBeforeHook(priority=20) → Preset × SideEffectLevel → allow 或 ask
    ask → raise ApprovalRequired → ApprovalManager → 用户 approve/always/deny

审批持久化:
    approval_overrides 表（SQLite）: (agent_runtime_id, tool_name, decision, created_at)
    Agent 实例级隔离，跨进程持久化

Bootstrap 简化:
    bootstrap:shared（~50 tokens 核心元信息）+ 角色卡片（~100-150 tokens 自定义描述）
    砍掉 bootstrap:general/ops/research/dev 4 个模板文件
```

### 1.3 架构变更图

```
变更前                               变更后
─────────────                        ─────────────
WorkerType(4种)                      PermissionPreset(3级)
  ├─ default_tool_groups ──→         (砍掉) 全量统一可见
  ├─ WorkerCapabilityProfile ──→     (砍掉) Preset + RoleCard 替代
  └─ bootstrap:shared + :type ──→    bootstrap:shared + role_card

ToolProfile(3级)                     PermissionPreset(3级)
  MINIMAL ─────→                     MINIMAL (none=allow, else=ask)
  STANDARD ────→                     NORMAL  (none+reversible=allow, irreversible=ask)
  PRIVILEGED ──→                     FULL    (all allow)

broker.execute() 硬拒绝 ──→          PresetBeforeHook(ask) + ApprovalOverrideHook
ApprovalManager 纯内存 ──→           approval_overrides SQLite 表

全量 schema 注入 ──→                  Core Tools schema + Deferred Tools 名称列表
```

---

## 2. 模块边界与职责划分

### 2.1 packages/tooling（核心变更层）

| 组件 | 变更类型 | 职责 |
|------|---------|------|
| `models.py` | 修改 | 新增 `PermissionPreset` 枚举、`ToolTier` 枚举；废弃 `ToolProfile`（保留短期兼容） |
| `broker.py` | 修改 | 移除 L272-283 硬编码 Profile 检查和 L286-309 FR-010a 强制拒绝；Hook Chain 接管 |
| `protocols.py` | 修改 | 更新 `BeforeHook` / `ToolBrokerProtocol` 中 Profile 相关类型引用 |
| `decorators.py` | 修改 | `@tool_contract` 新增可选 `tier: ToolTier` 参数 |
| `tool_index.py` | 修改 | 新增 `search_for_deferred()` facade 方法 |
| `hooks/preset_hook.py` | **新增** | `PresetBeforeHook` — Preset × SideEffectLevel 决策 |
| `hooks/approval_override_hook.py` | **新增** | `ApprovalOverrideHook` — always 覆盖查询 |

### 2.2 packages/policy（审批持久化）

| 组件 | 变更类型 | 职责 |
|------|---------|------|
| `approval_manager.py` | 修改 | `_allow_always` 从全局内存 dict 改为 Agent 实例级 + 委托持久化层 |
| `approval_override_store.py` | **新增** | `ApprovalOverrideRepository` 实现（SQLite `approval_overrides` 表） |
| `models.py` | 修改 | 新增 `ApprovalOverride` 模型 |

### 2.3 apps/gateway（上层适配）

| 组件 | 变更类型 | 职责 |
|------|---------|------|
| `capability_pack.py` | **重构** | 砍掉 `_build_worker_profiles()` + `_build_bootstrap_templates()` 中 4 个类型模板；统一工具集 + Deferred Tools 分区 |
| `agent_context.py` | 修改 | Bootstrap 组装从 WorkerType 模板改为 shared + role_card |
| `llm_service.py` | 修改 | 工具注入从全量 schema 改为 Core + DynamicToolset(deferred) |
| `tool_search_tool.py` | **新增** | `tool_search` 核心工具实现 |

### 2.4 packages/core（数据模型）

| 组件 | 变更类型 | 职责 |
|------|---------|------|
| `models/` | 修改 | `AgentRuntime` 新增 `permission_preset` 字段；`AgentSession` 新增 `role_card` 字段 |
| `migrations/` | **新增** | `approval_overrides` 表 DDL；`agent_runtimes` 新增列 |

---

## 3. 数据流设计

### 3.1 Deferred Tools 发现流程

```
Agent 对话启动
  │
  ├─① CapabilityPackService.build_tool_context()
  │    ├─ 从 ToolBroker 获取全量 ToolMeta 列表
  │    ├─ 按 ToolTier 分区: Core ToolMeta[] + Deferred ToolMeta[]
  │    ├─ Core Tools → 完整 JSON Schema → FunctionToolset（静态注入）
  │    └─ Deferred Tools → {name, description[:80]} 列表 → system prompt 注入
  │
  ├─② LLM 识别需要 Deferred 工具
  │    └─ 调用 tool_search(query="docker run container")
  │
  ├─③ tool_search 工具执行
  │    ├─ 调用 ToolIndex.select_tools(query)
  │    ├─ 返回匹配的 ToolMeta[]（含完整 schema）
  │    └─ 降级：ToolIndex 不可用 → 返回全部 Deferred 名称列表
  │
  ├─④ 运行时工具注入
  │    ├─ DynamicToolset 在下一个 run_step 前评估
  │    ├─ tool_search 结果中的工具 schema 注入活跃工具集
  │    └─ LLM 在后续步骤中可直接调用
  │
  └─⑤ 工具调用（Core 或 Deferred 均走同一路径）
       └─ ToolBroker.execute() → Hook Chain → 权限检查 → 执行
```

### 3.2 权限检查流程

```
ToolBroker.execute(tool_name, args, context)
  │
  ├─① 查找工具（不变）
  │
  ├─② Hook Chain 执行（按 priority 升序）
  │    │
  │    ├─ ApprovalOverrideHook(priority=10)
  │    │    ├─ 查询 ApprovalOverrideRepository: (agent_runtime_id, tool_name)
  │    │    ├─ 命中 always → BeforeHookResult(proceed=True)  ← 直接放行
  │    │    └─ 未命中 → BeforeHookResult(proceed=True)  ← 交给下一个 hook
  │    │
  │    ├─ PresetBeforeHook(priority=20)
  │    │    ├─ 读取 context.permission_preset（从 ExecutionContext 获取）
  │    │    ├─ 读取 tool_meta.side_effect_level
  │    │    ├─ 查询 PRESET_POLICY 矩阵:
  │    │    │    ├─ allow → BeforeHookResult(proceed=True)
  │    │    │    └─ ask → BeforeHookResult(proceed=False, rejection_reason="ask:preset_denied:...")
  │    │    └─ fail_mode=CLOSED（权限检查失败 = 拒绝执行）
  │    │
  │    └─ 其他 Hooks（日志、审计等，不变）
  │
  ├─③ ask 路径（上层捕获）
  │    ├─ ToolBroker 返回 ToolResult(is_error=True, error="ask:preset_denied:...")
  │    ├─ LLM 执行层识别 "ask:" 前缀
  │    ├─ 触发 ApprovalManager.register() → 创建审批请求
  │    ├─ SSE 推送 approval:requested → 前端/Telegram 展示审批 UI
  │    ├─ 等待用户决策:
  │    │    ├─ approve → 本次允许，重新执行工具
  │    │    ├─ always → 写入 approval_overrides 表 + 本次允许
  │    │    └─ deny → 返回拒绝信息给 LLM
  │    └─ 超时（600s）→ 默认 deny
  │
  └─④ 执行工具（proceed=True 路径，不变）
```

### 3.3 审批持久化流程

```
用户选择 always
  │
  ├─① ApprovalManager.resolve(decision=ALLOW_ALWAYS)
  │    ├─ 写入 APPROVAL_APPROVED 事件（Event Store）
  │    └─ 调用 ApprovalOverrideRepository.save_override()
  │
  ├─② ApprovalOverrideRepository.save_override()
  │    └─ INSERT INTO approval_overrides (agent_runtime_id, tool_name, decision, created_at)
  │
  ├─③ 后续同一 Agent 调用同一工具
  │    └─ ApprovalOverrideHook 查询 → 命中 always → 直接放行
  │
  └─④ 进程重启
       └─ ApprovalOverrideRepository.load_overrides(agent_runtime_id) → 恢复内存缓存
```

### 3.4 Skill-Tool 注入路径

```
Skill 加载
  │
  ├─① SkillDiscovery.get(skill_name) → SkillMdEntry
  │    └─ entry.tools_required: ["docker.run", "terminal.exec"]
  │
  ├─② DynamicToolset 评估（per_run_step）
  │    ├─ 检查 session.loaded_skill_names
  │    ├─ 收集所有 loaded skills 的 tools_required
  │    ├─ 将这些工具从 Deferred 提升到 Active
  │    └─ 生成 TOOL_PROMOTED 事件
  │
  ├─③ 工具调用（提升后的工具走正常路径）
  │    └─ Preset 权限检查不受影响 → 超出 Preset 仍触发 ask
  │
  └─④ Skill 卸载
       ├─ 重新评估 tools_required 引用计数
       └─ 无其他 Skill 引用的工具回退到 Deferred
```

---

## 4. 关键设计决策

### 4.1 Preset 作为 BeforeHook 而非内嵌 ToolBroker

**决策**: 将权限检查从 `broker.execute()` 内联逻辑移到 Hook Chain 中的 `PresetBeforeHook`。

**理由**:
1. ToolBroker 已有完整 Hook Chain 机制（FR-019/020），Preset 逻辑自然映射为 BeforeHook
2. ApprovalOverrideHook 可以作为更高优先级的 Hook，在 PresetBeforeHook 之前拦截
3. 符合 Constitution 原则 13A（优先提供上下文，而非堆积硬策略）
4. 与 Pydantic AI ApprovalRequired 天然对齐

**替代方案**: 方案 A（内嵌 ToolBroker）— 职责膨胀；方案 B（独立 PermissionService）— 过度抽象，当前规模不需要。

### 4.2 ask 信号通过 rejection_reason 前缀传递

**决策**: `PresetBeforeHook` 返回 `BeforeHookResult(proceed=False, rejection_reason="ask:preset_denied:{tool_name}:{side_effect_level}")`，上层通过 `ask:` 前缀识别为 soft deny。

**理由**:
1. 不修改 `BeforeHookResult` 数据结构（向后兼容）
2. ToolBroker 现有逻辑已处理 `proceed=False` → 返回 `ToolResult(is_error=True, error=reason)`
3. 上层（LLM 执行层）只需检查 error 是否以 `ask:` 开头即可区分 soft deny 和硬拒绝
4. 后续如需更丰富的信号，可扩展为结构化 `rejection_metadata` 字段

### 4.3 PermissionPreset 取代 ToolProfile

**决策**: `ToolProfile(MINIMAL/STANDARD/PRIVILEGED)` 演进为 `PermissionPreset(MINIMAL/NORMAL/FULL)`。迁移期间保留 ToolProfile 类型别名。

**理由**:
1. 语义更清晰：Profile 暗示"工具集过滤"，Preset 暗示"权限策略"
2. 映射关系清晰：`standard → normal`，`privileged → full`
3. 核心行为变化：从"硬拒绝不可见工具"到"统一可见 + 权限 ask"

**兼容策略**:
- `ToolProfile` 保留为 `PermissionPreset` 的别名（`ToolProfile = PermissionPreset`）
- `profile_allows()` 保留但标记废弃，内部委托到 `preset_decision()`
- `@tool_contract` 的 `tool_profile` 参数保留但标记废弃，推荐使用 `side_effect_level`（Preset 基于 side_effect_level 决策，不再基于 tool_profile）

### 4.4 Core Tools 选择策略

**决策**: Core Tools 初始清单（~10 个）基于高频使用场景确定，可配置。

**初始推荐清单**:
1. `tool_search` — 必须为 Core（保证 LLM 能搜索工具）
2. `project.inspect` — 项目元信息查询
3. `filesystem.list_dir` — 目录浏览
4. `filesystem.read_text` — 文件读取
5. `filesystem.write_text` — 文件写入
6. `terminal.exec` — 命令执行
7. `memory.recall` — 记忆检索
8. `memory.search` — 记忆搜索
9. `skills` — Skill 发现与加载
10. `subagents.spawn` — Subagent 创建

**理由**: 覆盖 Butler/Worker 最高频的 10 个操作场景，其余工具（browser.*、web.*、mcp.*、docker.* 等）通过 tool_search 按需加载。

### 4.5 always 授权持久化选择 SQLite 表

**决策**: CLR-001 确认，使用独立 SQLite 表 `approval_overrides` 而非 AgentSession.metadata 或 Event Store 回放。

**理由**:
1. 独立表结构最清晰，支持直接 SQL 查询（Web UI 展示）
2. 与 `agent_runtime_id` 直接关联，语义对齐 FR-011
3. 支持批量查询（"某 Agent 的所有 always 授权"）
4. 进程启动时批量加载到内存缓存，运行时 O(1) 查询

### 4.6 Deferred Tools 在框架层实现（非 API 层）

**决策**: 通过 Pydantic AI `DynamicToolset` 实现 Deferred Tools，不依赖 Claude API 原生 `defer_loading`。

**理由**:
1. 模型无关性：通过 LiteLLM Proxy 支持所有模型
2. Claude API `defer_loading` 可作为后续 Claude 专属加速优化
3. 框架层实现提供更精细的控制（Skill 工具提升、权限检查集成）

---

## 5. 实现阶段划分

### Phase 1: 权限 Preset 核心（P1，US1 + US5）

**目标**: Preset 枚举 + Hook Chain + 审批持久化

**变更文件**:
- `packages/tooling/src/octoagent/tooling/models.py` — PermissionPreset、ToolTier 枚举
- `packages/tooling/src/octoagent/tooling/broker.py` — 移除硬编码 Profile 检查
- `packages/tooling/src/octoagent/tooling/hooks/preset_hook.py` — 新增
- `packages/tooling/src/octoagent/tooling/hooks/approval_override_hook.py` — 新增
- `packages/policy/src/octoagent/policy/approval_override_store.py` — 新增
- `packages/policy/src/octoagent/policy/approval_manager.py` — 改造 always 持久化
- `packages/core/` — AgentRuntime 新增 permission_preset 字段 + migration

**验收**: US1 场景 1-10 + US5 场景 1-5 全部通过

### Phase 2: Deferred Tools 懒加载（P1，US2）

**目标**: Core/Deferred 分区 + tool_search 工具 + DynamicToolset 集成

**变更文件**:
- `packages/tooling/src/octoagent/tooling/decorators.py` — tier 参数
- `packages/tooling/src/octoagent/tooling/tool_index.py` — search_for_deferred()
- `apps/gateway/.../capability_pack.py` — build_tool_context() 分区逻辑
- `apps/gateway/.../tool_search_tool.py` — 新增 tool_search 核心工具
- `apps/gateway/.../llm_service.py` — CombinedToolset 集成

**验收**: US2 场景 1-6 全部通过 + SC-001 token 减少 ≥60%

### Phase 3: Bootstrap 简化（P2，US3）

**目标**: 砍掉 WorkerType 多模板，统一 shared + 角色卡片

**变更文件**:
- `apps/gateway/.../capability_pack.py` — 砍掉 _build_worker_profiles() + _build_bootstrap_templates() 中 4 个类型模板
- `apps/gateway/.../agent_context.py` — Bootstrap 组装改为 shared + role_card
- `packages/core/` — WorkerType 枚举标记废弃（短期保留兼容）

**验收**: US3 场景 1-4 + SC-006 + SC-007

### Phase 4: Skill-Tool 注入（P3，US4）

**目标**: Skill 加载时自动提升 tools_required 工具

**变更文件**:
- `apps/gateway/.../llm_service.py` — DynamicToolset 评估逻辑扩展
- `packages/skills/` — tools_required 解析 + 引用计数

**验收**: US4 场景 1-4

---

## 6. 迁移策略

### 6.1 ToolProfile → PermissionPreset 渐进迁移

```
Step 1: 新增 PermissionPreset 枚举（与 ToolProfile 值域不同）
Step 2: ToolProfile 保留为类型别名 + 标记 DeprecationWarning
Step 3: profile_allows() 保留但内部委托到 preset_decision()
Step 4: @tool_contract 的 tool_profile 参数标记废弃（Preset 不再基于此字段决策）
Step 5: ExecutionContext.profile → ExecutionContext.permission_preset
Step 6: 清理所有 ToolProfile 引用（后续 Feature）
```

### 6.2 WorkerType 多模板 → 统一工具集 + 角色卡片

```
Step 1: 保留 WorkerType 枚举（用于分类标签，不再作为工具过滤维度）
Step 2: _build_worker_profiles() 返回单一统一 profile（所有工具组）
Step 3: _build_bootstrap_templates() 仅保留 shared + 动态 role_card
Step 4: resolve_profile_first_tools() 不再按 default_tool_groups 过滤
Step 5: Worker 创建 API 新增 permission_preset 参数 + role_card 参数
Step 6: 前端 Worker 创建 UI 适配新参数
```

### 6.3 ApprovalManager always 持久化迁移

```
Step 1: 创建 approval_overrides SQLite 表
Step 2: ApprovalManager 注入 ApprovalOverrideRepository
Step 3: _allow_always 改为 agent_runtime_id 隔离的内存缓存
Step 4: resolve() ALLOW_ALWAYS 决策同时写入内存 + SQLite
Step 5: 进程启动 recover_from_store() 从 SQLite 恢复 always 缓存
Step 6: 旧的全局 _allow_always dict 移除
```

---

## 7. 风险与缓解

| # | 风险 | 概率 | 影响 | 缓解策略 |
|---|------|------|------|---------|
| R1 | LLM 不主动调用 tool_search，导致 Deferred 工具不可达 | 中 | 高 | Core Tools 覆盖 80% 场景；system prompt 明确引导"不确定时先搜索"；one_line_desc 提供足够线索；A/B 测试验证 |
| R2 | ask: 前缀的 soft deny 信号被上层误处理 | 低 | 高 | 在 LLM 执行层增加显式 ask 识别逻辑；单元测试覆盖所有 rejection_reason 模式 |
| R3 | Preset 迁移期间 ToolProfile 和 PermissionPreset 并存导致混淆 | 低 | 中 | 严格的类型别名 + 废弃警告；迁移文档明确映射关系 |
| R4 | Bootstrap 简化后 LLM 行为漂移 | 低 | 中 | 角色卡片保留核心引导；behavior pack 系统已承担详细行为规范；A/B 测试 |
| R5 | approval_overrides 表查询性能（高并发场景） | 低 | 低 | 进程启动时全量加载内存缓存；运行时 O(1) 查询；SQLite 仅做持久化写入 |
| R6 | DynamicToolset per_run_step 评估开销 | 低 | 低 | ToolIndex 内存索引查询 <1ms；schema 序列化可缓存 |
| R7 | Skill tools_required 引用不存在的工具 | 中 | 低 | 加载时记录警告事件；Skill 仍可加载（Constitution 原则 6: Degrade Gracefully） |

---

## 8. 可观测性设计

所有以下事件均写入 Event Store，可在 Web UI 事件流中查看（SC-009）：

| 事件类型 | 触发时机 | Payload 关键字段 |
|---------|---------|-----------------|
| `PRESET_CHECK` | PresetBeforeHook 执行后 | agent_runtime_id, tool_name, side_effect_level, preset, decision(allow/ask) |
| `APPROVAL_OVERRIDE_HIT` | ApprovalOverrideHook 命中 always | agent_runtime_id, tool_name |
| `TOOL_SEARCH_EXECUTED` | tool_search 工具执行后 | query, results_count, backend, is_fallback |
| `TOOL_PROMOTED` | Deferred 工具提升为 Active | tool_name, promoted_by(tool_search/skill), skill_name? |
| `TOOL_DEMOTED` | Active 工具回退为 Deferred | tool_name, reason(skill_unloaded) |
| `APPROVAL_REQUESTED` | soft deny 触发审批 | （现有，不变） |
| `APPROVAL_APPROVED/REJECTED` | 用户审批决策 | （现有，新增 decision=always 支持） |

---

## 9. 测试策略

### 9.1 单元测试

- `PresetBeforeHook`: 9 个 Preset × SideEffectLevel 组合的 allow/ask 决策
- `ApprovalOverrideHook`: always 命中/未命中/不同 agent_runtime_id 隔离
- `ApprovalOverrideRepository`: SQLite CRUD + 进程重启恢复
- `tool_search`: 正常检索 + ToolIndex 降级 + 空结果
- `ToolTier` 分区: Core/Deferred 分区逻辑
- `PermissionPreset` 映射: ToolProfile 兼容性

### 9.2 集成测试

- 完整权限检查链路: Agent 创建 → 工具调用 → Preset 检查 → ask → 审批 → 重新执行
- Deferred Tools 端到端: 对话启动 → 仅 Core 可见 → tool_search → Deferred 加载 → 调用
- Bootstrap 简化: Worker 创建 → shared + role_card 注入 → LLM 行为验证
- Skill-Tool 注入: Skill 加载 → tools_required 提升 → Preset 检查 → 调用
- always 持久化: approve always → 进程重启 → 再次调用 → 直接放行

### 9.3 性能测试

- SC-001: token 计数对比（全量 vs Deferred），验证 ≥60% 减少
- SC-002: PresetBeforeHook 延迟 <1ms
- SC-004: tool_search 延迟 <10ms
