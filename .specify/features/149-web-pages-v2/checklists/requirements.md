# Specification Quality Checklist: F149 Web 其余页面 v2

**Purpose**: 在 Design Gate 前验证需求完整性与可审查性
**Created**: 2026-07-20
**Feature**: `../spec.md`

## Content Quality

- [x] 聚焦用户价值与架构边界，没有进入具体生产实现
- [x] A/B 波路由、当前能力与明确非目标均有定义
- [x] 普通用户语言与 Advanced 技术信息边界明确
- [x] Claude Design 缺口与 actual input 前置被记录为 blocker，没有臆造布局；Reference/产品决定已齐，Prompt 标记 READY TO SEND
- [x] 产品 surface 边界明确：桌面端保留 Web；手机产品只走原生 iOS，不把 390px Web/手机浏览器作为移动产品入口；390px 仅为 Web 窄窗口响应式健壮性，不是手机产品/移动认证/iOS 验收，F149 不新增 iOS 实现任务
- [x] Claude Design 最开始方案是 Web/iOS 共同视觉/交互基线；现有 Web 仅为 current-state evidence。偏离只允许明确功能合同/可用性/无障碍/iOS 原生平台规范，记录原因与影响，禁止以实现方便为理由
- [x] iOS 保留同一视觉语言但使用 SwiftUI、Apple 原生导航/手势/控件/无障碍语义，不复制 Web 组件结构；iOS 实现由其 Feature 承担

## Requirement Completeness

- [x] 每条 functional requirement 可测试且无歧义
- [x] 10 个 Web surface 的唯一 Desktop/390 Web 窄窗口 frame、loading/empty/error/origin-403 与 accessibility 均有验收要求
- [x] REST OpenAPI、raw snapshot decoder、Gateway SSE、有限 action contract、UI view model 与单一 transport 边界明确
- [x] F150/F151/main 放行条件明确，且T000已在stable commits与rebase/recon证据齐全后关闭
- [x] 未遗留 `[NEEDS CLARIFICATION]` 占位符

## Quality Hard Gates

- [x] TDD RED → GREEN → REFACTOR 与 atomic relocation 证据要求进入 Spec
- [x] L4/L3/L1/L2 的职责、co-located L4、全路由 390 Web 窄窗口 sweep 和精确目标命令进入测试矩阵；不覆盖移动认证/iOS
- [x] 无歧义依赖规则、禁止 import、按需 structural port、禁止 fallback/全局注入/重复状态进入 Spec
- [x] FR/测试层/文件/命令/失败 oracle 有 Design Gate 初始映射
- [x] Python 命令从 `octoagent` cwd 使用 `PYTHONNOUSERSITE`、完整 PYTHONPATH、`uv run --project . --no-sync python -m pytest`
- [x] command policy 只解析实际 command，不用会命中禁令说明的负向 grep
- [x] TDD evidence 要求实际输出、exit code、UTC 时间、HEAD SHA、工作树状态与稳定 oracle
- [x] 坏味道审计含真实 source/line/baseline/current/处置/owner，并区分机械扫描与 adversarial review
- [x] frontend changed-lines 只统计 authored executable TS/TSX，排除 tests/generated/`.d.ts`/type-only；≥90% 不是质量替代

## Scope Safety

- [x] T000关闭前仅新增Spec Driver制品，没有生产实现；后续生产变更必须逐task TDD
- [x] 不修改 F150/F151 生产代码
- [x] 不 commit、不 push
- [x] Design/Tasks Gate均已通过；F150/F151 stable commit、rebase/recon与main Implement
  放行已在T000闭合

## Known External Inputs

- [x] current Reference pack 22/22 已按 `actual-input-manifest.md` 回存，并标 deterministic fixture/not real backend
- [x] canonical manifest 与 Claude 可见 `references/current/actual-input-manifest.md` 的用途已分离，生成副本通过 byte/hash equality gate
- [x] 解压后直接上传目录与备份 ZIP 均实际包含 nested manifest、metadata、22 PNG、READY Prompt、产品决定、发送说明与逐文件核对表
- [x] Prompt/发送说明只使用完整项目 UUID；ZIP 明确仅作备份，连接中断后必须重新核对 24 个 Reference 文件可见性
- [x] Claude Design READY Prompt 已提供 20 个唯一 page frame、逐页事实表、Shared-States/Advanced-Pattern/References，并明确 Tasks/Settings 归位、Spotify 禁止继承、F148/`--cp-*` provenance 与普通用户术语 absence
- [x] Claude Design anchor 4 首轮远端输出已观察到 20 个 frame 名称、Shared-States、Advanced-Pattern 与 References 22/22 基础结构
- [x] Claude Design 已回存可审查的 `.dc.html`：`../design-output/2026-07-21/OctoAgent Web.dc.html`；hash/项目/日期/来源见同目录 `export-manifest.md`
- [x] Claude Design 已交付恰好 20 行逐 frame F148/`--cp-*` provenance、Spotify=`none`、ordinary-language absence、states 与 390 Web 窄窗口 overflow/focus 证据
- [x] Claude Design 已交付 10 surface × loading/empty/error/origin403/not-found/disconnected/409 applicability matrix，并把适用状态落到各页面
- [x] Claude Design 已移除 unknown task event 默认时间线、MCP `5s`、restart `约 1 分钟/检查点续跑`、403 owner/重新登录、390 Web 窄窗口长按-only Advanced 与普通区实现词漂移
- [x] active Spotify selector=`0`，真实云端导出中 `_ds/spotify-design-system`、Spotify asset UUID 外链与 `Figtree` 均为 `0`
- [x] REST endpoint、SSE data、action-specific schema 缺口已进入 `contract-manifest.md`
- [x] F150 全局 Access 与 F149 origin-403 ownership 已进入 Spec，并有 Blueprint 待同步文案
- [x] secret/env/path/command sensitivity policy 已进入 Spec 与 Prompt
- [x] main 已决定：审批维持 F145 三类候选；自动化只 pause/resume；任务不新增 cancel/resume
- [x] main 已逐项决定 Tasks OperatorInbox/Recovery backup/export/update dry-run/apply/restart/verify 的页面位置：Tasks“待处理事项” + Settings Advanced“维护与恢复”
- [x] main 已视觉复核 22/22 deterministic Reference，并确认其只作为固定 fixture 的 current UI/layout evidence
- [x] Settings/MCP write-only secret 窄 contract slice 由 F149 自有，复用现有 boundary，不依赖独立 security Fix

## Design Review 状态

- [x] Round 1 实际已关闭：真实 TDD evidence、worktree Python command、单一 transport 审计、auth ownership、domain-co-located L4、390 Web 窄窗口 sweep、frontend changed-lines 定义
- [x] Round 2 已修订：开放 JSON boundary、write-only secret、actual input manifest、真实 call graph、registry drift、Gateway SSE、现有目录职责、坏味道基线、全出站 secret oracle、Prompt action facts、独立命令/prerequisite policy
- [x] Design Input Fix 已修订：nested manifest、完整 UUID、Spotify 显式禁止、旧 2-page 反例边界、解压上传目录/备份 ZIP、24 文件可见性清单与连接中断复核
- [x] Anchor 4 首轮只读复核已完成并判定 REJECT；窄返修 Prompt 见 `../claude-design-revision-round1.md`
- [x] Anchor 4 窄返修、visual-only polish、Spotify 解绑与真实云端 `.dc.html` 回存已完成；状态/协议不扩域，旧 page 1–3 保持
- [x] Round 2 Design Gate 通过（2026-07-21 main 独立最终复审，`GATE_DESIGN=true`；只放行 Plan/Tasks）

## Notes

- 本 checklist 的 Design 输入、窄返修、真实云端导出与 Plan/Tasks 已通过；T000已完成，
  Implement从T001开始逐task放行。
- Tasks Gate 必须把测试矩阵拆成逐行为 RED/GREEN/REFACTOR，并用仓库最终文件名替换任何规划阶段落点。

## Plan/Tasks Gate 状态

- [x] `plan.md` 已冻结 Claude Design 最开始方案为 Web/iOS 共同视觉/交互基线、现有 Web 非基线、390px 仅为 Web 窄窗口健壮性、iOS 原生适配但不复制 Web 结构，以及有限 REST/action/SSE/secret contract、唯一 transport/application seam、A/B 波实施顺序与 Implement 前置
- [x] `tasks.md` 有 40 tasks；32 个行为 task 各含完整 RED/GREEN/REFACTOR command，FR-001～030 与 SC-001～013 全映射
- [x] 尚不存在的 checker/npm alias 均先设计 behavior RED，不以缺文件/import/0 tests 冒充
- [x] Python command fields 全部符合 post-F151 canonical `core/provider/protocol/tooling/skills/policy/memory` 七个保留 packages + Gateway、retired SDK absent、PYTHONNOUSERSITE、`uv --no-sync python -m pytest`
- [x] L4/L3/L1/L2、frontend changed-lines ≥90%、坏味道 mechanical/adversarial Review 与 20-frame Design fidelity 进入 Tasks
- [x] planned atomic relocation=0；纯搬迁若出现由 T063 conditional task 五证约束
- [x] T000 要求 F151 stable commit 后 rebase/recon 并重读 tests/AGENTS、Playwright config 与 stage profile；canonical package set 不一致即回 Gate，禁止兼容 retired SDK
- [x] T050 使用独立 seeded negative fixtures 验 retired SDK、漏 package、CI retry、ambient/host path；actual post-F151 config 只作 accept control，已满足时不伪造 production change
- [x] main 明确 `GATE_TASKS=true`（2026-07-21 PASS；不得据此 Implement）
- [x] T000已记录F151 stable、F150 stable、`origin/master`、无冲突rebase、Design export
  SHA与post-SDK七包+Gateway profile；T001可以开始
