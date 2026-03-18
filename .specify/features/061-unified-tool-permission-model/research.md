# 技术方案详细研究: Feature 061

**Feature ID**: 061
**Date**: 2026-03-17

---

## FR-001/FR-002: 统一工具可见性 + 权限 Preset 隔离

### 当前实现分析

**WorkerType 多模板系统**:
- `capability_pack.py:_build_worker_profiles()` 为 4 种 WorkerType 各维护独立的 `default_tool_groups` 列表
- GENERAL: 12 个 tool_group（project, artifact, document, session, filesystem, terminal, network, browser, memory, supervision, delegation, mcp, skills）
- OPS: 9 个（runtime, session, project, filesystem, terminal, automation, delegation, mcp, skills）
- RESEARCH: 11 个（project, artifact, session, filesystem, network, browser, memory, document, media, mcp, skills）
- DEV: 12 个（project, artifact, session, filesystem, terminal, delegation, runtime, browser, document, media, mcp, skills）

**问题**: 工具可见性按 WorkerType 硬编码，新增工具需要手动更新多个列表。4 种类型的工具组高度重叠（至少 7 个共有组），维护成本与收益不成正比。

**ToolProfile 三级硬拒绝**:
- `broker.py:L272-283`: `profile_allows(meta.tool_profile, context.profile)` 返回 False 时直接返回 `ToolResult(is_error=True)`
- 这是硬拒绝——LLM 收到的是"权限不足"错误，无法请求用户授权
- 与 Constitution 原则 7（User-in-Control）冲突

### 实现方案

**Step 1: PermissionPreset 枚举**

在 `packages/tooling/src/octoagent/tooling/models.py` 新增:

```python
class PermissionPreset(StrEnum):
    MINIMAL = "minimal"   # 最保守
    NORMAL = "normal"     # 标准（原 standard）
    FULL = "full"         # 完全（原 privileged）

class PresetDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"

# Preset × SideEffectLevel → Decision 矩阵
PRESET_POLICY: dict[PermissionPreset, dict[SideEffectLevel, PresetDecision]] = {
    PermissionPreset.MINIMAL: {
        SideEffectLevel.NONE: PresetDecision.ALLOW,
        SideEffectLevel.REVERSIBLE: PresetDecision.ASK,
        SideEffectLevel.IRREVERSIBLE: PresetDecision.ASK,
    },
    PermissionPreset.NORMAL: {
        SideEffectLevel.NONE: PresetDecision.ALLOW,
        SideEffectLevel.REVERSIBLE: PresetDecision.ALLOW,
        SideEffectLevel.IRREVERSIBLE: PresetDecision.ASK,
    },
    PermissionPreset.FULL: {
        SideEffectLevel.NONE: PresetDecision.ALLOW,
        SideEffectLevel.REVERSIBLE: PresetDecision.ALLOW,
        SideEffectLevel.IRREVERSIBLE: PresetDecision.ALLOW,
    },
}
```

**Step 2: PresetBeforeHook**

作为 `BeforeHook` 实现，注册到 ToolBroker Hook Chain，`priority=20`，`fail_mode=CLOSED`:

```python
class PresetBeforeHook:
    name = "preset_check"
    priority = 20
    fail_mode = FailMode.CLOSED

    async def before_execute(self, tool_meta, args, context):
        preset = context.permission_preset  # 新增字段
        decision = PRESET_POLICY[preset][tool_meta.side_effect_level]
        if decision == PresetDecision.ALLOW:
            return BeforeHookResult(proceed=True)
        # ask → soft deny
        return BeforeHookResult(
            proceed=False,
            rejection_reason=f"ask:preset_denied:{tool_meta.name}:{tool_meta.side_effect_level.value}",
        )
```

**Step 3: 移除 broker.py 硬编码检查**

- 删除 L272-283 的 `profile_allows()` 检查
- 删除 L286-309 的 FR-010a `_has_policy_checkpoint()` 强制拒绝
- 这些安全保障由 `PresetBeforeHook` + Hook Chain 机制统一接管
- `_has_policy_checkpoint()` 原有的"必须有 fail_mode=CLOSED hook"语义，由 PresetBeforeHook（fail_mode=CLOSED）自动满足

**Step 4: ExecutionContext 扩展**

```python
class ExecutionContext(BaseModel):
    # ... 现有字段 ...
    permission_preset: PermissionPreset = Field(
        default=PermissionPreset.MINIMAL,
        description="当前 Agent 的权限 Preset",
    )
    # profile 字段保留但标记废弃
```

---

## FR-003/004/005/006: Agent 实例级 Preset 配置

### 实现方案

**AgentRuntime 模型扩展**:

`packages/core/` 的 `AgentRuntime` 模型新增:
```python
class AgentRuntime:
    # ... 现有字段 ...
    permission_preset: str = "normal"  # minimal/normal/full
```

**Preset 分配规则**:
- Butler 创建: `permission_preset = "full"`（AgentRuntimeRole.BUTLER）
- Worker 创建: `permission_preset = kwargs.get("preset", "normal")`
- Subagent 创建: 从 parent Worker 的 AgentSession 读取 `permission_preset`

**传递链路**:
```
AgentRuntime.permission_preset
  → AgentSession.metadata["permission_preset"]
  → LLM 执行层构造 ExecutionContext 时读取
  → ExecutionContext.permission_preset
  → PresetBeforeHook 读取
```

---

## FR-009/010/011/012/013/014: 二级审批机制

### 当前 ApprovalManager 分析

- `_allow_always: dict[str, bool]` — 全局共享，非 Agent 实例隔离
- 内存态，进程重启丢失
- `recover_from_store()` 只恢复 pending 审批，不恢复 always 白名单（仅通过事件回放恢复）
- `resolve()` 中 `ALLOW_ALWAYS` 只写内存 dict

### 实现方案

**Step 1: ApprovalOverrideRepository（新增）**

独立 SQLite 存储层，表结构:
```sql
CREATE TABLE approval_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_runtime_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'always',
    created_at TEXT NOT NULL,
    UNIQUE(agent_runtime_id, tool_name)
);
CREATE INDEX idx_overrides_agent ON approval_overrides(agent_runtime_id);
```

接口:
```python
class ApprovalOverrideRepository:
    async def save_override(self, agent_runtime_id: str, tool_name: str) -> None
    async def remove_override(self, agent_runtime_id: str, tool_name: str) -> None
    async def has_override(self, agent_runtime_id: str, tool_name: str) -> bool
    async def load_overrides(self, agent_runtime_id: str) -> list[ApprovalOverride]
    async def list_all_overrides(self) -> list[ApprovalOverride]
```

**Step 2: ApprovalOverrideHook（新增）**

优先级高于 PresetBeforeHook（priority=10），在 Preset 检查前拦截:
```python
class ApprovalOverrideHook:
    name = "approval_override"
    priority = 10
    fail_mode = FailMode.OPEN  # 查询失败不应阻止工具执行

    def __init__(self, override_repo: ApprovalOverrideRepository):
        self._repo = override_repo
        self._cache: dict[tuple[str, str], bool] = {}

    async def before_execute(self, tool_meta, args, context):
        key = (context.agent_runtime_id, tool_meta.name)
        if self._cache.get(key, False):
            return BeforeHookResult(proceed=True)
        # 未缓存命中，交给下一个 hook
        return BeforeHookResult(proceed=True)
```

**Step 3: ApprovalManager 改造**

```python
class ApprovalManager:
    def __init__(self, ..., override_repo: ApprovalOverrideRepository | None = None):
        self._override_repo = override_repo
        # _allow_always 改为 (agent_runtime_id, tool_name) → True
        self._allow_always: dict[tuple[str, str], bool] = {}

    async def resolve(self, approval_id, decision, resolved_by, *, agent_runtime_id: str = ""):
        # ... 现有逻辑 ...
        if decision == ApprovalDecision.ALLOW_ALWAYS:
            key = (agent_runtime_id, tool_name)
            self._allow_always[key] = True
            if self._override_repo:
                await self._override_repo.save_override(agent_runtime_id, tool_name)
```

**Step 4: 审批流与 ask 信号的桥接**

LLM 执行层（`llm_service.py` / Pydantic AI 集成层）识别 `ask:` 前缀:

```python
# 在工具调用结果处理中
if result.is_error and result.error and result.error.startswith("ask:"):
    # 解析 ask 信号
    parts = result.error.split(":")
    # 触发审批流
    approval_request = ApprovalRequest(
        approval_id=str(ULID()),
        task_id=context.task_id,
        tool_name=parts[2] if len(parts) > 2 else tool_name,
        side_effect_level=SideEffectLevel(parts[3]) if len(parts) > 3 else SideEffectLevel.IRREVERSIBLE,
        ...
    )
    record = await approval_manager.register(approval_request)
    decision = await approval_manager.wait_for_decision(record.approval_id)
    if decision in (ApprovalDecision.ALLOW_ONCE, ApprovalDecision.ALLOW_ALWAYS):
        # 重新执行工具
        ...
```

---

## FR-015/016/017/018/019/020/021/022/023: Deferred Tools 懒加载

### Context 占用估算

当前 49 个工具的 JSON Schema 估算:
- 每个工具 schema 约 200-500 tokens
- 总计约 10k-25k tokens

Deferred 模式估算:
- Core Tools（10 个）完整 schema: ~2k-5k tokens
- Deferred Tools 名称列表（~39 个 × ~15 tokens/条 = ~585 tokens）
- tool_search 工具自身 schema: ~200 tokens
- 总计: ~3k-6k tokens
- **节省: 60-75%**

### 实现方案

**Step 1: ToolTier 枚举**

```python
class ToolTier(StrEnum):
    CORE = "core"          # 始终加载完整 schema
    DEFERRED = "deferred"  # 仅名称+描述，按需加载
```

`@tool_contract` 新增:
```python
def tool_contract(
    *,
    side_effect_level: SideEffectLevel,
    tool_profile: ToolProfile,
    tool_group: str,
    tier: ToolTier = ToolTier.DEFERRED,  # 新增，默认 Deferred
    ...
)
```

**Step 2: tool_search 核心工具**

注册为 `@tool_contract(tier=ToolTier.CORE, side_effect_level=SideEffectLevel.NONE)`:

```python
@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_profile=ToolProfile.MINIMAL,
    tool_group="system",
    tier=ToolTier.CORE,
    tags=["search", "discovery", "deferred"],
)
async def tool_search(query: str, limit: int = 5) -> str:
    """按自然语言查询搜索可用工具，返回匹配工具的完整定义。

    Args:
        query: 自然语言查询（如"执行 docker 命令"、"发送邮件"）
        limit: 返回结果数量上限（默认 5）
    """
    # 调用 ToolIndex.select_tools()
    # 返回匹配工具的 {name, description, parameters_schema} 列表
    # 降级：ToolIndex 不可用 → 返回全部 Deferred 名称列表
```

**Step 3: DynamicToolset 集成**

在 Pydantic AI Agent 构造时:

```python
# Core Tools: 静态 FunctionToolset
core_toolset = FunctionToolset(core_tool_handlers)

# Deferred Tools: DynamicToolset（per_run_step 评估）
class DeferredToolResolver:
    def __init__(self):
        self._promoted_tools: dict[str, ToolHandler] = {}

    async def resolve(self, run_context) -> list[Tool]:
        # 返回当前已提升的 Deferred 工具列表
        return [Tool(name=name, handler=handler) for name, handler in self._promoted_tools.items()]

    def promote(self, tool_name: str, handler: ToolHandler):
        self._promoted_tools[tool_name] = handler

deferred_toolset = DynamicToolset(resolver, per_run_step=True)

# 组合
agent = Agent(
    toolset=CombinedToolset(core_toolset, deferred_toolset),
    ...
)
```

**Step 4: tool_search 结果注入**

`tool_search` 工具执行后，将结果中的工具 schema 注入到 DeferredToolResolver:

```python
async def tool_search(query: str, limit: int = 5) -> str:
    selection = await tool_index.select_tools(
        ToolIndexQuery(query=query, limit=limit)
    )
    for hit in selection.hits:
        meta, handler = broker._registry.get(hit.tool_name, (None, None))
        if meta and handler:
            deferred_resolver.promote(hit.tool_name, handler)
    # 返回工具描述给 LLM
    return format_tool_descriptions(selection.hits)
```

**Step 5: Deferred Tools 名称列表注入 system prompt**

在 bootstrap 或 system prompt 中注入:

```
## Available Tools (Deferred)

以下工具可通过 tool_search 搜索后使用：
- docker.run: 在 Docker 容器中执行命令
- web.search: 搜索互联网
- web.fetch: 获取网页内容
- browser.navigate: 浏览网页
- ...（共 39 个）

如需使用以上工具，请先调用 tool_search 进行搜索。
```

---

## FR-024/025/026/027/028: Bootstrap 模板最小化

### 当前模板 token 消耗分析

| 模板 | 字符数 | 估算 tokens |
|------|--------|------------|
| bootstrap:shared | ~500 字符 | ~180 tokens |
| bootstrap:general | ~700 字符 | ~250 tokens |
| bootstrap:ops | ~250 字符 | ~90 tokens |
| bootstrap:research | ~340 字符 | ~120 tokens |
| bootstrap:dev | ~180 字符 | ~65 tokens |
| **每个 Agent 合计** | **shared + 1** | **~245-430 tokens** |

### 目标状态

| 模板 | 字符数 | 估算 tokens |
|------|--------|------------|
| bootstrap:shared（精简） | ~120 字符 | ~50 tokens |
| 角色卡片 | ~300 字符 | ~100-150 tokens |
| **每个 Agent 合计** | | **~150-200 tokens** |

### 实现方案

**新 bootstrap:shared 内容**:
```
OctoAgent runtime。
Project: {{project_name}} ({{project_slug}})
Workspace: {{workspace_slug}}
Datetime: {{current_datetime_local}} {{current_weekday_local}}
Timezone: {{owner_timezone}}
Preset: {{permission_preset}}
```

**角色卡片（动态生成或用户自定义）**:

Butler 默认:
```
你是 OctoAgent 的主 Butler（管家），负责对话管理、任务拆分与 Worker 协调。
优先自己完成有界任务，只在需要并行或专业化分工时委派给 Worker。
```

Worker 默认模板:
```
你是 OctoAgent 的 Worker，执行 Butler 委派的任务。
角色定位: {{role_description}}
完成后用自然语言给出最终答复。
```

**关键变化**:
- 移除 `Worker Type: {{worker_type}}`、`Capabilities: {{worker_capabilities}}`、`Default Tool Groups: {{default_tool_groups}}`、`Runtime Kinds: {{runtime_kinds}}` 等在 Preset + 统一工具集下冗余的字段
- 移除 `必须继续走 ToolBroker / Policy / audit，不得绕过治理面。` — 这类治理指令由 behavior pack 系统承担
- 移除 `Ambient Degraded Reasons` — 降级状态由运行时 hints 注入

---

## FR-029/030/031/032: Skill-Tool 注入路径

### 当前 Skill 系统分析

`SkillMdEntry` 已支持 `tools_required: list[str]` 字段（Feature 057）。当前 Skill 加载时仅注入 system prompt 内容，不处理 tools_required。

### 实现方案

**Step 1: Skill 加载时收集 tools_required**

在 `DynamicToolset` 的 per_run_step 评估中:

```python
async def resolve(self, run_context):
    # 1. 收集已加载 Skill 的 tools_required
    loaded_skills = run_context.session.metadata.get("loaded_skill_names", [])
    skill_required_tools = set()
    for skill_name in loaded_skills:
        entry = skill_discovery.get(skill_name)
        if entry and entry.tools_required:
            skill_required_tools.update(entry.tools_required)

    # 2. 将 Skill 依赖的工具提升为 Active
    for tool_name in skill_required_tools:
        if tool_name not in self._promoted_tools:
            meta, handler = broker._registry.get(tool_name, (None, None))
            if meta and handler:
                self._promoted_tools[tool_name] = handler
                # 生成 TOOL_PROMOTED 事件
                ...

    # 3. tool_search 结果的工具也保持提升
    return [Tool(name=name, handler=handler) for name, handler in self._promoted_tools.items()]
```

**Step 2: 引用计数管理**

维护 `tool_name → set[source]` 的引用计数:

```python
# _tool_promotion_sources: dict[str, set[str]]
# 例: {"docker.run": {"skill:coding-agent", "tool_search:query_abc"}}

# Skill 卸载时
for tool_name in unloaded_skill.tools_required:
    sources = self._tool_promotion_sources.get(tool_name, set())
    sources.discard(f"skill:{unloaded_skill.name}")
    if not sources:
        # 无其他来源引用，回退到 Deferred
        del self._promoted_tools[tool_name]
```

---

## FR-033/034/035/036/037: 可观测性

### 事件生成点

1. **PresetBeforeHook**: 每次执行生成 `PRESET_CHECK` 事件
   - 不论 allow 还是 ask 都记录
   - Payload: `{agent_runtime_id, tool_name, side_effect_level, preset, decision}`

2. **tool_search**: 每次调用生成 `TOOL_SEARCH_EXECUTED` 事件
   - Payload: `{query, results_count, result_names, backend, is_fallback, latency_ms}`

3. **ApprovalManager.resolve()**: 现有事件（APPROVAL_APPROVED/REJECTED），扩展 payload 支持 `decision=always`

4. **DeferredToolResolver.promote()**: 工具提升生成 `TOOL_PROMOTED` 事件
   - Payload: `{tool_name, promoted_by, source(tool_search|skill), skill_name?}`

5. **ToolIndex 降级**: 生成 `TOOL_INDEX_DEGRADED` 事件
   - Payload: `{reason, fallback_mode}`

---

## FR-038/039/040: 兼容性与迁移

### ToolProfile → PermissionPreset 映射

| ToolProfile | PermissionPreset | 行为变化 |
|-------------|-----------------|---------|
| MINIMAL | MINIMAL | 语义一致，但从"硬拒绝不可见工具"变为"可见但 ask" |
| STANDARD | NORMAL | 同上 |
| PRIVILEGED | FULL | 同上 |

### @tool_contract 兼容性

`@tool_contract` 的 `tool_profile` 参数**保留但语义变化**:
- 旧语义: 决定工具在哪些 Profile 中可见
- 新语义: 仅作为参考标签，实际权限决策由 Preset × SideEffectLevel 驱动
- 过渡期: `tool_profile` 参数不再影响 Preset 决策，但保留用于 ToolIndex 检索的元数据

### schema 反射保证

所有通过 `tool_search` 加载的 Deferred 工具:
- schema 仍由 `reflect_tool_schema()` 从函数签名生成（不变）
- 已注册到 ToolBroker，schema 存在于 ToolMeta 中
- `tool_search` 返回的是 ToolBroker 注册表中的 ToolMeta 原始 schema，不是重新生成的
- Constitution 原则 3（Tools are Contracts）完全满足
