# Spec Review: Feature 017 — Unified Operator Inbox + Mobile Task Controls

**特性分支**: `codex/feat-017-operator-inbox-mobile-controls`
**审查日期**: 2026-03-07
**审查范围**: FR-001 ~ FR-018

## 结论

- 结论: **PASS**
- 说明: 017 的统一 inbox、Web/Telegram 等价动作、operator audit 与 pairing 产品化入口已全部落到现有 gateway / provider / frontend 代码路径。

## FR 对齐检查

| FR | 状态 | 证据 |
|----|------|------|
| FR-001 | ✅ | `operator_inbox.py` 聚合 approval / alert / retryable failure / pairing request |
| FR-002 | ✅ | 017 只做 query-time projection，直接消费 `ApprovalManager` / `TaskJournalService` / `task_jobs` / `TelegramStateStore` |
| FR-003 | ✅ | `OperatorInboxItem` / `OperatorInboxResponse` 提供统一最小展示字段 |
| FR-004 | ✅ | `GET /api/operator/inbox` + `OperatorInboxPanel` 接入首页 |
| FR-005 | ✅ | `TelegramGatewayService` 发送 inline keyboard operator card，不再回退为纯文本提醒 |
| FR-006 | ✅ | `OperatorActionRequest` / `OperatorActionResult` 同时供 Web 与 Telegram 复用 |
| FR-007 | ✅ | approve / deny / retry / ack / approve_pairing / reject_pairing 统一进入 `OperatorActionService.execute()` |
| FR-008 | ✅ | pending pairing 进入统一 inbox，并支持直接审批 |
| FR-009 | ✅ | `OperatorActionOutcome` 明确区分 succeeded / already_handled / expired / stale_state / not_allowed / not_found |
| FR-010 | ✅ | inbox projection 消费最近 audit event，回显 `recent_action_result` |
| FR-011 | ✅ | `OPERATOR_ACTION_RECORDED` + `OperatorActionAuditPayload` |
| FR-012 | ✅ | task-bound action 写入来源 task；pairing 写入 `ops-operator-inbox` 统一审计链 |
| FR-013 | ✅ | alert ack 与 approval callback 重复点击返回幂等结果 |
| FR-014 | ✅ | inbox summary 暴露 `degraded_sources`，pairing 状态损坏时保留局部降级 |
| FR-015 | ✅ | 未重写 ApprovalManager / Watchdog detector / Telegram ingress 基础链路 |
| FR-016 | ✅ | action contract 与前端页面解耦，可直接复用到 future mobile/PWA |
| FR-017 | ✅ | retry 创建 successor task，并把 `result_task_id` 写回 audit |
| FR-018 | ✅ | operator card 默认投递到 `first_approved_user()`；无目标时显式降级为不投递 |

## 边界场景检查

- EC-1（Web / Telegram 同时处理同一审批）: ✅ 后到端返回 `already_handled`
- EC-2（同一 alert 重复 ack）: ✅ 第二次返回 `already_handled`
- EC-3（pairing 已过期或已处理）: ✅ 返回 `expired` / `already_handled`
- EC-4（无 approved operator target）: ✅ Telegram 不伪造发送成功，Web inbox 仍为真相源
