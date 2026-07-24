# F149 Claude Design 逐文件上传核对表

> 目标项目 UUID：`851e3fb2-2b5b-4251-a095-8a678b1b7fec`。
> 只有 Claude 项目文件列表实际显示本表要求的文件，才算上传成功。ZIP 存在、上传进度完成、本机文件存在或此前会话看见过附件均不能替代此核对。

## Prompt 发送前硬门

- [ ] 当前项目 UUID 与上方完整 UUID 完全一致。
- [ ] 已直接上传解压后的文件/文件夹，没有依赖 Claude Design 自动解压 ZIP。
- [ ] `references/current/actual-input-manifest.md` 与 worktree canonical manifest 的 SHA-256 均为 `557ab716eed26134c3983e35d3d9ff1d08278b133591820a207225a0cf1e6b96`。
- [ ] `references/current/capture-metadata.json` SHA-256 为 `b9c03bc4aa56df4f1cee3c012bc3fd10bd09ff60002f4480e18eae786128c2dc`。
- [ ] 下表 24 个 Reference 文件全部在 Claude 项目文件列表可见，名称与 byte size 一致。
- [ ] `claude-design-prompt.md`、`pending-product-decisions.md`、`claude-design-send-instructions.md` 与本核对表也可见。
- [ ] 已明确不使用当前选择器中的 Spotify Design System；唯一视觉 SoT 为 F148 shell 与现有 `--cp-*` tokens。
- [ ] 若刚发生连接中断、应用重启或切换项目，已重新完成全部核对。

未全部勾选时：**不得发送 Prompt**。

## Claude 项目必须可见的 24 个 Reference 文件

| # | Claude 可见路径 | byte size | 已看见 |
|---:|---|---:|---|
| 1 | `references/current/actual-input-manifest.md` | 6159 | [ ] |
| 2 | `references/current/capture-metadata.json` | 6973 | [ ] |
| 3 | `references/current/agents-390.png` | 82815 | [ ] |
| 4 | `references/current/agents-desktop.png` | 228832 | [ ] |
| 5 | `references/current/approvals-390.png` | 59552 | [ ] |
| 6 | `references/current/approvals-desktop.png` | 212786 | [ ] |
| 7 | `references/current/automation-390.png` | 76554 | [ ] |
| 8 | `references/current/automation-desktop.png` | 228180 | [ ] |
| 9 | `references/current/f148-shell-390.png` | 98211 | [ ] |
| 10 | `references/current/f148-shell-desktop.png` | 244471 | [ ] |
| 11 | `references/current/files-390.png` | 73913 | [ ] |
| 12 | `references/current/files-desktop.png` | 225888 | [ ] |
| 13 | `references/current/mcp-390.png` | 50929 | [ ] |
| 14 | `references/current/mcp-desktop.png` | 201704 | [ ] |
| 15 | `references/current/memory-390.png` | 129895 | [ ] |
| 16 | `references/current/memory-desktop.png` | 363920 | [ ] |
| 17 | `references/current/settings-390.png` | 107753 | [ ] |
| 18 | `references/current/settings-desktop.png` | 372451 | [ ] |
| 19 | `references/current/skills-390.png` | 90915 | [ ] |
| 20 | `references/current/skills-desktop.png` | 243317 | [ ] |
| 21 | `references/current/task-detail-390.png` | 23230 | [ ] |
| 22 | `references/current/task-detail-desktop.png` | 52350 | [ ] |
| 23 | `references/current/tasks-390.png` | 100391 | [ ] |
| 24 | `references/current/tasks-desktop.png` | 262869 | [ ] |

## 数量 oracle

- Reference 文件：`1 manifest + 1 metadata + 22 PNG = 24`。
- 解压上传目录总文件：`4 根级 Markdown + 24 Reference = 28`。
- PNG 数量不是 22、Reference 总数不是 24、上传目录总数不是 28，均视为输入失败。
- Claude 返回任一 `INPUT MISSING` 时，补传后必须重新核对整张表，而不是只检查报错文件。
