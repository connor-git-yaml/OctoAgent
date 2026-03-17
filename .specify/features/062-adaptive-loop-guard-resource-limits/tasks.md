---
feature_id: "062"
title: "Adaptive Loop Guard & Resource Limits — 任务清单"
created: "2026-03-17"
updated: "2026-03-17"
---

# 任务清单

## Phase 0: SkillOutputEnvelope Token/Cost 数据回传 [P0 前置]

- [x] **T0.1** 在 `skills/models.py` 给 `SkillOutputEnvelope` 新增 `token_usage: dict[str, int]` 和 `cost_usd: float` 字段
- [x] **T0.2** 修改 `litellm_client.py` SSE 流式路径：设 `stream_options: {"include_usage": true}`，从最终 chunk 提取 token usage + cost 写入 `SkillOutputEnvelope`
- [x] **T0.3** 修改 `litellm_client.py` Responses API 路径：从 response 提取 usage + cost（替代硬编码 `cost_usd=0.0`）
- [x] **T0.4** 编写 `tests/test_litellm_token_data.py`：SSE / Responses 两条路径均返回 token 数据 + SkillOutputEnvelope 默认值回归

## Phase 1: UsageLimits 模型 + SkillRunner 集成 [P0]

- [x] **T1.1** 在 `skills/models.py` 新增 `_MAX_STEPS_HARD_CEILING = 500` 常量
- [x] **T1.2** 在 `skills/models.py` 新增 `UsageLimits(BaseModel)` 数据类
- [x] **T1.3** 在 `skills/models.py` 新增 `UsageTracker`（**`@dataclass`**，非 BaseModel）+ `check_limits()` + `to_dict()` 方法
- [x] **T1.4** 在 `skills/models.py` 扩展 `ErrorCategory`（TOKEN_LIMIT_EXCEEDED / TOOL_CALL_LIMIT_EXCEEDED / BUDGET_EXCEEDED / TIMEOUT_EXCEEDED）
- [x] **T1.5** 在 `skills/models.py` 新增 `SkillRunStatus.STOPPED`
- [x] **T1.6** 在 `skills/models.py` 扩展 `SkillRunResult`（新增 `usage: dict[str, Any]` + `total_cost_usd: float`）
- [x] **T1.7** 给 `LoopGuardPolicy` 添加 `to_usage_limits()` 转换方法 + `model_validator` deprecated 警告
- [x] **T1.8** 在 `skills/models.py` 给 `SkillExecutionContext` 新增 `usage_limits: UsageLimits`（强类型）
- [x] **T1.9** 在 `skills/models.py` 给 `SkillManifestModel` 新增 `resource_limits: dict[str, Any]` 字段
- [x] **T1.10** 重构 `skills/runner.py` `run()` 方法：创建 `UsageTracker`，替代原有 `steps >= max_steps`（L95）检查
- [x] **T1.11** 在 `runner.py` 中每步累加 `raw_output.token_usage` / `raw_output.cost_usd` / `tool_calls` 到 tracker
- [x] **T1.12** 在 `runner.py` 中将重复签名检查改读 `limits.repeat_signature_threshold`（L173）
- [x] **T1.13** 在 `runner.py` 中 emit 事件时改读 `limits.max_steps`（L467）
- [x] **T1.14** 在 EventType 枚举中新增 `SKILL_USAGE_REPORT` 和 `RESOURCE_LIMIT_HIT`
- [x] **T1.15** 在 `runner.py` 结束时 emit `SKILL_USAGE_REPORT` 事件 + 超限时 emit `RESOURCE_LIMIT_HIT` 事件
- [x] **T1.16** 在 `llm_service.py` 新增 4 种 ErrorCategory 的中文友好提示模板
- [x] **T1.17** 编写 `tests/test_usage_limits.py` 单元测试（各维度独立/组合触发、to_usage_limits、超时、浮点容差、to_dict）
- [x] **T1.18** 编写 SkillRunner 集成测试：不同维度超限时返回正确 ErrorCategory + 友好提示

## Phase 2: per-Profile 差异化配置 [P0]

- [x] **T2.1** `AgentProfile` 新增 `resource_limits: dict[str, Any]` 字段
- [x] **T2.2** `WorkerProfile` 新增 `resource_limits: dict[str, Any]` 字段
- [x] **T2.3** `sqlite_init.py` 添加 schema 迁移（两张表各加一列）
- [x] **T2.4** `control_plane.py` `AgentProfileItem` + `WorkerProfileStaticConfig` 新增 `resource_limits` 字段
- [x] **T2.5** 前端 TypeScript 类型 `AgentProfileItem` + `WorkerProfileStaticConfig` 同步新增 `resource_limits`
- [x] **T2.6** `skill_models.py` `SkillMdEntry` 新增 `resource_limits: dict[str, Any]` 字段
- [x] **T2.7** `discovery.py` `_parse_skill_file()` 显式提取 frontmatter `resource_limits` 字段
- [x] **T2.8** 新建 `skills/limits.py`：实现 `_PRESETS` 常量映射 + `get_preset_limits()` + `merge_usage_limits()` 合并逻辑
- [x] **T2.9** `agent_context.py`（或 ContextResolver）：组装 ContextFrame 时注入 `metadata["resource_limits"]` + `metadata["worker_archetype"]`
- [x] **T2.10** `llm_service.py` 集成：从 metadata 读取 profile_rl + 从 Skill 读取 skill_rl → 查预设 → merge → 设到 `SkillExecutionContext.usage_limits`
- [x] **T2.11** 编写 `tests/test_limits_merge.py` 单元测试（空覆盖 / 单字段 / 多层优先级 / 预设查询 / 未知类型 fallback / None 值不覆盖）

## Phase 3: 成本熔断器 [P1]

- [x] **T3.1** 验证 Phase 0+1 已完成成本数据源 + 累加 + check_limits 逻辑（确认无遗漏）
- [x] **T3.2** 编写 `tests/test_cost_fuse.py` 集成测试（Mock 每步 $0.01 + budget=$0.03 → 第 4 步前终止）
- [x] **T3.3** 编写成本边界测试：cost_usd=0.0 不触发熔断
- [x] **T3.4** 编写浮点精度测试：100 步 × $0.003 = $0.30 ± 容差

## Phase 4: StopHook 自定义停止条件 [P1]

- [x] **T4.1** `hooks.py` 新增 `async def should_stop()` 方法签名 + `NoopSkillRunnerHook` 默认返回 `False`
- [x] **T4.2** `runner.py` 新增 `async def _check_stop_hooks()` 独立方法（不复用 `_call_hook()`，因后者忽略返回值）
- [x] **T4.3** `runner.py` 每步结束后在 `check_limits()` 之后调用 `_check_stop_hooks()`，返回 True 时标记 `STOPPED`
- [x] **T4.4** `llm_service.py` 处理 STOPPED 状态：返回最后有效 output，不走 Echo fallback
- [x] **T4.5** 编写 `tests/test_stop_hook.py`（should_stop True/False、多 hook 任一 True 即停止、STOPPED 不走 Echo）

## Phase 5: 智能降级重试 [P2]

- [x] **T5.1** `RetryPolicy` 新增 `downgrade_scope_on_fail: bool` + `fallback_model_alias: str`
- [x] **T5.2** `llm_service.py` 实现降级逻辑：FAILED → 切模型 → `max_steps = min(int(max_steps * 1.5), _MAX_STEPS_HARD_CEILING)` → **`max_budget_usd` 不放宽** → `is_degraded_retry=True` → 重试一次
- [x] **T5.3** 编写 `tests/test_degradation_retry.py`（降级成功 / 防递归 / max_steps clamp / budget 不放宽）

## Phase 6: Settings 可配置 [P2]

- [x] **T6.1** `UsageLimits` 默认值支持环境变量读取（`OCTOAGENT_DEFAULT_MAX_STEPS` / `_MAX_BUDGET_USD` / `_MAX_DURATION_SECONDS`）
- [x] **T6.2** Settings UI（`domains/settings/` 目录）新增"资源限制"配置区（全局 + per-Agent 选择器）
- [x] **T6.3** 后端 `agent_profile.update_resource_limits` action + 广播变更
- [x] **T6.4** 编写环境变量优先级单元测试（env var > 代码默认值、Settings > env var）
- [x] **T6.5** 端到端测试：Settings 修改 → 新请求立即生效
