# 数据模型设计: Feature 061

**Feature ID**: 061
**Date**: 2026-03-17

---

## 1. 新增枚举

### 1.1 PermissionPreset

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class PermissionPreset(StrEnum):
    """Agent 实例级权限 Preset — 取代 ToolProfile

    决定工具调用时的默认 allow/ask 策略。
    基于工具的 SideEffectLevel 做出决策，不再基于工具的 ToolProfile。

    映射关系（从 ToolProfile 迁移）:
    - MINIMAL ← ToolProfile.MINIMAL
    - NORMAL ← ToolProfile.STANDARD
    - FULL ← ToolProfile.PRIVILEGED
    """
    MINIMAL = "minimal"     # 保守：仅 none=allow，其余 ask
    NORMAL = "normal"       # 标准：none+reversible=allow，irreversible=ask
    FULL = "full"           # 完全：所有 allow
```

### 1.2 PresetDecision

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class PresetDecision(StrEnum):
    """Preset 检查决策结果

    注意：没有 DENY — 所有 Preset 不允许的操作都走 ASK（soft deny），
    用户可通过审批临时提升权限（Constitution 原则 7: User-in-Control）。
    """
    ALLOW = "allow"     # 直接放行
    ASK = "ask"         # 触发审批请求（soft deny）
```

### 1.3 PRESET_POLICY 矩阵

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
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

### 1.4 ToolTier

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class ToolTier(StrEnum):
    """工具层级标记 — 决定初始 context 中的呈现方式

    CORE: 完整 JSON Schema 始终加载到 LLM context（~10 个高频工具）
    DEFERRED: 仅暴露 {name, one_line_desc} 列表，通过 tool_search 按需加载
    """
    CORE = "core"
    DEFERRED = "deferred"
```

---

## 2. 新增数据模型

### 2.1 ApprovalOverride

**位置**: `packages/policy/src/octoagent/policy/models.py`

```python
class ApprovalOverride(BaseModel):
    """用户审批覆盖记录 — always 授权的持久化表示

    绑定到 agent_runtime_id（Agent 实例级隔离）。
    跨进程重启后通过 SQLite 恢复。

    对齐 CLR-001 决策: 方案 A，SQLite 表 approval_overrides。
    对齐 CLR-002: always 授权绑定到 agent_runtime_id，不同 Agent 实例互相独立。
    """
    id: int | None = Field(default=None, description="自增主键（SQLite 自动生成）")
    agent_runtime_id: str = Field(description="Agent 实例 ID（如 Butler、Worker）")
    tool_name: str = Field(description="工具名称（如 docker.run）")
    decision: str = Field(default="always", description="授权决策（当前仅 always）")
    created_at: str = Field(description="创建时间 ISO 格式")
```

### 2.2 DeferredToolEntry

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class DeferredToolEntry(BaseModel):
    """Deferred 工具的精简表示 — 用于 system prompt 注入

    仅包含名称和单行描述，不包含完整 schema。
    LLM 通过 tool_search 获取完整信息。
    """
    name: str = Field(description="工具名称")
    one_line_desc: str = Field(description="单行描述（≤80 字符）")
```

### 2.3 CoreToolSet

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class CoreToolSet(BaseModel):
    """Core Tools 配置 — 定义始终加载完整 schema 的工具清单

    Core Tools 清单可通过配置文件或 Event Store 使用频率统计确定。
    至少必须包含 tool_search 自身（FR-018）。
    """
    tool_names: list[str] = Field(
        description="Core 工具名称列表",
        min_length=1,
    )

    # 默认初始清单
    @classmethod
    def default(cls) -> CoreToolSet:
        return cls(tool_names=[
            "tool_search",
            "project.inspect",
            "filesystem.list_dir",
            "filesystem.read_text",
            "filesystem.write_text",
            "terminal.exec",
            "memory.recall",
            "memory.search",
            "skills",
            "subagents.spawn",
        ])
```

### 2.4 ToolSearchResult

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class ToolSearchResult(BaseModel):
    """tool_search 工具的返回结果"""
    query: str = Field(description="原始查询")
    results: list[ToolSearchHit] = Field(default_factory=list, description="匹配结果")
    is_fallback: bool = Field(default=False, description="是否为降级模式（全量返回）")
    backend: str = Field(default="", description="使用的检索后端")

class ToolSearchHit(BaseModel):
    """单个工具搜索命中"""
    tool_name: str = Field(description="工具名称")
    description: str = Field(description="工具描述")
    parameters_schema: dict[str, Any] = Field(description="完整参数 JSON Schema")
    score: float = Field(default=0.0, description="匹配得分")
    side_effect_level: str = Field(default="", description="副作用等级")
    tool_group: str = Field(default="", description="工具分组")
```

---

## 3. 对现有模型的变更

### 3.1 ToolProfile 废弃策略

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
# 废弃注释
# ToolProfile 已被 PermissionPreset 取代。
# 保留此类型仅供迁移期兼容，后续版本将删除。
class ToolProfile(StrEnum):
    """[DEPRECATED] 使用 PermissionPreset 替代"""
    MINIMAL = "minimal"
    STANDARD = "standard"
    PRIVILEGED = "privileged"

# 兼容映射
TOOL_PROFILE_TO_PRESET: dict[ToolProfile, PermissionPreset] = {
    ToolProfile.MINIMAL: PermissionPreset.MINIMAL,
    ToolProfile.STANDARD: PermissionPreset.NORMAL,
    ToolProfile.PRIVILEGED: PermissionPreset.FULL,
}
```

### 3.2 ToolMeta 扩展

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class ToolMeta(BaseModel):
    # ... 现有字段不变 ...

    # 新增字段
    tier: ToolTier = Field(
        default=ToolTier.DEFERRED,
        description="工具层级: CORE（始终加载 schema）或 DEFERRED（按需加载）",
    )
```

### 3.3 ExecutionContext 扩展

**位置**: `packages/tooling/src/octoagent/tooling/models.py`

```python
class ExecutionContext(BaseModel):
    # ... 现有字段 ...
    profile: ToolProfile = Field(
        default=ToolProfile.MINIMAL,
        description="[DEPRECATED] 使用 permission_preset 替代",
    )
    # 新增
    permission_preset: PermissionPreset = Field(
        default=PermissionPreset.MINIMAL,
        description="当前 Agent 的权限 Preset（决定工具调用的 allow/ask 策略）",
    )
```

### 3.4 AgentRuntime 扩展

**位置**: `packages/core/src/octoagent/core/models/` (具体文件视模型定义位置)

```python
class AgentRuntime:
    # ... 现有字段 ...
    permission_preset: str = Field(
        default="normal",
        description="权限 Preset（minimal/normal/full）",
    )
    role_card: str = Field(
        default="",
        description="Agent 角色卡片文本（替代 WorkerType 多模板的角色引导）",
    )
```

### 3.5 AgentSession 扩展

**位置**: `packages/core/src/octoagent/core/models/`

AgentSession.metadata 新增约定字段:
```python
# metadata 字典中的约定 key:
# "permission_preset": str  — 从 AgentRuntime 继承
# "role_card": str           — 从 AgentRuntime 继承
# "promoted_tools": list[str] — 当前 session 中被提升的 Deferred 工具名称
# "tool_promotion_sources": dict[str, list[str]] — 工具提升来源引用计数
```

---

## 4. 数据库变更

### 4.1 新增表: approval_overrides

```sql
-- Feature 061: 审批覆盖持久化表
-- 存储用户 "always" 授权决策，绑定到 Agent 实例
CREATE TABLE IF NOT EXISTS approval_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_runtime_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'always',
    created_at TEXT NOT NULL,
    -- Agent 实例 + 工具名唯一约束（同一 Agent 对同一工具最多一条 always 记录）
    UNIQUE(agent_runtime_id, tool_name)
);

-- 按 Agent 实例查询索引
CREATE INDEX IF NOT EXISTS idx_overrides_agent
    ON approval_overrides(agent_runtime_id);

-- 按工具名查询索引（管理界面用）
CREATE INDEX IF NOT EXISTS idx_overrides_tool
    ON approval_overrides(tool_name);
```

### 4.2 现有表变更: agent_runtimes

```sql
-- 新增列: permission_preset（默认 normal）
ALTER TABLE agent_runtimes ADD COLUMN permission_preset TEXT NOT NULL DEFAULT 'normal';

-- 新增列: role_card（默认空字符串）
ALTER TABLE agent_runtimes ADD COLUMN role_card TEXT NOT NULL DEFAULT '';
```

---

## 5. 废弃清单

以下模型/字段将在 Feature 061 完成后标记废弃（保留短期兼容），后续 Feature 清理:

| 位置 | 名称 | 替代 | 清理时间 |
|------|------|------|---------|
| `tooling/models.py` | `ToolProfile` 枚举 | `PermissionPreset` | 下一个 Feature |
| `tooling/models.py` | `profile_allows()` | `preset_decision()` (PRESET_POLICY 查表) | 下一个 Feature |
| `tooling/models.py` | `PROFILE_LEVELS` | `PRESET_POLICY` | 下一个 Feature |
| `tooling/models.py` | `ExecutionContext.profile` | `ExecutionContext.permission_preset` | 下一个 Feature |
| `tooling/decorators.py` | `tool_contract(tool_profile=)` | 仅保留 side_effect_level（Preset 基于此决策） | 下一个 Feature |
| `core/models/` | `WorkerType` 枚举 | 保留为分类标签，不再作为工具过滤维度 | 长期保留但语义变化 |
| `core/models/` | `WorkerCapabilityProfile` | 砍掉，由 PermissionPreset + RoleCard 替代 | 下一个 Feature |
| `gateway/capability_pack.py` | `_build_worker_profiles()` | 统一工具集 + Preset | Feature 061 内砍掉 |
| `gateway/capability_pack.py` | `bootstrap:general/ops/research/dev` | `bootstrap:shared` + 动态角色卡片 | Feature 061 内砍掉 |

---

## 6. 事件 Payload 新增

### 6.1 PRESET_CHECK 事件

```python
class PresetCheckEventPayload(BaseModel):
    """Preset 权限检查事件 payload"""
    agent_runtime_id: str
    agent_session_id: str
    tool_name: str
    side_effect_level: str  # none/reversible/irreversible
    permission_preset: str  # minimal/normal/full
    decision: str           # allow/ask
    override_hit: bool = False  # 是否命中 always 覆盖
```

### 6.2 TOOL_SEARCH_EXECUTED 事件

```python
class ToolSearchExecutedPayload(BaseModel):
    """tool_search 工具执行事件 payload"""
    query: str
    results_count: int
    result_names: list[str]
    backend: str           # in_memory/lancedb
    is_fallback: bool
    latency_ms: int
```

### 6.3 TOOL_PROMOTED / TOOL_DEMOTED 事件

```python
class ToolPromotionPayload(BaseModel):
    """工具层级变更事件 payload"""
    tool_name: str
    direction: str        # promoted/demoted
    source: str           # tool_search/skill
    source_id: str = ""   # skill_name 或 search query_id
    agent_runtime_id: str
    agent_session_id: str
```
