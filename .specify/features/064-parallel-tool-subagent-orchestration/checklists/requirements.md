# Feature 064 — 需求质量检查表

**Feature**: 并行工具调用 + Subagent 编排增强
**Spec Version**: 2026-03-19 Draft
**Preset**: quality-first
**检查日期**: 2026-03-19

---

## 1. Constitution 合规性

逐项检查 spec 是否符合项目宪法 14 条原则。

| # | 原则 | 状态 | 说明 |
|---|------|------|------|
| 1 | **Durability First** | **PASS** | FR-064-13 Child Task 通过 `parent_task_id` 持久化关联；FR-064-20 Subagent 异常退出自动清理到 FAILED 终态；NFR-064-07 要求进程崩溃后孤儿 Subagent 自动流转到 FAILED。Event Store append-only 保证事件不丢失 |
| 2 | **Everything is an Event** | **PASS** | FR-064-05/06 新增 TOOL_BATCH 事件包裹并行批次；FR-064-17 TASK_HEARTBEAT 事件上报进度；FR-064-22 A2A_MESSAGE_RECEIVED 事件冒泡到父 Task；FR-064-29 CONTEXT_COMPACTION_COMPLETED 事件记录压缩操作。所有新增操作均有对应事件 |
| 3 | **Tools are Contracts** | **PASS** | FR-064-01 明确基于 `SideEffectLevel` 分桶，依赖工具声明的副作用等级做安全决策；FR-064-11 扩展 `ToolCallSpec` 模型保持 schema 一致性 |
| 4 | **Side-effect Two-Phase** | **PASS** | FR-064-04 `IRREVERSIBLE` 工具串行执行并触发 WAITING_APPROVAL 流程（复用 Feature 061 PresetBeforeHook），符合 Plan → Gate → Execute 模式 |
| 5 | **Least Privilege** | **PASS** | FR-064-16 明确 Subagent 继承父 Worker 的 `permission_preset`，权限不高于父 Worker |
| 6 | **Degrade Gracefully** | **PASS** | FR-064-35 Notification Channel 支持多 channel 注册，channel 不可用时降级；FR-064-07 并行工具中单个失败不影响其他结果；风险表中 Telegram 通知失败仅记录日志不影响 Task 执行 |
| 7 | **User-in-Control** | **PASS** | FR-064-04 DESTRUCTIVE 工具自动触发审批；FR-064-18 父 Worker 可通过 CANCEL 取消 Subagent；FR-064-33 Telegram 审批请求含批准/拒绝按钮 |
| 8 | **Observability** | **PASS** | FR-064-05/06 TOOL_BATCH 事件；FR-064-17 HEARTBEAT 事件；FR-064-29 CONTEXT_COMPACTION 事件；FR-064-34 Web UI 实时展示进度；SC-064-08 要求 Subagent 全生命周期可在 Task Detail 页面查看 |
| 9 | **不猜关键配置** | **PASS** | 并行分桶基于工具声明的 `SideEffectLevel`（从 ToolBroker registry 查询），非猜测；上下文压缩阈值可配置 |
| 10 | **Bias to Action** | **PASS** | FR-064-24 Subagent 结果自动注入父 Worker 对话触发下一轮循环；FR-064-25 父 Worker spawn 后继续自己的工作不阻塞 |
| 11 | **Context Hygiene** | **PASS** | FR-064-26~31 完整的上下文压缩策略：截断大输出 → 早期对话摘要 → 丢弃最老摘要；FR-064-31 保护 system prompt 不被压缩 |
| 12 | **记忆写入治理** | **N/A** | 本 Feature 不涉及 Memory/SoR 写入操作，无需检查 |
| 13 | **失败可解释** | **WARN** | FR-064-20 Subagent 异常退出时 Child Task 流转到 FAILED 并发送 A2A ERROR 消息，但 **spec 未明确要求 ERROR 消息必须包含失败分类（模型/解析/工具/业务）和恢复路径建议**。建议在 FR-064-20 验收标准中补充：ERROR 消息 payload 包含 `error_category` 和 `recovery_hint` |
| 13A | **上下文优先于硬策略** | **PASS** | 并行分桶基于工具声明的 `SideEffectLevel` 而非硬编码规则；上下文压缩通过可配置阈值和策略实现而非固定行为 |
| 14 | **A2A 协议兼容** | **PASS** | FR-064-14 创建 A2AConversation；FR-064-19 支持 UPDATE(input-required)；FR-064-21 RESULT 消息；NG5 明确不修改 A2A 协议模型定义，复用现有 6 种消息类型 |

---

## 2. FR 完整性（验收标准检查）

逐个 FR 检查是否具备明确、可测试的验收标准。

### P0-A: 并行工具调用

| FR | 状态 | 说明 |
|----|------|------|
| FR-064-01 | **PASS** | 验收标准明确：单元测试验证分桶逻辑，三种桶映射关系清晰 |
| FR-064-02 | **PASS** | 验收标准量化：总耗时 < 最慢单个调用 x 1.2，返回顺序与输入顺序一致 |
| FR-064-03 | **PASS** | 验收标准明确：2 个 WRITE 工具按序执行，第一个完成后再开始第二个 |
| FR-064-04 | **PASS** | 验收标准明确：触发审批流程，审批通过后执行。复用已有机制 |
| FR-064-05 | **PASS** | 验收标准明确：payload 字段列举完整（batch_id、tool_names、execution_mode） |
| FR-064-06 | **PASS** | 验收标准明确：嵌套事件流结构清晰 |
| FR-064-07 | **PASS** | 验收标准明确：部分失败场景下结果保留策略清晰 |
| FR-064-08 | **PASS** | 验收标准明确：三桶执行顺序固定（READ → WRITE → DESTRUCTIVE） |

### P0-B: 工具结果回填

| FR | 状态 | 说明 |
|----|------|------|
| FR-064-09 | **PASS** | 验收标准明确：标准 OpenAI tool role message 格式，含 tool_call_id |
| FR-064-10 | **PASS** | 验收标准明确：Responses API 路径的 function_call_output type |
| FR-064-11 | **PASS** | 验收标准明确：ToolCallSpec 扩展字段，传递链路清晰 |
| FR-064-12 | **PASS** | 验收标准明确：向后兼容回退逻辑清晰 |

### P1-A: Subagent 独立执行

| FR | 状态 | 说明 |
|----|------|------|
| FR-064-13 | **PASS** | 验收标准明确：Child Task 关联关系清晰 |
| FR-064-14 | **PASS** | 验收标准明确：A2AConversation 创建和 TASK 消息发送 |
| FR-064-15 | **PASS** | 验收标准明确：独立 asyncio.Task、不阻塞父 Worker |
| FR-064-16 | **PASS** | 验收标准明确：权限继承规则清晰 |
| FR-064-17 | **PASS** | 验收标准明确：心跳间隔可配置，事件包含 loop_step 和 summary |
| FR-064-18 | **PASS** | 验收标准明确：A2A CANCEL 消息 → 优雅停止 → CANCELLED 状态 |
| FR-064-19 | **PASS** | 验收标准明确：input-required 流程完整 |
| FR-064-20 | **WARN** | 验收标准基本完整但缺少失败分类。A2A ERROR 消息 payload 应包含 `error_category`（模型/工具/业务/系统）和 `recovery_hint`，以符合 Constitution #13 |

### P1-B: Subagent Announce

| FR | 状态 | 说明 |
|----|------|------|
| FR-064-21 | **PASS** | 验收标准明确：RESULT 消息字段列举完整 |
| FR-064-22 | **PASS** | 验收标准明确：事件冒泡到父 Task |
| FR-064-23 | **PASS** | 验收标准明确：双路广播逻辑清晰 |
| FR-064-24 | **PASS** | 验收标准明确：结果注入 → 触发下一轮循环 |
| FR-064-25 | **WARN** | 验收标准描述"按到达顺序注入"，但 **未定义并发注入时的互斥/序列化机制**。如果两个 Subagent 近乎同时完成，对话历史的写入可能产生竞态。建议补充：结果注入操作需通过 asyncio.Lock 或队列序列化 |

### P2-A: 上下文压缩

| FR | 状态 | 说明 |
|----|------|------|
| FR-064-26 | **PASS** | 验收标准明确：token 计数方法可配置，默认近似方案 |
| FR-064-27 | **PASS** | 验收标准明确：阈值可配置，默认 80% |
| FR-064-28 | **PASS** | 验收标准明确：三级压缩策略依序执行，每级后重新检测 |
| FR-064-29 | **PASS** | 验收标准明确：事件 payload 字段完整 |
| FR-064-30 | **PASS** | 验收标准明确：独立模型 alias 可配置 |
| FR-064-31 | **PASS** | 验收标准明确：保护区域定义清晰 |

### P2-B: 后台通知

| FR | 状态 | 说明 |
|----|------|------|
| FR-064-32 | **PASS** | 验收标准明确：终态推送通知 |
| FR-064-33 | **PASS** | 验收标准明确：Telegram inline keyboard 审批交互 |
| FR-064-34 | **WARN** | 验收标准中要求心跳事件包含"预估完成度"，但 **FR-064-17 的 HEARTBEAT 事件定义中只包含 `loop_step` 和 `summary`，未包含预估完成度字段**。需要对齐：要么在 FR-064-17 补充预估完成度字段，要么在 FR-064-34 中移除该期望 |
| FR-064-35 | **PASS** | 验收标准明确：协议签名完整，降级要求清晰 |
| FR-064-36 | **PASS** | 验收标准明确：基于 event_id 幂等去重 |

---

## 3. 跨 FR 一致性

检查 FR 之间是否存在矛盾、重叠或缺口。

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 3.1 | FR-064-02 (并行执行) vs FR-064-08 (分桶顺序) | **PASS** | 无矛盾。FR-064-02 描述并行组的执行方式，FR-064-08 描述三个桶之间的执行顺序，互为补充 |
| 3.2 | FR-064-04 (IRREVERSIBLE 审批) vs FR-064-08 (分桶顺序) | **PASS** | 一致。DESTRUCTIVE 排在最后，先完成 READ/WRITE 再审批执行 |
| 3.3 | FR-064-09 (Chat Completions 回填) vs FR-064-10 (Responses API 回填) | **PASS** | 两条路径独立互不干扰，分别处理不同 API 格式 |
| 3.4 | FR-064-12 (向后兼容) vs FR-064-09 (标准格式) | **PASS** | 无矛盾。`tool_call_id` 为空时回退到旧模式，否则使用新格式 |
| 3.5 | FR-064-17 (Subagent HEARTBEAT) vs FR-064-34 (Web UI 心跳展示) | **WARN** | **字段不一致**。FR-064-17 定义心跳包含 `loop_step` + `summary`，FR-064-34 要求展示"预估完成度"。心跳事件定义中缺少 `estimated_progress` 字段。参见 2.P2-B.FR-064-34 |
| 3.6 | FR-064-15 (Subagent 独立 asyncio.Task) vs FR-064-25 (多 Subagent 并行) | **PASS** | 一致。每个 Subagent 为独立 asyncio.Task，天然支持并行执行 |
| 3.7 | FR-064-18 (CANCEL) vs FR-064-20 (异常退出) | **PASS** | 两条终止路径互补：主动取消 → CANCELLED，异常退出 → FAILED |
| 3.8 | FR-064-22 (事件冒泡) vs FR-064-23 (SSE 双路广播) | **PASS** | 互补。FR-064-22 处理 Event Store 层面的冒泡，FR-064-23 处理 SSE 实时推送层面的双路广播 |
| 3.9 | FR-064-24 (结果注入对话) vs FR-064-25 (多 Subagent 并行注入) | **WARN** | 参见 2.P1-B.FR-064-25。并发注入缺少序列化机制定义。FR-064-24 描述单个注入，FR-064-25 描述多个并行，但未定义多个同时到达时的处理顺序保证 |
| 3.10 | FR-064-16 (权限继承) vs FR-064-04 (审批流程) | **PASS** | 一致。Subagent 继承父 Worker 的 permission_preset，IRREVERSIBLE 工具仍触发审批 |
| 3.11 | FR-064-26~31 (上下文压缩) vs FR-064-09~10 (工具结果回填) | **PASS** | 上下文压缩在 `generate()` 调用前执行，工具结果回填在执行后写入，时序上互不冲突 |
| 3.12 | Edge Case "压缩与工具调用引用" vs FR-064-31 | **WARN** | Edge Cases 中提到"正在执行的工具调用引用了被压缩掉的历史信息"，但 **FR 和缓解措施中均未给出具体解决方案**。FR-064-31 只保证 system prompt 和最近一轮不被压缩，但工具调用可能跨多轮引用历史。建议补充缓解措施或在实现时明确处理策略 |

---

## 4. NFR 可测量性

检查每个非功能需求是否有可量化、可验证的指标。

| NFR | 状态 | 说明 |
|-----|------|------|
| NFR-064-01 | **PASS** | 量化指标明确：TOOL_BATCH 事件写入 p99 < 5ms，可通过基准测试验证 |
| NFR-064-02 | **PASS** | 定性但可验证：现有 Skill 和 SKILL.md 配置零修改，可通过回归测试确认 |
| NFR-064-03 | **PASS** | 量化指标明确：单个 Subagent SkillRunner 实例内存增量 < 10MB，可通过 profiling 测量 |
| NFR-064-04 | **PASS** | 量化指标明确：压缩操作 p95 < 3 秒（快速模型），可通过基准测试验证 |
| NFR-064-05 | **PASS** | 量化指标明确：事件写入到 Telegram 送达 < 5 秒，可通过端到端测试验证 |
| NFR-064-06 | **PASS** | 定性但可验证：单个工具超时/崩溃不影响同批次其他工具，可通过故障注入测试验证 |
| NFR-064-07 | **PASS** | 定性但可验证：进程崩溃后重启时孤儿检测，可通过模拟崩溃场景测试 |
| NFR-064-08 | **PASS** | 定性但可验证：两条 API 路径集成测试覆盖 |

---

## 5. 风险覆盖度

检查识别的风险是否都有对应的缓解措施，以及是否有遗漏的风险。

### 已识别风险

| 风险 | 缓解措施状态 | 说明 |
|------|-------------|------|
| 并行工具共享状态竞争 | **PASS** | READ_ONLY 无副作用 + 有副作用的串行执行；底层 ToolBroker/EventStore 已线程安全 |
| 工具回填格式变更致 LLM 退化 | **PASS** | 保留向后兼容回退路径（`tool_call_id` 为空时回退） |
| Subagent 执行循环泄漏 | **PASS** | asyncio.Task 使用 try/finally 清理 + Watchdog 检测孤儿 Task |
| 上下文压缩丢失关键信息 | **PASS** | system prompt + 最近一轮保护不被压缩；摘要质量由 LLM 保证；压缩前后 token 数记录在事件中 |
| 多 Subagent 并行致父 Worker 对话混乱 | **PASS** | 结果注入时标注来源（subagent_runtime_id + task 摘要） |
| Telegram 通知发送失败 | **PASS** | 遵循 Constitution #6 降级；Web SSE 作为备用通道 |

### 遗漏风险评估

| # | 潜在风险 | 状态 | 说明 |
|---|---------|------|------|
| 5.1 | **asyncio.gather 异常传播策略** | **WARN** | FR-064-07 描述了部分失败场景，但 **风险表中未提及 `asyncio.gather(return_exceptions=True)` 还是使用默认行为**。如果使用默认行为，一个工具抛出异常将取消其他并行工具。建议在实现说明中明确使用 `return_exceptions=True` |
| 5.2 | **Subagent 资源耗尽** | **WARN** | spec 依赖 Feature 062 的 `UsageLimits`，但 **未明确 Subagent 的资源限制是独立的还是与父 Worker 共享**。如果多个 Subagent 各自有独立配额，可能在聚合层面超出预算。建议明确 Subagent 资源限制的继承/分配策略 |
| 5.3 | **上下文压缩与 tool_call_id 引用一致性** | **WARN** | 压缩可能移除包含 `tool_call_id` 的历史消息，如果后续 LLM 生成的响应引用了已被压缩的 tool_call，可能导致不一致。Edge Cases 中已提及但 **无具体缓解措施** |
| 5.4 | **Subagent spawn 速率限制** | **WARN** | 未提及 Subagent spawn 的速率或数量限制。如果 Agent 在循环中不断 spawn Subagent，可能导致资源耗尽。建议补充最大并发 Subagent 数配置项 |
| 5.5 | **压缩摘要生成的 LLM 调用失败** | **WARN** | FR-064-30 指定使用快速/便宜模型生成摘要，但 **未定义摘要生成本身失败时的回退策略**。如果摘要模型不可用，是跳过压缩、使用简单截断还是报错？应遵循 Constitution #6 定义降级路径 |

---

## 6. 接口契约完整性

检查新增的事件类型、A2A 消息、协议接口是否完整定义。

| # | 接口/契约 | 状态 | 说明 |
|---|----------|------|------|
| 6.1 | `TOOL_BATCH_STARTED` EventType | **PASS** | payload 字段定义完整：`batch_id`、`tool_names`、`execution_mode` |
| 6.2 | `TOOL_BATCH_COMPLETED` EventType | **PASS** | payload 字段定义完整：`batch_id`、`duration_ms`、`success_count`、`error_count` |
| 6.3 | `CONTEXT_COMPACTION_COMPLETED` EventType | **PASS** | payload 字段定义完整：`before_tokens`、`after_tokens`、`strategy_used` |
| 6.4 | `ToolCallSpec.tool_call_id` 扩展 | **PASS** | 类型（str）、默认值（""）、填充来源（LLM response ID）均已明确 |
| 6.5 | `SkillExecutionContext.parent_task_id` 扩展 | **PASS** | 类型（`str | None`）、用途（Child Task 关联）已明确 |
| 6.6 | `SkillManifest.compaction_model_alias` 扩展 | **PASS** | 类型（`str | None`）、用途（压缩模型）已明确，标注为可选 |
| 6.7 | `SkillManifest.heartbeat_interval_steps` 扩展 | **PASS** | 类型（int）、默认值（5）、用途（Subagent 心跳间隔）已明确 |
| 6.8 | `NotificationChannelProtocol` 接口 | **PASS** | 方法签名完整：`async notify(task_id, event_type, payload) -> bool` |
| 6.9 | A2A 消息类型复用 | **PASS** | 明确复用现有 6 种消息类型（TASK/RESULT/CANCEL/UPDATE/ERROR 等），NG5 明确不新增 |
| 6.10 | A2A ERROR 消息 payload | **WARN** | **未定义 ERROR 消息的 payload schema**。FR-064-20 要求发送 A2A ERROR 消息，但未定义其中应包含的字段（error_category、error_message、recovery_hint 等）。建议在 Key Entities 中补充 ERROR 消息的 payload 契约 |
| 6.11 | A2A RESULT 消息 payload | **PASS** | FR-064-21 定义包含 summary、artifacts、terminal state |
| 6.12 | SSE Hub `broadcast()` 扩展接口 | **WARN** | FR-064-23 描述了"同时广播到 Child Task 和父 Task 的 task_id"的行为，但 **未定义 broadcast 方法签名的变更**（是新增 `parent_task_id` 参数，还是内部自动查找父 Task？）。建议在接口契约中明确扩展方式 |
| 6.13 | Subagent 结果注入对话历史的消息格式 | **WARN** | FR-064-24 描述将结果摘要注入父 Worker 对话，但 **未定义注入的消息 role 和格式**（是 system message？user message？还是特殊的 context injection？）。建议明确注入消息的 role 和格式模板 |

---

## 汇总

| 维度 | PASS | WARN | FAIL | 总计 |
|------|------|------|------|------|
| Constitution 合规性 | 13 | 1 | 0 | 14 |
| FR 完整性 | 33 | 3 | 0 | 36 |
| 跨 FR 一致性 | 9 | 3 | 0 | 12 |
| NFR 可测量性 | 8 | 0 | 0 | 8 |
| 风险覆盖度（已识别） | 6 | 0 | 0 | 6 |
| 风险覆盖度（遗漏评估） | 0 | 5 | 0 | 5 |
| 接口契约完整性 | 10 | 3 | 0 | 13 |
| **合计** | **79** | **15** | **0** | **94** |

---

## 改进建议优先级

### 应在 Plan 阶段前修复（High）

1. **FR-064-20 补充失败分类**：A2A ERROR 消息 payload 应包含 `error_category` 和 `recovery_hint`，符合 Constitution #13（失败必须可解释）
2. **FR-064-17/34 字段对齐**：心跳事件定义需要在 FR-064-17 中增加 `estimated_progress` 字段，或者在 FR-064-34 中移除该期望
3. **FR-064-25 并发注入序列化**：补充多 Subagent 结果同时到达时的注入序列化机制（如 asyncio.Lock 或消息队列）

### 建议在实现阶段明确（Medium）

4. **asyncio.gather 异常策略**：在 P0-A Implementation Notes 中明确使用 `return_exceptions=True`
5. **Subagent 资源限制继承策略**：明确 Subagent 的 UsageLimits 是独立配额还是与父 Worker 共享
6. **Subagent 最大并发数**：补充 `max_concurrent_subagents` 配置项（建议默认 3~5）
7. **上下文压缩失败降级**：定义摘要生成 LLM 调用失败时的回退策略（建议：降级为简单截断 + 发射 WARN 事件）
8. **SSE broadcast 扩展方式**：明确 broadcast 方法签名变更方案
9. **Subagent 结果注入消息格式**：定义注入的 message role 和内容模板

### 可延后处理（Low）

10. **上下文压缩与 tool_call_id 引用一致性**：实现时需考虑被压缩消息中 tool_call_id 的处理策略
11. **A2A ERROR 消息 payload schema**：在 Key Entities 中补充完整定义
