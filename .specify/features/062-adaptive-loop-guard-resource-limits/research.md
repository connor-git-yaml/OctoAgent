---
feature_id: "062"
title: "Adaptive Loop Guard & Resource Limits — 跨产品对比调研"
created: "2026-03-17"
updated: "2026-03-17"
---

# 调研：循环保护与资源限制的行业实践

## 调研目的

OctoAgent 当前 SkillRunner 的循环保护（`LoopGuardPolicy`）仅依赖写死的 `max_steps=30` + 重复签名检测（`repeat_signature_threshold=3`）。对比行业主流 Agent 框架/产品的做法，识别更灵活、更多维度的资源限制方案，为 Feature 062 提供设计输入。

---

## 一、跨产品对比表

| 维度 | OctoAgent | Claude Code SDK | Pydantic AI | Agent Zero | OpenClaw |
|------|-----------|----------------|-------------|------------|---------|
| **主循环步数限制** | `max_steps=30`（硬写，全局统一） | `max_turns`（默认无限，用户设定） | `max_result_retries=1`（仅重试次数） | 无硬限制，Agent 自行决定何时 stop | 多层配置：global → channel → account → object |
| **Token 消耗限制** | 无 | 无显式限制（依赖 `max_turns` 间接控制） | `UsageLimits.request_tokens` / `response_tokens`（三层检查：请求前/请求后/完整响应后） | 无（依赖上下文压缩保持窗口内） | 无 |
| **工具调用次数限制** | 无（通过 max_steps 间接限制） | 无 | `UsageLimits.max_tool_calls`（递增计数，超限终止） | 无 | 无 |
| **成本/预算限制** | 无 | `max_budget_usd`（按 token 价格累加，到达后终止） | 无内置（可通过 `result_validator` 模拟） | 无 | 无 |
| **超时限制** | 无运行时超时 | 无全局超时（个别工具有 timeout） | 无内置超时 | 无全局超时 | 无 |
| **自定义停止条件** | 无 | `Stop` hook（`StopHook` 接口，每轮调用，返回 stop/continue） | `result_validator`（每次工具结果后调用） | Agent 内置 `response_tool`（LLM 主动调用表示"我完成了"） | 无 |
| **重复检测** | `repeat_signature_threshold=3`（连续 N 次相同工具签名） | 无内置 | 无内置 | 无内置（依赖 Agent 自我纠错） | 无 |
| **模型降级/Fallback** | FallbackManager: primary → echo（仅错误降级） | 无内置 | 无内置 | `get_chat_response()` 里有重试，无自动降级 | 支持多模型配置，按优先级 fallback |
| **per-Agent/per-Skill 差异化** | 全局统一 `LoopGuardPolicy` | 全局 `max_turns`，无 per-tool 差异 | 每次 `agent.run()` 可传不同 `UsageLimits` | 全局统一 | 多层覆盖：global < channel < account < object |
| **运行时可调** | 无（启动时固定） | 启动参数 | 每次 run 传入 | 无 | 管理后台实时调整 |

---

## 二、各产品详细分析

### 2.1 Claude Code SDK（`claude_agent_sdk`）

**核心机制**：
- `max_turns`：Agent 执行的最大轮数（一轮 = 一次 LLM 调用 + 工具执行），默认不限。调用方按任务复杂度设定
- `max_budget_usd`：按 token 价格累加消耗，达到预算上限后优雅终止。**唯一内置的成本防护**
- `Stop` Hook：`StopHook` 接口，每轮结束后调用。可基于输出内容、累计 token、外部条件等决定是否提前终止。比固定步数限制灵活得多

**亮点**：
- **成本维度是一等公民**：不只限制步数，还直接限制美元消耗
- **Hook 扩展性**：Stop 条件完全用户自定义，框架不预设策略
- **无冗余限制**：没有"重复检测"之类的 heuristic，认为 LLM 应该自己处理

**局限**：
- 没有 token 级别细粒度限制（不像 Pydantic AI 分 request/response tokens）
- 没有工具调用次数限制

### 2.2 Pydantic AI

**核心机制**：
- `UsageLimits` 数据类，支持四个维度：
  - `request_limit`：最大请求次数（≈ max_turns）
  - `request_tokens_limit`：累计输入 token 上限
  - `response_tokens_limit`：累计输出 token 上限
  - `max_tool_calls`：累计工具调用次数上限（v0.0.41+ 新增）
- **三层检查点**：
  1. 请求前（pre-request）：检查 request_limit
  2. 请求后（post-request）：检查 token 累计
  3. 完整响应后（post-response）：检查工具调用累计
- 超限时抛 `UsageLimitExceeded` 异常，调用方决定如何处理

**亮点**：
- **多维度正交**：步数、token、工具调用独立限制，任一触发即终止
- **可组合**：每次 `agent.run()` 传不同 `UsageLimits`，无需全局配置
- **token 分离**：input/output 分开限制，防止模型生成过长响应

**局限**：
- 没有成本（美元）维度
- 没有自定义停止条件 hook（依赖 `result_validator`，但那更多是数据校验而非停止策略）

### 2.3 Agent Zero

**核心机制**：
- **无硬限制**：主循环没有 max_steps，Agent 通过调用 `response_tool`（一个特殊工具）来表示"我完成了"
- **自我管理**：Agent 的 system prompt 里明确指示何时应该停止
- **上下文压缩兜底**：即使 Agent 不停，上下文压缩会确保不超 token 窗口

**亮点**：
- **Agent 自治**：不用外部限制截断，Agent 自己决定何时够了
- **无误杀**：不会因为固定步数把正在正常工作的 Agent 截断

**局限**：
- **无兜底安全网**：如果 Agent 进入死循环或 hallucinate 不调用 response_tool，没有硬限制可以停止
- **成本不可控**：没有预算限制，长时间运行可能消耗大量 token

### 2.4 OpenClaw

**核心机制**：
- **多层配置覆盖**：global → channel → account → object，每一层可以覆盖上一层的限制
- **配置化而非代码化**：限制参数在管理后台配置，运行时生效
- **按 Agent/渠道差异化**：不同 Agent、不同渠道可以有不同的限制

**亮点**：
- **运维友好**：不需要改代码/重启就能调整限制
- **多租户感知**：天然支持不同用户/Agent 不同限制

**局限**：
- 限制维度较少（主要是步数/轮数）
- 没有 token 或成本维度

---

## 三、关键洞察

### 3.1 OctoAgent 当前的核心问题

1. **单维度**：只有 `max_steps` 一个硬限制，没有 token、成本、工具调用次数等维度
2. **全局统一**：所有 Agent/Skill 共用同一套参数，无法差异化（简单问答 vs 复杂编码任务需要的步数天差地别）
3. **不可运行时调整**：参数写死在代码默认值里，调整需要改代码重启
4. **无成本防护**：缺少美元级别的预算限制，长时间运行的 Worker 成本不可控
5. **Echo 降级太粗暴**：SKILL_FAILED 后的 fallback 链最终到 Echo，用户体验差（已部分修复为友好错误提示）
6. **缺少自定义停止条件**：无法根据输出内容、外部条件等灵活决定是否继续

### 3.2 推荐借鉴路径

| 优先级 | 借鉴来源 | 机制 | 适用场景 |
|--------|---------|------|---------|
| **P0** | Pydantic AI | 多维度 `UsageLimits`（steps + tokens + tool_calls） | 所有 SkillRunner 执行 |
| **P0** | OpenClaw | per-Profile 差异化配置（AgentProfile.loop_guard） | 不同 Agent 不同限制 |
| **P1** | Claude SDK | `max_budget_usd` 成本熔断 | Worker 长任务防护 |
| **P1** | Claude SDK | `StopHook` 自定义停止条件 | 主 Agent 监督决策 |
| **P2** | Agent Zero | Agent 自主停止（response_tool） | 成熟 Worker 自治模式 |
| **P2** | Pydantic AI | token 分离（input/output 独立限制） | 防止模型生成过长响应 |
| **P3** | OpenClaw | 运行时可调（管理后台/API） | 运维阶段动态调参 |

---

## 四、OctoAgent 现有阈值全景

> 以下为代码库中所有硬编码/默认阈值的完整清单。

### 4.1 SkillRunner 层

| 参数 | 文件 | 当前值 | 范围 | 用途 |
|------|------|--------|------|------|
| `LoopGuardPolicy.max_steps` | `skills/models.py:48` | **30** | [1, 200] | 最大工具调用步数 |
| `LoopGuardPolicy.repeat_signature_threshold` | `skills/models.py:49` | **3** | [2, 20] | 重复签名检测阈值 |
| `RetryPolicy.max_attempts` | `skills/models.py:40` | **3** | [1, 20] | 模型调用重试次数 |
| `RetryPolicy.backoff_ms` | `skills/models.py:41` | **500** | [0, 60000] | 重试退避毫秒 |
| `ContextBudgetPolicy.max_chars` | `skills/models.py:53` | **1500** | [200, 50000] | 工具反馈截断字符数 |
| `ContextBudgetPolicy.summary_chars` | `skills/models.py:54` | **240** | [50, 2000] | 工具反馈摘要字符数 |

### 4.2 上下文压缩层

| 参数 | 文件 | 当前值 | 用途 |
|------|------|--------|------|
| `max_input_tokens` | `context_compaction.py:35` | **6000** | 模型输入 token 上限 |
| `soft_limit_ratio` | `context_compaction.py:44` | **0.75** | 触发压缩阈值 |
| `target_ratio` | `context_compaction.py:52` | **0.55** | 压缩目标 |
| `recent_turns` | `context_compaction.py:60` | **2** | 保留最近轮数 |
| `min_turns_to_compact` | `context_compaction.py:65` | **4** | 触发压缩最小轮数 |
| `async_compaction_timeout` | `context_compaction.py:79` | **10.0s** | 异步压缩超时 |

### 4.3 全局 Token 预算规划

| 参数 | 文件 | 当前值 | 用途 |
|------|------|--------|------|
| `_SYSTEM_BLOCKS_BASE` | `context_budget.py:60` | **1800** tokens | 系统块基础开销 |
| `_SESSION_REPLAY_BUDGET` | `context_budget.py:62` | **400** tokens | 会话回放预留 |
| `_SKILL_PER_ENTRY` | `context_budget.py:64` | **250** tokens | 每个 Skill token 消耗 |
| `_MEMORY_PER_HIT` | `context_budget.py:66` | **60** tokens | 每个 memory 命中 token |
| `_MIN_CONVERSATION_BUDGET` | `context_budget.py:69` | **800** tokens | 对话预算下限 |

### 4.4 Watchdog 监控

| 参数 | 文件 | 当前值 | 环境变量 | 用途 |
|------|------|--------|---------|------|
| `scan_interval_seconds` | `watchdog/config.py:20` | **15** | `WATCHDOG_SCAN_INTERVAL_SECONDS` | 扫描周期 |
| `no_progress_cycles` | `watchdog/config.py:28` | **3** | `WATCHDOG_NO_PROGRESS_CYCLES` | 无进展判定周期 |
| `cooldown_seconds` | `watchdog/config.py:34` | **60** | `WATCHDOG_COOLDOWN_SECONDS` | 告警冷却时间 |
| `failure_window_seconds` | `watchdog/config.py:40` | **300** | `WATCHDOG_FAILURE_WINDOW_SECONDS` | 失败统计窗口 |
| `repeated_failure_threshold` | `watchdog/config.py:46` | **3** | `WATCHDOG_REPEATED_FAILURE_THRESHOLD` | 重复失败阈值 |

### 4.5 MCP 连接池

| 参数 | 文件 | 当前值 | 用途 |
|------|------|--------|------|
| `_INIT_TIMEOUT_S` | `mcp_session_pool.py:24` | **10** | 初始化超时 |
| `_HEALTH_CHECK_TIMEOUT_S` | `mcp_session_pool.py:25` | **5** | 健康检查超时 |
| `_RECONNECT_MAX_ATTEMPTS` | `mcp_session_pool.py:27` | **3** | 重连尝试次数 |

### 4.6 核心常量

| 参数 | 文件 | 当前值 | 用途 |
|------|------|--------|------|
| `EVENT_PAYLOAD_MAX_BYTES` | `core/config.py:34` | **8192** | 事件 payload 上限 |
| `ARTIFACT_INLINE_THRESHOLD` | `core/config.py:39` | **4096** | Artifact 内联阈值 |
| `SSE_HEARTBEAT_INTERVAL` | `core/config.py:44` | **15** | SSE 心跳间隔 |
| `MESSAGE_PREVIEW_LENGTH` | `core/config.py:49` | **200** | 消息预览截断 |
