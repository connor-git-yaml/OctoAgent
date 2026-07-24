# READY TO PASTE — F149 Anchor 4 视觉保真窄返修

只调整 `OctoAgent Web.dc.html` 的 **F149 anchor 4** 视觉表达。不要修改旧 page 1–3；不要删除、重建或改名现有 20 个 page frame、`F149/Shared-States`、`F149/Advanced-Pattern`、`F149/References`、20 行逐 frame 验收表与 10×7 状态矩阵。

这不是回退到 current UI，也不是重新设计功能。当前 reference 截图只用于说明已有信息结构和问题，**不得把它们当成逐像素视觉模板**。用户明确希望保留 Claude Design 第一轮更精致、更有设计感的方向；F148 shell/`--cp-*` 是主题约束，不等于复制旧页面的密度、边框和排版。

## 必须保留的视觉品质

在 20 个 frame 中恢复并统一以下视觉品质，同时保持已经冻结的功能、文案、状态和权限语义不变：

- 清晰的编辑式信息层级：标题、摘要、关键状态、主操作、辅助信息一眼可分；不要把所有信息压成同权重的表格或旧式管理后台。
- 更从容的留白与节奏：使用分组间距、卡片内边距和内容密度变化建立层级，避免 current UI 的拥挤堆叠。
- 有克制的深度：允许使用 `--cp-*` 派生的面板层级、柔和边界、轻微高光/阴影和局部强调，但不要发明第二主题。
- 保留 Claude 第一轮有辨识度的卡片构图、状态胶囊、重点区块和移动端单列节奏；不要把 390 退化为桌面表格的压缩版。
- 绿色仅作为关键状态与主操作强调；大面积背景、文字和辅助状态仍使用 F148 `--cp-*` 中性色，避免“整页 Spotify 化”。
- 图标、按钮、drawer/sheet 与空态应保持现代、清晰、可触达；不要为了接近 reference 截图而降低可读性或触控面积。

## 视觉 SoT 与 Spotify 禁令

- 唯一视觉 SoT 仍是 F148 shell 与现有 `--cp-*` tokens。
- 当前项目已经解除 Spotify Design System 选择。**不得重新选择、导入或引用** Spotify 的 file、token、class、component、font、template、theme、`styles.css`、`_ds_bundle.js` 或 `_ds_manifest.json`。
- 可以保留第一轮中“层级清楚、卡片精致、留白舒适、状态醒目”等抽象设计品质，但必须使用 F148/`--cp-*` 重新实现；不得复制 Spotify 资产或 token 名称。
- `#1ed760` 仅在它确实来自现有 F148 `--cp-accent` 时使用，并在验收表保持 F148 provenance；不得把它当作 Spotify 来源。

## 不得回退的 Gate 结论

以下内容已经闭合，本轮只做视觉润色，不得改变：

1. 20 行验收表和 10×7 状态矩阵的内容与行数；
2. unknown/historical task event 只进入 scrubbed Advanced diagnostic；
3. MCP polling cadence、restart duration/task resume 继续为 `CONTRACT GAP`，不得恢复固定承诺；
4. origin 403 只处理资源权限，F150 继续拥有登录/401/session 过期；
5. Automation、Agents、MCP 390 的 Advanced 入口继续可见、可聚焦、有 accessible name，长按不是唯一入口；
6. 普通区继续使用用户语言，不恢复 API Key/write-only/review/apply/dry-run/vault 等实现词；
7. Tasks 与 Settings 的既定落点、Approvals 三类候选、Automation pause/resume、Agents behavior restore 与不新增 task cancel/resume 均不变；
8. secret、path、command、diagnostic 的 sensitivity policy 不变。

## 逐页视觉检查

逐个检查 10 surface 的 Desktop 与 390：

- Desktop：避免 reference/current UI 式的平均分栏与边框堆叠；保留明确 hero/summary、主内容与 Advanced 的视觉层级。
- 390：保持单列、无页面横向溢出、主操作显著、触区至少 44px、drawer/sheet 关闭后焦点归还；用留白和分组解决密度，不靠缩小字号。
- loading/empty/error/origin403/not-found/disconnected/409：沿用状态矩阵语义，但让状态卡与对应页面视觉系统一致，不要做成通用旧式告警框的机械复制。
- Settings 的维护与恢复仍是 Advanced 内窄 section；视觉上与普通设置明确分层，不占普通首屏。
- Tasks 的“待处理事项”非零时显著但不过度警报化，0 项仍折叠；普通任务列表继续是首屏主角。

## 完成与证据

完成后只报告本轮视觉变化，不重写产品说明。必须明确：

- 旧 page 1–3 未改；20 frame 名称及全部 Gate 行为未改；
- Spotify Design System selection/import/reference 仍为 0；
- 逐 frame 的视觉 provenance 仍为 F148/`--cp-*`；
- 哪些第一轮视觉品质被保留，以及如何用 `--cp-*` 重新表达；
- 更新后的 `OctoAgent Web.dc.html` 可供导出回存。

任一约束无法满足时输出 `BLOCKED: <item>`。在 worktree 导出制品和 truth sync 完成前，保持 `GATE_DESIGN=false`。
