# F105 Multi-Platform Gateway v0.1 — Completion Report

**分支**: feature/105-platform-gateway-v01（基线 origin/master @ 02e139fd）
**完成日期**: 2026-06-10
**状态**: 实现完成 + 双评审闭环，**待用户拍板合入（不主动 push）**

## 1. 计划 vs 实际（Phase 对照）

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| 块 A 侦察 | 四路 grep 实证 | ✅ phase-1-recon.md（baseline 3899 固化 + PYTHONPATH 锁定纪律）| 无 |
| spec/plan/tasks | OC-1/2/6/7 + H1 + 行为零变更 | ✅ + pre-impl 双评审修订（FR-C2 撤销 / UNIQUE 四元组 / direct-worker 排除 / US-2 收窄）| review 驱动的设计修正，全记录 spec §10 |
| Phase A channels 骨架 | Protocol + registry + 测试 | ✅ 9 测试 | 无 |
| Phase B ConversationBinding | 模型+表+store+resolver | ✅ 8 测试；UNIQUE 升四元组（CODEX-H3）| 无 |
| Phase C adapter + 装配 | 两 adapter + harness C-1..C-7 | ✅ 9 测试；**C-5 撤销**（route 不动，OPUS-M2/CODEX-H2 定调）；chat.py 工厂接线顺延到 Phase D commit（同文件 C/D 改动）| 已声明 |
| Phase D binding 热路径 | telegram + chat 写入 + H1 排除 | ✅ 7 测试（含 Codex Final H1 回归）| 无 |
| Phase E verify + docs | 回归/e2e/双评审/四制品/living-docs | ✅ 本 commit | 无 |

## 2. 解决的问题（用户视角）

1. **新平台接入成本结构化**：v0.2 加 Slack/Discord 时，通知推送、任务完成回复、生命周期三面"实现 Protocol + 注册"自动获得——harness 原有 4 处硬编码 telegram 接触点（通知注册 ×2 / completion_notifier / startup-shutdown）全部 registry 化。inbound（route/解析）仍 per-platform（诚实边界，见 spec §1——双评审一致定调强行统一 inbound 是假抽象）。
2. **会话路由状态有了底座**：conversation_bindings 表记录"用户最后在哪个平台哪个会话说话"（last-route），v0.2 出站渠道选择（explicit→last_active→single-configured）的 resolver 已实现并测试，只差接线。
3. **H1 被构造性固化**：此前"Telegram 全进主 Agent"只是事实；现在 binding 写入面物理上写不进非主 Agent（签名无该参数）+ direct-worker 直聊会话显式排除——OpenClaw 式"平台指向不同 agentId"在 v0.1 写入面不可能发生。
4. **现有体验零变化**：Telegram 收发/pairing/审批按钮/通知 dismiss、Web 聊天流/通知列表全部照旧（3931 passed 0 regression + 153 个现有渠道测试 0 修改 + e2e_smoke 8/8）。

## 3. 改动清单

**新增**（8 文件）：
- `gateway/channels/`: adapter.py / registry.py / telegram_adapter.py / web_adapter.py / __init__.py
- `core/models/conversation_binding.py` + `core/store/conversation_binding_store.py`
- `docs/codebase-architecture/platform-gateway.md`

**修改**（7 文件）：
- `core/store/sqlite_init.py`（conversation_bindings 表 + 索引）/ `core/store/__init__.py`（StoreGroup 挂载）/ `core/models/__init__.py`（export）
- `gateway/harness/octo_harness.py`（装配 registry 化 + notify_text 死引用删除）
- `gateway/services/telegram.py`（binding 登记 helper）
- `gateway/routes/chat.py`（工厂接线 + binding 登记 + direct-worker 排除 + 续聊 project 反解）
- `docs/blueprint/module-design.md` §9.3 + `docs/blueprint/milestones.md` F105 行

**测试**（5 文件 32 个）：test_f105_platform_registry(9) / test_conversation_binding_store(8) / test_f105_channel_adapter(7) / test_f105_harness_wiring(2) / test_f105_conversation_binding(7，含 Codex Final H1 回归)

**净体量**：生产代码 +~700 行（channels 包 ~450 + binding 模型/store ~230 + 热路径接线 ~70 - harness 内联迁出 ~50）；telegram.py 1057→1104；chat.py 560→620。

## 4. 双评审全记录（SC-6）

### Pre-impl（spec/plan，2026-06-10）
- **Codex**（needs-attention，4 HIGH）：H1 baseline 污染【拒绝——时序实证 baseline 先于全部代码，吸收 docs/code 分 commit 纪律】；H2 US-2 免改 harness 过载【接受——收窄 + handoff ingress 提案】；H3 binding 键丢维度【接受——UNIQUE 四元组】；H4 direct-worker 污染 H1【接受——排除规则 + 测试】
- **Opus**（APPROVE-WITH-CHANGES，0H/4M/2L，事实核查 9/9）：M1 completion 失败日志面【接受文档化，grep 证无断言】；M2 双轨 fallback 坏味道【接受——FR-C2 撤销 + module import 工厂】；M3 抽象命名过载【接受——§1 诚实边界段】；M4 粒度不对称 + scope 抖动【部分接受——不对称文档化 L3；"scope_id 抖动"机制经 telegram.py L544 实读驳回】；L1/L2 全接受
- **分歧人裁项**：CODEX-H2 vs OPUS-M3 同题 severity 分歧（HIGH vs MED）——处理方向一致已吸收，无实质待裁；OPUS-M4 部分机制驳回带代码证据

### Final（实现，2026-06-10）
- **Codex**（needs-attention，1 HIGH + 1 MED）：
  - F-H1 续聊写空 project 第二行污染 last-route【**接受，真 bug**——纯 task_id 续聊时请求侧 project_id 空 → (web, thread, '') 新行。修复：从 existing_task.scope_id 反解 project（与首条创建语义恒一致）+ 回归测试 test_project_scoped_continue_touches_same_binding】
  - F-M1 Phase E 制品缺失【接受——review 跑在 Phase D commit 后、Phase E 写作中；本 commit 补齐四制品 + living-docs】
- **Opus Final**：（见下节）

### Opus Final 评审结果

**APPROVE-WITH-CHANGES，0 HIGH / 2 MED / 4 LOW**。亮点：①全部 6 项 pre-impl finding 逐条 grep 实证真兑现；②三个定向追问（shutdown 段条件切换会否漏停 telegram polling / registry lazy import 失败面 / chat_id 冻结时机）**全部经 bootstrap 时序实证闭死**——"polling 已启动 ⟹ registry 存在"不变量成立；③H1 封死性独立验证（构造性签名 + ON CONFLICT 不触 agent_profile_id/binding_kind + metadata 无走私面 + kind-based 排除对主 Agent profile 正确放行）；④AC↔Test 绑定抽查 10 条全部真实且语义匹配，184 测试实跑 PASS。

| Finding | 处理 |
|---------|------|
| OPUS2-M1 tasks.md 勾选态脱节 | **接受**——已据实更新全部勾选 + Phase 偏离声明 |
| OPUS2-M2 Phase E 制品/全量数字未固化 | **接受**——本 commit 补齐（verification-report 含 3931 终验数字 + e2e_smoke 记录；living-docs 三处同步）；review 时点恰在 Phase E 写作中 |
| OPUS2-L1 direct-worker 排除 kind-based vs spec 字面 presence-based | **接受（实现优于字面）**——spec FR-E3 已校正措辞并记 intentional deviation：显式传主 Agent profile 的会话 kind=SELF 正确登记，presence-based 反而会错跳 |
| OPUS2-L2 resolve_outbound_route explicit 2 元组 vs 4 元组键歧义 | **接受**——handoff §2.2 已记录（v0.2 接线前扩 explicit 维度），补 list-order 语义说明 |
| OPUS2-L3 store assert 在 -O 被剥离（F124 同类）| **接受已修**——改 `if None: raise RuntimeError` |
| OPUS2-L4 harness 注释引用不存在的 `_bootstrap_channels` 方法名 | **接受已修**——两处改 `_bootstrap_runtime_services` |

**Codex Final ↔ Opus Final 分歧人裁项**：无实质分歧——Codex F-H1（续聊 project 污染）是 Opus 未覆盖的真 bug（已修）；Opus 的 OPUS2-L1（kind-based 优于字面）Codex 未提，已按"实现优于字面、保留 intent"处理并校正 spec 措辞。两者对"0 实质代码 HIGH 残留"结论一致。

## 5. 已知 limitations（spec §8 + handoff §3）

L1 telegram 通知 chat_id bootstrap 冻结（baseline 既有）/ L2 observation telegram 通知恒不发（死引用已删语义如旧）/ L3 telegram conversation_id=chat 级（topic 维度在 metadata.last_*）/ L4 completion 失败日志 key 变更（已文档化非用户可见）。

## 6. living-docs 漂移闸（SDD 规则）

- ✅ `docs/codebase-architecture/platform-gateway.md` 新建（channels 包实现级文档）
- ✅ `docs/blueprint/module-design.md` §9.3 同步（F105 组件清单）
- ✅ `docs/blueprint/milestones.md` F105 行更新（实现完成态 + 数字）
- 无 drift 残留：core-design.md 渠道叙述（L688 inline keyboard / L908 多渠道 I/O）与实现不冲突（描述的是 UX/职责层，未涉装配细节）；CLAUDE.local.md F105 行更新留主 session 合入时做（worktree 不动用户私有文件）

## 7. deferred / 二级 follow-up（不套娃，全部归 handoff）

v0.2：Slack/Discord + ingress 契约 + last-route 接线 + source_channel_id 写入端（与 A2A source 泛化一并）+ ApprovalBroadcaster 统一评估 + CONFIGURED 配置面（H1 校验单点）+ L1 修复。F106：adapter 作为 plugin 候选扩展点。详见 handoff.md。

## 8. 合入建议

**建议合入 origin/master**：行为零变更有三层证据（0 regression 精确对账 + 现有测试 0 修改 + e2e_smoke 过闸）；双评审 4 轮（pre-impl ×2 + final ×2）0 HIGH 残留；新增面全部 additive 带降级保护。风险面主要在 harness 装配重导向，已有装配序断言测试 + e2e_smoke 全 bootstrap 覆盖。
