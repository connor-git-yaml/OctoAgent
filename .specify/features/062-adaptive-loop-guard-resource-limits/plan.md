---
feature_id: "062"
title: "Adaptive Loop Guard & Resource Limits — 实施计划"
created: "2026-03-17"
updated: "2026-03-17"
---

# 实施计划

## 实施顺序

```
Phase 1 → Phase 2 → Phase 3 / Phase 4 (可并行) → Phase 5 → Phase 6
```

---

## Phase 1: UsageLimits 模型 + SkillRunner 集成 [P0]

**预估改动量**：3 个文件新增/修改，~250 行

### Step 1.1: 新增 UsageLimits + UsageTracker + ErrorCategory 扩展

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- 新增 `UsageLimits(BaseModel)` — 多维度限制参数容器
- 新增 `UsageTracker(BaseModel)` — 运行时累加器 + `check_limits()` 方法
- `ErrorCategory` 新增：`TOKEN_LIMIT_EXCEEDED`, `TOOL_CALL_LIMIT_EXCEEDED`, `BUDGET_EXCEEDED`, `TIMEOUT_EXCEEDED`
- `SkillRunResult` 新增 `usage: dict[str, Any] = Field(default_factory=dict)` 和 `total_cost_usd: float = 0.0`
- `LoopGuardPolicy` 添加 `to_usage_limits() -> UsageLimits` 转换方法

### Step 1.2: SkillRunner.run() 集成 UsageTracker

**文件**: `octoagent/packages/skills/src/octoagent/skills/runner.py`

- `run()` 开始时：
  - 从 `execution_context.usage_limits` 获取 `UsageLimits`（如无则用 `LoopGuardPolicy.to_usage_limits()` 兼容）
  - 创建 `UsageTracker(start_time=time.monotonic())`
- 每步循环中：
  - LLM 调用后：`tracker.request_tokens += usage.prompt_tokens`、`tracker.response_tokens += usage.completion_tokens`、`tracker.cost_usd += cost`
  - 工具调用后：`tracker.tool_calls += len(tool_calls)`
  - 步骤计数：`tracker.steps += 1`
  - 检查：`exceeded = tracker.check_limits(limits)`，替代原有 `steps >= max_steps`
- 循环结束后：将 `tracker` 数据写入 `SkillRunResult.usage`

### Step 1.3: SkillExecutionContext 支持 UsageLimits

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- `SkillExecutionContext` 新增 `usage_limits: dict[str, Any] = Field(default_factory=dict)`
- `SkillManifest` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

### Step 1.4: 单元测试

**文件**: `octoagent/packages/skills/tests/test_usage_limits.py`（新文件）

- `UsageLimits` 默认值正确
- `UsageTracker.check_limits()` 各维度独立触发
- `UsageTracker.check_limits()` 多维度组合（取最先触发的）
- `LoopGuardPolicy.to_usage_limits()` 转换正确
- 超时检查（`max_duration_seconds`）

---

## Phase 2: per-Profile 差异化配置 [P0]

**预估改动量**：5 个文件修改，~150 行

### Step 2.1: AgentProfile / WorkerProfile 新增 resource_limits

**文件**: `octoagent/packages/core/src/octoagent/core/models/agent_context.py`

- `AgentProfile` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`
- `WorkerProfile` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

### Step 2.2: Schema 迁移

**文件**: `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`

- `_migrate_legacy_tables()` 新增:
  - `ALTER TABLE agent_profiles ADD COLUMN resource_limits TEXT DEFAULT '{}'`
  - `ALTER TABLE worker_profiles ADD COLUMN resource_limits TEXT DEFAULT '{}'`

### Step 2.3: 限制合并逻辑

**文件**: `octoagent/packages/skills/src/octoagent/skills/limits.py`（新文件）

- `merge_usage_limits(base: UsageLimits, *overrides: dict[str, Any]) -> UsageLimits`
- 合并策略：逐字段，后面的非 None/非零值覆盖前面的
- 调用示例：`merge_usage_limits(global_default, agent_profile.resource_limits, skill_manifest.resource_limits)`

### Step 2.4: LLMService 集成

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

- `_try_call_with_tools()` 中：
  - 从当前 AgentProfile/WorkerProfile 读取 `resource_limits`
  - 从 SkillManifest 读取 `resource_limits`
  - 调用 `merge_usage_limits()` 生成最终 `UsageLimits`
  - 设置到 `SkillExecutionContext.usage_limits`

### Step 2.5: 单元测试

**文件**: `octoagent/packages/skills/tests/test_limits_merge.py`（新文件）

- 空覆盖 = 基础值不变
- 单字段覆盖
- 多层覆盖优先级
- SKILL.md frontmatter 覆盖 WorkerProfile 覆盖 AgentProfile

---

## Phase 3: 成本熔断器 [P1]

**预估改动量**：2 个文件修改，~50 行

### Step 3.1: SkillRunner 成本累加

**文件**: `octoagent/packages/skills/src/octoagent/skills/runner.py`

- 在 LLM 调用结果中提取 `cost_usd`（从 `ModelCallResult` 或等效数据）
- 累加到 `UsageTracker.cost_usd`
- `check_limits()` 中检查 `cost_usd >= limits.max_budget_usd`

### Step 3.2: SkillRunResult 成本回传

- `SkillRunResult.total_cost_usd` 取自 `UsageTracker.cost_usd`
- 上层 `LLMService` 可利用此值做统计

### Step 3.3: 集成测试

- Mock 一个每步消耗 $0.01 的 Skill，设 `max_budget_usd=0.03`，验证在第 4 步之前终止

---

## Phase 4: StopHook 自定义停止条件 [P1]

**预估改动量**：2 个文件修改，~80 行

### Step 4.1: 扩展 SkillRunnerHook

**文件**: `octoagent/packages/skills/src/octoagent/skills/hooks.py`

- 新增 `should_stop(manifest, context, tracker, last_output) -> bool` 方法
- `NoopSkillRunnerHook` 默认返回 `False`

### Step 4.2: SkillRunner 集成

**文件**: `octoagent/packages/skills/src/octoagent/skills/runner.py`

- 每步结束后，在 `check_limits()` 之后，调用所有 hook 的 `should_stop()`
- 任一返回 `True`：标记 `SkillRunStatus.STOPPED`，优雅退出

### Step 4.3: 新增 SkillRunStatus.STOPPED

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- `SkillRunStatus.STOPPED = "STOPPED"`

### Step 4.4: LLMService 处理 STOPPED

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

- STOPPED 状态：返回最后一次有效 output（如有），不走 Echo fallback

---

## Phase 5: 智能降级重试 [P2]

**预估改动量**：2 个文件修改，~100 行

### Step 5.1: RetryPolicy 扩展

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- `RetryPolicy` 新增 `downgrade_scope_on_fail: bool = False`
- `RetryPolicy` 新增 `fallback_model_alias: str = ""`

### Step 5.2: LLMService 降级逻辑

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

- 当 `SkillRunResult.status == FAILED` 且满足降级条件时：
  - 切换 model_alias 到 `fallback_model_alias`
  - 增加步数上限 `max_steps *= 1.5`
  - 重新执行一次（标记 `is_degraded_retry=True` 防止递归）
- 降级重试最多一次

---

## Phase 6: Settings 可配置 + 环境变量 [P2]

**预估改动量**：3 个文件修改/新增，~200 行

### Step 6.1: 环境变量读取

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- `UsageLimits` 的默认值从环境变量读取：
  - `OCTOAGENT_DEFAULT_MAX_STEPS` → `max_steps`
  - `OCTOAGENT_DEFAULT_MAX_BUDGET_USD` → `max_budget_usd`
  - `OCTOAGENT_DEFAULT_MAX_DURATION_SECONDS` → `max_duration_seconds`

### Step 6.2: Settings UI 配置区

**文件**: `octoagent/frontend/src/pages/SettingsPage.tsx`

- "资源限制"配置卡片
- 字段：max_steps / max_budget_usd / max_duration_seconds / max_tool_calls
- 全局默认值 + per-Agent 选择器
- 保存后通过 Control Plane action 更新 AgentProfile.resource_limits

### Step 6.3: 后端 Action

- `agent_profile.update_resource_limits` action
- 更新 AgentProfile.resource_limits 并广播变更

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 成本计算依赖 LiteLLM 返回的 token 价格，可能不准 | Phase 3 先用 `cost_usd` 字段（已有），后续可对接 LiteLLM 成本 API |
| per-Profile 配置增加了理解复杂度 | 提供合理默认值矩阵，用户无需手动配置 |
| 旧 SKILL.md 的 `loop_guard:` 与新 `resource_limits:` 共存 | `LoopGuardPolicy.to_usage_limits()` 自动转换，6 个月后移除 |
| StopHook 可能被滥用导致 Skill 过早终止 | Hook 只能建议停止，STOPPED 状态仍返回最后有效 output |

---

## 依赖

- Feature 060（Context Engineering Enhancement）：token 预算体系。Phase 1-2 不依赖，Phase 3 的 token 计数可复用 060 的 `ContextBudgetPlanner`
- LiteLLM Proxy：成本数据来源（`ModelCallResult.cost_usd`）
- 现有 Event Store：`SKILL_USAGE_REPORT` 事件存储
