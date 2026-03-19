# Feature 064 实施后 — 四系统能力对比总结

> 生成时间: 2026-03-19
> 对比对象: Claude Code / OpenClaw / Agent Zero / OctoAgent（Feature 064 后）

---

## 一、核心能力矩阵

| 维度 | Claude Code | OpenClaw | Agent Zero | OctoAgent（Feature 064 后） |
|------|-------------|----------|------------|---------------------------|
| 工具定义 | LLM API 原生 function calling | TypeBox JSON Schema + execute fn | Prompt-driven 纯文本 JSON | Pydantic `@tool_contract` + JSON Schema 反射 |
| 工具并行 | LLM 多 tool_use → Promise.all | 不支持 | 不支持 | LLM 多 tool_calls → SideEffectLevel 分桶 → asyncio.gather |
| 并行安全策略 | 双层：LLM 决定依赖 + client 约束只读/写 | — | — | 三桶：NONE 并行 / REVERSIBLE 串行 / IRREVERSIBLE 审批串行 |
| 并行事件审计 | 无 | — | — | TOOL_BATCH_STARTED/COMPLETED 事件 + 每个工具独立 TOOL_CALL 事件 |
| 工具结果回填 | 标准 tool role message | PI SDK 内置 | 纯文本 JSON 拼接 | 标准 tool role message（Chat Completions + Responses API 双路径） |
| Subagent 模型 | 上下文隔离（独立 200K 窗口） | 异步 Session spawn | 同步递归，单子代理槽位 | Child Task + 独立 SkillRunner + A2A 全链路审计 |
| Subagent 并行 | run_in_background 后台并发 | spawn 多个异步子代理 | 不支持 | asyncio.Task 异步运行，不阻塞父 Worker |
| Subagent 通信 | 单向 prompt-in / result-out（无协议） | Announce 推送（注入 user message，无结构化协议） | 纯文本消息传递 | A2A 6 种消息类型（TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT）+ 双重审计 |
| Subagent 取消 | 不支持 | session_send 发消息 | 不支持 | A2A CANCEL 消息 + asyncio.Task.cancel() + Task→CANCELLED |
| Subagent 进度 | 无 | 无结构化进度 | 无 | TASK_HEARTBEAT 事件（可配置间隔） |
| 结果通知 | Hook 系统通知 | Push-based announce（注入 session） | 无 | A2A RESULT → 父 Task 事件冒泡 → SSE 双路广播 → SubagentResultQueue FIFO 注入 |
| 上下文压缩 | subagent 独立窗口隔离 | Compaction 自动摘要 | 三级压缩（topic→bulk→discard） | 三级压缩（截断大输出→LLM 摘要→丢弃最老）+ CONTEXT_COMPACTION 事件 |
| 后台通知 | Hook + Desktop Notification | Cron delivery（announce/webhook） | DeferredTask 线程 | NotificationService 多渠道分发 + Telegram inline keyboard 审批 + 去重 |
| 审批门禁 | 无 | 两阶段注册-等待 | 无 | WAITING_APPROVAL 状态 + 跨渠道审批（Telegram/Web）+ Policy Profile |
| Task 状态机 | 无 | 无结构化状态机 | 无持久化状态 | 10 状态（含 WAITING_INPUT/WAITING_APPROVAL/PAUSED 治理状态） |
| 事件溯源 | 无 | JSONL transcript（平面文件） | 无 | 70+ EventType + SQLite Event Store + replay/projection 重建 |
| 断线恢复 | 无 | JSONL + resubscribe | 无（进程重启丢失） | Event Store replay + Checkpoint resume + SSE 断线重连 |

---

## 二、并行工具调用深度对比

| 维度 | Claude Code | OctoAgent |
|------|-------------|-----------|
| 并行决策者 | LLM 判断依赖关系（训练内置） | LLM 返回多 tool_calls + 系统按 SideEffectLevel 分桶 |
| 安全约束 | client 侧区分只读/写（推断） | 三级分桶：NONE→并行 / REVERSIBLE→串行 / IRREVERSIBLE→审批 |
| 未知工具处理 | 未知 | fail-closed 为 IRREVERSIBLE（最高风险） |
| 结果顺序 | 按 tool_use block 顺序 | 按原始 tool_calls 顺序（`call_index_map` O(1) 映射） |
| 失败隔离 | Promise.allSettled 隔离 | `asyncio.gather(return_exceptions=True)` 隔离 |
| 审计 | 无 | TOOL_BATCH_STARTED/COMPLETED 包裹 + 每个工具独立 TOOL_CALL 事件 |
| 审批集成 | 无 | IRREVERSIBLE 工具触发 PresetBeforeHook → WAITING_APPROVAL |

OctoAgent 的差异化：Claude Code 的并行是"快但不可审计"，OctoAgent 是"快且可审计可审批"。

---

## 三、Subagent 架构深度对比

| 维度 | Claude Code | OpenClaw | Agent Zero | OctoAgent |
|------|-------------|----------|------------|-----------|
| 隔离级别 | 上下文隔离（独立 200K 窗口） | Session 隔离（独立 transcript） | 共享 AgentContext（仅 history 独立） | Task 隔离 + 上下文隔离 + A2A 协议隔离 |
| 通信协议 | 无（prompt in, result out） | 无（注入 user message） | 无（函数返回值） | A2A 6 种消息类型 + 幂等保护 + replay protection |
| 生命周期管理 | 无持久化 | SubagentRunRecord 内存 Map | Agent 实例引用 | Child Task（SQLite 持久化）+ AgentRuntime + AgentSession + A2AConversation |
| 取消能力 | 不支持 | session_send（非结构化） | 不支持 | A2A CANCEL → asyncio.Task.cancel() → Task CANCELLED |
| 进度上报 | 无 | 无 | 无 | TASK_HEARTBEAT 事件（含 loop_step/max_steps/summary） |
| 结果投递 | 返回摘要字符串 | Announce 注入 user message | 返回 response 文本 | A2A RESULT → 事件冒泡 → SSE 双路广播 → SubagentResultQueue → 父 Worker 注入 |
| 失败处理 | 父 agent 收到错误字符串 | announce 错误 | 异常被吞掉 | A2A ERROR → Child Task FAILED + 事件审计 → 父 Worker 收到结构化错误 |
| 资源限制 | max_turns 参数 | runTimeoutSeconds | 无 | 独立 UsageLimits（max_steps/max_duration_seconds/max_tokens） |
| 权限控制 | 工具子集（按 agent type） | Subagent Policy 受限工具集 | 继承父 agent 全部权限 | 继承父 Worker PermissionPreset + 支持 spawn 时降级 |
| 嵌套深度 | 不支持嵌套 | MAX_SPAWN_DEPTH 限制 | 无限递归 | hop_count/max_hops 控制（默认最大 3 跳） |
| 参数传递 | Agent tool 的 prompt string | SpawnSubagentParams + Context 配置对象 | 2 个参数（message + reset） | SubagentSpawnParams + SubagentSpawnContext 配置对象 |
| 状态枚举 | 无 | const + union type | 无 | SubagentOutcome(StrEnum) |

OctoAgent 的差异化：其他系统的 Subagent 是"Fire and hope"，OctoAgent 是"Fire, monitor, audit, cancel, recover"。

---

## 四、上下文管理对比

| 维度 | Claude Code | OpenClaw | Agent Zero | OctoAgent |
|------|-------------|----------|------------|-----------|
| 压缩策略 | 无（靠独立 200K 窗口） | Compaction（LLM 摘要，单级） | 三级压缩 | 三级压缩（Level1 截断→Level2 LLM 摘要→Level3 丢弃最老） |
| 触发机制 | 自动 | 自动 | 自动 | 自动（threshold_ratio 默认 80%，可配置） |
| 压缩模型 | 内置 | 同主模型 | 同主模型 | 可配置独立模型（compaction_model_alias），不消耗主模型预算 |
| 不可压缩保护 | 无 | 无 | 保留首尾消息 | system prompt + 最近一轮 user/assistant 永不压缩 |
| 失败降级 | 不适用 | 超时保护 | 无 | 压缩失败→简单截断降级 + CONTEXT_COMPACTION_FAILED 事件 |
| 事件审计 | 无 | 无 | 无 | CONTEXT_COMPACTION_COMPLETED / FAILED 事件 |
| 回滚方案 | 不适用 | 无 | 无 | threshold_ratio=1.0 时永不触发 |
| 历史清理 | session 结束回收 | 45s TTL cache | GC 自然回收 | task 终态 pop + maxsize=100 兜底 |
| HTTP 连接 | SDK 内部 | 全局 undici dispatcher | 委托 LiteLLM SDK | per-instance 长生命周期 httpx.AsyncClient |

---

## 五、通知与审批对比

| 维度 | Claude Code | OpenClaw | Agent Zero | OctoAgent |
|------|-------------|----------|------------|-----------|
| 通知架构 | Hook 系统 | Cron delivery | notify_user 工具 | NotificationService + NotificationChannelProtocol 多渠道 |
| 渠道支持 | Desktop Notification | Telegram/Discord/Slack | Web UI 弹窗 | SSE（Web UI）+ Telegram + 可扩展 Protocol |
| 审批交互 | 终端 y/n prompt | 两阶段注册-等待 | 无 | Telegram inline keyboard（批准/拒绝）+ WAITING_APPROVAL 状态 |
| 通知去重 | 无 | 无 | 无 | (task_id, event_type) 去重 + 10K 上限防泄漏 |
| 降级策略 | Hook 失败不影响主流程 | 无 | 无 | channel 不可用→仅记录日志（Constitution #6） |

---

## 六、代码质量与工程实践对比

| 维度 | Claude Code | OpenClaw | Agent Zero | OctoAgent |
|------|-------------|----------|------------|-----------|
| 事件发射 | 无统一抽象 | emitAgentEvent() 全局总线 | context.log.log() 统一入口 | emit_task_event() 统一 helper（packages/core 层） |
| HTTP 连接 | SDK 内部管理 | 全局 undici dispatcher | 委托 LiteLLM SDK | per-instance httpx.AsyncClient + close() 生命周期 |
| 状态类型 | 不适用 | const + union type | 无状态 | StrEnum（SubagentOutcome/CompactionStrategy/TaskStatus） |
| 参数管理 | Agent tool 单个 prompt | 配置对象分层 | 极简 2 参数 | Pydantic dataclass 分包（Params + Context） |
| 内存管理 | session 结束回收 | 45s TTL cache + cleanup | GC 自然回收 | task 终态 pop + maxsize 兜底 + Queue drain 清理 + 去重集合上限 |

---

## 七、总结

| 系统 | 定位 | 核心优势 | 核心短板 |
|------|------|---------|---------|
| Claude Code | 开发者 CLI 工具 | 并行速度快、subagent 上下文隔离优雅 | 无审计、无审批、无持久化、不可恢复 |
| OpenClaw | 个人 AI 助手 | 成熟容错（Auth 轮转 + Model Fallback）、Cron 自动化 | 工具串行、无结构化 subagent 协议、无治理状态 |
| Agent Zero | 开源 Agent 框架 | 极简易扩展、Profile 覆盖体系优雅 | 完全串行、单子代理、无持久化、无事件溯源 |
| OctoAgent | 个人智能操作系统 | 可控+可观测+可恢复：A2A 协议审计、Task 治理状态、Event Store 溯源、并行+审批融合 | 尚无模型 Fallback 链、Cron 自动化待完善 |

Feature 064 后，OctoAgent 在工具并行和 Subagent 编排上已达到或超过 Claude Code / OpenClaw 的能力水平，同时保持了其他系统不具备的结构化协议治理 + 事件溯源 + 审批门禁三重保障。这是"个人 AI OS"与"聊天工具"的根本区别——不只是快，而是每一步都可审计、可中断、可恢复。

---

## 附：Feature 064 实现统计

| 指标 | 数值 |
|------|------|
| 总 Task 数 | 22（P0: 8 / P1: 9 / P2: 5）+ 5 项代码质量优化 |
| 总 FR 数 | 36 条（全覆盖） |
| 新增测试 | 103 个 |
| 测试总数 | 1412 passed, 0 failed |
| 新增文件 | 10 个 |
| 修改文件 | 18 个 |
| 净增代码 | ~2800 行（含测试） |
