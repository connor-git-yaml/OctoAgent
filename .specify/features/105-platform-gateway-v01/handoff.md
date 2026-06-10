# F105 v0.1 → 下游 handoff

**接收方**：F105 v0.2（Slack/Discord）/ F106 User Plugin Loader / 后续渠道域 Feature。
**基线**：feature/105-platform-gateway-v01（5 commits on 02e139fd），3930 passed / e2e_smoke 8/8。

## 1. v0.2 加 Slack/Discord 的实际工作量（诚实预期，CODEX-H2 定调）

**自动获得**（实现 ChannelAdapter Protocol + `PlatformRegistry.register` 一行）：
- 通知扇出（NotificationService 渠道注册，经 `notification_channel()`）
- 任务完成回复派发（`registry.notify_task_completion` → adapter 自判归属）
- 生命周期（startup_all/shutdown_all）
- alias 解析 + capability meta 查询

**仍需 per-platform 写**（inbound 不在 Protocol，spec D3）：
- webhook route（FastAPI route 挂载 + secret 校验）或 socket/polling loop
- 平台事件解析 → NormalizedMessage（scope_id/thread_id/idempotency_key 约定参照 telegram：`chat:{platform}:{conversation}` / 平台原生线程语义）
- octoagent.yaml `channels.{platform}` config schema（现 ChannelsConfig 仅 telegram 字段）
- 授权模型（telegram 的 pairing/allowlist 是 service 内置，新平台自定）

**v0.2 建议第一步：ingress 契约**——给 Protocol 加可选 `inbound_router() -> APIRouter | None`（adapter 自描述 route），harness 统一挂载；telegram 的 webhook route 届时迁入 adapter（v0.1 刻意保留 routes/telegram.py 直读 service，撤销的 FR-C2 背景见 spec §10 OPUS-M2）。

## 2. ConversationBinding 接出站路由前必做（v0.2）

1. **L3 topic 粒度评估**：telegram conversation_id=chat 级，多 topic 群塌一行；topic 维度在 `metadata.last_message_thread_id / last_reply_thread_root_id` 滚动。出站到 topic 群需要 thread 寻址——评估"chat 级行 + metadata"够不够，不够则升 topic 级 row（迁移成本低：表新增行即可）。
2. **resolve_outbound_route 的 explicit 元组不含 project_id**（OPUS2-L2）：多 project 同 thread 场景 explicit 按传入 bindings 的 list 序命中第一条（无确定性保证——调用方 list_by_platform 按 last_active DESC）；接线时若按 project 维度出站必须扩 explicit 形参为三元组或文档化 list-order 契约。
3. **CONFIGURED kind 写入面**：v0.1 物理上只有 runtime 写入。加配置面时**必须收敛到 store 单一入口** + 在该入口做 H1 校验（agent_profile_id 非空必须显式用户拍板动作，参照 spec D5 演进说明）。
4. **source_channel_id 写入端**：dispatch_service USER_CHANNEL 分支（F099）至今 dead branch；接线会改变 A2A audit chain 行为——与 A2A source 泛化一并做，单独立项不要顺手。

## 3. 已知 limitations 清单（spec §8 镜像）

| # | 内容 | 建议处理 |
|---|------|----------|
| L1 | TelegramNotificationChannel chat_id bootstrap 冻结（启动时无 approved user → 通知静默到重启）| v0.2 顺手修：notification_channel 改为惰性解析 chat_id（每次 notify 时查 first_approved_user）——是行为变更，需显式立项 |
| L2 | observation promoter telegram 通知恒不发（notify_text 死引用已删，ObservationRoutine telegram_notify_fn=None）| v0.2 评估是否要 observation 通知面；若要，走 NotificationService 而非独立 fn |
| L3 | telegram 多 topic 塌一行 | 见 §2.1 |
| L4 | completion 失败日志 key 变更（task_runner_completion_notifier_failed → platform_completion_notify_failed）| 已文档化，运维 grep 注意 |

## 4. ApprovalBroadcaster 统一评估（v0.2 范围声明承接）

CompositeApprovalBroadcaster(SSE, TelegramApprovalBroadcaster) 仍直连（spec 2.2 排除）。v0.2 评估：approval broadcast 面是否并入 ChannelAdapter（加 `approval_broadcaster()` 可选面）。注意 TelegramApprovalBroadcaster 委托 service.notify_approval_event（operator target=first_approved_user），与通知通道的 chat_id 冻结语义不同（每次调用现查）。

## 5. F106 Plugin Loader 触点

- ChannelAdapter 是 plugin 形态的天然候选（blueprint module-design L175 的 `channel.telegram` plugin manifest 构想）：plugin 提供 adapter 类 + config schema + route 描述，loader 注册进 PlatformRegistry
- registry fail-fast 语义（Protocol 检查 / platform_id 重复 / alias 冲突）天然适配 plugin 装载失败降级（Constitution #6：单 plugin 坏不拖垮 gateway）

## 6. 给 v0.2 的工程纪律备忘

- 行为零变更对照命令（PYTHONPATH 锁定）固化在 phase-1-recon.md §0——共享 venv 跨 worktree 时**必须**用，禁 uv sync
- "telegram"/"web_sse" channel_name 与 "telegram"/"web" platform_id 是两个命名空间（alias 桥接）；USER.md summary_channels 用前者
- 现有 telegram/notification/chat 测试是行为契约——动 adapter 内部可以，动这些测试断言 = 红旗
- harness `_main_module` 符号间接层保留（测试 monkeypatch 路径依赖）
