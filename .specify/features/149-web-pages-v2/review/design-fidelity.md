# F149 Design Fidelity Review

## 结论

**PASS**。F149 Web 实现继续以 Claude Design 最初方案为视觉与交互基线，没有回退到旧 Web 的密集平铺，也没有为了复用旧实现而要求设计迁就代码。F148 `--cp-*` 只承担共享主题与组件边界；它不是另一套视觉基线。

本轮没有发现需要回写 Claude Design 源文件的视觉缺陷，因此没有消耗 Claude Design token，也没有对云端设计做无意义改动。若后续发现真实设计源问题，仍应在 Claude Design 中删除不佳的中间方案，而不是在代码里长期叠加补丁。

## 权威参考

- 设计导出：`design-output/2026-07-21/OctoAgent Web.dc.html`
- SHA-256：`a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`
- Claude Design project：`851e3fb2-2b5b-4251-a095-8a678b1b7fec`
- 导出已机械确认 Spotify selector、外链 bundle 与 Figtree 残留均为 0。

## 20 个 viewport surface 逐项复核

桌面截图来自本轮真实 Gateway + 浏览器会话。所有桌面页面均为
`clientWidth=scrollWidth=1440`，且恰有一个 `<main>`。Task Detail 刻意使用不存在的
task id，验证真实 not-found 状态而不是伪造 happy path。

本轮浏览器工具在切换到 390px 后因本地 URL 安全策略拒绝继续导航；没有绕过安全策略，也没有把设计参考图冒充实现截图。390px 行明确引用已通过的正式
`T051` Playwright 参数化合同：十页均验证页面级 overflow、`main`、一级标题、键盘焦点、accessible name 与 reduced-motion。它只代表桌面 Web 窄窗口健壮性，不是手机产品或 iOS 验收。

| # | surface | route | viewport | evidence | hierarchy / spacing / card rhythm | overflow / focus | result |
|---:|---|---|---|---|---|---|---|
| 1 | Approvals | `/approvals` | Desktop 1440 | `screenshots/desktop/approvals.png` | 三类候选分组清晰，宽松卡片与留白保留 | 1440 无溢出；单 main | PASS |
| 2 | Approvals | `/approvals` | Web narrow 390 | T051 formal Playwright | 与同页响应式合同一致 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 3 | Tasks | `/work` | Desktop 1440 | `screenshots/desktop/tasks.png` | 主次层级与任务卡节奏清晰 | 1440 无溢出；单 main | PASS |
| 4 | Tasks | `/work` | Web narrow 390 | T051 formal Playwright | 保持用户语言和卡片结构 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 5 | Task Detail | `/tasks/f149-l1-missing` | Desktop 1440 | `screenshots/desktop/task-detail.png` | not-found 状态仍保留页面骨架和视觉节奏 | 1440 无溢出；单 main | PASS |
| 6 | Task Detail | `/tasks/f149-l1-missing` | Web narrow 390 | T051 formal Playwright | 错误状态不退回原始 JSON/内部 ID | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 7 | Automation | `/automation` | Desktop 1440 | `screenshots/desktop/automation.png` | hero、分区与动作卡节奏符合原稿 | 1440 无溢出；单 main | PASS |
| 8 | Automation | `/automation` | Web narrow 390 | T051 formal Playwright | 窄窗口不改变功能归属 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 9 | Settings | `/settings` | Desktop 1440 | `screenshots/desktop/settings.png` | 普通设置与 Advanced 分层，避免旧式密集表单 | 1440 无溢出；单 main | PASS |
| 10 | Settings | `/settings` | Web narrow 390 | T051 formal Playwright | 底部操作与 Advanced 内容可达 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 11 | Agents | `/agents` | Desktop 1440 | `screenshots/desktop/agents.png` | 模板/实例关系以卡片层级表达 | 1440 无溢出；单 main | PASS |
| 12 | Agents | `/agents` | Web narrow 390 | T051 formal Playwright | 普通区不暴露内部 provider 字段 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 13 | Memory | `/memory` | Desktop 1440 | `screenshots/desktop/memory.png` | 信息密度受控，主动作与历史内容分层 | 1440 无溢出；单 main | PASS |
| 14 | Memory | `/memory` | Web narrow 390 | T051 formal Playwright | 候选与历史态保持可读 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 15 | Files | `/files` | Desktop 1440 | `screenshots/desktop/files.png` | 文件工作台没有回退到运维表格 | 1440 无溢出；单 main | PASS |
| 16 | Files | `/files` | Web narrow 390 | T051 formal Playwright | chooser、modal 与焦点路径可达 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 17 | Skills | `/skills` | Desktop 1440 | `screenshots/desktop/skills.png` | 安装与详情保持原稿卡片张力 | 1440 无溢出；单 main | PASS |
| 18 | Skills | `/skills` | Web narrow 390 | T051 formal Playwright | 文件输入和 Escape 焦点归还已由 T053 复验 | overflow/focus/a11y/reduced-motion 全过 | PASS |
| 19 | MCP | `/mcp` | Desktop 1440 | `screenshots/desktop/mcp.png` | wizard 与 provider 卡片保持宽松层级 | 1440 无溢出；单 main | PASS |
| 20 | MCP | `/mcp` | Web narrow 390 | T051 formal Playwright | Advanced 可见、可聚焦且不是 long-press-only | overflow/focus/a11y/reduced-motion 全过 | PASS |

## 状态与文案

- 10×7 状态矩阵继续覆盖 loading、empty、recoverable error、origin 403、not-found、disconnected、409；不适用状态保留明确理由。
- 401 归 F150 全局 Access；origin 403 留在 F149 资源页。F149 页面不新增重新登录或 owner 身份动作。
- unknown/historical/extended task event 经净化后只进入默认收起的 Advanced diagnostics，不驱动业务状态。
- 普通区域不展示 JWT、JWKS、raw command/env/provider id、write-only secret value 等内部概念。
- 390px 仅为 Web 窄窗口回归；手机产品只走原生 iOS App。iOS 延续同一视觉语言，但按 SwiftUI / Apple 原生导航、手势、控件与无障碍语义实现。

## 视觉偏离记录

本轮未发现需要登记的无理由偏离。实现阶段发生的结构调整均有明确合同理由：

- 缺少 `main` landmark 的五页只替换语义根元素，没有改布局和视觉。
- Task Detail 隐藏内部 task id、补 Advanced Escape 焦点归还，属于普通语言与无障碍修复。
- Skills 安装弹层补初始焦点和焦点归还，属于无障碍修复。
- 401/403 owner 修复没有修改 CSS、布局或 Claude Design 排版。

不存在以“实现方便”为理由的视觉重排；也不存在用现有旧 Web 样式反向改造 Claude Design 的情况。
