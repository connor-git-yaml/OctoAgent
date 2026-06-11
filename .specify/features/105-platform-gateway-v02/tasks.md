# F105 v0.2 Tasks（按 plan Phase 实际执行对账）

## Phase 0 设计 + pre-impl 双评审
- [x] T-0.1 实测侦察（phase-1-recon.md：baseline 3931 固化于代码落盘前 + 8 域 grep/实跑实证 + 外部 API 检索核查）
- [x] T-0.2 spec.md（D1-D17b + 5 US + FR-A~F + AC↔test 绑定）
- [x] T-0.3 plan.md（Phase A-F + EQ-A1~A6 等价论证）
- [x] T-0.4 Codex adversarial pre-impl review（3 HIGH + 1 MED 全接受闭环）
- [x] T-0.5 Opus 第二评审（0 HIGH / 3 MED / 4 LOW 全接受；16/16 事实核查）
- [x] T-0.6 分歧裁定（D13 棘轮：Codex 机制证明 vs Opus 浅层赞同 → 实读 resolver 裁定 Codex 正确）+ spec §11 闭环记录

## Phase A ingress 契约（行为零变更，commit 9442bb55）
- [x] T-A.1 Protocol + inbound_router()（FR-A1）
- [x] T-A.2 telegram adapter 返回 routes 模块单例 router（D2）；web 返回 None（FR-A2）
- [x] T-A.3 harness 挂载循环（FR-A3）+ main.py 撤直挂行 + routes/telegram.py 补 tags（EQ-A6）
- [x] T-A.4 v0.1 FakeAdapter 补方法（FR-A4，grep 实证唯一触点 test_f105_platform_registry）
- [x] T-A.5 test_f105v02_ingress.py 5 测试 + 全量 3936 / 0 failed

## Phase B Slack（commit 含 R5 闭环）
- [x] T-B.1 SlackChannelConfig + DiscordChannelConfig（占位）+ ChannelsConfig 扩展
- [x] T-B.2 SlackApiClient（mrkdwn:false，OPUS-L3）
- [x] T-B.3 SlackGatewayService（验签/授权 D5 修订版/D17a enqueue/binding conversation_type）
- [x] T-B.4 routes/slack.py（200-vs-401 语义）+ SlackChannelAdapter
- [x] T-B.5 SlackNotificationChannel + DiscordNotificationChannel（D9 eligibility）+ channel_reply.py
- [x] T-B.6 harness 构造注册 + main 符号区 + daily_routine 映射（D15）+ hermetic env（R8）
- [x] T-B.7 测试 26 项 + R5 意图保留式闭环 + 全量 3962→闭环全绿

## Phase C Discord（commit 含 cryptography 声明）
- [x] T-C.1 DiscordGatewayService（Ed25519 验签 401 语义 / PING / command / ephemeral 拒绝）
- [x] T-C.2 DiscordApiClient（REST create_message，不存 interaction token）
- [x] T-C.3 routes/discord.py + DiscordChannelAdapter + harness/main 接线
- [x] T-C.4 gateway pyproject + uv lock（diff 2 行，零漂移，未 sync）
- [x] T-C.5 测试 19 项 + 全量 3983 / 0 failed

## Phase D CONFIGURED + 活跃信号 + resolver v2（commit 385eae5b）
- [x] T-D.1 last_runtime_active_at 列（DDL + ensure-column 存量迁移）+ 模型字段
- [x] T-D.2 runtime upsert 恒写活跃证据
- [x] T-D.3 upsert_configured_binding（H1 raise + 棘轮 + 不触碰活跃证据）
- [x] T-D.4 resolve_outbound_route v2（CODEX-H3 闭环；v0.1 测试 0 修改全绿）+ FR-D4 docstring
- [x] T-D.5 harness default_notify_channel 消费（FR-D3，enabled 门 + per-platform 降级）
- [x] T-D.6 测试 9 项 + 全量 3992 / 0 failed

## Phase E L1 惰性 chat_id（独立 commit 可回退）
- [x] T-E.1 TelegramNotificationChannel + chat_id_resolver（additive）+ _effective_chat_id
- [x] T-E.2 telegram_adapter 改传 resolver（删冻结块）
- [x] T-E.3 test_f105v02_telegram_lazy_chat_id.py 6 测试；契约面 0 修改全绿；v0.1 冻结断言按 spec 行为变更区升级

## Phase F 收口
- [x] T-F.1 platform-gateway.md living-docs 同步（L1 关闭 + L5-L8 新增）
- [x] T-F.2 verification-report.md（AC↔test 机械校验 + 回归账目 + 测试改动对照）
- [x] T-F.3 completion-report.md + handoff.md
- [x] T-F.4 Final 双评审（Codex + Opus）+ finding 闭环
- [x] T-F.5 归总报告（等用户拍板，不主动 push）
