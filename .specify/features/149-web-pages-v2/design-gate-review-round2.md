# F149 Design Gate 第二轮返修闭环表

> 结论：PASS；`GATE_DESIGN=true`（2026-07-21，main 独立最终复审）。当前只单次放行 Plan/Tasks Gate，不授权 Implement、测试执行、stage/commit/push。

## 12 项闭环

| # | main Critical/Major | 本轮制品证据 | 当前判定 |
|---:|---|---|---|
| 1 | 收紧 unknown/object 规则 | `contract-manifest.md` §1/§3；`spec.md` FR-010/025；research §4/4.1 | 制品已修：ban any；命名 raw/metadata/schema boundary + decoder；不闭合全 CP。实现前置未完成 |
| 2 | write-only secret mutation | manifest §6/§8；FR-022；Prompt Settings/MCP | F149 自有窄端到端 slice；Settings/MCP 复用现有 config/secret/application boundary 与 transport；keep/replace/remove、placeholder 拒绝、清空与全出站已冻结；不依赖 F151 HTTP representation或独立 security Fix |
| 3 | actual Reference pack 由 Worktree/main 提供 | canonical `actual-input-manifest.md`、Claude-upload nested manifest、22 张 PNG、`capture-metadata.json`、逐文件核对表、deterministic capture config | 22/22 READY，均标 not real backend；canonical/upload copy 由 byte+SHA-256 gate 保证一致；Claude 首轮已读取并在 References 展示 22/22；页面无 Vite overlay/RouteError；main 已视觉复核并确认 current UI/layout evidence 边界 |
| 4 | 实际 import/call graph；restore；删 Memory dead actions；snapshot 选择 | manifest §3/§5；recon Agents/Memory | 已补 restore；Memory dead actions移出；选择 raw snapshot→projection |
| 5 | action registry handler 缺项与唯一 seam | manifest §5.2；FR-027 | 已记录 7 个缺项与 handler line；Plan 后续必须同源，当前不得进 Plan |
| 6 | SSE 属 Gateway/web adapter；仅消费 payload 强类型 | manifest §7/§9；FR-028 | 已移出 core 目标，枚举 transition/artifact/diagnostic boundary |
| 7 | 分层映射现有目录 | manifest §2；research §3；FR-012 | 已改为 api/client、platform、pure projection、page/WorkbenchContext；page 可用 context |
| 8 | 坏味道真实 source/metric/owner | `code-smell-baseline.md` | 已记录 mechanical/adversarial 表；未来 AST checker 未创建，不能报告 Gate PASS |
| 9 | 后端 L4/L3 secret 全出站 + evidence scan | manifest §8/§9；tdd policy §3 | 已覆盖 config/MCP/SSE/error/log/DOM/clipboard/evidence；实施测试未开始 |
| 10 | Prompt restore + Tasks/Settings 产品事实 | Prompt Agents；`pending-product-decisions.md` 8 行 | restore 已补；8/8 DECIDED：Tasks“待处理事项” + Settings Advanced“维护与恢复”；Prompt 使用完整 UUID，并显式禁止 Spotify/旧 2-page/internal-term inheritance；Prompt READY TO SEND |
| 11 | 8 packages+gateway；独立命令；未来 script 纪律 | `tdd-evidence-policy.md`；`gate-prerequisites.md`；spec matrix | 已修数量与 cwd；不存在命令均标 BLOCKED prerequisite，不当现成 Gate |
| 12 | Round2 诚实结论 | 本文件；Round1 顶部撤销声明 | 已修；不再声称 12/12 Gate closure |

## 第一轮实际已关闭项

- 真实 TDD evidence 字段与稳定 RED oracle；
- `octoagent/tests/AGENTS.md` worktree Python command；
- 单一 transport 触达闭包审计；
- F150 全局 Access / F149 origin 403 ownership；
- domain-co-located L4 与最小 Playwright；
- 全路由 390 geometry/a11y sweep；
- frontend changed-lines 只统计 authored executable TS/TSX 的定义。

这些项继续有效；本轮 Design 输出证据已回存，但不会替代未来 Tasks/Implement 的真实测试证据。

## 四项硬门摘要

- **TDD**：未来行为 task 必须 RED→GREEN→REFACTOR，保存真实命令/输出/exit/time/SHA/status/oracle/secret scan；不存在 script 不能作 RED。
- **测试分层**：projection/state/decoder/a11y 以 frontend/Gateway L4 为主；deterministic API/SSE/secret egress 用 L3；L1 只保留 390 sweep、A/B/auth 边界及真实 DOM/clipboard/focus/file chooser/SSE 浏览器语义；无新增 L2。
- **架构分层**：唯一 transport=`api/client`；application=`platform queries/actions`；pure projection/state；page/WorkbenchContext composition。raw JSON 只停命名 boundary；action 与 SSE 各有唯一 seam。
- **坏味道**：10 direct fetch、2 auth helpers、4 bypass clusters、dead Memory section/links、3 secret leaks、4 raw DTO clusters为当前重点；0 import cycle 建 ratchet；职责漂移/mock-self 需 adversarial review。维护能力归位必须以窄 section 组合，禁止继续增加 Settings God component或第二状态源。

## 已关闭的 main 决策

- Reference pack 22/22 可作为固定 fixture 的 current UI/layout evidence；不证明后端/权限/错误/SSE。
- Tasks/Settings 8 项位置全部冻结；详见 `pending-product-decisions.md`。
- Settings/MCP write-only secret HTTP/application slice由 F149 承担；不新开 security Fix，F151 只保留底层 runtime/package/secret reference边界。

## Design Input Fix 闭环

- Claude 可见 manifest 固定为 `references/current/actual-input-manifest.md`；worktree 根 manifest 是唯一 canonical，nested copy 只由 canonical 机械生成，hash 一致才允许打包。
- Prompt 与发送说明只使用完整项目 UUID `851e3fb2-2b5b-4251-a095-8a678b1b7fec`；active 文案不得把短前缀当项目 ID。
- Spotify Design System 被列为最高优先级禁止项；每个 Desktop/390 frame 必须提供 F148/`--cp-*` provenance 与 Spotify absence。
- 现有 2 pages 只作旧现状/反例；20 个 frame 逐页验证普通用户可见区域没有内部术语。
- `claude-design-upload/` 是唯一推荐直接上传目录；ZIP 只作离线备份，不假设 Claude 自动解压。
- `claude-design-upload-checklist.md` 逐项列出 manifest、metadata 与 22 PNG 的文件名/size/hash oracle；连接中断或应用重启后重新确认。

## Anchor 4 首轮输出复核与窄返修闭环

### 已满足的基础结构

- 10 surface 各 Desktop/390 的 20 个 frame 名称已出现；
- `F149/Shared-States`、`F149/Advanced-Pattern`、`F149/References` 已出现；
- References 显示 22/22 与 fixture/commit/date/not-real-backend 边界；
- Tasks/Settings 归位、Agents restore、write-only secret 与 F150/F149 ownership 的总体方向已进入设计。

### 窄返修已关闭

1. 已补恰好 20 行逐 frame 验收表：F148/`--cp-*` provenance、Spotify=`none`、普通用户术语 absence、state 与 390 overflow/focus 均逐行记录。
2. 已补 10 surface × loading/empty/error/origin403/not-found/disconnected/409 applicability matrix，并把适用状态落到页面。
3. unknown/historical task event 只进入 scrubbed Advanced diagnostic，不进入默认业务时间线。
4. MCP `每 5s` 与 restart `约 1 分钟/检查点续跑` 已删除或收敛为 `CONTRACT GAP`。
5. origin 403 只表达资源权限不足，不出现 owner/重新登录动作；F150 继续拥有全局认证 Gate。
6. Automation/Agents/MCP 390 Advanced 使用可见、可聚焦且有 accessible name 的触发器，不再依赖长按-only。
7. 普通区已移除 API Key/write-only/review/apply/dry-run/vault 等实现词漂移；必要技术事实只在 Advanced。
8. 云端 `.dc.html` 已原样回存；`design-output/2026-07-21/export-manifest.md` 记录 UUID、日期、bytes、SHA-256 与检查结果。

返修严格遵守 `claude-design-revision-round1.md`：只修 anchor 4，保留 20 frame/References，不改旧 page 1–3，不改 Spec 范围。

## 视觉方向与 Spotify P1 闭环

- 用户/main 已明确：保留 Claude 原稿的视觉层级、留白、卡片节奏、信息密度与视觉张力；F148 是 `--cp-*` token、主题/组件边界和功能合同，不是旧 Web 外观复刻模板。
- `claude-design-visual-polish-round2.md` 只恢复上述视觉质量，没有增加新 Round 的产品设计或改变状态/协议。
- active Spotify selector=`0`。selector 清理后真实云端 HTML 仍发现 baked import，随后由 `claude-design-import-cleanup.md` 精确删除 4 个 CSS、1 个 bundle 与 Figtree font-family 依赖。
- 最终真实云端导出中 `_ds/spotify-design-system` path、Spotify asset UUID 外链与 `Figtree` 均为 `0`；20 frames、Shared-States、Advanced-Pattern、References 与旧 page 1–3 markers 均保留。
- 最终导出：`design-output/2026-07-21/OctoAgent Web.dc.html`，SHA-256=`a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`，bytes=`280442`，project UUID=`851e3fb2-2b5b-4251-a095-8a678b1b7fec`。

## 唯一 Gate 阻断与剩余风险

1. Design Gate blocker 已关闭；main 已机械复核 bytes/hash、20 unique frames、3 个辅助 frame、20 行表、10×7 矩阵、旧 page 1–3 markers 与 Spotify/Figtree 真实引用为零，并结合云端视觉抽查判定 PASS。
2. action contract同源 seam与 generator 只能到后续 Plan 决定；当前禁止进入 Plan。
3. F150/F151 稳定与 main 生产放行仍是 Implement 硬前置，不影响 Prompt 发送。
4. AST/checker 与真实 RED 证据尚未产生；按 `gate-prerequisites.md` 保留为未来 Tasks/Implement 前置，不冒充当前 PASS。
