# READY TO PASTE — F149 Claude Design Anchor 4 窄返修 Prompt

请只修当前 `OctoAgent Web.dc.html` 的 **F149 anchor 4**。保留现有 20 个 F149 page frame、`F149/Shared-States`、`F149/Advanced-Pattern` 与 22/22 `F149/References`；不要删除、重建或改名这些现有交付。**不要修改旧 page 1–3**，它们继续只作为历史现状/反例。不要新增页面、动作、后端能力、主题、状态源或产品范围。

本轮是 Design Gate 定向返修，不是重新设计。以下十项全部完成前，不要宣称通过。

## 1. 交付 20 行逐 frame 验收表

为以下每个 frame 单独提供一行，不能用一条全局声明代替：

- `F149/Approvals/Desktop`、`F149/Approvals/390`
- `F149/Tasks/Desktop`、`F149/Tasks/390`
- `F149/Task-Detail/Desktop`、`F149/Task-Detail/390`
- `F149/Automation/Desktop`、`F149/Automation/390`
- `F149/Settings/Desktop`、`F149/Settings/390`
- `F149/Agents/Desktop`、`F149/Agents/390`
- `F149/Memory/Desktop`、`F149/Memory/390`
- `F149/Files/Desktop`、`F149/Files/390`
- `F149/Skills/Desktop`、`F149/Skills/390`
- `F149/MCP/Desktop`、`F149/MCP/390`

验收表必须恰好包含以下列：

| frame | F148 / `--cp-*` provenance | Spotify imports/tokens/components/theme | ordinary-language absence | states covered | 390 overflow/focus |
|---|---|---|---|---|---|

逐行要求：

- `F148 / --cp-* provenance` 写清该 frame 继承的 F148 shell/token 来源；不得只在表外写一次全局声明。
- `Spotify imports/tokens/components/theme` 必须逐行明确为 `none`。Claude Design 选择器当前即使仍显示 Spotify Design System，也不得导入、继承或套用其 token、颜色、组件、排版或 theme；若 anchor 4 已使用，必须替换为 F148/`--cp-*`。
- `ordinary-language absence` 逐行确认普通用户可见区没有 debug、JWT、AUD、JWKS、LiteLLM、operator、ops、raw schema、runtime ID，以及第 8 节列出的实现词。
- `states covered` 必须引用第 2 节状态矩阵中该 surface 的适用状态与实际 frame/variant/annotation，不得只写 `PASS`。
- `390 overflow/focus` 对 Desktop 行可写 `N/A — Desktop`；对 390 行必须写横向溢出、可见主操作、触发器 accessible name、焦点顺序与关闭后焦点归还的实际证据。

## 2. 交付 10 surface × 7 状态 applicability matrix

新增恰好 10 行的矩阵，列固定为：

| surface | loading | empty | recoverable error | origin 403 | not-found | disconnected | 409 |
|---|---|---|---|---|---|---|---|

Surface 固定为 Approvals、Tasks、Task-Detail、Automation、Settings、Agents、Memory、Files、Skills、MCP。每格只能写：

- `APPLIES → <对应 frame/variant/annotation>`；或
- `N/A → <产品/契约理由>`。

不得把 Approvals 的 empty 当成全站 empty。为每个适用页面补该页面自己的空态文案、失败恢复动作与资源权限文案，并把对应 variant/annotation 落到该页面；`F149/Shared-States` 只能提供共享视觉模式，不能替代十页适用性证据。不要新增未授权状态；not-found、disconnected、409 是否适用必须按现有 F149 产品事实判断，无法证明时标 `CONTRACT GAP`，不要猜。

## 3. 收紧 Task event 边界

- 从 Task-Detail 默认可视化业务时间线删除“未知类型事件仅按时间原样列出”及任何 unknown/historical raw event 展示。
- 默认时间线只展示已有契约支持且能映射为用户语义的事件。
- unknown/historical/extension event 只能经过 scrub 后进入默认收起的 Advanced diagnostic；不得驱动业务状态、阶段或新 UI。
- raw payload、未净化 metadata 与 secret 不得进入 default timeline、DOM 或复制入口。

## 4. 删除未冻结的实现承诺

- 删除 MCP 的“每 5s 轮询一次”及任何新固定轮询频率。若设计确实需要表达频率但现有契约没有冻结，写 `CONTRACT GAP: polling cadence`；普通 UI 只说“正在检查安装状态”。
- 删除 Settings restart 的“约 1 分钟不可用”“运行中任务从检查点续跑”及等价保证。只保留 main 已冻结的“会短暂不可用”和强确认；恢复时长与任务续跑行为没有契约证据时写 `CONTRACT GAP`。

## 5. 修正 F150 / F149 auth ownership

- origin 403 只表达“当前账号没有查看或操作此资源的权限”，保留 shell，并提供真实的返回、关闭或联系管理员等资源级恢复动作。
- 不得出现 `owner`、token、认证实现、页面级登录、切换身份或“重新登录”动作。
- 未登录、401、session 过期、重新登录和全局 Access Gate 全部属于 F150；F149 page/Shared-States 不得重建这些流程。

## 6. 修正 390 Advanced 入口的可访问性

Automation、Agents、MCP 的 390 frame 不得再用长按-only 打开 Advanced。改为：

- 普通视图中可见、可聚焦的“高级”或“更多”按钮/菜单触发器；
- 有明确 accessible name、键盘/读屏可达、至少 44px 触区；
- 打开 drawer/sheet/menu 后有可感知标题、Escape/关闭动作与 focus trap（适用时）；
- 关闭后焦点归还原触发器。

长按可以作为附加快捷方式，但不能是唯一入口。

## 7. 修正普通用户术语

逐页检查普通用户可见区并替换实现词：

- `API Key` → 用户可理解的“访问密钥”或同等产品文案；
- `write-only` → “现有值不可查看；修改时重新输入”；
- `review/apply` → “检查改动/保存并生效”或同等用户动作；
- `dry-run` → “试运行”；
- `vault` → “已安全保存”或同等安全摘要；
- 其他 raw status、runtime ID、schema、command/env、认证实现只允许在明确 Advanced 且已净化的区域。

不得通过把实现词缩小、变灰或藏在普通卡片说明中规避。确有管理员必要的技术事实只能进入默认收起的 Advanced，并继续遵守 secret/path/command sensitivity policy。

## 8. 保持已冻结范围

- Approvals 仍只有 F145 三类知识候选；不合并通用工具审批。
- Tasks 只保留“待处理事项”，不新增 task cancel/resume；Recovery/backup/export/update/restart/verify 继续留在 Settings → Advanced → 维护与恢复。
- Automation 只 pause/resume；不新增 create/run/delete。
- Agents 保留 behavior version restore；Memory 不加入不可达管理动作。
- Settings/MCP 保持同一 write-only secret 安全语义，但不新建 registry、transport、state source 或独立页面。

## 9. 导出并回存可审查制品

完成后必须导出并回存以下任一完整制品：

- 更新后的 `OctoAgent Web.dc.html`；或
- 20 个 page frame + `F149/Shared-States` + `F149/Advanced-Pattern` + `F149/References` 的逐 frame 截图。

同时回存第 1 节 20 行验收表与第 2 节 10 行状态矩阵。只在远端画布显示、自报“20/20”或提供一条全局声明，都不算可审查回存。

## 10. 最终自检与停止条件

提交前逐项报告：

1. 旧 page 1–3 未改；anchor 4 的 20 frame 名称与 References 未删、未改名；
2. 20 行验收表恰好 20 行，且每行有 F148/`--cp-*` provenance、Spotify=`none`、术语 absence、state evidence 与 390 overflow/focus；
3. 状态矩阵恰好 10 行，所有适用状态已落到对应页面；
4. unknown/historical task event 只在 scrubbed Advanced diagnostic；
5. 不再出现 MCP `5s`、restart `1 分钟`/`checkpoint` 等未冻结承诺；
6. 403 不含 owner/重新登录；
7. Automation/Agents/MCP 390 有可见、可聚焦、有 accessible name 的 Advanced 触发器；
8. 普通区不含 API Key/write-only/review/apply/dry-run/vault 等实现词；
9. 导出制品、20 行表、10 行矩阵均已回存；
10. 未新增范围、动作、主题、后端字段或状态源。

任一项未满足时，明确输出 `BLOCKED: <item>`，不要宣称完成。**在返修输出回存并通过复核前，`GATE_DESIGN=false`。**
