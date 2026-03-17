---
feature_id: "062"
title: "Adaptive Loop Guard & Resource Limits — 实施计划"
created: "2026-03-17"
updated: "2026-03-17"
---

# 实施计划

## 实施顺序

```
Phase 0 (前置) → Phase 1 → Phase 2 → Phase 3 / Phase 4 (可并行) → Phase 5 → Phase 6
```

---

## Phase 0: SkillOutputEnvelope Token/Cost 数据回传 [P0 前置]

**预估改动量**：2 个文件修改，~60 行

**目标**：解决 SkillRunner 无法获取 token usage 和 cost 数据的根本问题。Phase 1/3 的硬前置依赖。

### Step 0.1: 扩展 SkillOutputEnvelope

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- `SkillOutputEnvelope` 新增 `token_usage: dict[str, int] = Field(default_factory=dict)` 和 `cost_usd: float = Field(default=0.0)`
- StructuredModelClientProtocol 返回类型不变（仍为 `SkillOutputEnvelope`），新字段为可选，不破坏现有实现

### Step 0.2: LiteLLM Client SSE 路径回传 token/cost

**文件**: `octoagent/packages/provider/src/octoagent/provider/litellm_client.py`

- `_call_proxy()` SSE 流式路径：在流结束后从最后一个 chunk 的 `usage` 字段提取 token 数据
- 需设 `stream_options: {"include_usage": true}` 以确保 LiteLLM 在最终 chunk 返回 usage
- 写入 `SkillOutputEnvelope.token_usage`（`{"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}`）和 `cost_usd`（调用 `litellm.completion_cost()` 或从 response 自带 cost 字段提取）

### Step 0.3: LiteLLM Client Responses API 路径回传 token/cost

**文件**: `octoagent/packages/provider/src/octoagent/provider/litellm_client.py`

- `_call_proxy_responses()` Responses API 路径：从 response 中提取 `usage` 和 `cost_usd`
- 替代当前硬编码 `cost_usd=0.0`
- 使用 `litellm.completion_cost()` 或 response 自带的 cost 字段

### Step 0.4: 单元测试 + 回归测试

**文件**: `octoagent/packages/provider/tests/test_litellm_token_data.py`（新文件）

- SSE 路径返回 token_usage 和 cost_usd
- Responses API 路径返回 token_usage 和 cost_usd
- SkillOutputEnvelope 新字段默认值正确（空 dict / 0.0）
- 回归：现有 StructuredModelClientProtocol 实现不受影响

---

## Phase 1: UsageLimits 模型 + SkillRunner 集成 [P0]

**预估改动量**：4 个文件新增/修改，~300 行

### Step 1.1: 新增 UsageLimits + UsageTracker + ErrorCategory 扩展

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- 新增 `_MAX_STEPS_HARD_CEILING = 500` 常量
- 新增 `UsageLimits(BaseModel)` — 多维度限制参数容器，`max_steps` 上限为 `_MAX_STEPS_HARD_CEILING`
- 新增 `UsageTracker`（**`@dataclass`，非 BaseModel**）— 运行时累加器 + `check_limits()` + `to_dict()` 方法。使用 dataclass 避免高频字段更新时的 Pydantic validate_assignment 开销
- `ErrorCategory` 新增 4 个成员：`TOKEN_LIMIT_EXCEEDED`, `TOOL_CALL_LIMIT_EXCEEDED`, `BUDGET_EXCEEDED`, `TIMEOUT_EXCEEDED`
- `SkillRunStatus` 新增 `STOPPED = "STOPPED"`
- `SkillRunResult` 新增 `usage: dict[str, Any] = Field(default_factory=dict)` 和 `total_cost_usd: float = 0.0`
- `LoopGuardPolicy` 标记 deprecated：添加 `to_usage_limits() -> UsageLimits` 转换方法 + `model_validator(mode='after')` 当 `max_steps != 30` 时 emit DeprecationWarning
- `SkillExecutionContext` 新增 `usage_limits: UsageLimits = Field(default_factory=UsageLimits)`（**强类型**，gateway 已依赖 skills 包）
- `SkillManifestModel` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

### Step 1.2: SkillRunner.run() 集成 UsageTracker

**文件**: `octoagent/packages/skills/src/octoagent/skills/runner.py`

- `run()` 方法开始时：
  - 从 `execution_context.usage_limits` 获取 `UsageLimits`
  - 如果 `execution_context.usage_limits` 为默认值且 `manifest.loop_guard` 非默认值，调用 `manifest.loop_guard.to_usage_limits()` 向后兼容
  - 创建 `UsageTracker(start_time=time.monotonic())`
- 每步循环中（替代原有 L95 `while steps < manifest.loop_guard.max_steps`）：
  - LLM 调用后从 `raw_output.token_usage` / `raw_output.cost_usd` 更新 tracker：`tracker.request_tokens += token_usage.get("prompt_tokens", 0)`、`tracker.response_tokens += token_usage.get("completion_tokens", 0)`、`tracker.cost_usd += cost_usd`
  - 工具调用后：`tracker.tool_calls += len(tool_calls)`
  - 步骤计数：`tracker.steps += 1`
  - 检查：`exceeded = tracker.check_limits(limits)` 替代原有 `steps >= max_steps`
  - 重复签名检查从 `manifest.loop_guard.repeat_signature_threshold`（L173）改读 `limits.repeat_signature_threshold`
- 循环结束后：
  - 将 `tracker.to_dict()` 写入 `SkillRunResult.usage`
  - `tracker.cost_usd` 写入 `SkillRunResult.total_cost_usd`
  - emit 事件时从 `limits.max_steps` 取值替代 `manifest.loop_guard.max_steps`（L467）

### Step 1.3: Observability 事件

**文件**: `octoagent/packages/core/src/octoagent/core/models/enums.py`（或 EventType 所在文件）

- `EventType` 新增 `SKILL_USAGE_REPORT` 和 `RESOURCE_LIMIT_HIT`

**文件**: `octoagent/packages/skills/src/octoagent/skills/runner.py`

- `run()` 结束时 emit `SKILL_USAGE_REPORT` 事件（payload = `tracker.to_dict()` + `skill_id`）
- 超限终止时额外 emit `RESOURCE_LIMIT_HIT` 事件（payload 含 `error_category` + 当前消耗值 + 限制值）

### Step 1.4: Error UX 友好提示

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

- 扩展现有 `step_limit_exceeded` 处理分支，新增 4 种 ErrorCategory 的友好中文提示模板：
  - `token_limit_exceeded` → "本次对话消耗 token 较多（{tokens}），已达上限。建议开启新对话或缩减请求范围。"
  - `tool_call_limit_exceeded` → "工具调用次数较多（{calls} 次），已达上限。请尝试更具体的指令。"
  - `budget_exceeded` → "本次请求成本已达预算上限（${budget}）。如需继续，请在设置中调整预算限制。"
  - `timeout_exceeded` → "请求处理时间过长（{seconds}s），已超时。请稍后重试或简化请求。"
- 从 `result.usage` dict 取值替换占位符

### Step 1.5: 单元测试

**文件**: `octoagent/packages/skills/tests/test_usage_limits.py`（新文件）

- `UsageLimits` 默认值正确
- `UsageTracker.check_limits()` 各维度独立触发
- `UsageTracker.check_limits()` 多维度组合（取最先触发的）
- `LoopGuardPolicy.to_usage_limits()` 转换正确
- 超时检查（`max_duration_seconds`）
- 浮点容差检查（`cost_usd >= max_budget_usd - 1e-9`）
- `UsageTracker.to_dict()` 序列化正确
- SkillRunner 集成测试：不同维度超限时返回正确 ErrorCategory

---

## Phase 2: per-Profile 差异化配置 [P0]

**预估改动量**：8 个文件修改/新增，~200 行

### Step 2.1: AgentProfile / WorkerProfile 新增 resource_limits

**文件**: `octoagent/packages/core/src/octoagent/core/models/agent_context.py`

- `AgentProfile` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`
- `WorkerProfile` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

### Step 2.2: Schema 迁移

**文件**: `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`

- `_migrate_legacy_tables()` 新增：
  - `ALTER TABLE agent_profiles ADD COLUMN resource_limits TEXT DEFAULT '{}'`
  - `ALTER TABLE worker_profiles ADD COLUMN resource_limits TEXT DEFAULT '{}'`

### Step 2.3: Control Plane 投影同步

**文件**: `octoagent/packages/core/src/octoagent/core/models/control_plane.py`

- `AgentProfileItem` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`
- `WorkerProfileStaticConfig` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict)`

**文件**: `octoagent/frontend/src/types/index.ts`（或 TypeScript 类型定义文件）

- `AgentProfileItem` 和 `WorkerProfileStaticConfig` TypeScript 类型同步新增 `resource_limits`

### Step 2.4: SKILL.md frontmatter 解析

**文件**: `octoagent/packages/skills/src/octoagent/skills/skill_models.py`

- `SkillMdEntry` 新增 `resource_limits: dict[str, Any] = Field(default_factory=dict, description="资源限制覆盖")`

**文件**: `octoagent/packages/skills/src/octoagent/skills/discovery.py`

- `_parse_skill_file()` 显式提取 frontmatter 中的 `resource_limits` 字段写入 `SkillMdEntry.resource_limits`

### Step 2.5: 限制合并逻辑 + 默认值预设

**文件**: `octoagent/packages/skills/src/octoagent/skills/limits.py`（新文件）

- `_PRESETS: dict[str, dict[str, Any]]` 常量映射（按 Agent 类型定义默认值矩阵）：
  - `butler`: `{"max_steps": 50, "max_budget_usd": 0.50, "max_duration_seconds": 300, "max_tool_calls": 30}`
  - `worker`: `{"max_steps": 30, "max_budget_usd": 0.30, "max_duration_seconds": 180, "max_tool_calls": 20}`
  - `worker_coding`: `{"max_steps": 100, "max_budget_usd": 1.00, "max_duration_seconds": 600, "max_tool_calls": 80}`
  - `worker_research`: `{"max_steps": 60, "max_budget_usd": 0.50, "max_duration_seconds": 300, "max_tool_calls": 40}`
  - `subagent`: `{"max_steps": 15, "max_budget_usd": 0.10, "max_duration_seconds": 60, "max_tool_calls": 10}`
- `get_preset_limits(agent_type: str) -> UsageLimits` — 根据类型返回预设的 UsageLimits
- `merge_usage_limits(base: UsageLimits, *overrides: dict[str, Any]) -> UsageLimits`
- 合并策略：逐字段，后面的非 None/非零值覆盖前面的。`None` 值表示"不覆盖"，`0` 也不覆盖
- 调用链：`merge_usage_limits(preset_base, agent_profile.resource_limits, skill.resource_limits)`

### Step 2.6: LLMService 集成（数据传递链路实现）

**注入端** — **文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`（或 ContextResolver）

- 组装 ContextFrame 时，将 `AgentProfile.resource_limits` 写入 `metadata["resource_limits"]`
- 同时写入 `metadata["worker_archetype"]` 或 `metadata["agent_role"]`（如尚未注入）

**消费端** — **文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

- `_try_call_with_tools()` 中：
  1. 从 `metadata["resource_limits"]` 读取 Profile 级 resource_limits（dict）
  2. 从当前已加载 Skill 的 `SkillMdEntry.resource_limits` 读取 Skill 级覆盖（dict）
  3. 根据 `metadata["worker_archetype"]` / `metadata["agent_role"]` 查找预设 `base = get_preset_limits(agent_type)`，无匹配时 fallback 到 `UsageLimits()`
  4. 调用 `merge_usage_limits(base, profile_rl, skill_rl)` 生成最终 `UsageLimits`
  5. 设置到 `SkillExecutionContext.usage_limits`

### Step 2.7: 单元测试

**文件**: `octoagent/packages/skills/tests/test_limits_merge.py`（新文件）

- 空覆盖 = 基础值不变
- 单字段覆盖
- 多层覆盖优先级（SKILL.md > WorkerProfile > AgentProfile > preset > 全局默认）
- `get_preset_limits()` 各 Agent 类型返回正确预设
- 未知 Agent 类型 fallback 到全局默认
- `None` / `0` 值不覆盖（防止误置零）

---

## Phase 3: 成本熔断器 [P1]

**预估改动量**：1 个文件修改，~20 行（大部分逻辑已在 Phase 0+1 实现）

### Step 3.1: 成本累加已就绪

- Phase 0 已解决数据源（`SkillOutputEnvelope.cost_usd`）
- Phase 1 已在 SkillRunner 中每步累加 `raw_output.cost_usd` 到 `UsageTracker.cost_usd`
- `check_limits()` 使用容差比较 `cost_usd >= max_budget_usd - 1e-9` 避免浮点精度问题

### Step 3.2: 边界条件处理

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`（已在 Phase 1 实现）

- `cost_usd` 为 `0.0`（LiteLLM 未返回成本）时**不触发**熔断（`max_budget_usd is not None` 才检查）
- `max_budget_usd` 为 `None`（未配置）时跳过成本检查

### Step 3.3: 成本回传

- `SkillRunResult.total_cost_usd` 取自 `UsageTracker.cost_usd`（已在 Phase 1 Step 1.2 实现）
- 上层 LLMService 可利用此值做统计

### Step 3.4: 集成测试

**文件**: `octoagent/packages/skills/tests/test_cost_fuse.py`（新文件）

- Mock 一个每步消耗 $0.01 的 Skill，设 `max_budget_usd=0.03`，验证在第 4 步之前终止
- `cost_usd=0.0`（LiteLLM 未返回）时不触发熔断
- 浮点累加精度验证（100 步 × $0.003 = $0.30 ± 容差）

---

## Phase 4: StopHook 自定义停止条件 [P1]

**预估改动量**：3 个文件修改，~80 行

### Step 4.1: 扩展 SkillRunnerHook

**文件**: `octoagent/packages/skills/src/octoagent/skills/hooks.py`

- 新增 `async def should_stop(self, manifest, context, tracker, last_output) -> bool` 方法（**async**，与其他 hook 方法保持一致）
- `NoopSkillRunnerHook` 默认返回 `False`

### Step 4.2: SkillRunner 新增 `_check_stop_hooks()`

**文件**: `octoagent/packages/skills/src/octoagent/skills/runner.py`

- **不能复用 `_call_hook()`**（L395-403，它遍历 hooks 调用方法但**忽略返回值**），需新增独立方法
- `async def _check_stop_hooks(self, manifest, ctx, tracker, output) -> bool`：遍历所有 hook 调用 `should_stop()`，任一返回 `True` 即返回 `True`
- 每步结束后在 `check_limits()` **之后**调用 `_check_stop_hooks()`
- 任一返回 True：标记 `SkillRunStatus.STOPPED`，优雅退出循环

### Step 4.3: LLMService 处理 STOPPED

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`

- STOPPED 状态：如果有最后一次有效 output（`result.output is not None`），返回其 content
- 否则返回 "请求已被停止。"
- **不走 Echo fallback**

### Step 4.4: 单元测试

**文件**: `octoagent/packages/skills/tests/test_stop_hook.py`（新文件）

- `should_stop()` 返回 True 时 SkillRunner 标记 STOPPED + 返回最后有效 output
- `should_stop()` 返回 False 时继续正常执行
- 多个 hook 时任一返回 True 即停止
- STOPPED 状态在 LLMService 中不走 Echo fallback

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
  - 切换 `model_alias` 到 `fallback_model_alias`
  - `max_steps` 放宽为 `min(int(max_steps * 1.5), _MAX_STEPS_HARD_CEILING)`（**clamp 到 500 上限**，避免 Pydantic `le=500` 校验溢出）
  - **`max_budget_usd` 不放宽**：降级后的更大模型 + 更多步数必须在原预算内完成
  - 重新执行一次（标记 `is_degraded_retry=True` 防止递归）
- 降级重试最多一次（`is_degraded_retry=True` 时不再降级）

### Step 5.3: 集成测试

**文件**: `octoagent/packages/skills/tests/test_degradation_retry.py`（新文件）

- FAILED 后降级重试成功返回结果
- 降级重试后再次 FAILED 不再重试（防递归验证）
- `max_steps * 1.5` 被 clamp 到 `_MAX_STEPS_HARD_CEILING`
- `max_budget_usd` 降级后不放宽

---

## Phase 6: Settings 可配置 + 环境变量 [P2]

**预估改动量**：4 个文件修改/新增，~200 行

### Step 6.1: 环境变量读取

**文件**: `octoagent/packages/skills/src/octoagent/skills/models.py`

- `UsageLimits` 的默认值从环境变量读取：
  - `OCTOAGENT_DEFAULT_MAX_STEPS` → `max_steps`
  - `OCTOAGENT_DEFAULT_MAX_BUDGET_USD` → `max_budget_usd`
  - `OCTOAGENT_DEFAULT_MAX_DURATION_SECONDS` → `max_duration_seconds`
- 优先级：Settings UI 配置 > 环境变量 > 代码默认值

### Step 6.2: Settings UI 配置区

**文件**: `octoagent/frontend/src/domains/settings/` 目录下新增资源限制配置组件

- "资源限制"配置卡片
- 字段：max_steps / max_budget_usd / max_duration_seconds / max_tool_calls
- 全局默认值 + per-Agent 选择器
- 保存后通过 Control Plane action 更新 `AgentProfile.resource_limits`

### Step 6.3: 后端 Action

- 新增 `agent_profile.update_resource_limits` action
- 更新 `AgentProfile.resource_limits` 并广播变更
- 新请求立即使用新限制值

### Step 6.4: 单元测试

- 环境变量覆盖代码默认值
- Settings 配置 > 环境变量优先级
- 端到端测试：Settings 修改 → 新请求立即生效

---

## LoopGuardPolicy 迁移清单

| 文件 | 行 | 当前用法 | 迁移方式 |
|------|-----|---------|---------|
| `runner.py` | L95 | `while steps < manifest.loop_guard.max_steps` | 改为 `tracker.check_limits(limits)` |
| `runner.py` | L173 | `manifest.loop_guard.repeat_signature_threshold` | 改读 `limits.repeat_signature_threshold` |
| `runner.py` | L467 | `manifest.loop_guard.max_steps` (emit event) | 改读 `limits.max_steps` |
| `manifest.py` | L29 | `SkillManifest.loop_guard` 字段 | 保留，deprecated |
| `models.py` | L123 | `SkillManifestModel.loop_guard` 字段 | 保留，deprecated + 新增 `resource_limits` |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| SkillOutputEnvelope 新字段可能在某些 LiteLLM 版本下拿不到 token/cost | Phase 0 兜底默认值（空 dict / 0.0），不触发 None 异常；cost_usd=0.0 时不触发成本熔断 |
| 成本计算依赖 LiteLLM 返回的 token 价格，可能不准 | 使用容差比较 `>= budget - 1e-9`；Phase 3 集成测试验证浮点精度 |
| per-Profile 配置增加了理解复杂度 | 提供合理默认值矩阵按 Agent 类型自动应用，用户无需手动配置 |
| 旧 SKILL.md 的 `loop_guard:` 与新 `resource_limits:` 共存 | `LoopGuardPolicy.to_usage_limits()` 自动转换，6 个月后移除 |
| StopHook 可能被滥用导致 Skill 过早终止 | Hook 只能建议停止，STOPPED 状态仍返回最后有效 output |
| 降级重试 max_steps * 1.5 可能超过 Pydantic le=500 校验 | clamp 到 `_MAX_STEPS_HARD_CEILING=500` |
| 新 ErrorCategory 可能触发 Watchdog 误告警 | 需确保 Watchdog error_category 过滤逻辑兼容 4 个新值 |

---

## 依赖

- **Feature 060**（Context Engineering Enhancement）：Phase 1-2 不依赖。Phase 3 的 token 计数可复用 060 的 `ContextBudgetPlanner`
- **LiteLLM Proxy**：成本数据来源（`SkillOutputEnvelope.cost_usd`，需 `stream_options: {"include_usage": true}`）
- **现有 Event Store**：`SKILL_USAGE_REPORT` / `RESOURCE_LIMIT_HIT` 事件存储
- **SkillDiscovery**：SKILL.md frontmatter 解析扩展
- **AgentContextService**：metadata 注入 resource_limits
