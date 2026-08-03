# F158 Web 当前分支功能与视觉复验

## 复验身份

- 日期：2026-07-31
- 代码提交：`2d2a40a0d9396713723a1a84d98b09383819b807`
- 分支：`codex/f158-milestone-product-closure`
- 工作目录：`octoagent/frontend`
- 完成时间（UTC）：`2026-07-31T03:32:19Z`

## 唯一命令链

```bash
npm test && npm run build && npm run check:complexity && npm run test:e2e
```

没有执行快照更新命令，没有删除、替换或重新生成 committed visual baseline。

## 结果

| 门 | 结果 |
|---|---|
| Vitest | `70 files / 598 tests`，全部通过 |
| production build | PASS |
| frontend complexity | PASS |
| Playwright | `39/39` 通过 |
| Playwright retry | `0`（配置禁用重试） |
| Claude 早期视觉基线 | `14/14` 通过，未更新 baseline |

Playwright 实际启动本地 Gateway-backed Web，覆盖 UI、API、SSE 与确定性业务场景。
这证明当前代码提交的本地功能与视觉合同成立；它不替代个人部署登录态下的
SPA/API/SSE 复验。

390px 场景只证明桌面 Web 窄窗口响应式健壮性，不是手机产品入口。手机产品只走
原生 iOS App。

## Claude 早期视觉基线字节

| 基线 | SHA-256 |
|---|---|
| `claude-early-composer.png` | `382a1b13f2de7fbdf3858a7a901a99fbb453252627e41a0f1817443c11cae2cb` |
| `claude-early-navigation-row.png` | `4bfc0ffdb0a9fd3d9889ca05f152f6a9fec12bfa4e6d4be50f1b965985c56679` |
| `claude-early-run-panel-head.png` | `cc510fc04ab9a731a92fa47afa8cc47ac6b4d3d584b17e4de26ab2bb5c88ff24` |
| `claude-early-sidebar-brand.png` | `24c36c726256ddc1fedd8f35e14391a5596b1852774fc1af424465491acec8bc` |
| `claude-early-agents.png` | `62e5044ecf0dd92381f44e2288c00354667c6253968caf7ae0e43f23e9377764` |
| `claude-early-approvals.png` | `8b360edc602279d5ded471bad402ba46ae6b7dc69dff85dd76fc7e8e4cad3978` |
| `claude-early-automation.png` | `4566681c83eaf6569b828cbb4e41701df4c8f9cdab7a2afedb44a9899787abdb` |
| `claude-early-files.png` | `26bc4b11dee301f1c1338684a08ee01a1b4d096b14ce09699cba6472702a01cf` |
| `claude-early-mcp.png` | `abb58fa9f38cd754390ffb4f07f5ac7f556651a9184c9ead66b58af470312d0f` |
| `claude-early-memory.png` | `3d36a4dc505b89a3698ea1e98bcd385022e41a3c1300470f7553d3248e5b3be2` |
| `claude-early-settings.png` | `55d0ed0728ce2ff3647789f41a887bcc1e721cebc9c73415cd3d64c18c050359` |
| `claude-early-skills.png` | `47dbea43dda7b96b27d92a84b05f5efb6ba7cdfa752fc6d3e8b611d25f327b46` |
| `claude-early-task-detail.png` | `d62122b9458b0262fc923f0a12dd1047e5e6a70e81be80f3297149f12d7f595d` |
| `claude-early-tasks.png` | `4734ef1cab7f7dc88f516afd2fedf1e7de495b5193ded5eeecec68cbb3bd87c1` |

前四张组件基线位于
`octoagent/frontend/e2e/__snapshots__/visual-claude-baseline.spec.ts/`，后十张
业务 surface 基线位于
`octoagent/frontend/e2e/__snapshots__/visual-claude-surfaces.spec.ts/`。
