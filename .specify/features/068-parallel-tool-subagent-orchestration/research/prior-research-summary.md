# Feature 064 — 前置调研摘要

> 本文档为 Feature 064 启动前已完成的深度调研的浓缩摘要。
> 完整调研在对话上下文中，覆盖 Claude Code / OpenClaw / Agent Zero / OctoAgent 四系统的源码级对比。

## 调研范围

- 工具调用机制（定义、schema、执行流程）
- 并行工具调用能力
- Subagent / 子代理模型（创建、通信、并行）
- Agent 行动编排（主循环、重试、恢复）
- 上下文管理与压缩
- 后台执行与通知

## 核心发现

### 1. 并行工具调用

| 系统 | 支持情况 | 机制 |
|------|---------|------|
| Claude Code | **支持** | LLM 单次返回多 tool_use block → client Promise.all 并行执行 |
| OpenClaw | 不支持 | 串行处理 tool_use 事件 |
| Agent Zero | 不支持 | 每轮 LLM 只输出单个 JSON（单工具） |
| OctoAgent | **不支持** | SkillRunner._execute_tool_calls() 串行 for 循环 |

### 2. Subagent 模型

| 系统 | 模型 | 并行 | 通信 |
|------|------|------|------|
| Claude Code | 上下文隔离（独立 200K 窗口） | run_in_background 后台并发 | 单向 prompt-in / result-out |
| OpenClaw | 异步 Session spawn | 多子代理并行 | Push-based announce（结果注入父 Session） |
| Agent Zero | 同步递归，单子代理槽位 | 不支持 | 纯文本消息传递 |
| OctoAgent | 数据库记录（spawn/kill） | 不支持 | **无独立执行循环** |

### 3. OctoAgent 独有优势

- **A2A 协议**：6 种消息类型（TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT），完整审计链
- **Task 治理状态**：WAITING_INPUT / WAITING_APPROVAL / PAUSED（A2A 标准无此概念）
- **Event Store**：70+ 事件类型，支持 replay 和 projection 重建
- **SSE Hub**：基于 asyncio.Queue 的 pub/sub，按 task_id 广播
- **ToolBroker side_effect_level**：READ_ONLY / WRITE / DESTRUCTIVE 分级

## 设计方案（P0-P2）

### P0-A: 并行工具调用
- SkillRunner 按 side_effect_level 分桶：READ_ONLY → asyncio.gather()，WRITE → 串行，DESTRUCTIVE → 审批
- 新增 TOOL_BATCH_STARTED/COMPLETED 事件包裹并行批次

### P0-B: 修复工具结果回填
- 用标准 tool role message 替代自然语言摘要

### P1-A: Subagent 独立执行循环
- Subagent 获得独立 SkillRunner + 独立 A2AConversation
- 创建 Child Task（parent_task_id 关联）
- 通过 A2A TASK/HEARTBEAT/RESULT 消息完整审计

### P1-B: Subagent Announce
- 双通道通知：子 Task 事件 + 父 Task A2A_MESSAGE_RECEIVED 冒泡
- SSE Hub 同时广播到两个 task_id

### P2-A: 上下文压缩
- 三级策略：截断大输出 → 摘要早期对话 → 丢弃最老摘要
- 发射 CONTEXT_COMPACTION_COMPLETED 事件

### P2-B: 后台执行 + 通知
- HEARTBEAT 事件作为进度上报
- Telegram 推送审批请求和完成通知

## 关键源码文件

- `packages/skills/src/octoagent/skills/runner.py` — SkillRunner（核心 agent loop）
- `packages/skills/src/octoagent/skills/litellm_client.py` — LLM 交互 + tool_use 解析
- `packages/tooling/src/octoagent/tooling/broker.py` — ToolBroker
- `apps/gateway/src/octoagent/gateway/services/orchestrator.py` — Orchestrator
- `apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` — Subagent CRUD
- `packages/protocol/src/octoagent/protocol/models.py` — A2A 协议模型
- `packages/core/src/octoagent/core/models/enums.py` — TaskStatus 状态机
- `apps/gateway/src/octoagent/gateway/services/sse_hub.py` — SSE Hub
