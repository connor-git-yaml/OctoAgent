# §13 测试策略（Testing Strategy）

> 本文件是 [blueprint.md](../blueprint.md) §13 的完整内容。

---

## 13. 测试策略（Testing Strategy）

> 测试策略按层级递进：基础设施 → 单元 → LLM 交互 → 集成 → 编排 → 安全 → 可观测 → 回放 → 韧性。
> 每层都需与 Constitution（C1-C8）和成功判据（S1-S6）对齐，见 §13.10 覆盖矩阵。

### 13.1 测试基础设施（Test Infrastructure）

- **框架**：pytest + anyio（async 测试）+ pytest-asyncio
- **全局 LLM 安全锁**：测试环境设置 `ALLOW_MODEL_REQUESTS = False`，防止意外调用真实 LLM API
- **conftest.py 核心 fixture**：
  - `InMemoryEventStore`：内存实现的 Event Store，替代 SQLite 加速单元测试
  - `TestModel` / `FunctionModel`：Pydantic AI 提供的确定性 LLM mock（零成本、类型安全）
  - `dirty-equals`：处理非确定性字段（`IsStr()`、`IsDatetime()`），用于事件断言
  - `inline-snapshot`：结构化输出断言，自动更新预期值
- **VCR 录制回放**：pytest-recording + vcrpy 录制真实 LiteLLM 请求，集成测试回放时无需网络
- **Logfire 测试隔离**：每个测试后 `logfire.shutdown(flush=False)`，防止 OTel span 跨测试泄漏
- **测试目录结构**：
  ```
  tests/
    unit/           # 纯逻辑、无 IO
    integration/    # 真实 SQLite + Docker + SSE
    replay/         # golden test 事件流回放
    evals/          # LLM 输出质量评估（预留）
    conftest.py     # 全局 fixture + 安全锁
  ```

### 13.2 单元测试（Unit）

- **domain models**：
  - Task 状态机转移覆盖：所有合法转移路径 + 非法转移拒绝
  - Pydantic validator / serializer 正确性（NormalizedMessage / Event / Artifact）
  - Artifact parts 多部分结构校验（对齐 A2A Part 规范）
- **event store 事务一致性**：写事件 + 更新 projection 必须在同一事务内（原子性）
- **tool schema 反射一致性**（contract tests）：schema 生成与函数签名 + 类型注解 + docstring 一致（对齐 C3）
- **policy engine 决策矩阵**：allow / ask / deny 全路径覆盖；Policy Profile 优先级测试
- **A2AStateMapper 映射**：内部状态 ↔ A2A TaskState 双向映射幂等性；终态一一对应
- **成本计算逻辑**：按 model alias 聚合 tokens/cost 正确性（对齐 S6）
- **memory 模型**：SoR current 唯一性约束；Fragments append-only 不可变性（对齐 S5）

### 13.3 LLM 交互测试（LLM Interaction）

> Agent 系统最核心也最难测的部分。借鉴 Pydantic AI 的 TestModel / FunctionModel 模式。

- **TestModel override**：自动调用所有注册工具，生成 schema 兼容参数，验证工具调用链路正确
- **FunctionModel**：精确控制 LLM 响应，适用于测试 Orchestrator 多轮决策路径、Worker 分支逻辑
- **LiteLLM alias 路由**：测试环境通过 alias 将请求路由到 mock 后端，验证 Provider 抽象层
- **非确定性输出策略**：
  - 验证输出结构 / 必填字段 / 类型，而非精确字符串
  - 使用 dirty-equals（`IsStr(regex=...)`）做模糊匹配
  - 关键路径使用 FunctionModel 保证确定性
- **预留：LLM-as-Judge 评估**：pydantic-evals 或自定义 judge 函数，评判 Agent 输出质量（后续迭代）

### 13.4 集成测试（Integration）

- **task 全流程**：从 `ingest_message` → task 创建 → Worker 派发 → stream events → 终态
- **approval flow**：ask → approve → resume；ask → reject → REJECTED 终态（对齐 C4 / C7）
- **worker 执行**：JobRunner docker backend 启动 / 执行 / 产物回收 / 超时处理
- **memory arbitration**：WriteProposal → 冲突检测 → commit；SoR current/superseded 转换一致性（对齐 S5）
- **Skill Pipeline checkpoint**：
  - 正常路径：Pipeline 从起点到终点，验证每个节点 checkpoint 写入
  - 恢复路径：从任意中间 checkpoint 恢复，不重跑已完成节点
  - 中断路径：WAITING_APPROVAL → 审批后从中断点继续
- **多渠道消息路由**：同一 thread_id 的消息落到同一 scope_id；不同渠道的消息隔离（对齐 S4）
- **SSE 事件流**：`/stream/task/{task_id}` 端到端验证事件顺序与完整性

### 13.5 编排与循环测试（Orchestration & Loop）

> Orchestrator 和 Workers 都是 Free Loop，需要专门验证循环控制与异常恢复。

- **Orchestrator 路由决策**：给定 NormalizedMessage，验证目标分类、Worker 选择、risk_level 评估
- **Worker Free Loop 终止**：验证正常完成、budget 耗尽、deadline 到期、用户取消等终止条件
- **死循环检测**：
  - 输出相似度阈值（连续 N 轮输出 similarity > 0.85 → 强制中断）
  - 最大迭代次数限制（硬上限）
  - 测试验证检测机制能正确触发并生成 ERROR 事件
- **Worker 崩溃恢复**：模拟 Worker 进程中断，验证从 Event 历史恢复状态、从最后 checkpoint 续跑（对齐 C1 / S1）
- **多 Worker 协作**：Orchestrator 派发子任务 → Workers 并行执行 → 事件回传 → Orchestrator 汇总

### 13.6 安全与策略测试（Security & Policy）

- **Docker 沙箱隔离**：验证工具执行在容器内，无法访问宿主文件系统 / 网络（除白名单）
- **secrets 不泄漏**：验证 Vault 中的 secrets 不出现在 LLM 上下文、Event payload、日志输出中（对齐 C5）
- **Two-Phase 门禁端到端**：不可逆操作必须经历 Plan → Gate → Execute；跳过 Gate 的请求被拒绝（对齐 C4）
- **工具权限分级**：
  - `read-only` 工具：默认 allow，无需审批
  - `reversible` 工具：默认 allow，可配置为 ask
  - `irreversible` 工具：默认 ask，必须审批后执行
- **未签名插件拒绝**：未通过 manifest 校验的插件默认禁用

### 13.7 可观测性与成本测试（Observability & Cost）

- **事件完整性**：每个 task 的关键步骤必须产生对应 event（对齐 C2 / C8）：
  - `TASK_CREATED` / `MODEL_CALL_STARTED` / `MODEL_CALL_COMPLETED` / `TOOL_CALL` / `TOOL_RESULT` / `STATE_TRANSITION` / `ARTIFACT_CREATED`
  - 缺失任何关键 event 类型 → 测试失败
- **成本追踪正确性**：验证每个 task 的 tokens / cost 聚合与实际 `MODEL_CALL_COMPLETED` 事件 payload 一致（对齐 S6）
- **Logfire span 完整性**：关键操作（LLM 调用、工具执行、状态转移）必须生成 OTel span
- **structlog 输出**：验证日志包含 task_id / trace_id / span_id 等结构化字段，便于关联查询

### 13.8 回放测试（Replay / Golden Tests）

> 利用事件溯源的天然优势，验证系统确定性与可重现性（对齐 S2）。

- **golden test 场景清单**（10 个典型任务事件流）：
  1. 简单问答：单轮 USER_MESSAGE → MODEL_CALL_STARTED → MODEL_CALL_COMPLETED → 回复
  2. 工具调用：MODEL_CALL_STARTED → MODEL_CALL_COMPLETED → TOOL_CALL → TOOL_RESULT → 回复
  3. 多轮对话：多次 USER_MESSAGE 交替
  4. 审批通过：APPROVAL_REQUESTED → APPROVED → 继续执行
  5. 审批拒绝：APPROVAL_REQUESTED → REJECTED → REJECTED 终态
  6. 长任务 + checkpoint：多节点 Pipeline，中间有 CHECKPOINT_SAVED
  7. 任务取消：用户主动 CANCELLED
  8. 工具失败 + 重试：TOOL_CALL → ERROR → 重试 → 成功
  9. 子任务派发：Orchestrator → Worker 子任务 → 回传
  10. 崩溃恢复：中断 → 从 checkpoint 恢复 → 完成
- **一致性断言**：replay 后的 tasks projection、artifacts 列表、终态必须与原始执行一致
- **event schema 兼容**：不同 `schema_version` 的事件 replay 时正确解析
- **发布门禁**：凡涉及 Event schema 或 projection 逻辑变更，必须通过历史事件回放套件（不通过禁止合并）

### 13.9 降级与恢复测试（Resilience）

> 验证 Constitution C1（Durability First）和 C6（Degrade Gracefully）。

- **进程崩溃恢复**：
  - 模拟 kernel/worker 进程崩溃后重启
  - 所有未完成任务在 UI 中可见，且能 resume 或 cancel（对齐 S1）
- **Provider 不可用**：
  - 模拟 LLM Provider 返回 429 / 500 / 超时
  - 验证 LiteLLM fallback 机制触发，切换到备选模型
  - 验证事件记录失败原因（对齐 C6）
- **插件崩溃隔离**：
  - 单个插件 / 工具抛异常不导致整体系统不可用
  - 自动 disable 故障插件并记录 incident（对齐 C6）
- **SQLite WAL 并发一致性**：
  - 模拟两个 task 同时写事件，验证 projection 最终一致
  - 模拟数据库崩溃，验证 WAL 恢复后 projection 可从 events 重建
- **网络中断**：Telegram / Web 渠道断连后重连，消息不丢失、不重复

### 13.10 测试覆盖对齐矩阵

| Constitution / 成功判据 | 对应测试 |
|------------------------|---------|
| C1 Durability First | §13.8 回放测试、§13.9 崩溃恢复 |
| C2 Everything is Event | §13.7 事件完整性 |
| C3 Tools are Contracts | §13.2 contract tests |
| C4 Side-effect Two-Phase | §13.4 approval flow、§13.6 Two-Phase 门禁 |
| C5 Least Privilege | §13.6 secrets 不泄漏 |
| C6 Degrade Gracefully | §13.9 Provider / 插件降级 |
| C7 User-in-Control | §13.4 approval flow、§13.5 用户取消 |
| C8 Observability is Feature | §13.7 事件完整性、Logfire span |
| S1 重启后可恢复 | §13.9 进程崩溃恢复 |
| S2 任务可完整回放 | §13.8 golden tests |
| S3 高风险需审批 | §13.4 approval flow、§13.6 权限分级 |
| S4 多渠道一致性 | §13.4 多渠道消息路由 |
| S5 记忆一致性 | §13.2 memory 模型、§13.4 memory arbitration |
| S6 成本可见 | §13.2 成本计算、§13.7 成本追踪 |

---
