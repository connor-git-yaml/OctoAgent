# F105 v0.2 Completion Report

**分支**: feature/105-platform-gateway-v02（base origin/master 088ce2d4）
**完成日期**: 2026-06-12
**回归**: 3997 passed / 0 failed（baseline 3931 + 66 新测试，0 regression）；每 commit pre-commit hook e2e_smoke 8/8 实跑（未用 SKIP_E2E）

## 1. 计划 vs 实际（Phase 对照）

| Phase（plan） | 状态 | 偏离 |
|---------------|------|------|
| 0 设计 + pre-impl 双评审 | ✅ | Codex 3H+1M / Opus 3M+4L 全接受；1 真分歧裁定（§4） |
| A ingress 契约 | ✅ | 无偏离；六条等价论证全成立 |
| B Slack | ✅ | ①DiscordChannelConfig 提前进 B commit（config 同文件拆分不值得，commit message 注明占位）；②FR-D2 通知渠道类按 plan 落 B（含 Discord 类——同 patch 自然）；③R5 预判命中：test_f102 样例 "slack" 失效 → 意图保留式闭环 |
| C Discord | ✅ | uv.lock diff 仅 2 行（R3 最优情形，无需升级决策点） |
| D CONFIGURED + resolver v2 | ✅ | v0.1 resolver 测试 **0 修改全绿**（FR-D6 ⑤ 评估的"或仅构造调整"未发生——_runtime_activity_at 兜底设计兑现） |
| E L1 惰性 chat_id | ✅ | test_f105_channel_adapter 冻结断言升级（spec 行为变更区内，§3 论证） |
| F 文档 + Final 双评审 | ✅ | 见 §4 |

## 2. 交付物清单（用户视角）

1. **Slack 接入**：配置 signing secret / bot token / allow_users 后，DM 或授权频道给 bot 发消息 → 任务执行 → 同 thread 收回复；daily summary 等通知送到最后私聊的 DM（或配置的 default_notify_channel）。
2. **Discord 接入**：`/octo prompt:...` slash command → 即时受理回执 → 完成后频道收结果。普通频道消息监听（需 WS Gateway）显式 v0.2 范围外。
3. **配置即可收通知**：`channels.{slack,discord}.default_notify_channel` 配好重启即生效，不需要先发一条消息。
4. **Telegram 通知不再要求重启**（L1 修复）：新装机启动后完成配对，第一条通知就能送达。
5. **架构面**：新平台接入成本验证成立——outbound 三面 + route 挂载经"实现 Protocol + register"自动获得（SC-4：Slack/Discord 接入对 main.py route 注册零改动、对 harness 通知/完成回复/生命周期装配逻辑零改动）。

## 3. 安全/哲学要点

- **公网 webhook 面 deny-by-default**：Slack v0 HMAC（raw body + 防重放 + constant-time）/ Discord Ed25519（失败必 401）；授权 allowlist 空 = 拒（含"非 DM 必须显式 allowed_channels"，CODEX-M1 闭环）；Slack 可选 team_id workspace 边界。
- **通知防泄露**（CODEX-H2 闭环）：多人频道的 runtime binding 永不作为通用通知目标——eligibility = DM 类 runtime ∪ 显式 CONFIGURED。
- **重试恢复**（CODEX-H1 闭环）：duplicate + task 仍 CREATED → 补 enqueue（状态守卫防终态重入队）；平台 retry 从"被浪费"变"恢复窗口"。
- **H1 不变量**：runtime 写入面签名仍无 agent_profile_id；CONFIGURED 唯一入口非空必 raise（应用层构造性收敛，OPUS-M3 口径）；新平台 inbound 无任何 agent 选择参数。
- **secrets 纪律**：signing secret / bot tokens 全 env 间接引用；Discord 公钥（非 secret）落 config；e2e hermetic 清单纳入 3 个新 env（R8）。

## 4. 双评审闭环（SC-6）

- **Pre-impl**：Codex needs-attention（3 HIGH + 1 MED）+ Opus APPROVE-WITH-CHANGES（0H/3M/4L）→ 11 条全接受闭环（spec §11 表）。**分歧人裁记录**：D13 棘轮设计 Opus 认可 vs Codex H3 机制性否定——主 session 实读 resolver tier 2 代码裁定 Codex 正确（Opus 该点未检查 resolver 交互），按 Codex 方案修订（D17b）。0 HIGH 残留。
- **Final**：见 codex-review-final 记录（spec §12 / 本报告附录）——结论 0 HIGH 残留后才收口。

## 5. 已知 limitations（v0.2 显式接受，platform-gateway.md §5 镜像）

| # | 内容 | 去向 |
|---|------|------|
| L2 | observation promoter 通知恒不发 | 维持范围外 |
| L3 | telegram chat 级 binding（评估结论：v0.2 出站消费者无 topic 需求） | 失效条件入 handoff |
| L5 | Discord 仅 slash command 可达（WS Gateway 范围外） | v0.3+ |
| L6 | Slack/Discord 无交互式审批/dismiss 按钮（send_approval_request 恒 False） | v0.3 与 interactive components |
| L7 | doctor 不诊断新平台 config（OPUS-M2 显式排除） | 运维指引：靠 webhook 探测信号 |
| L8 | telegram ingest 同型"落盘未入队"窗口（baseline 既有，零变更红线未动） | 独立 fix Feature（handoff §3） |
| L9 | `_build_plain_state_change_text` 与 telegram 实例方法 ~20 行平行实现（零变更红线下的有意重复） | 下个触碰 notification.py 的 Feature 顺手合并 |

## 6. Living-docs 漂移闸

- `docs/codebase-architecture/platform-gateway.md` 已同步 v0.2（组件地图 + 平台接入要点表 + binding v2 语义 + limitations 表 L1 关闭/L5-L8 新增）。
- Blueprint 影响评估：渠道域属 module-design §9.3 范围，platform-gateway.md 是其实现级文档（v0.1 先例）；milestones.md F105 行待用户拍板合入后更新（主 session 归总报告流程）。
