# F149 Design Gate 第一轮返修记录（闭环结论已撤销）

> 历史说明：本轮曾自评 12 项闭环，但 main 第二轮审查证明该结论不成立。本文件只保留审查历史，不得作为 Gate PASS 证据；最新状态见 `design-gate-review-round2.md`。

| # | main 审查项 | 当时证据 | 当时自评（已全部撤销） |
|---:|---|---|---|
| 1 | 10 个 surface 必须逐页唯一 Desktop/390 frame、逐页事实表、现状/F148 shell 参考 | `claude-design-prompt.md`：20 个唯一 frame、10 行事实表、`F149/References`、Shared-States、Advanced-Pattern | 历史自评；Reference 与 Prompt 前置后来被重做 |
| 2 | endpoint/stream/action contract manifest；SSE final 与动态 action 强类型 | `contract-manifest.md` §2–§7；`spec.md` FR-010 | 历史自评错误采用 blanket unknown/object 禁令；以 Round2 命名 raw boundary + decoder 规则为准 |
| 3 | L3 Python 命令必须符合 worktree 契约 | `contract-manifest.md` §7、`tdd-evidence-policy.md` §2 | 历史自评；命令事实以 Round2 为准 |
| 4 | 禁令负向 rg 自我失败；TDD 必须有实际 RED 时间/SHA/输出 | `tdd-evidence-policy.md` §1–§5；`spec.md` FR-014/017 | 历史自评；证据纪律以 Round2 为准 |
| 5 | 单一 transport 审计覆盖 approval/memory API helper；409/404 与 auth 分离 | `contract-manifest.md` §4/§8；`recon.md` §4.1/§12 | 历史自评；触达闭包事实以 Round2 为准 |
| 6 | F150 全局 Access 与 F149 resource 403 ownership | `recon.md` §8、`spec.md` FR-021、`blueprint-sync.md` | 历史自评；ownership 以 Round2 为准 |
| 7 | env/path/command sensitivity policy，secret 永不下发/渲染/复制 | `recon.md` §10、`spec.md` FR-022、Prompt 全局约束、`blueprint-sync.md` | 历史自评；write-only mutation/egress 以 Round2 为准 |
| 8 | Application Port 只是按需 seam，不增每页实体/第二状态 | `recon.md` §7、`research/tech-research.md` §3、`spec.md` FR-024 | 历史自评；目录职责映射以 Round2 为准 |
| 9 | generated wire DTO 硬要求；types-only 优先；禁止第二 fetch client | `contract-manifest.md` §1、`research/tech-research.md` §4、`spec.md` FR-010/011 | 历史自评；wire boundary 以 Round2 为准 |
| 10 | L4 按 domain co-locate；全路由 390 sweep；A/B 旅程保持最小 | `spec.md` FR-016 与测试矩阵；`recon.md` §9 | 历史自评；测试分层以 Round2 为准 |
| 11 | frontend changed-lines 仅 authored executable TS/TSX，排除 tests/generated/.d.ts/type-only | `spec.md` FR-018 与测试矩阵；`research/tech-research.md` §5 | 历史自评；coverage gate 以 Round2 为准 |
| 12 | 依赖规则无箭头歧义 | `recon.md` §7、`research/tech-research.md` §3、`spec.md` Architectural Constraints | 历史自评；分层规则以 Round2 为准 |

## main 已完成的范围决定

- Approvals 维持 F145 的新记忆、记忆整合、行为压缩三类候选；不合并通用 tool approval。
- Automation 维持 pause/resume；不增加 create/run/delete。
- Tasks 不增加 cancel/resume。

上述决定已进入 `recon.md`、`spec.md` 与最新版 Claude Design Prompt，不再请求用户拍板。

## 历史时点尚缺的设计制品

以下是第一轮记录，不是当前行动指令。当前 Prompt 必须等 `pending-product-decisions.md` 8/8 决定后才可发送；Reference pack 已由 Worktree 回存：

1. 20/20 唯一页面 frame：10 个 surface 各 Desktop 与 390；
2. ~~`F149/References`：每页现状截图、F148 shell Desktop/390、路由/宽度/日期；~~ 后续已由 Worktree 以 deterministic fixture 完成并经 main 视觉复核；
3. `F149/Shared-States`；
4. `F149/Advanced-Pattern`；
5. 可机械核对 frame 名称的 `.dc.html` 或逐 frame 导出截图。

在以上制品回存前，本 Feature 停留在 Design Gate。
