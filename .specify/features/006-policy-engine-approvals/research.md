# 技术决策研究: Feature 006 — Policy Engine + Approvals + Chat UI

**Feature Branch**: `feat/006-policy-engine-approvals`
**日期**: 2026-03-02
**状态**: Final
**输入**: spec.md (28 FR), tech-research.md (竞品分析), constitution.md, Feature 004 契约

---

## 决策索引

| # | 决策 | 结论 | 置信度 |
|---|------|------|--------|
| D1 | Policy Pipeline 架构模式 | 纯函数 Pipeline（OpenClaw 风格） | 高 |
| D2 | Two-Phase Approval 等待原语 | asyncio.Event + Event Store 双写 | 高 |
| D3 | PolicyCheckHook 与 ToolBroker 集成方式 | hook 内部等待审批（不修改 ToolBroker） | 高 |
| D4 | PolicyDecision 到 CheckResult 的映射 | PolicyCheckHook 内部适配器映射 | 高 |
| D5 | TaskStatus 扩展策略 | 新增 WAITING_APPROVAL 状态 + 3 条转换规则 | 高 |
| D6 | 审批超时实现 | asyncio.get_event_loop().call_later() + APPROVAL_EXPIRED 事件 | 高 |
| D7 | allow-always 白名单持久化 | M1 仅内存，M2 持久化到 SQLite | 中 |
| D8 | SSE 审批推送架构 | 复用 M0 SSE 基础设施 + event type 分发 | 高 |
| D9 | 前端状态管理 | React useState + useEffect（无 Zustand） | 中 |
| D10 | Pipeline deny 短路策略 | 遇到 deny 立即短路返回 | 高 |
| D11 | 参数脱敏机制 | 复用 Feature 004 Sanitizer + 敏感字段掩码 | 高 |
| D12 | 新增 package 位置 | packages/policy/ 独立包 | 高 |

---

## D1: Policy Pipeline 架构模式

### Decision

采用**纯函数 Pipeline 模式**，即 OpenClaw `applyToolPolicyPipeline()` 的 Python 移植版。

### Rationale

1. **Blueprint 8.6.4 直接映射**: Blueprint 明确定义了 4 层 Pipeline（Profile -> Global -> Agent -> Group），纯函数 Pipeline 是其 1:1 映射
2. **OpenClaw 生产验证**: OpenClaw 在 Personal AI OS 场景中验证了 7 层 Cascade 策略管道的可行性
3. **可测试性极佳**: Pipeline 是纯函数（输入 steps + tool_meta + context，输出 PolicyDecision），每层可独立单元测试
4. **与 Feature 004 契约天然对齐**: PolicyCheckHook 包装 Pipeline + ApprovalManager，无需修改 BeforeHook Protocol
5. **M1 scope 最小化**: 仅需实现 Layer 1（Profile 过滤） + Layer 2（Global 规则），M2 追加 Layer 3/4

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| Strategy + Provider 模式 | AgentStudio 风格，可插拔 Provider 链 | 过度工程化，单用户场景不需要外部审计 Provider；与 Feature 004 契约适配成本高 |
| Pydantic AI ApprovalRequired | 抛异常中断 Agent run + message_history 续传 | 不适用于 OctoAgent Free Loop 模式（不重启 run） |
| 单层 if-else 决策 | 直接在 PolicyCheckHook 中硬编码规则 | 不可扩展，违反"只收紧不放松"原则，M2 扩展时需重构 |

---

## D2: Two-Phase Approval 等待原语

### Decision

使用 Python 标准库 `asyncio.Event` 作为等待原语，配合 Event Store 双写确保持久化。

### Rationale

1. **asyncio.Event 满足需求**: 单进程单 event loop（FastAPI + Uvicorn），不需要跨进程同步
2. **双写保证耐久性**: 每次 register/resolve 操作同时写入 Event Store，满足 Constitution C1 Durability First
3. **启动恢复**: 进程重启时扫描 Event Store 中未配对的 APPROVAL_REQUESTED 事件，重建 pending 状态
4. **OpenClaw 验证**: OpenClaw 的 ExecApprovalManager 使用 Promise（等价于 asyncio.Event），已验证此模式
5. **零依赖**: 不引入 Redis/Celery 等分布式原语，符合 Blueprint "先单机打牢"原则

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| asyncio.sleep 轮询 | Agent Zero 模式 | 反模式，CPU 浪费（Constitution 效率约束） |
| Redis Pub/Sub | 跨进程审批通知 | 引入外部依赖，违反 Constitution V.2 "不引入重量级编排器" |
| asyncio.Queue | 生产者-消费者模式 | 不支持幂等注册（同 ID 返回同 awaitable），语义不匹配 |
| 数据库轮询 | 定期查询 Event Store 中的审批状态 | 延迟高（轮询间隔），CPU 浪费 |

---

## D3: PolicyCheckHook 与 ToolBroker 集成方式

### Decision

PolicyCheckHook 在 `before_execute()` 内部完成 Pipeline 评估 + 审批等待，返回最终的 `BeforeHookResult` 给 ToolBroker。ToolBroker 不感知审批概念。

### Rationale

1. **不修改 Feature 004 锁定契约**: BeforeHookResult 仅有 `proceed: bool` + `rejection_reason: str | None` + `modified_args`，不需要新增字段
2. **OpenClaw 已验证**: OpenClaw 的审批等待封装在 bash-tools 的 exec-approval-request 内部，调用方只看到最终结果
3. **关注点分离**: 审批是 Policy 层的关注点，不应泄漏到工具执行层
4. **tech-research 推荐**: 调研报告 R2 的推荐方案明确选择 "hook 内部等待"

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| 扩展 BeforeHookResult | 增加 requires_approval 和 approval_id 字段 | 需要修改 Feature 004 锁定契约（LOCKED 状态），成本高 |
| ToolBroker 感知审批 | ToolBroker.execute() 检测审批状态并等待 | 违反关注点分离，增加 ToolBroker 复杂度 |

---

## D4: PolicyDecision 到 CheckResult 的映射

### Decision

PolicyCheckHook 内部使用 PolicyPipeline 产生 `PolicyDecision`，然后映射为 Feature 004 的 `CheckResult`:

```python
# allow -> CheckResult(allowed=True)
# ask   -> (注册审批 + 等待决策) -> CheckResult(allowed=True/False)
# deny  -> CheckResult(allowed=False, reason=...)
```

### Rationale

1. **spec Clarification #2 已确认此方案**: PolicyCheckpoint Protocol 不可变更，PolicyCheckHook 作为适配器负责语义映射
2. **tech-research 8.4 节示例代码已验证可行性**
3. **ask 决策的特殊处理**: ask 在 hook 内部转化为审批等待，最终仍映射为 allowed=True（批准）或 allowed=False（拒绝/超时），不引入第四种状态

### Alternatives

无替代方案。此为 spec 和 tech-research 共同确认的唯一方案。

---

## D5: TaskStatus 扩展策略

### Decision

在现有 TaskStatus 枚举中新增 `WAITING_APPROVAL` 状态，新增 3 条合法转换规则:

- `RUNNING -> WAITING_APPROVAL`（策略决策为 ask）
- `WAITING_APPROVAL -> RUNNING`（用户批准）
- `WAITING_APPROVAL -> REJECTED`（用户拒绝 / 超时）

### Rationale

1. **m1-feature-split 明确**: "激活 WAITING_APPROVAL（M0 已在 TaskStatus 枚举中预留）"
2. **Blueprint 14 (A2A 兼容)**: 内部 Task 状态机是 A2A TaskState 的超集，WAITING_APPROVAL 映射为 A2A `input-required`
3. **不复用 PAUSED**: Blueprint 明确区分 PAUSED（用户主动暂停）和 WAITING_APPROVAL（策略触发），保持语义清晰
4. **WAITING_APPROVAL -> REJECTED（非 FAILED）**: REJECTED 区分策略拒绝与运行时失败，对齐 Constitution 终态定义

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| 复用 PAUSED + metadata | 用 metadata 区分暂停原因 | Blueprint 明确区分两者语义，复用会降低状态精度，违反 Constitution 14 |
| 不新增状态 | Task 保持 RUNNING + 内部标记 | 失去 Event Store 的状态可追溯性，违反 Constitution C2 |

---

## D6: 审批超时实现

### Decision

使用 `asyncio.get_event_loop().call_later(timeout_s, expire_callback)` 注册超时回调。超时后自动执行:

1. 将审批状态设为已过期
2. 设置 asyncio.Event（唤醒等待者）
3. 写入 APPROVAL_EXPIRED 事件到 Event Store
4. 推送 SSE 通知

### Rationale

1. **Python 标准库**: 无需第三方定时器库
2. **精确性**: call_later 由 event loop 管理，精度足够（秒级）
3. **与 asyncio.Event 配合**: 超时回调中设置 Event，等待者自然被唤醒
4. **持久化兜底**: Event Store 记录 APPROVAL_EXPIRED，进程重启后不会重复处理已过期审批

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| asyncio.wait_for() | 在 wait_for_decision() 中用 timeout 参数 | 超时时仅抛异常，无法执行清理逻辑（写事件、推 SSE） |
| APScheduler 定时任务 | 注册定时任务执行过期 | 过重，审批超时是短期一次性任务，不适合调度器 |
| 后台 asyncio.Task | 启动一个 sleep + expire 的后台任务 | 可行但不如 call_later 简洁，且需要管理 Task 取消 |

---

## D7: allow-always 白名单持久化

### Decision

M1 阶段 allow-always 仅保持在 ApprovalManager 的内存字典中（`_allow_always: set[str]`），进程重启后失效。M2 实现持久化到 SQLite。

### Rationale

1. **spec Scope Boundaries 明确排除**: "allow-always 白名单持久化（Safe Bins）" 列为 M2+ 延伸
2. **spec Clarification #3 已确认**: M1 的 allow-always 通过 ApprovalManager 内存字典实现
3. **MVP 可接受限制**: 单用户场景下，进程重启后重新审批不构成严重体验问题
4. **不引入 Safe Bins**: m1-feature-split 中 Safe Bins 仅为辅助说明，非 M1 验收标准

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| SQLite 持久化 | 每次 allow-always 写入 SQLite 表 | 超出 M1 spec scope |
| JSON 配置文件 | 写入 data/policy-allowlist.json | 超出 M1 spec scope，且缺少事务保证 |

---

## D8: SSE 审批推送架构

### Decision

复用 M0 已有的 SSE 基础设施（sse-starlette），在现有 SSE 事件流中新增审批相关事件类型:

- `approval:requested` — 新审批请求
- `approval:resolved` — 审批已决策
- `approval:expired` — 审批已过期

前端通过 EventSource 监听，按 event type 分发到 Approvals 面板或 Chat UI。

### Rationale

1. **复用现有基础设施**: M0 已建立 SSE 事件流，无需新建连接
2. **单连接多类型**: 避免多 SSE 连接带来的资源消耗
3. **spec FR-022 对齐**: "SSE 实时接收审批状态变更通知" + "轮询兜底"
4. **tech-research R4 缓解**: SSE 断线时前端自动降级为 30s 轮询

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| WebSocket | 双向通信 | M0 基础设施为 SSE，切换成本高；审批推送不需要客户端到服务端的实时通道 |
| 独立 SSE 连接 | /stream/approvals 专用连接 | 资源浪费，增加前端连接管理复杂度 |
| 纯轮询 | GET /api/approvals 定期轮询 | 延迟高（最坏 30s），不满足 SC-003（3s 内可见） |

---

## D9: 前端状态管理

### Decision

M1 阶段使用 React 原生 `useState` + `useEffect` 管理状态，不引入 Zustand/Redux。Approvals 面板和 Chat UI 使用独立的自定义 hooks（`useApprovals`, `useChatStream`）。

### Rationale

1. **M1 前端 scope 小**: 仅 Approvals 面板 + 基础 Chat UI，状态不复杂
2. **减少依赖**: 不引入额外状态管理库，降低前端包体积
3. **M2 可升级**: 如果 M2 前端状态交叉增多，可引入 Zustand（增量迁移成本低）
4. **Custom hooks 隔离**: `useApprovals` 封装 SSE + 轮询 + 审批列表状态，`useChatStream` 封装消息流

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| Zustand | 轻量状态管理 | M1 scope 下 overkill，增加依赖 |
| Redux Toolkit | 重量级状态管理 | 严重 overkill |
| Jotai / Recoil | 原子化状态 | 学习成本，M1 不需要 |

---

## D10: Pipeline deny 短路策略

### Decision

Pipeline 评估遇到 `deny` 决策时立即短路返回，不继续执行后续层。

### Rationale

1. **spec Clarification #4 已确认**: "Pipeline 遇到 deny 立即短路返回，不继续执行后续层"
2. **逻辑正确性**: deny 是最严格决策，后续层只能收紧不能放松（FR-003），因此后续层不可能改变结果
3. **性能优化**: 避免不必要的 evaluator 计算
4. **tech-research 附录 B #4**: 明确标记 "Provider 链无短路" 为反模式

### Alternatives

无替代方案。deny 短路是逻辑必然。

---

## D11: 参数脱敏机制

### Decision

审批 payload（ApprovalRequest.tool_args_summary、Event payload、Approvals 面板展示）中的敏感参数值使用掩码 `***` 替换。脱敏规则复用 Feature 004 ToolBroker 的 Sanitizer 机制。

### Rationale

1. **spec FR-028 明确要求**: 复用 Feature 004 ToolBroker 的 Sanitizer 机制
2. **Constitution C5 + C8**: Least Privilege（secrets 不进 LLM 上下文）+ Observability（敏感原文不写入 Event payload）
3. **单一事实源**: 脱敏规则在 Sanitizer 中集中定义，PolicyEngine 和 ToolBroker 共用同一逻辑

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| 自定义脱敏逻辑 | PolicyEngine 内部独立实现 | 违反 DRY，与 ToolBroker Sanitizer 规则不一致风险 |
| 不脱敏 | 审批 payload 展示原始参数 | 违反 Constitution C5 和 C8 |

---

## D12: 新增 package 位置

### Decision

Feature 006 的核心代码放置在 `packages/policy/` 独立包中。API 路由放在 `apps/gateway/routes/`，前端组件放在 `frontend/src/`。

### Rationale

1. **Blueprint Repo 结构**: `packages/` 目录用于领域包，`apps/` 用于应用层
2. **m1-feature-split**: "新增模块 PolicyEngine + PolicyProfile + ApprovalService"
3. **与现有模块平行**: `packages/core/`（domain models）、`packages/tooling/`（Tool Broker）、`packages/policy/`（Policy Engine）
4. **依赖清晰**: packages/policy 依赖 packages/core（Event Store）+ packages/tooling（ToolMeta, Hook Protocol），不反向依赖

### Alternatives

| 替代方案 | 描述 | 拒绝理由 |
|----------|------|----------|
| apps/kernel/policy/ | 放在 Kernel 应用内 | Policy 是可复用领域逻辑，不应绑定到特定 app |
| packages/tooling/policy/ | 作为 tooling 子模块 | 违反关注点分离，Policy 依赖 tooling 但不属于 tooling |
