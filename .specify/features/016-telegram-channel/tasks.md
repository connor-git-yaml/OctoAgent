# Tasks: Feature 016 — Telegram Channel + Pairing + Session Routing

**Input**: `.specify/features/016-telegram-channel/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-07
**Status**: Ready

## Phase 1: Foundation

- [ ] T001 [P0] [B] 扩展 `octoagent.yaml` 配置模型，新增 `channels.telegram` → `packages/provider/src/octoagent/provider/dx/config_schema.py`
- [ ] T002 [P0] [B] 实现 Telegram state / pairing store → `packages/provider/src/octoagent/provider/dx/telegram_pairing.py`
- [ ] T003 [P0] [B] 实现 Telegram Bot API client → `packages/provider/src/octoagent/provider/dx/telegram_client.py`
- [ ] T004 [P0] [P] 为 config schema / pairing store / client 编写单测 → `packages/provider/tests/`

## Phase 2: DX 闭环

- [ ] T005 [P0] [B] 实现真实 Telegram verifier → `packages/provider/src/octoagent/provider/dx/telegram_verifier.py`
- [ ] T006 [P0] [B] 将 verifier 注入 `octo onboard` → `packages/provider/src/octoagent/provider/dx/cli.py`
- [ ] T007 [P0] [B] 为 `octo doctor` 增加 Telegram 检查 → `packages/provider/src/octoagent/provider/dx/doctor.py`
- [ ] T008 [P0] [P] 为 verifier / doctor / onboard 编写单测 → `packages/provider/tests/`

## Phase 3: Gateway Telegram Transport

- [ ] T009 [P0] [B] 扩展 `NormalizedMessage` / `UserMessagePayload`，保留 Telegram reply/thread/update 元数据 → `packages/core/src/octoagent/core/models/`
- [ ] T010 [P0] [B] 实现 Telegram Gateway service（normalize + policy + idempotency） → `apps/gateway/src/octoagent/gateway/services/telegram.py`
- [ ] T011 [P0] [B] 实现 Telegram webhook route → `apps/gateway/src/octoagent/gateway/routes/telegram.py`
- [ ] T012 [P0] [B] 在 Gateway 生命周期注册 Telegram runtime → `apps/gateway/src/octoagent/gateway/main.py`
- [ ] T013 [P0] [P] 编写 webhook / pairing / routing / dedupe 集成测试 → `apps/gateway/tests/`

## Phase 4: Outbound Surface

- [ ] T014 [P0] [B] 实现 Telegram outbound notifier（成功 / 审批等待 / 失败结果） → `apps/gateway/src/octoagent/gateway/services/telegram.py`
- [ ] T015 [P0] [P] 为 outbound notifier 与审批提示补测 → `apps/gateway/tests/`

## Phase 5: Verification

- [ ] T016 [P0] [B] 运行 provider/gateway 相关测试，确认 WebChannel 无回归
- [ ] T017 [P0] 更新 verification/report 与必要文档

