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

- `UsageLimits` 多维度限制模型（替代单一 `LoopGuardPolicy`）
- per-Profile 限制配置（AgentProfile.resource_limits / WorkerProfile.resource_limits / SKILL.md frontmatter）
- 成本累加追踪 + `max_budget_usd` 熔断
- `StopHook` 扩展点（`SkillRunnerHook.should_stop()` 方法）
- 智能降级：FAILED 后根据 `RetryPolicy.upgrade_model_on_fail` / `downgrade_scope_on_fail` 重试
- Settings 页面资源限制配置区
- 现有 `LoopGuardPolicy` 向后兼容 + 迁移

### Out of Scope

- 上下文压缩优化（Feature 060 覆盖）
- Token 预算规划重构（Feature 060 覆盖）
- 全局 Watchdog 告警优化（已有独立 Watchdog 模块）
- Agent 自主停止（response_tool 模式，留待 Agent 自治阶段）
- 多租户配额管理

---

## Data Model

### 新增：`UsageLimits`（替代 `LoopGuardPolicy` 的超集）

```python
class UsageLimits(BaseModel):
    """多维度资源限制。任一维度触发即终止执行。"""

    # 步数限制（原 LoopGuardPolicy.max_steps）
    max_steps: int = Field(default=30, ge=1, le=500)

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

```python
class UsageTracker(BaseModel):
    """运行时资源消耗追踪。"""

    steps: int = 0
    request_tokens: int = 0
    response_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    start_time: float = 0.0  # monotonic

    def check_limits(self, limits: UsageLimits) -> ErrorCategory | None:
        """检查是否超限。返回 None 表示未超限，否则返回对应的 ErrorCategory。"""
        ...
```

### 修改：`AgentProfile` 新增 `resource_limits`

```python
class AgentProfile(BaseModel):
    ...
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    # 序列化后为 UsageLimits 的 JSON 表示
    # 例：{"max_steps": 50, "max_budget_usd": 0.5}
```

### 修改：`WorkerProfile` 新增 `resource_limits`

```python
class WorkerProfile(BaseModel):
    ...
    resource_limits: dict[str, Any] = Field(default_factory=dict)
```

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
SKILL.md resource_limits > WorkerProfile.resource_limits > AgentProfile.resource_limits > 全局默认 UsageLimits()
```

合并策略：高优先级的非空值覆盖低优先级。

### 新增 ErrorCategory

```python
class ErrorCategory(StrEnum):
    ...
    TOKEN_LIMIT_EXCEEDED = "token_limit_exceeded"
    TOOL_CALL_LIMIT_EXCEEDED = "tool_call_limit_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT_EXCEEDED = "timeout_exceeded"
```

---

## Detailed Design

### Phase 1: UsageLimits 模型 + SkillRunner 集成 [P0]

**目标**：用 `UsageLimits` + `UsageTracker` 替代 `LoopGuardPolicy` 的单一 `max_steps` 检查。

#### 1.1 新增 `UsageLimits` 和 `UsageTracker`

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - 新增 `UsageLimits` 数据类
  - 新增 `UsageTracker` 数据类，含 `check_limits()` 方法
  - `LoopGuardPolicy` 标记为 deprecated，添加 `to_usage_limits()` 转换方法
  - 新增 `ErrorCategory` 成员

#### 1.2 SkillRunner 集成 UsageTracker

- **`octoagent/packages/skills/src/octoagent/skills/runner.py`**
  - `run()` 方法开始时创建 `UsageTracker`
  - 每次 LLM 调用后更新 `request_tokens` / `response_tokens` / `cost_usd`
  - 每次工具调用后更新 `tool_calls`
  - 每步结束调用 `tracker.check_limits(limits)` 替代原有 `steps >= max_steps` 检查
  - `SkillRunResult` 新增 `usage: UsageTracker` 字段（返回最终消耗统计）

#### 1.3 SkillManifest / SkillExecutionContext 传入 UsageLimits

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - `SkillExecutionContext` 新增 `usage_limits: UsageLimits = Field(default_factory=UsageLimits)`
  - `SkillManifest` 的 `loop_guard` 字段保留但 deprecated，新增 `resource_limits: dict[str, Any]`

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

#### 2.2 限制合并逻辑

- **`octoagent/packages/skills/src/octoagent/skills/limits.py`**（新文件）
  - `merge_usage_limits(base: UsageLimits, *overrides: dict[str, Any]) -> UsageLimits`
  - 合并策略：后面的非空值覆盖前面的
  - 调用链：`AgentProfile.resource_limits → WorkerProfile.resource_limits → SKILL.md resource_limits`

#### 2.3 LLMService / Orchestrator 集成

- **`octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`**
  - `_try_call_with_tools()` 从 AgentProfile/WorkerProfile 读取 `resource_limits`
  - 与 SkillManifest 的 `resource_limits` 合并后传入 `SkillExecutionContext`

---

### Phase 3: 成本熔断器 [P1]

**目标**：`max_budget_usd` 按 token 价格累加，达到预算即终止。

#### 3.1 成本计算集成

- **`octoagent/packages/skills/src/octoagent/skills/runner.py`**
  - 每次 LLM 调用后，从 `ModelCallResult.cost_usd` 累加到 `UsageTracker.cost_usd`
  - `check_limits()` 中检查 `cost_usd >= limits.max_budget_usd`

#### 3.2 成本元数据回传

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - `SkillRunResult` 新增 `total_cost_usd: float = 0.0`
  - 用于上层统计和 Settings 展示

---

### Phase 4: StopHook 自定义停止条件 [P1]

**目标**：让外部代码可以在每步结束后决定是否提前终止。

#### 4.1 扩展 SkillRunnerHook

- **`octoagent/packages/skills/src/octoagent/skills/hooks.py`**
  - 新增 `should_stop(manifest, context, tracker, last_output) -> bool` 方法
  - 默认实现返回 `False`（不停止）
  - 如果任何 hook 返回 `True`，SkillRunner 优雅终止（不算 FAILED，标记为 STOPPED）

#### 4.2 新增 SkillRunStatus.STOPPED

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - `SkillRunStatus` 新增 `STOPPED = "STOPPED"`
  - 区分"被外部停止"和"超限失败"

---

### Phase 5: 智能降级重试 [P2]

**目标**：SKILL_FAILED 后可选择降级模型 / 缩减 scope 重试一次。

#### 5.1 RetryPolicy 扩展

- **`octoagent/packages/skills/src/octoagent/skills/models.py`**
  - `RetryPolicy` 新增 `downgrade_scope_on_fail: bool = False`
  - `RetryPolicy` 新增 `fallback_model_alias: str = ""`（降级时使用的模型 alias）

#### 5.2 LLMService 降级逻辑

- **`octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`**
  - 当 `SkillRunStatus.FAILED` 且 `retry_policy.upgrade_model_on_fail=True` 时：
    - 切换到 `fallback_model_alias`（或自动选择更大模型）
    - 增加 `max_steps` 上限（如 × 1.5）
    - 重新执行一次
  - 最多降级重试一次，避免无限循环

---

### Phase 6: Settings 可配置 + 环境变量 [P2]

**目标**：限制参数可通过 Settings UI / 环境变量运行时调整。

#### 6.1 环境变量支持

- `OCTOAGENT_DEFAULT_MAX_STEPS`（默认 30）
- `OCTOAGENT_DEFAULT_MAX_BUDGET_USD`（默认 None）
- `OCTOAGENT_DEFAULT_MAX_DURATION_SECONDS`（默认 None）

#### 6.2 Settings UI

- **`octoagent/frontend/src/pages/SettingsPage.tsx`**
  - 新增"资源限制"配置区域
  - 全局默认值 + per-Agent 覆盖
  - 配置变更实时生效（通过 Control Plane action 更新 AgentProfile）

---

## Migration & Backward Compatibility

### LoopGuardPolicy → UsageLimits 迁移

1. `LoopGuardPolicy` 保留但标记 deprecated
2. `SkillRunner.run()` 内部：如果只收到 `LoopGuardPolicy`，自动转换为 `UsageLimits`
3. SKILL.md 的 `loop_guard:` frontmatter 继续支持，内部转换为 `resource_limits:`
4. 6 个月后移除 `LoopGuardPolicy`

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

这些值作为推荐预设，用户可通过 Settings / SKILL.md / AgentProfile 覆盖。

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

---

## Observability

- 每次 SkillRunner 执行结束，emit `SKILL_USAGE_REPORT` 事件，包含完整 `UsageTracker` 数据
- Settings 页面展示近 24h 的资源消耗统计（步数分布、token 消耗、成本趋势）
- 超限事件 emit `RESOURCE_LIMIT_HIT` 告警事件（供 Watchdog 统计）

---

## 验证方式

### Phase 1+2
- 单元测试：`UsageLimits` 各维度独立触发、组合触发
- 单元测试：`merge_usage_limits()` 优先级覆盖
- 集成测试：SkillRunner 在不同维度超限时返回正确的 `ErrorCategory` 和友好提示
- 集成测试：per-Profile 限制覆盖全局默认值

### Phase 3
- 集成测试：多步 Skill 执行中累计成本达到 `max_budget_usd` 时终止
- 验证 `SkillRunResult.total_cost_usd` 准确

### Phase 4
- 单元测试：`should_stop()` hook 返回 True 时 SkillRunner 标记 STOPPED
- 集成测试：外部 hook 可以基于输出内容停止执行

### Phase 5
- 集成测试：FAILED 后降级重试成功返回结果
- 验证降级重试最多执行一次

### Phase 6
- 手动测试：Settings 修改限制参数 → 立即对新请求生效
- 验证环境变量覆盖默认值

---

## 实施优先级与顺序

```
Phase 1 (UsageLimits 模型 + SkillRunner 集成) [P0]
  → Phase 2 (per-Profile 配置) [P0]
    → Phase 3 (成本熔断) [P1]
    → Phase 4 (StopHook) [P1]
      → Phase 5 (智能降级) [P2]
      → Phase 6 (Settings UI) [P2]
```

**最小可交付**：Phase 1 + 2 = 多维度限制 + per-Profile 配置
**核心防护**：Phase 3 + 4 = 成本熔断 + 自定义停止
**体验完善**：Phase 5 + 6 = 智能降级 + UI 可配置
