# F149 Claude Design Actual Input Manifest

> Owner：Worktree/main。Claude Design 无法访问本地应用或源码，不能自行补现状。本表 22/22 文件与元数据已完成，并由 main 视觉复核；可随 READY Prompt 一起发送。
> 基线 commit：`9d5e1e48691c5ae5a12b33f224d64ac03d5442fc`。计划 capture date：2026-07-20。

## Canonical 与 Claude-upload 副本

- 本文件 `.specify/features/149-web-pages-v2/actual-input-manifest.md` 是唯一 canonical source，只在 worktree 中维护。
- Claude Design 必须看到的路径是 `references/current/actual-input-manifest.md`。该文件由 canonical source 逐字节生成，不得独立编辑，也不构成第二事实源。
- 每次重建上传目录或 ZIP，必须以 `cmp -s` 与 SHA-256 校验两份内容完全一致；实际 hash 记录在 `claude-design-upload-checklist.md` 与 `trace.md`。不一致时禁止发送 Prompt。
- Claude 项目文件列表必须先显示该上传副本、`capture-metadata.json` 与 22 张 PNG；只看到 ZIP、根 manifest 或本机路径均不算输入可见。

## Capture 规则

- Desktop viewport 固定 `1440×1024`；mobile 固定 `390×844`。
- 每张图必须使用当前 production UI 与 F148 shell；允许 deterministic fixture，但 metadata 必须写 `data_source=deterministic-fixture`、fixture ID，并明确 `not_real_backend=true`。
- 禁止把 deterministic fixture 标成真实后端、真实用户数据或 live 状态；禁止图中出现 token/key/env value 等 secret。
- Task-Detail 使用固定 route `/tasks/f149-reference-task`；截图注释必须保留该 fixture ID。
- 文件放在 `references/current/`，文件名不可复用或覆盖其他 surface。

## 22 张必需输入

| # | Surface | Route | Viewport | 文件 | capture date | data source | not real backend | 状态 |
|---:|---|---|---|---|---|---|---|---|
| 1 | Approvals | `/approvals` | 1440×1024 | `references/current/approvals-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 2 | Approvals | `/approvals` | 390×844 | `references/current/approvals-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 3 | Tasks | `/work` | 1440×1024 | `references/current/tasks-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 4 | Tasks | `/work` | 390×844 | `references/current/tasks-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 5 | Task-Detail | `/tasks/f149-reference-task` | 1440×1024 | `references/current/task-detail-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 6 | Task-Detail | `/tasks/f149-reference-task` | 390×844 | `references/current/task-detail-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 7 | Automation | `/automation` | 1440×1024 | `references/current/automation-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 8 | Automation | `/automation` | 390×844 | `references/current/automation-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 9 | Settings | `/settings` | 1440×1024 | `references/current/settings-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 10 | Settings | `/settings` | 390×844 | `references/current/settings-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 11 | Agents | `/agents` | 1440×1024 | `references/current/agents-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 12 | Agents | `/agents` | 390×844 | `references/current/agents-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 13 | Memory | `/memory` | 1440×1024 | `references/current/memory-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 14 | Memory | `/memory` | 390×844 | `references/current/memory-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 15 | Files | `/files` | 1440×1024 | `references/current/files-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 16 | Files | `/files` | 390×844 | `references/current/files-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 17 | Skills | `/skills` | 1440×1024 | `references/current/skills-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 18 | Skills | `/skills` | 390×844 | `references/current/skills-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 19 | MCP | `/mcp` | 1440×1024 | `references/current/mcp-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 20 | MCP | `/mcp` | 390×844 | `references/current/mcp-390.png` | 2026-07-20 | deterministic fixture | true | READY |
| 21 | F148 shell | `/` | 1440×1024 | `references/current/f148-shell-desktop.png` | 2026-07-20 | deterministic fixture | true | READY |
| 22 | F148 shell | `/` | 390×844 | `references/current/f148-shell-390.png` | 2026-07-20 | deterministic fixture | true | READY |

## Capture metadata sidecar

回存图片时同时创建 `references/current/capture-metadata.json`，每项至少包含：

```json
{
  "file": "approvals-desktop.png",
  "route": "/approvals",
  "viewport": { "width": 1440, "height": 1024 },
  "captured_at": "ISO-8601",
  "commit": "9d5e1e48691c5ae5a12b33f224d64ac03d5442fc",
  "data_source": "deterministic-fixture",
  "fixture_id": "f149-current-v1",
  "not_real_backend": true
}
```

## Mechanical Gate

Gate 必须逐行验证：22 个相对路径存在、PNG 非空、metadata file/route/viewport/date/commit/data_source/fixture_id/not_real_backend 一致；canonical manifest 与 `references/current/actual-input-manifest.md` 字节及 SHA-256 一致；Claude 项目文件列表中 manifest、metadata、22 张 PNG 共 24 个 Reference 文件全部可见。连接中断或应用重启后必须重新确认，不得假设附件仍在。当前截图捕获 22/22 READY，页面无 Vite overlay/RouteError，main 已视觉复核。它们只证明 deterministic fixture 下的 current UI/layout 与响应式问题，不证明真实后端数据、权限、错误或 SSE；Tasks 运维内容占满首屏、Settings 390px 信息密度/断行等问题必须保留为设计输入。F149 总 Design Gate 仅因 Claude 输出未回存而 BLOCKED。
