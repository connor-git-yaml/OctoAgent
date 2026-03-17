---
feature_id: "062"
title: "Adaptive Loop Guard & Resource Limits"
milestone: "M4"
status: "Draft"
created: "2026-03-17"
updated: "2026-03-17"
research_mode: "cross-product-benchmark"
blueprint_ref: "docs/blueprint.md §14 Constitution #6 Degrade Gracefully；§8.7 Skill Pipeline"
predecessor: "Feature 058（MCP Install Lifecycle）、Feature 060（Context Engineering Enhancement）"
research_ref: "062-adaptive-loop-guard-resource-limits/research.md"
---

# Feature Specification: Adaptive Loop Guard & Resource Limits

**Feature Branch**: `feat/062-adaptive-loop-guard-resource-limits`
**Created**: 2026-03-17
**Updated**: 2026-03-17
**Status**: Draft
**Input**: 跨产品对比调研（Claude Code SDK / Pydantic AI / Agent Zero / OpenClaw），结合 OctoAgent 在生产环境中暴露的 Echo fallback、步数上限不合理等问题，系统性升级 SkillRunner 的资源限制与循环保护机制。

---

## Problem Statement

### 直接触发事件

1. **Echo Fallback 事件**：用户正常请求（如"用 MCP search 搜索一下"）被 SkillRunner 截断（原 `max_steps=8`），触发 SKILL_FAILED → Echo 降级，返回 "Echo: {原文}"。已临时修复为 `max_steps=30` + 友好错误提示，但根本问题未解决
2. **MCP 工具发现失败**：安装 MCP 后 Agent 仍使用内置工具，因工具解析需要额外步骤，在低步数限制下更容易触及上限

### 结构性问题

| # | 问题 | 影响 |
|---|------|------|
| 1 | **单维度限制**：只有 `max_steps` 一个硬限制 | 无法区分"步数多但 token 少"（正常）vs"步数少但 token 爆"（异常） |
| 2 | **全局统一阈值**：所有 Agent/Skill 共用 `max_steps=30` | 简单问答 5 步够了，复杂编码任务 30 步可能不够 |
| 3 | **无成本防护**：缺少美元级预算限制 | Worker 长任务可能无限消耗 token |
| 4 | **无自定义停止条件**：不能根据输出内容灵活决定是否继续 | 主 Agent 监督无法干预 Worker 执行 |
| 5 | **降级链不够智能**：SKILL_FAILED 后只能返回错误提示 | 无法尝试降级模型/缩减 scope 重试 |
| 6 | **阈值不可运行时调整**：参数写死代码默认值 | 调整需改代码重启 |
| 7 | **SkillOutputEnvelope 不携带 token/cost 数据**：`generate()` 返回的 `SkillOutputEnvelope.metadata` 在 SSE 流式路径下为空 dict，Responses API 路径中 `cost_usd` 硬编码为 `0.0` | 任何 token/cost 维度的限制都无法工作 |

---

## User Stories

- **US-1** 作为 Agent 使用者，我希望复杂任务（如编码、调研）不被过早截断，同时简单问答也不会浪费太多资源
- **US-2** 作为系统管理员，我希望为 Worker 设置预算上限，避免长任务无限消耗 token 导致成本失控
- **US-3** 作为 Agent 运维者，我希望不同 Agent 类型有差异化的资源限制（简单问答 vs 复杂编码）
- **US-4** 作为主 Agent 监督者，我希望能通过自定义停止条件干预 Worker 执行（如检测到无效输出时提前终止）
- **US-5** 作为用户，当请求因资源限制终止时，我希望看到清晰的中文提示和建议动作，而不是技术错误码

---

## Functional Requirements

| FR | 描述 | 来源 | 验收标准 |
|----|------|------|---------|
| FR-001 | SkillRunner 支持多维度资源限制（步数 + token + 工具调用 + 成本 + 超时），任一维度触发即终止 | US-1 | 单元测试各维度独立 + 组合触发 |
| FR-002 | SkillOutputEnvelope 携带结构化的 token usage 和 cost 数据 | 结构性问题 #7 | SSE/Responses 两条路径均返回 token 数据 |
| FR-003 | AgentProfile / WorkerProfile / SKILL.md 可各自指定 resource_limits，按优先级合并 | US-3 | per-Profile 覆盖全局默认值 |
| FR-004 | `max_budget_usd` 按 token 价格累加，达到预算即终止 | US-2 | 集成测试成本累加触发熔断 |
| FR-005 | SkillRunnerHook 支持 `should_stop()` 自定义停止条件 | US-4 | hook 返回 True 时标记 STOPPED |
| FR-006 | SKILL_FAILED 后可降级模型重试一次 | 结构性问题 #5 | 降级重试成功返回结果 + 最多一次 |
| FR-007 | 限制参数可通过 Settings UI / 环境变量运行时调整 | US-3 | Settings 修改后新请求立即生效 |
| FR-008 | 超限时返回对应 ErrorCategory 的中文友好提示 | US-5 | 5 种 ErrorCategory 各有对应提示模板 |
| FR-009 | 每次执行 emit SKILL_USAGE_REPORT 事件 + 超限 emit RESOURCE_LIMIT_HIT 事件 | Constitution #2 | EventType 枚举包含新成员，事件可查询 |

---

## Non-Functional Requirements

| NFR | 描述 | 指标 |
|-----|------|------|
| NFR-001 | UsageTracker.check_limits() 调用不应显著增加 SkillRunner 主循环延迟 | p99 < 0.1ms |
| NFR-002 | 向后兼容：现有 LoopGuardPolicy 配置无需改动即可正常工作 | 迁移后现有 SKILL.md 和 Profile 配置零修改 |
| NFR-003 | 成本计算精度不影响业务判断 | 在 100 步累加场景下误差 < $0.01 |
| NFR-004 | 新增字段的 Schema 迁移在 SQLite WAL 模式下安全，不锁表 | ALTER TABLE ADD COLUMN 幂等执行 |

---

## Product Goal

将 SkillRunner 的资源限制从"单一写死步数阈值"升级为**多维度、可配置、自适应的资源限制体系**：

1. **多维度 UsageLimits**：步数 + token + 工具调用次数 + 成本，任一维度触发即终止
2. **per-Profile 差异化**：AgentProfile / WorkerProfile / SKILL.md 可各自指定限制参数
3. **成本熔断器**：`max_budget_usd` 按 token 价格累加，达到预算即终止
4. **自定义停止条件 Hook**：扩展 `SkillRunnerHook` 支持 `should_stop()` 判断
5. **智能降级重试**：SKILL_FAILED 后可降级模型 / 缩减 scope 重试一次
6. **Settings 可配置**：限制参数可通过 Settings UI / 环境变量运行时调整

---

## Scope

### In Scope

- SkillOutputEnvelope 扩展：携带 token usage + cost 数据（FR-002，Phase 0 前置）
- `UsageLimits` 多维度限制模型（替代单一 `LoopGuardPolicy`）
- per-Profile 限制配置（AgentProfile.resource_limits / WorkerProfile.resource_limits / SKILL.md frontmatter）
- 成本累加追踪 + `max_budget_usd` 熔断
- `StopHook` 扩展点（`SkillRunnerHook.should_stop()` 方法）
- 智能降级：FAILED 后根据 `RetryPolicy.upgrade_model_on_fail` / `downgrade_scope_on_fail` 重试
- Settings 页面资源限制配置区
- 现有 `LoopGuardPolicy` 向后兼容 + 迁移
- Error UX 友好提示模板（5 种 ErrorCategory）
- Observability 事件（SKILL_USAGE_REPORT / RESOURCE_LIMIT_HIT）

### Out of Scope

- 上下文压缩优化（Feature 060 覆盖）
- Token 预算规划重构（Feature 060 覆盖）
- 全局 Watchdog 告警优化（已有独立 Watchdog 模块，但需确保新 ErrorCategory 不触发误告警）
- Agent 自主停止（response_tool 模式，留待 Agent 自治阶段）
- 多租户配额管理

---

## Data Model

### 修改：`SkillOutputEnvelope` 新增 token/cost 字段

```python
class SkillOutputEnvelope(BaseModel):
    """Skill 统一输出封装。"""
    content: str = Field(default="")
    complete: bool = Field(default=False)
    skip_remaining_tools: bool = Field(default=False)
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # --- 新增 ---
    token_usage: dict[str, int] = Field(default_factory=dict)
    # 例：{"prompt_tokens": 500, "completion_tokens": 120, "total_tokens": 620}
    cost_usd: float = Field(default=0.0)
    # LLM 调用成本（美元），从 LiteLLM response 提取
```

### 新增：`UsageLimits`（替代 `LoopGuardPolicy` 的超集）

```python
_MAX_STEPS_HARD_CEILING = 500  # 降级重试 clamp 上限

class UsageLimits(BaseModel):
    """多维度资源限制。任一维度触发即终止执行。"""

    # 步数限制（原 LoopGuardPolicy.max_steps）
    max_steps: int = Field(default=30, ge=1, le=_MAX_STEPS_HARD_CEILING)

    # Token 限制
    max_request_tokens: int | None = Field(default=None, ge=1)      # 累计输入 token
    max_response_tokens: int | None = Field(default=None, ge=1)     # 累计输出 token

    # 工具调用次数限制
    max_tool_calls: int | None = Field(default=None, ge=1)

    # 成本限制（美元）
    max_budget_usd: float | None = Field(default=None, ge=0.0)

    # 超时限制（秒）
    max_duration_seconds: float | None = Field(default=None, ge=1.0)

    # 重复检测（原 LoopGuardPolicy.repeat_signature_threshold）
    repeat_signature_threshold: int = Field(default=3, ge=2, le=20)
```

### 新增：`UsageTracker`（运行时累加器）

使用 `@dataclass` 而非 `BaseModel`，因为每步都要更新多个字段，BaseModel 的 validate_assignment 开销不必要。

```python
@dataclass
class UsageTracker:
    """运行时资源消耗追踪。高频更新场景使用 dataclass 避免 Pydantic 校验开销。"""

    steps: int = 0
    request_tokens: int = 0
    response_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    start_time: float = 0.0  # time.monotonic()

    def check_limits(self, limits: UsageLimits) -> ErrorCategory | None:
        """检查是否超限。返回 None 表示未超限，否则返回对应的 ErrorCategory。"""
        if self.steps >= limits.max_steps:
            return ErrorCategory.STEP_LIMIT_EXCEEDED
        if limits.max_request_tokens and self.request_tokens >= limits.max_request_tokens:
            return ErrorCategory.TOKEN_LIMIT_EXCEEDED
        if limits.max_response_tokens and self.response_tokens >= limits.max_response_tokens:
            return ErrorCategory.TOKEN_LIMIT_EXCEEDED
        if limits.max_tool_calls and self.tool_calls >= limits.max_tool_calls:
            return ErrorCategory.TOOL_CALL_LIMIT_EXCEEDED
        if limits.max_budget_usd is not None and self.cost_usd >= limits.max_budget_usd - 1e-9:
            return ErrorCategory.BUDGET_EXCEEDED
        if limits.max_duration_seconds is not None:
            elapsed = time.monotonic() - self.start_time
            if elapsed >= limits.max_duration_seconds:
                return ErrorCategory.TIMEOUT_EXCEEDED
        return None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict，用于写入 SkillRunResult.usage。"""
        return {
            "steps": self.steps,
            "request_tokens": self.request_tokens,
            "response_tokens": self.response_tokens,
            "tool_calls": self.tool_calls,
            "cost_usd": self.cost_usd,
            "duration_seconds": round(time.monotonic() - self.start_time, 2),
        }
```

### 修改：`SkillExecutionContext` 新增 `usage_limits`

```python
class SkillExecutionContext(BaseModel):
    ...
    usage_limits: UsageLimits = Field(default_factory=UsageLimits)
    # 强类型。gateway 已依赖 skills 包（import SkillRunner/SkillManifest），无新增跨包依赖。
```

### 修改：`SkillManifest` / `SkillManifestModel` 新增 `resource_limits`

```python
class SkillManifestModel(BaseModel):
    ...
    loop_guard: LoopGuardPolicy = Field(default_factory=LoopGuardPolicy)  # deprecated，保留向后兼容
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    # 从 SKILL.md frontmatter 的 resource_limits 字段读取
```

### 修改：`SkillRunResult` 新增 usage / cost

```python
class SkillRunResult(BaseModel):
    ...
    usage: dict[str, Any] = Field(default_factory=dict)
    # 由 UsageTracker.to_dict() 生成，包含 steps/tokens/cost/duration
    total_cost_usd: float = Field(default=0.0)
```

### 修改：`SkillRunStatus` 新增 STOPPED

```python
class SkillRunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"  # 被 StopHook 或用户取消优雅终止
```

### 修改：`AgentProfile` / `WorkerProfile` 新增 `resource_limits`

```python
class AgentProfile(BaseModel):
    ...
    resource_limits: dict[str, Any] = Field(default_factory=dict)

class WorkerProfile(BaseModel):
    ...
    resource_limits: dict[str, Any] = Field(default_factory=dict)
```

### 修改：`SkillMdEntry` 新增 `resource_limits`

```python
class SkillMdEntry(BaseModel):
    ...
    resource_limits: dict[str, Any] = Field(default_factory=dict, description="资源限制覆盖")
```

### 修改：Control Plane 投影同步

```python
class AgentProfileItem(BaseModel):
    ...
    resource_limits: dict[str, Any] = Field(default_factory=dict)

class WorkerProfileStaticConfig(BaseModel):
    ...
    resource_limits: dict[str, Any] = Field(default_factory=dict)
```

前端 TypeScript 类型同步更新 `AgentProfileItem` 和 `WorkerProfileStaticConfig`。

### 修改：SKILL.md frontmatter 支持限制覆盖

```yaml
---
name: coding-agent
resource_limits:
  max_steps: 100
  max_budget_usd: 1.0
  max_duration_seconds: 300
---
```

### 限制参数合并优先级

```
SKILL.md resource_limits > WorkerProfile.resource_limits > AgentProfile.resource_limits > 默认值预设（按 Agent 类型）> 全局默认 UsageLimits()
```

合并策略：逐字段，后面的非 None/非零值覆盖前面的。

### 数据传递链路（CRITICAL 接缝定义）

```
                                     ┌─────────────────────────────┐
                                     │  AgentContextService        │
                                     │  (ContextFrame 组装)        │
                                     │                             │
                                     │  metadata["resource_limits"]│
                                     │  = agent_profile            │
                                     │    .resource_limits         │
                                     └────────────┬────────────────┘
                                                  │
                                                  ▼
┌─────────────────┐    ┌──────────────────────────────────────────┐
│ SkillMdEntry    │    │  LLMService._try_call_with_tools()       │
│ .resource_limits│───▶│                                          │
│ (from SKILL.md) │    │  1. profile_rl = metadata["resource_limits"]
└─────────────────┘    │  2. skill_rl = loaded_skill.resource_limits
                       │  3. limits = merge_usage_limits(          │
                       │       UsageLimits(), profile_rl, skill_rl)│
                       │  4. ctx.usage_limits = limits             │
                       └────────────┬─────────────────────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │  SkillRunner.run()       │
                       │  tracker = UsageTracker()│
                       │  limits = ctx.usage_limits│
                       │  每步: tracker.check()   │
                       └─────────────────────────┘
```

### 新增 ErrorCategory

```python
class ErrorCategory(StrEnum):
    ...
    TOKEN_LIMIT_EXCEEDED = "token_limit_exceeded"
    TOOL_CALL_LIMIT_EXCEEDED = "tool_call_limit_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT_EXCEEDED = "timeout_exceeded"
```

### 新增 EventType

```python
class EventType(StrEnum):
    ...
    SKILL_USAGE_REPORT = "SKILL_USAGE_REPORT"
    RESOURCE_LIMIT_HIT = "RESOURCE_LIMIT_HIT"
```

---

## Detailed Design

### Phase 0: SkillOutputEnvelope Token/Cost 数据回传 [P0 前置]

**目标**：解决 SkillRunner 无法获取 token usage 和 cost 数据的根本问题。这是 Phase 1/3 的硬前置依赖。

#### 0.1 扩展 SkillOutputEnvelope

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - `SkillOutputEnvelope` 新增 `token_usage: dict[str, int]` 和 `cost_usd: float` 字段

#### 0.2 LiteLLM Client 回传 token/cost

- **`octoagent/packages/provider/src/octoagent/provider/litellm_client.py`**
  - **SSE 流式路径** (`_call_proxy()`): 在流结束后从最后一个 chunk 的 `usage` 字段提取 token 数据，写入 `SkillOutputEnvelope.token_usage` 和 `cost_usd`（需设 `stream_options: {"include_usage": true}`）
  - **Responses API 路径** (`_call_proxy_responses()`): 从 response 中提取 `usage` 和 `cost_usd`（替代当前硬编码 `0.0`，使用 `litellm.completion_cost()` 或 response 自带的 cost 字段）

#### 0.3 StructuredModelClientProtocol 协议不变

- `generate()` 返回类型仍为 `SkillOutputEnvelope`，新增字段为可选（默认空 dict / 0.0），不破坏现有实现

---

### Phase 1: UsageLimits 模型 + SkillRunner 集成 [P0]

**目标**：用 `UsageLimits` + `UsageTracker` 替代 `LoopGuardPolicy` 的单一 `max_steps` 检查。

#### 1.1 新增 `UsageLimits` 和 `UsageTracker`

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - 新增 `UsageLimits` 数据类（BaseModel）
  - 新增 `UsageTracker` 数据类（`@dataclass`，非 BaseModel，避免高频更新的校验开销）
  - `LoopGuardPolicy` 标记为 deprecated，添加 `to_usage_limits()` 转换方法
  - 新增 `ErrorCategory` 成员（4 个新值）
  - `SkillRunStatus` 新增 `STOPPED`
  - `SkillRunResult` 新增 `usage: dict[str, Any]` + `total_cost_usd: float`

#### 1.2 SkillRunner 集成 UsageTracker

- **`octoagent/packages/skills/src/octoagent/skills/runner.py`**
  - `run()` 方法开始时：从 `execution_context.usage_limits` 获取 `UsageLimits`；创建 `UsageTracker(start_time=time.monotonic())`
  - 如果 `execution_context.usage_limits` 为默认值且 `manifest.loop_guard` 非默认值，调用 `manifest.loop_guard.to_usage_limits()` 向后兼容
  - 每步循环中：
    - LLM 调用后从 `raw_output.token_usage` / `raw_output.cost_usd` 更新 tracker
    - 工具调用后：`tracker.tool_calls += len(tool_calls)`
    - 步骤计数：`tracker.steps += 1`
    - 检查：`exceeded = tracker.check_limits(limits)` 替代原有 `steps >= max_steps`（L95）
    - 重复签名检查从 `manifest.loop_guard.repeat_signature_threshold` 改读 `limits.repeat_signature_threshold`（L173）
  - 循环结束后：将 `tracker.to_dict()` 写入 `SkillRunResult.usage`，`tracker.cost_usd` 写入 `total_cost_usd`

#### 1.3 SkillExecutionContext / SkillManifest 支持 UsageLimits

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - `SkillExecutionContext` 新增 `usage_limits: UsageLimits = Field(default_factory=UsageLimits)`（强类型）
  - `SkillManifestModel` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`
  - `LoopGuardPolicy` 添加 `model_validator(mode='after')` 当 `max_steps != 30` 时 emit DeprecationWarning

#### 1.4 Observability 事件

- **`octoagent/packages/core/src/octoagent/core/models/enums.py`**
  - `EventType` 新增 `SKILL_USAGE_REPORT` 和 `RESOURCE_LIMIT_HIT`

- **`octoagent/packages/skills/src/octoagent/skills/runner.py`**
  - `run()` 结束时 emit `SKILL_USAGE_REPORT` 事件（payload = tracker.to_dict() + skill_id）
  - 超限终止时额外 emit `RESOURCE_LIMIT_HIT` 事件（payload 含 error_category + 当前消耗 + 限制值）

#### 1.5 Error UX 友好提示

- **`octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`**
  - 扩展现有 `step_limit_exceeded` 处理，新增 4 种 ErrorCategory 的友好提示模板（详见 Error UX 章节）

---

### Phase 2: per-Profile 差异化配置 [P0]

**目标**：不同 Agent/Worker/Skill 可以有不同的资源限制。

#### 2.1 AgentProfile / WorkerProfile 新增字段

- **`octoagent/packages/core/src/octoagent/core/models/agent_context.py`**
  - `AgentProfile` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`
  - `WorkerProfile` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

- **`octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`**
  - 迁移 `ALTER TABLE agent_profiles ADD COLUMN resource_limits TEXT DEFAULT '{}'`
  - 迁移 `ALTER TABLE worker_profiles ADD COLUMN resource_limits TEXT DEFAULT '{}'`

#### 2.2 Control Plane 投影同步

- **`octoagent/packages/core/src/octoagent/core/models/control_plane.py`**
  - `AgentProfileItem` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`
  - `WorkerProfileStaticConfig` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

- **`octoagent/frontend/src/types/index.ts`**
  - `AgentProfileItem` 和 `WorkerProfileStaticConfig` TypeScript 类型同步新增 `resource_limits`

#### 2.3 SKILL.md frontmatter 解析

- **`octoagent/packages/skills/src/octoagent/skills/skill_models.py`**
  - `SkillMdEntry` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

- **`octoagent/packages/skills/src/octoagent/skills/discovery.py`**
  - `_parse_skill_file()` 显式提取 frontmatter 中的 `resource_limits` 字段

#### 2.4 限制合并逻辑

- **`octoagent/packages/skills/src/octoagent/skills/limits.py`**（新文件）
  - `merge_usage_limits(base: UsageLimits, *overrides: dict[str, Any]) -> UsageLimits`
  - 合并策略：逐字段，后面的非 None/非零值覆盖前面的
  - `None` 值表示"不覆盖"，`0` 值也不覆盖（防止误置零）

#### 2.5 LLMService 集成（数据传递链路实现）

- **`octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`**（或 ContextResolver）
  - 组装 ContextFrame 时，将 `AgentProfile.resource_limits` 写入 `metadata["resource_limits"]`

- **`octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`**
  - `_try_call_with_tools()` 中：
    1. 从 `metadata["resource_limits"]` 读取 Profile 级 resource_limits（dict）
    2. 从当前已加载 Skill 的 `SkillMdEntry.resource_limits` 读取 Skill 级覆盖（dict）
    3. 调用 `merge_usage_limits(UsageLimits(), profile_rl, skill_rl)` 生成最终 `UsageLimits`
    4. 设置到 `SkillExecutionContext.usage_limits`

#### 2.6 默认值预设应用机制

默认值矩阵（见下方）作为代码常量定义在 `limits.py` 中。当 `AgentProfile.resource_limits` 为空时，`merge_usage_limits()` 的 `base` 参数由调用方根据当前 Agent 类型（从 metadata 中获取 `worker_archetype` 或 `agent_role`）选择对应预设。无匹配时 fallback 到 `UsageLimits()` 全局默认。

---

### Phase 3: 成本熔断器 [P1]

**目标**：`max_budget_usd` 按 token 价格累加，达到预算即终止。

#### 3.1 成本计算集成

- 已在 Phase 0 中解决数据源（SkillOutputEnvelope.cost_usd）
- 已在 Phase 1 中集成到 UsageTracker（每步累加 raw_output.cost_usd）
- `check_limits()` 使用容差比较 `cost_usd >= max_budget_usd - 1e-9` 避免浮点精度问题

#### 3.2 边界条件处理

- `cost_usd` 为 `0.0`（LiteLLM 未返回成本）时**不触发**熔断
- `max_budget_usd` 为 `None`（未配置）时跳过成本检查

---

### Phase 4: StopHook 自定义停止条件 [P1]

**目标**：让外部代码可以在每步结束后决定是否提前终止。

#### 4.1 扩展 SkillRunnerHook

- **`octoagent/packages/skills/src/octoagent/skills/hooks.py`**
  - 新增 `async def should_stop(self, manifest, context, tracker, last_output) -> bool` 方法（**async**，与其他 hook 方法保持一致）
  - `NoopSkillRunnerHook` 默认返回 `False`

#### 4.2 SkillRunner 新增 `_check_stop_hooks()`

- **`octoagent/packages/skills/src/octoagent/skills/runner.py`**
  - 不能复用 `_call_hook()`（它忽略返回值），需新增 `_check_stop_hooks(manifest, ctx, tracker, output) -> bool`
  - 遍历所有 hook 调用 `should_stop()`，任一返回 `True` 即返回 `True`
  - 每步结束后在 `check_limits()` **之后**调用 `_check_stop_hooks()`

#### 4.3 LLMService 处理 STOPPED

- **`octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`**
  - STOPPED 状态：如果有最后一次有效 output（`result.output is not None`），返回其 content；否则返回 "请求已被停止。"
  - 不走 Echo fallback

---

### Phase 5: 智能降级重试 [P2]

**目标**：SKILL_FAILED 后可选择降级模型 / 缩减 scope 重试一次。

#### 5.1 RetryPolicy 扩展

- `RetryPolicy` 新增 `downgrade_scope_on_fail: bool = False`
- `RetryPolicy` 新增 `fallback_model_alias: str = ""`

#### 5.2 LLMService 降级逻辑

- 当 `SkillRunStatus.FAILED` 且满足降级条件时：
  - 切换 `model_alias` 到 `fallback_model_alias`
  - `max_steps` 放宽为 `min(int(max_steps * 1.5), _MAX_STEPS_HARD_CEILING)`（**clamp 到上限**，避免 Pydantic 校验溢出）
  - **`max_budget_usd` 不放宽**：这是硬限制，降级后的更大模型 + 更多步数必须在原预算内完成
  - 重新执行一次（标记 `is_degraded_retry=True` 防止递归）
- 降级重试最多一次

---

### Phase 6: Settings 可配置 + 环境变量 [P2]

**目标**：限制参数可通过 Settings UI / 环境变量运行时调整。

#### 6.1 环境变量支持

- `OCTOAGENT_DEFAULT_MAX_STEPS`（默认 30）
- `OCTOAGENT_DEFAULT_MAX_BUDGET_USD`（默认 None）
- `OCTOAGENT_DEFAULT_MAX_DURATION_SECONDS`（默认 None）
- 优先级：Settings UI 配置 > 环境变量 > 代码默认值

#### 6.2 Settings UI

- **`octoagent/frontend/src/domains/settings/`** 目录下新增资源限制配置组件
  - 全局默认值 + per-Agent 选择器
  - 配置变更通过 Control Plane action `agent_profile.update_resource_limits` 更新

#### 6.3 后端 Action

- 新增 `agent_profile.update_resource_limits` action
- 更新 `AgentProfile.resource_limits` 并广播变更

---

## Migration & Backward Compatibility

### LoopGuardPolicy → UsageLimits 迁移

1. `LoopGuardPolicy` 保留但标记 deprecated（`model_validator` 触发 DeprecationWarning）
2. `SkillRunner.run()` 内部：如果 `execution_context.usage_limits` 为默认值且 `manifest.loop_guard` 非默认值，自动调用 `to_usage_limits()` 转换
3. SKILL.md 的 `loop_guard:` frontmatter 继续支持，SkillDiscovery 同时提取 `loop_guard` 和 `resource_limits`，后者优先
4. 6 个月后移除 `LoopGuardPolicy`

### LoopGuardPolicy 使用点迁移清单

| 文件 | 行 | 当前用法 | 迁移方式 |
|------|-----|---------|---------|
| `runner.py` | L95 | `while steps < manifest.loop_guard.max_steps` | 改为 `tracker.check_limits(limits)` |
| `runner.py` | L173 | `manifest.loop_guard.repeat_signature_threshold` | 改读 `limits.repeat_signature_threshold` |
| `runner.py` | L467 | `manifest.loop_guard.max_steps` (emit event) | 改读 `limits.max_steps` |
| `manifest.py` | L29 | `SkillManifest.loop_guard` 字段 | 保留，deprecated |
| `models.py` | L123 | `SkillManifestModel.loop_guard` 字段 | 保留，deprecated |

### Schema 迁移

- `agent_profiles` 表新增 `resource_limits TEXT DEFAULT '{}'`
- `worker_profiles` 表新增 `resource_limits TEXT DEFAULT '{}'`
- 无数据迁移需要（新字段默认空 dict，走全局默认值）

---

## 默认值设计

基于调研和生产经验，推荐的默认值矩阵：

| Agent 类型 | max_steps | max_budget_usd | max_duration_seconds | max_tool_calls |
|-----------|-----------|----------------|---------------------|----------------|
| Butler（主 Agent） | 50 | 0.50 | 300 | 30 |
| Worker（通用） | 30 | 0.30 | 180 | 20 |
| Worker（coding） | 100 | 1.00 | 600 | 80 |
| Worker（research） | 60 | 0.50 | 300 | 40 |
| Subagent | 15 | 0.10 | 60 | 10 |

**应用机制**：`limits.py` 中定义 `_PRESETS: dict[str, dict[str, Any]]` 常量映射。`merge_usage_limits()` 的调用方（LLMService）根据 `metadata["worker_archetype"]` 或 `metadata["agent_role"]` 选择对应预设作为 `base` 参数。无匹配时 fallback 到 `UsageLimits()` 全局默认。

用户可通过 Settings / SKILL.md / AgentProfile 覆盖。

---

## Error UX

### 超限时的用户提示模板

| ErrorCategory | 用户提示 |
|---------------|---------|
| `step_limit_exceeded` | "处理步骤较多（{steps} 步），已达上限。请尝试拆分为更小的问题。" |
| `token_limit_exceeded` | "本次对话消耗 token 较多（{tokens}），已达上限。建议开启新对话或缩减请求范围。" |
| `tool_call_limit_exceeded` | "工具调用次数较多（{calls} 次），已达上限。请尝试更具体的指令。" |
| `budget_exceeded` | "本次请求成本已达预算上限（${budget}）。如需继续，请在设置中调整预算限制。" |
| `timeout_exceeded` | "请求处理时间过长（{seconds}s），已超时。请稍后重试或简化请求。" |

**实现位置**：`llm_service.py` 中 `_try_call_with_tools()` 的 SKILL_FAILED 处理分支，根据 `result.error_category` 选择模板并替换占位符（从 `result.usage` dict 取值）。

---

## Observability

- 每次 SkillRunner 执行结束，emit `SKILL_USAGE_REPORT` 事件，payload 含完整 `UsageTracker.to_dict()` + `skill_id`
- 超限终止时额外 emit `RESOURCE_LIMIT_HIT` 告警事件，payload 含 `error_category` + 当前消耗 + 限制值
- Settings 页面展示近 24h 的资源消耗统计（步数分布、token 消耗、成本趋势）
- 需确保 Watchdog 的 error_category 过滤逻辑兼容 4 个新 ErrorCategory（不触发误告警或被静默吞掉）

---

## 验证方式

### Phase 0
- 单元测试：SkillOutputEnvelope 新字段默认值正确
- 集成测试：SSE 流式路径和 Responses API 路径均返回 token_usage 和 cost_usd 数据

### Phase 1+2
- 单元测试：`UsageLimits` 各维度独立触发、组合触发
- 单元测试：`UsageTracker.check_limits()` 边界条件（None 值跳过、浮点容差）
- 单元测试：`LoopGuardPolicy.to_usage_limits()` 转换正确
- 单元测试：`merge_usage_limits()` 优先级覆盖（空覆盖、单字段、多层）
- 集成测试：SkillRunner 在不同维度超限时返回正确的 `ErrorCategory` 和友好提示
- 集成测试：per-Profile 限制覆盖全局默认值
- 集成测试：SKILL.md frontmatter resource_limits 正确传递到 SkillRunner

### Phase 3
- 集成测试：多步 Skill 执行中累计成本达到 `max_budget_usd` 时终止
- 边界测试：cost_usd 为 0.0（LiteLLM 未返回）时不触发熔断
- 边界测试：浮点累加精度（100 步 × $0.003 = $0.30）

### Phase 4
- 单元测试：`should_stop()` hook 返回 True 时 SkillRunner 标记 STOPPED
- 集成测试：外部 hook 可以基于输出内容停止执行
- 集成测试：STOPPED 状态在 LLMService 中返回最后有效 output，不走 Echo

### Phase 5
- 集成测试：FAILED 后降级重试成功返回结果
- 边界测试：降级重试后再次失败不再重试（防递归）
- 边界测试：max_steps * 1.5 被 clamp 到 _MAX_STEPS_HARD_CEILING

### Phase 6
- 手动测试：Settings 修改限制参数 → 立即对新请求生效
- 单元测试：环境变量覆盖代码默认值
- 集成测试：Settings 配置 > 环境变量优先级

---

## 实施优先级与顺序

```
Phase 0 (Token/Cost 数据回传) [P0 前置]
  → Phase 1 (UsageLimits 模型 + SkillRunner 集成) [P0]
    → Phase 2 (per-Profile 配置) [P0]
      → Phase 3 (成本熔断) [P1]
      → Phase 4 (StopHook) [P1]
        → Phase 5 (智能降级) [P2]
        → Phase 6 (Settings UI) [P2]
```

**最小可交付**：Phase 0 + 1 + 2 = 数据基础 + 多维度限制 + per-Profile 配置
**核心防护**：Phase 3 + 4 = 成本熔断 + 自定义停止
**体验完善**：Phase 5 + 6 = 智能降级 + UI 可配置
