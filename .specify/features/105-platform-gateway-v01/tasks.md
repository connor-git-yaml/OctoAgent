# F105 任务分解（tasks.md）

> Phase 与依赖见 plan.md；每任务标注 FR/AC 对应。勾选态据实更新（Final OPUS2-M1）。

## Phase A：channels 包骨架 ✅
- [x] T-A1 `channels/adapter.py`：ChannelCapabilityMeta + ChannelAdapter Protocol（FR-A1/A2/A3）
- [x] T-A2 `channels/registry.py`：PlatformRegistry register/get/resolve/list + notify_task_completion + startup_all/shutdown_all（FR-B1~B4；startup/shutdown 异常传播与 baseline 直调一致——比 plan 初稿更严的等价）
- [x] T-A3 `channels/__init__.py` re-export
- [x] T-A4 测试 test_f105_platform_registry.py（9 个）
- [x] T-A5 回归 + commit

## Phase B：ConversationBinding ✅
- [x] T-B1 模型 + Kind 枚举（FR-E1）
- [x] T-B2 sqlite_init 建表（**UNIQUE 四元组含 project_id**，CODEX-H3）
- [x] T-B3 store：upsert_runtime_binding（无 agent_profile_id 参数）/ get / list / resolve_outbound_route（FR-E2/E4/E5）
- [x] T-B4 StoreGroup 挂载 + export
- [x] T-B5 测试 test_conversation_binding_store.py（8 个，含跨 project 不碰撞）
- [x] T-B6 回归 + commit

## Phase C：adapter 实现 + 装配重导向 ✅
- [x] T-C1 telegram_adapter.py（notification_channel 构造逐行迁移）
- [x] T-C2 web_adapter.py（meta + build_web_inbound_message 工厂）
- [x] T-C3 harness：registry 构造注册（web→telegram）+ C-1/C-2 通知注册 + C-3 completion_notifier + C-4 startup/shutdown + C-6 notify_text 死引用删除 + C-7 app.state
- [x] T-C4 ~~routes/telegram.py 经 registry~~（FR-C2 撤销，route 零变更——pre-impl OPUS-M2 定案）
- [x] T-C5 chat.py 构造点改调 build_web_inbound_message（module-level import；commit 落在 Phase D——同文件 C/D 改动，completion-report §1 声明）
- [x] T-C6 测试 test_f105_channel_adapter.py（7）+ test_f105_harness_wiring.py（2）
- [x] T-C7 回归（现有 153 测试 0 修改全绿）+ commit

## Phase D：binding 热路径写入 ✅
- [x] T-D1 telegram accepted/duplicate touch + thread 维度 metadata + 降级（FR-E3）
- [x] T-D2 chat.py 新会话 + 续聊 touch + **direct-worker 排除**（kind-based，OPUS2-L1 有意偏离字面）+ 降级
- [x] T-D3 测试 test_f105_conversation_binding.py（7 个，含 Codex Final H1 回归 test_project_scoped_continue_touches_same_binding）
- [x] T-D4 回归 + commit（e2e_smoke hook 正常跑 8/8 PASS）

## Phase E：Verify + 文档 ✅
- [x] T-E1 终验全量回归 3931 passed / 0 failed（= baseline 3899 + 32）+ e2e_smoke 8/8（Phase D commit hook + Phase E commit hook）
- [x] T-E2 Codex Final review（1 HIGH 续聊 project 污染——已修+回归测试；1 MED 制品——本 Phase 补齐）
- [x] T-E3 Opus Final 第二评审（0 HIGH / 2 MED 收口项 / 4 LOW 全处理）
- [x] T-E4 completion-report.md + handoff.md + verification-report.md
- [x] T-E5 living-docs：platform-gateway.md 新建 + module-design §9.3 + milestones F105 行
- [x] T-E6 归总报告（等用户拍板，不主动 push）
