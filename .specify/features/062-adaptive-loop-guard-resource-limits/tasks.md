---
feature_id: "062"
title: "Adaptive Loop Guard & Resource Limits — 任务清单"
created: "2026-03-17"
updated: "2026-03-17"
---

# 任务清单

## Phase 1: UsageLimits 模型 + SkillRunner 集成 [P0]

- [ ] **T1.1** 在 `skills/models.py` 新增 `UsageLimits` 数据类
- [ ] **T1.2** 在 `skills/models.py` 新增 `UsageTracker` 数据类 + `check_limits()` 方法
- [ ] **T1.3** 在 `skills/models.py` 扩展 `ErrorCategory`（TOKEN_LIMIT_EXCEEDED / TOOL_CALL_LIMIT_EXCEEDED / BUDGET_EXCEEDED / TIMEOUT_EXCEEDED）
- [ ] **T1.4** 在 `skills/models.py` 扩展 `SkillRunResult`（新增 `usage` + `total_cost_usd`）
- [ ] **T1.5** 给 `LoopGuardPolicy` 添加 `to_usage_limits()` 转换方法
- [ ] **T1.6** 在 `skills/models.py` 给 `SkillExecutionContext` 新增 `usage_limits` 字段
- [ ] **T1.7** 在 `skills/models.py` 给 `SkillManifest` 新增 `resource_limits` 字段
- [ ] **T1.8** 重构 `skills/runner.py` `run()` 方法：创建 `UsageTracker`，替代原有 `steps >= max_steps` 检查
- [ ] **T1.9** 在 `runner.py` 中每步累加 token/cost/tool_calls 到 tracker
- [ ] **T1.10** 编写 `tests/test_usage_limits.py` 单元测试

## Phase 2: per-Profile 差异化配置 [P0]

- [ ] **T2.1** `AgentProfile` 新增 `resource_limits: dict[str, Any]` 字段
- [ ] **T2.2** `WorkerProfile` 新增 `resource_limits: dict[str, Any]` 字段
- [ ] **T2.3** `sqlite_init.py` 添加 schema 迁移（两张表各加一列）
- [ ] **T2.4** 新建 `skills/limits.py`，实现 `merge_usage_limits()` 合并逻辑
- [ ] **T2.5** `llm_service.py` 集成：从 Profile 读取 resource_limits → 合并 → 传入 SkillExecutionContext
- [ ] **T2.6** 编写 `tests/test_limits_merge.py` 单元测试

## Phase 3: 成本熔断器 [P1]

- [ ] **T3.1** `runner.py` 从 LLM 调用结果提取 `cost_usd` 累加到 tracker
- [ ] **T3.2** `check_limits()` 中增加成本检查逻辑
- [ ] **T3.3** `SkillRunResult.total_cost_usd` 取自 tracker
- [ ] **T3.4** 编写成本熔断集成测试

## Phase 4: StopHook 自定义停止条件 [P1]

- [ ] **T4.1** `hooks.py` 新增 `should_stop()` 方法签名 + Noop 默认实现
- [ ] **T4.2** `models.py` 新增 `SkillRunStatus.STOPPED`
- [ ] **T4.3** `runner.py` 每步结束后调用 `should_stop()` hook
- [ ] **T4.4** `llm_service.py` 处理 STOPPED 状态（返回最后有效 output）
- [ ] **T4.5** 编写 StopHook 单元测试

## Phase 5: 智能降级重试 [P2]

- [ ] **T5.1** `RetryPolicy` 新增 `downgrade_scope_on_fail` + `fallback_model_alias`
- [ ] **T5.2** `llm_service.py` 实现降级重试逻辑（FAILED → 切模型 → 重试一次）
- [ ] **T5.3** 编写降级重试集成测试

## Phase 6: Settings 可配置 [P2]

- [ ] **T6.1** `UsageLimits` 默认值支持环境变量读取
- [ ] **T6.2** Settings UI 新增"资源限制"配置区
- [ ] **T6.3** 后端 `agent_profile.update_resource_limits` action
- [ ] **T6.4** 端到端测试：Settings 修改 → 新请求立即生效
