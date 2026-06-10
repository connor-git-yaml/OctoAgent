# F105 任务分解（tasks.md）

> Phase 与依赖见 plan.md；每任务标注 FR/AC 对应。

## Phase A：channels 包骨架
- [ ] T-A1 `channels/adapter.py`：ChannelCapabilityMeta + ChannelAdapter Protocol（FR-A1/A2/A3）
- [ ] T-A2 `channels/registry.py`：PlatformRegistry register/get/resolve/list + notify_task_completion + startup_all/shutdown_all（FR-B1~B4）
- [ ] T-A3 `channels/__init__.py` re-export
- [ ] T-A4 测试 test_f105_platform_registry.py（US-2 AC-1/AC-2；FakeAdapter 扇出/alias/冲突/生命周期/异常隔离）
- [ ] T-A5 回归 + commit（feat(channels)）

## Phase B：ConversationBinding
- [ ] T-B1 `core/models/conversation_binding.py`：模型 + Kind 枚举（FR-E1）
- [ ] T-B2 sqlite_init.py 建表（UNIQUE 三元组 + last_active idx）
- [ ] T-B3 `core/store/conversation_binding_store.py`：upsert_runtime_binding（无 agent_profile_id 参数，FR-E2）/ get / list_by_platform / list_recent / resolve_outbound_route 纯函数（FR-E4/E5）
- [ ] T-B4 StoreGroup 挂载 + __init__ export
- [ ] T-B5 测试 test_conversation_binding_store.py（US-3 AC-4 三级策略 + upsert/touch/唯一性）
- [ ] T-B6 回归 + commit（feat(core)）

## Phase C：adapter 实现 + 装配重导向
- [ ] T-C1 `channels/telegram_adapter.py`（FR-C1；notification_channel 构造逻辑自 harness 迁移，逐行对账）
- [ ] T-C2 `channels/web_adapter.py`（FR-D1/D2 工厂）
- [ ] T-C3 octo_harness 装配：构造 registry + 注册 web→telegram + C-1/C-2 通知注册重导向 + C-3 completion_notifier + C-4 startup/shutdown + C-6 notify_text 死引用删除 + C-7 app.state.platform_registry（FR-C3/C4）
- [x] T-C4 ~~routes/telegram.py 经 registry~~（FR-C2 撤销，route 零变更——pre-impl review OPUS-M2 定案）
- [ ] T-C5 chat.py 构造点改调 build_web_inbound_message（FR-D2；module-level import，无 fallback）
- [ ] T-C6 测试 test_f105_channel_adapter.py + test_f105_harness_wiring.py（US-1 AC-3/AC-4，US-2 AC-3）
- [ ] T-C7 回归（现有 telegram/notification/chat 测试 0 修改验证）+ commit（refactor(gateway)）

## Phase D：binding 热路径写入
- [ ] T-D1 telegram `_ingest_update` accepted/duplicate 路径 upsert + thread 维度 metadata + 降级（FR-E3，CODEX-H3）
- [ ] T-D2 chat.py 新会话 + 续聊路径 upsert + **direct-worker 排除** + 降级（FR-E3，CODEX-H4）
- [ ] T-D3 测试 test_f105_conversation_binding.py（US-3 AC-1/2/3/5 + test_direct_worker_session_not_bound）
- [ ] T-D4 回归 + commit（feat(gateway)）

## Phase E：Verify + 文档
- [ ] T-E1 全量回归 0 regression + e2e_smoke 8/8（SC-1/SC-2）
- [ ] T-E2 Codex Final adversarial review（background）→ finding 闭环
- [ ] T-E3 Opus 第二评审（spec 对齐专项）→ 分歧人裁清单（SC-6）
- [ ] T-E4 completion-report.md + handoff.md + verification-report.md
- [ ] T-E5 living-docs：docs/codebase-architecture + blueprint 同步（漂移闸）
- [ ] T-E6 归总报告（等用户拍板，不主动 push）
