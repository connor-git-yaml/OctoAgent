# 技术调研报告：Feature 016 Telegram Channel + Pairing + Session Routing

**特性分支**: `codex/feat-016-telegram-channel`
**调研日期**: 2026-03-07
**调研模式**: 离线 + 在线官方文档
**产品调研基础**: [product-research.md](product-research.md)

## 1. 调研目标

**核心问题**:
- 如何在不破坏当前 Web/Gateway 链路的前提下接入 Telegram transport
- 如何稳定实现 webhook / polling 双模式、重复投递去重和 thread/session 路由
- 如何把 015 的 `channel verifier` 从 fake contract 变成真实 Telegram readiness / first-message verifier

**产品 MVP 范围（来自产品调研）**:
- 入站 Telegram 消息进入 `NormalizedMessage -> Task`
- DM pairing / allowlist 与群组 allowlist
- DM / 群聊 / topic / reply 的 `scope_id` / `thread_id` 稳定映射
- Telegram 出站回传与 onboarding/doctor 诊断闭环

## 2. 架构方案对比

| 维度 | 方案 A：FastAPI 直连 Bot API | 方案 B：aiogram transport + Gateway bridge | 方案 C：完整插件化 channel framework |
|---|---|---|---|
| 概述 | 手写 webhook、polling、update 解析与 outbound HTTP | 用 aiogram 负责 Telegram update 生命周期，Gateway 负责 normalize / task bridge / policy | 先补齐完整插件系统，再落 Telegram |
| 与蓝图兼容 | 中 | 高 | 高 |
| 实现复杂度 | 中高 | 中 | 高 |
| 可维护性 | 中低 | 高 | 中 |
| 上手速度 | 高 | 中高 | 低 |
| 风险 | 需要自己维护 Telegram 细节与错误恢复 | 依赖新增 SDK，但 transport 细节更稳 | 范围明显超出 016 |
| 适合当前仓库 | 一般 | 最优 | 不适合 MVP |

### 推荐方案

**推荐**: 方案 B，`aiogram transport + Gateway bridge`

**理由**:

1. 蓝图已经明确 Telegram 栈基线是 `aiogram`，继续手写 transport 只会制造未来返工。
2. 当前 Gateway 已经有 `NormalizedMessage -> TaskService -> Event/SSE` 的成熟主链路；016 只需要补 Telegram 入口与回传桥，而不是重写任务系统。
3. 与 015 的 verifier contract、015/017/018 的并行边界最清晰。

## 3. 与现有代码的兼容性分析

| 现有模块 | 当前状态 | 对 016 的意义 |
|---|---|---|
| `NormalizedMessage` / `Task` | 已有 `channel/thread_id/scope_id` 字段 | 可以直接承载 Telegram 路由元数据 |
| `TaskService.create_task()` | 已有幂等键检查和任务创建原子提交 | 可复用为 Telegram update 去重落点 |
| `/api/message` | 只处理通用文本消息 | 可参考其入站模式，但 Telegram 更适合专用 route/service |
| `OnboardingService` / `ChannelVerifierRegistry` | 015 已交付 | 016 应提供真实 Telegram verifier，并接入 registry |
| 配置 Schema | 只有 provider/runtime | 016 需要新增 `channels.telegram` |
| Gateway lifespan | 只启动 Web/API 组件 | 016 需要把 Telegram runtime 注册到生命周期中 |

## 4. 推荐设计模式

### 模式 1：Gateway 拥有 Telegram transport

- Telegram webhook、polling、update 归一化、outbound reply 都放在 Gateway
- Kernel/Worker 不感知 Telegram，只处理 `NormalizedMessage` 与任务事件
- 符合蓝图的 `Channels -> OctoGateway -> OctoKernel` 分层

### 模式 2：配置驱动 + fail-closed 默认值

- `channels.telegram` 作为单一事实源，统一保存 mode、bot token env、allowlist、group policy、webhook 设置
- DM 默认 `pairing`
- 群默认 `allowlist`
- webhook secret 或 allowlist 缺失时默认拒绝，而不是放通

### 模式 3：幂等与路由规则前置冻结

- 先冻结 `idempotency_key`、`scope_id`、`thread_id`、`reply_to_message_id`、`message_thread_id`
- 让入站、出站、审批、重试、回放都复用同一套 canonical 规则

## 5. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---|---|---|---|
| 1 | webhook 与 polling 切换造成重复投递 | 高 | 高 | 使用 Telegram `update_id + chat_id + message_id/callback_query_id` 生成幂等键；同一 bot 只允许单 runner |
| 2 | pairing / allowlist 只放内存，重启丢失 | 中 | 高 | pairing、approved allowlist、polling cursor 使用 durable store |
| 3 | `scope_id/thread_id` 编码不稳定导致串会话 | 中 | 高 | 在 spec 阶段先冻结 canonical mapping，并用集成测试保护 |
| 4 | 群聊授权继承私聊 pairing，扩大攻击面 | 中 | 高 | 明确 DM 与群组授权隔离，群组只认 `groups/groupAllowFrom` |
| 5 | 把 017 的 inbox/action surface 一起做进 016 | 中 | 中 | 016 只做基础 outbound action/result contract，不做统一 inbox 聚合 |

## 6. Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|---|---|---|
| Durability First | ✅ 兼容 | pairing、cursor、delivery retry 不能只存内存 |
| Everything is an Event | ✅ 兼容 | pairing request / approve / reject / outbound fail 至少要有可审计事件或持久化记录 |
| Side-effect Must be Two-Phase | ✅ 兼容 | pairing approval、危险操作按钮需走既有 approval/event 语义 |
| Least Privilege | ✅ 兼容 | bot token 只能通过 env/config 引用，不能进 LLM 上下文 |
| Degrade Gracefully | ✅ 兼容 | webhook 不可用时应可退 polling；SDK/网络失败不能拖死 Gateway |

## 7. 结论与建议

### 总结

从技术角度看，Feature 016 已具备良好的接入基础：核心缺的不是任务系统，而是 Telegram transport、channel config、真实 verifier 和 durable access/routing 规则。最优路线是“用 aiogram 承担 Telegram 生命周期，用现有 Gateway/TaskService 承担任务闭环”。

### 对产研汇总的建议

- 把“真实 verifier + doctor/onboard readiness”视为 MVP，不要把它当附带工作
- 把 `scope_id/thread_id` 规则写成产品级 contract，再进入 plan 阶段
- 明确 016 的出站能力只覆盖“文本回复 + 审批提示 + 错误/重试结果”，避免与 017 混线

