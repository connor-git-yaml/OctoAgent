# Tasks: Feature 017 — Unified Operator Inbox + Mobile Task Controls

**Input**: `.specify/features/017-operator-inbox-mobile-controls/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-07
**Status**: Completed

**Task Format**: `- [x] T{三位数} [P0/P1] [P?] [USN?] 描述 → 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 为 M2 operator control 主闭环阻塞项）
- `[P]`: 可并行执行（不同文件、无前置依赖）
- `[B]`: 阻塞后续关键路径
- `[USN]`: 所属 User Story（US1–US4）；Setup/Foundational 阶段不标注
- `[SKIP]`: 明确不在 017 落地

---

## Phase 1: Setup（模块骨架与控制面边界）

**目标**: 先把 017 的共享落点固定住，避免后续把逻辑继续散落到 approvals panel、task detail、telegram.py 临时分支里。

- [x] T001 [P0] [B] 创建 017 所需模块骨架：`operator_inbox.py`、`operator_actions.py`、`routes/operator_inbox.py`、`core/models/operator_inbox.py`、`useOperatorInbox.ts`、`OperatorInboxPanel.tsx` → `octoagent/packages/core/src/octoagent/core/models/`、`octoagent/apps/gateway/src/octoagent/gateway/services/`、`octoagent/apps/gateway/src/octoagent/gateway/routes/`、`octoagent/frontend/src/`

- [x] T002 [P0] [P] 创建对应测试文件骨架，保证后续可并行补测 → `octoagent/packages/core/tests/test_operator_models.py`、`octoagent/apps/gateway/tests/test_operator_inbox_api.py`、`test_operator_actions.py`、`test_operator_inbox_service.py`、`test_telegram_operator_actions.py`

**Checkpoint**: 代码落点和测试入口清晰，可进入共享模型与 contract 实现

---

## Phase 2: Foundational（共享 schema / 事件 / callback 规则冻结）

**目标**: 先冻结 017 的共享模型、审计事件和 Telegram callback 规则。任何用户故事都建立在这一层之上。

> **警告**: Phase 2 未完成前，不得开始 Web inbox、Telegram inline action 和 retry 逻辑实现

- [x] T003 [P0] [B] 实现 `OperatorItemKind`、`OperatorActionKind`、`OperatorActionOutcome`、`OperatorInboxItem`、`OperatorActionRequest`、`OperatorActionResult`、`OperatorInboxSummary` 模型 → `octoagent/packages/core/src/octoagent/core/models/operator_inbox.py`

- [x] T004 [P0] [B] 更新 `core.models.__init__`、`EventType` 和 `payloads`，新增 `OPERATOR_ACTION_RECORDED` 与 `OperatorActionAuditPayload` → `octoagent/packages/core/src/octoagent/core/models/__init__.py`、`enums.py`、`payloads.py`

- [x] T005 [P0] 为 operator models 编写单元测试，覆盖枚举值、序列化、`recent_action_result`、retry launch 引用 → `octoagent/packages/core/tests/test_operator_models.py`

- [x] T006 [P0] [B] 在 gateway 侧实现 callback 编码/解码辅助，冻结 `oi|...` 紧凑格式与 64-byte 限制校验 → `octoagent/apps/gateway/src/octoagent/gateway/services/operator_actions.py`（或独立 `telegram_operator_actions.py`）

- [x] T007 [P0] 为 callback codec 编写测试，覆盖 approval / alert / retry / pairing 四类动作及超长保护 → `octoagent/apps/gateway/tests/test_telegram_operator_actions.py`

**Checkpoint**: 017 的共享 schema 和 Telegram callback contract 已冻结，可分线并行

---

## Phase 3: User Story 1 — 统一查看所有待处理 operator 工作项（Priority: P1）

**目标**: Web 打开一个入口就能看到 approvals、alerts、retryable failures、pending pairings

**Independent Test**: 同时造出 pending approval、drift alert、retryable failure 和 pending pairing，访问 inbox API 与 Web panel，断言四类 item 都可见

- [x] T008 [P0] [US1] [B] 实现 `OperatorInboxService`：聚合 `ApprovalManager`、`TaskJournalService`、`task_jobs`、`TelegramStateStore` → `octoagent/apps/gateway/src/octoagent/gateway/services/operator_inbox.py`

- [x] T009 [P0] [US1] [B] 实现 `GET /api/operator/inbox` 路由，返回 `OperatorInboxResponse`，并注册到 `main.py` → `octoagent/apps/gateway/src/octoagent/gateway/routes/operator_inbox.py`、`main.py`

- [x] T010 [P0] [US1] 为 `OperatorInboxService` 编写测试，覆盖四类 item 聚合、排序规则和局部 source 降级 → `octoagent/apps/gateway/tests/test_operator_inbox_service.py`

- [x] T011 [P0] [US1] 为 inbox API 编写集成测试，覆盖 summary 计数、局部降级、pairing request 可见性 → `octoagent/apps/gateway/tests/test_operator_inbox_api.py`

- [x] T012 [P1] [US1] [P] 扩展前端类型与 API client，新增 inbox response / action result 类型与 `fetchOperatorInbox()` → `octoagent/frontend/src/types/index.ts`、`api/client.ts`

- [x] T013 [P1] [US1] [P] 实现 `useOperatorInbox` hook，负责拉取 inbox、提交动作、刷新最近动作结果 → `octoagent/frontend/src/hooks/useOperatorInbox.ts`

- [x] T014 [P1] [US1] 实现 `OperatorInboxPanel`，展示 summary、pending 数量、过期信息和四类 item → `octoagent/frontend/src/components/OperatorInboxPanel.tsx`

- [x] T015 [P1] [US1] 将 `OperatorInboxPanel` 接入 `TaskList` 首页，保持现有任务列表不回归 → `octoagent/frontend/src/pages/TaskList.tsx`

**Checkpoint**: Web 侧已形成统一收件箱，而不是继续拆散在多处入口

---

## Phase 4: User Story 2 — 在 Web 或 Telegram 上完成等价操作（Priority: P1）

**目标**: approve / deny / retry / cancel / acknowledge 走同一动作服务，Web 与 Telegram 不再双套语义

**Independent Test**: 从 Web 和 Telegram 对同类 item 触发动作，验证得到同样的 `OperatorActionResult`

- [x] T016 [P0] [US2] [B] 实现 `OperatorActionService.execute()`，接入 approve / deny、cancel、retry、ack、pairing approve/reject 分发 → `octoagent/apps/gateway/src/octoagent/gateway/services/operator_actions.py`

- [x] T017 [P0] [US2] [B] 实现 `POST /api/operator/actions` 路由，统一返回 `OperatorActionResult` → `octoagent/apps/gateway/src/octoagent/gateway/routes/operator_inbox.py`

- [x] T018 [P0] [US2] [B] 冻结 retry 语义：从来源任务创建 successor task / attempt，并返回 `result_task_id` → `octoagent/apps/gateway/src/octoagent/gateway/services/operator_actions.py`、必要时复用 `TaskService` / `TaskRunner`

- [x] T019 [P0] [US2] 为动作服务编写测试，覆盖 approve / deny / cancel / retry / ack / pairing decision 成功路径 → `octoagent/apps/gateway/tests/test_operator_actions.py`

- [x] T020 [P0] [US2] 为动作服务编写测试，覆盖 `already_handled / expired / stale_state / not_allowed / not_found` 五类幂等或失败结果 → `octoagent/apps/gateway/tests/test_operator_actions.py`

- [x] T021 [P1] [US2] 在前端 hook / panel 中接入统一动作提交与 optimistic refresh，避免不同按钮各自发散 → `octoagent/frontend/src/hooks/useOperatorInbox.ts`、`components/OperatorInboxPanel.tsx`

**Checkpoint**: Web 与后端已经共享同一动作 contract，Telegram 只需复用这层

---

## Phase 5: User Story 3 — 最近动作结果与 Telegram 移动端等价操作（Priority: P1）

**目标**: Telegram 成为真正的 operator action surface；跨端重复点击有明确结果

**Independent Test**: Web 先处理一项后，Telegram 再次点击，返回 `already_handled`；Telegram 成功动作后原消息更新为结果态

- [x] T022 [P0] [US3] [B] 扩展 `TelegramBotClient`，支持 inline keyboard、`answerCallbackQuery`、`editMessageText` / `editMessageReplyMarkup` → `octoagent/packages/provider/src/octoagent/provider/dx/telegram_client.py`

- [x] T023 [P0] [US3] [B] 扩展 Telegram update model，支持 `callback_query` 解析，不再要求 callback 带普通文本 → `octoagent/packages/provider/src/octoagent/provider/dx/telegram_client.py`、`octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py`

- [x] T024 [P0] [US3] [B] 在 `TelegramGatewayService` 中接入 operator action callback 分发与结果回写，默认投递到 approved operator DM；无目标时显式降级 → `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py`

- [x] T025 [P0] [US3] [P] 用 inline keyboard 重写 approval/operator card 发送逻辑，避免继续只发“请去 Web 端处理”文本 → `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py`

- [x] T026 [P0] [US3] 为 Telegram operator action 编写测试，覆盖 callback 成功、重复点击、过期、无 operator target 降级 → `octoagent/apps/gateway/tests/test_telegram_operator_actions.py`

- [x] T027 [P1] [US3] 在 inbox projection 中接入最近动作结果，确保 Web 端能看到 Telegram 刚刚执行的结果 → `octoagent/apps/gateway/src/octoagent/gateway/services/operator_inbox.py`

**Checkpoint**: Telegram 具备真正的移动端操作面，且跨端结果一致

---

## Phase 6: User Story 4 — 审计与回放闭环（Priority: P2）

**目标**: 所有 operator action 都进事件链，后续可审计、可回放

**Independent Test**: 对 approval、alert、retry/cancel 至少各执行一次动作，随后查询事件链，确认存在 `OPERATOR_ACTION_RECORDED`

- [x] T028 [P0] [US4] [B] 实现 `OPERATOR_ACTION_RECORDED` 事件写入封装，区分 task-bound action 与 pairing operational task action → `octoagent/apps/gateway/src/octoagent/gateway/services/operator_actions.py`

- [x] T029 [P0] [US4] [B] 在 inbox projection 中消费最近 operator audit event，用于 `recent_action_result` 与 alert ack 抑制逻辑 → `octoagent/apps/gateway/src/octoagent/gateway/services/operator_inbox.py`

- [x] T030 [P0] [US4] 为 audit event 编写测试，覆盖成功动作、失败动作、pairing operational task 三类写入 → `octoagent/apps/gateway/tests/test_operator_actions.py`

- [x] T031 [P1] [US4] 编写 replay/回放测试，验证 alert ack 与 retry successor task 的可追溯关系 → `octoagent/apps/gateway/tests/test_operator_actions.py`

**Checkpoint**: operator control 不再是黑盒按钮，而是可回放的事件链

---

## Phase 7: E2E / 回归与边界保护

**目标**: 用自动化验证把 017 的主闭环、Telegram parity 和既有能力边界固定住

- [x] T032 [P0] [B] 编写端到端集成测试：四类 item 同时存在 -> Web inbox 可见 -> 执行动作 -> recent result 刷新 → `octoagent/apps/gateway/tests/test_operator_inbox_api.py`、`frontend` 最小 smoke

- [x] T033 [P0] [P] 编写跨端幂等测试：Web 先处理，Telegram 后点击，返回 `already_handled` → `octoagent/apps/gateway/tests/test_telegram_operator_actions.py`

- [x] T034 [P0] [P] 执行回归测试：approvals、journal、cancel、telegram ingress、frontend build，确认 006 / 011 / 016 / 019 无回归 → `octoagent/apps/gateway/tests/`、`octoagent/packages/provider/tests/`、`octoagent/frontend/`

---

## Deferred / Boundary Tasks

- [ ] T035 [P1] [SKIP] 原生 mobile app / PWA 专用 operator 客户端 → 后续 Feature 处理
  **SKIP 原因**: 017 先完成 Web + Telegram parity，不扩展新端

- [ ] T036 [P1] [SKIP] 完整 Telegram pairing 后台审批中心 / 多 operator 分发策略 → 后续 Feature 或 M3 处理
  **SKIP 原因**: 017 只消费现有 approved operator target 语义，不扩展复杂多 operator 策略

---

## 并行建议

在 Phase 2 完成后，可按最大并发拆成三条线：

1. `查询线`：T008-T015（projection + API + Web）
2. `动作线`：T016-T021、T028-T031（统一 action + audit + retry）
3. `Telegram 线`：T022-T027（client + callback + operator cards）

唯一硬前置是：T003-T007（共享 schema / 事件 / callback 规则）先完成，再进入三线并行推进。
