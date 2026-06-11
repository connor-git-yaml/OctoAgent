# F105 v0.2 → 下游 handoff

**接收方**：F105 v0.3（交互组件/审批统一）/ source_channel_id+A2A 泛化立项 / F106 User Plugin Loader / telegram enqueue 窗口 fix Feature。
**基线**：feature/105-platform-gateway-v02（base 088ce2d4），3997 passed / e2e_smoke 8/8。

## 1. v0.3 的实际工作量画像

**自动获得**（v0.2 起新平台 = 实现 Protocol + register）：通知扇出 / 完成回复派发 / 生命周期 / **inbound route 挂载**（v0.2 ingress 契约）。
**仍 per-platform**：config schema / 事件解析（验签+分流）/ 授权模型 / 出站 client。SC-4 实证：Slack→Discord 第二平台接入对 main.py 与 harness 装配逻辑零改动。

**v0.3 建议第一步：interactive components**（Slack block actions + Discord message components）——这是 L6（无按钮审批/dismiss）与 ApprovalBroadcaster 统一（§4）的共同前置。需要新增 interactivity 回调端点（Slack 单独的 interactivity URL POST + Discord type 3 component interaction），建议直接走 ingress 契约挂载。

## 2. 接线现状与边界（v0.2 落定）

1. **resolve_outbound_route v2 语义**：tier 2 按 `_runtime_activity_at`（last_runtime_active_at > RUNTIME 兜底 last_active_at）不分 kind；configured 升级保留活跃证据。**首个 explicit 生产消费者**须处理 list-order 契约（docstring 已写）或扩三元组（OPUS2-L2 归档至该消费者）。
2. **通知 eligibility**：DM 类 runtime（conversation_type ∈ {im,dm}）∪ CONFIGURED；conversation_type 由各平台 ingest 写入 binding metadata——新平台必须沿用该键（CODEX-H2 防泄露不变量）。
3. **CONFIGURED 单一入口**：`upsert_configured_binding` 是唯一合法 CONFIGURED 写入面（H1 非空 raise）；`reconcile_config_managed_binding`（Final CODEX-F-H1）是其上的**配置单例 reconcile**（metadata.source="default_notify_config" 标记；旧行降级/删除；内部仍经单一入口写入）。未来"显式用户拍板"的非主 Agent 配置面必须在该入口扩展校验，不得另开写入口；未来手工 CONFIGURED 面与 config-managed 行共存时，reconcile 只动带标记的行（纪律已固化在 docstring）。
4. **L3 失效条件**：当出现"主动发消息到指定 telegram topic"类消费者时，重评 topic 级 binding row（v0.2 出站消费者全部 chat/DM 级 + task 锚定，chat 级够用）。

## 3. 待立项/顺手项清单

| # | 内容 | 建议 |
|---|------|------|
| H-1 | **telegram ingest "落盘未入队"窗口**（R9/L8）：telegram 仅 created 时 enqueue，webhook 重投/polling 重读判 duplicate 不补队——与 D17a 同型缺口，baseline 既有 | 独立小 fix Feature（~30 行 + 测试，照搬 slack `_maybe_enqueue` 状态守卫模式）；动 telegram 行为面，需走 review |
| H-2 | **source_channel_id 写入端**：dispatch_service USER_CHANNEL 分支（F099）仍 dead branch；slack/discord 接入后该泛化更有价值 | 与 A2A source 泛化一并**单独立项**（会改 audit chain，v0.1/v0.2 两次确认不顺手做） |
| H-3 | L9 **两对**平行实现合并：①通知文本（_build_plain_state_change_text vs telegram 实例方法）；②完成回复文本（channel_reply.build_task_result_text vs telegram._build_result_text，OPUS2-L-3）——改任一侧须同步另一侧 | 下个触碰对应文件的 Feature 顺手（telegram 改用共享 helper，零变更红线解除后） |
| H-4 | Slack/Discord e2e_live 用例（webhook 签名链路 + binding 出站闭环） | 归 F119 模式的下一轮 e2e 补全 |
| H-5 | sender_name 增强：slack 仅 user id（无 display name，需 users.info API + 缓存）；discord 已有 username | v0.3 评估（出站 API 调用 + 缓存层，别顺手） |

## 4. ApprovalBroadcaster 统一评估结论（v0.2 评估-only 承诺兑现）

**不并入 v0.2，推荐 v0.3 与 interactive components 同期**：纯文本审批推送收到也批不了（负 UX，F125 反"狼来了"同类教训）；现状两条审批路径（ApprovalManager→Composite(SSE,Telegram) operator 定向 + NotificationService→channel.send_approval_request F101）合一需要装配改造，收益只在按钮落地后成立。届时方案：Composite 改为 registry 遍历 `approval_broadcaster()` 可选面，telegram 的 first_approved_user 每次现查语义与 v0.2 L1 修复后的通知面已对齐。

## 5. 运维接入指引（doctor 不覆盖新平台，L7）

- **Slack**：创建 app → Event Subscriptions 开启，Request URL=`https://<host>/api/slack/events`（系统会自动应答 url_verification challenge）→ 订阅 bot events：`message.im`（DM）+ 按需 `message.channels`（频道，配 allowed_channels）→ **不要订阅 app_mention**（与 message 双事件会双任务，service 已 ignore app_mention 兜底）→ OAuth scopes：`chat:write` + `im:history`（+`channels:history` 如订阅频道）→ env：`SLACK_SIGNING_SECRET` / `SLACK_BOT_TOKEN`；yaml：enabled/allow_users(Slack user id)/团队边界 team_id 建议配置。
- **Discord**：创建 application → General Information 取 PUBLIC KEY 填 `channels.discord.public_key` → Interactions Endpoint URL=`https://<host>/api/discord/interactions`（验签失败 401 是注册探测预期，配置正确后保存成功）→ 注册 slash command（一次性，如 `POST /applications/{app_id}/commands` body `{"name":"octo","description":"...","options":[{"type":3,"name":"prompt","description":"...","required":true}]}`）→ bot 加入 server 且对目标频道有 Send Messages 权限 → env：`DISCORD_BOT_TOKEN`；yaml：enabled/allow_users(Discord user id)/allowed_channels(guild 频道必填)。
- **配置校验信号**：webhook 401=签名/secret 配错；403=env 缺失（blocked）；503=未 enabled。
- **通知**：USER.md `summary_channels` 现接受 `slack` / `discord` 值；`default_notify_channel` 配置后无 inbound 也能收通知。

## 6. 工程纪律备忘（沿 v0.1 §6 + v0.2 新增）

- PYTHONPATH 锁定范式（v0.2 phase-1-recon §0）+ 禁 uv sync；`uv lock` 是 resolution-only 安全（v0.2 实证 diff 2 行）。
- 通知 channel_name 命名空间新增 "slack"/"discord"（与 platform_id 同名直映，无 alias 桥接差异——telegram/web_sse 的历史包袱不复制）。
- **现有 telegram/notification/chat 测试断言仍是行为契约**；v0.2 仅有的两处既有测试改动（test_f102 样例 token / test_f105_channel_adapter 冻结断言）均为 spec 显式行为变更区的意图保留式更新，先例论证见 verification-report §3——下游不得引用为"可以改契约断言"的先例。
- harness `_main_module` 间接层新增 SlackGatewayService/SlackApiClient/DiscordGatewayService/DiscordApiClient 四符号（monkeypatch 契约）。
- conversation_bindings 新列 last_runtime_active_at 经 ensure-column 迁移（幂等）；存量行 NULL 由 resolver RUNTIME 兜底正确处理——**不需要数据回填**。
