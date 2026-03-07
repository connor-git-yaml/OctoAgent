# Implementation Plan: Feature 016 — Telegram Channel + Pairing + Session Routing

**Branch**: `codex/feat-016-telegram-channel` | **Date**: 2026-03-07 | **Spec**: `.specify/features/016-telegram-channel/spec.md`
**Input**: `.specify/features/016-telegram-channel/spec.md` + `research/research-synthesis.md`

---

## Summary

Feature 016 的目标是把 Telegram 从“onboarding 中的占位 verifier”推进为真正可用的外部渠道。实现策略不是重写新的任务系统，而是在现有 `Gateway -> TaskService -> Event/SSE` 闭环外侧增加一层 Telegram transport 与 access/routing 适配：

1. **配置层**：扩展 `octoagent.yaml`，新增 `channels.telegram` 作为单一事实源。
2. **接入层**：在 Gateway 生命周期中注册 Telegram runtime，支持 webhook 与 polling 双模式。
3. **授权层**：新增 durable pairing / allowlist store，默认 DM `pairing`、群默认 `allowlist`。
4. **路由层**：把 Telegram update 规范化为稳定的 `NormalizedMessage`，冻结 `scope_id` / `thread_id` contract。
5. **回传层**：新增 Telegram outbound notifier，让成功、等待审批、失败和重试结果能回到原会话。
6. **DX 闭环**：把 Telegram verifier 注入 `octo onboard` / `octo doctor`，让 readiness 与首条消息验证走真实链路。

---

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**:
- `httpx`（已有）— Telegram Bot API 探测与发送消息
- `pydantic>=2`（已有）— 配置模型、pairing store、update/outbound contract
- `fastapi`（已有）— Telegram webhook route
- `filelock`（已有）— pairing / cursor store 原子读写
- `structlog`（已有）— Telegram transport / pairing / delivery 结构化日志

**Storage**:
- `octoagent.yaml`（扩展）— Telegram channel config
- `data/telegram-state.json`（新增）— pending pairing、approved allowlist、polling offset、last delivery snapshot
- 现有 SQLite Event Store（复用）— Task / Event / 审批事件主链路

**Testing**:
- `pytest`
- `httpx.AsyncClient + ASGITransport`
- monkeypatch / fake httpx transport
- Gateway route 集成测试 + provider DX 单元测试

**Constraints**:
- 不引入新的独立服务
- 不破坏现有 Web/API、TaskRunner、ApprovalManager
- 默认 fail-closed
- 016 不实现统一 operator inbox，仅提供 Telegram 端基础通知 surface

---

## Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|---|---|---|---|
| 原则 1: Durability First | 直接适用 | PASS | pairing、approved allowlist、polling offset 使用项目级持久化存储，不能只放内存 |
| 原则 2: Everything is an Event | 直接适用 | PASS | Telegram 入站消息仍走现有 Task/Event 主链路；审批通知复用现有 approval 事件 |
| 原则 4: Side-effect Must be Two-Phase | 直接适用 | PASS | pairing approval 与高风险任务审批继续走显式批准路径 |
| 原则 6: Degrade Gracefully | 直接适用 | PASS | Telegram 失败不拖垮 Gateway；webhook 不可用可退 polling |
| 原则 7: User-in-Control | 直接适用 | PASS | 未授权消息默认拦截，owner 通过 pairing/allowlist 明确放行 |
| 原则 8: Observability is a Feature | 直接适用 | PASS | doctor/onboard/structured log/Task Event 一起暴露 Telegram readiness 与失败原因 |

---

## Project Structure

### 文档制品

```text
.specify/features/016-telegram-channel/
├── spec.md
├── plan.md
├── data-model.md
├── contracts/
│   ├── telegram-config-schema.md
│   ├── telegram-webhook-api.md
│   └── telegram-verifier.md
├── tasks.md
├── checklists/
└── research/
```

### 源码变更布局

```text
octoagent/packages/provider/src/octoagent/provider/dx/
├── config_schema.py
├── doctor.py
├── cli.py
├── onboarding_service.py
├── telegram_pairing.py          # 新增：pairing / allowlist / polling offset 持久化
├── telegram_verifier.py         # 新增：真实 Telegram readiness / first-message verifier
└── telegram_client.py           # 新增：Bot API 探测与基础发送

octoagent/apps/gateway/src/octoagent/gateway/
├── main.py
├── deps.py
├── routes/telegram.py           # 新增：webhook ingress
└── services/telegram.py         # 新增：normalizer / policy / runtime / outbound notifier

octoagent/packages/core/src/octoagent/core/models/
├── message.py
└── payloads.py

octoagent/packages/provider/tests/
├── test_telegram_pairing.py
├── test_telegram_verifier.py
└── test_doctor.py               # 扩展 Telegram 检查

octoagent/apps/gateway/tests/
├── test_telegram_route.py
├── test_telegram_service.py
└── test_task_service_hardening.py  # 扩展 Telegram 去重元数据覆盖
```

---

## Architecture

### 核心设计

#### 1. Telegram 配置与状态

- `octoagent.yaml` 扩展 `channels.telegram`
- Telegram runtime 只读取配置引用，不直接读取凭证明文
- 项目级 `telegram-state.json` 保存：
  - pending pairing requests
  - approved DM user IDs
  - allowed groups / group allow users 快照
  - polling offset

#### 2. 入站处理

- webhook route 校验 secret header 后，把 Telegram update 交给 `TelegramGatewayService`
- polling runner 复用同一 `TelegramGatewayService`
- service 负责：
  - 解析 update
  - 执行 pairing / allowlist / group policy
  - 生成稳定 `idempotency_key`
  - 规范化为 `NormalizedMessage`
  - 调用现有 `TaskService.create_task()`

#### 3. session routing

- `scope_id` 固定：`chat:telegram:<chat_id>`
- `thread_id`：
  - DM: `tg:<user_id>`
  - 群聊: `tg_group:<chat_id>`
  - forum topic: `tg_group:<chat_id>:topic:<message_thread_id>`
  - 普通 reply thread: `tg_group:<chat_id>:reply:<reply_to_message_id>`

#### 4. 出站回传

- Telegram outbound 最小 contract：
  - 文本结果
  - 审批等待提示
  - 失败 / 重试结果
- notifier 通过 Task requester 与首条 `USER_MESSAGE` payload 中的 Telegram 元数据回发消息

#### 5. DX 闭环

- `octo onboard --channel telegram` 自动注入真实 verifier
- `octo doctor` 新增 Telegram 配置/凭证/readiness 检查
- verifier 首条消息验证优先向第一个已批准 DM 用户发送测试消息

---

## Implementation Strategy

### Phase 1

- 扩展配置模型和 Telegram state store
- 实现 Bot API client 与 verifier
- 补齐 doctor / onboard 注入

### Phase 2

- 实现 Gateway Telegram route / service / webhook path
- 实现 pairing / allowlist / routing / inbound idempotency

### Phase 3

- 实现 outbound notifier
- 扩展测试与回归验证

