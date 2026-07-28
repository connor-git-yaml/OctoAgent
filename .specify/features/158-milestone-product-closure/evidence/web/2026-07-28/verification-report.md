# F158 Web 功能与视觉验收报告

## 结论

截至 2026-07-28，当前分支中的桌面 Web 已完成以下可复现闭环：

- production build 与前端复杂度门通过；
- 70 个 Vitest 文件、598 条测试全部通过；
- 真实 hermetic Gateway 下完整 Playwright 结果为 38 条通过、1 条因一次性候选已消费
  条件跳过、0 条失败；重建 fresh L1 fixture 后，该审批接受/落盘旅程独立 1/1 通过；
- 主工作台 4 个像素基线和 9 个业务 surface + 真实任务详情 10 个像素基线均通过；
- 390px Web 窄窗口对 10 个 surface 完成 overflow、键盘焦点、accessible name 和
  reduced-motion sweep；
- 人工复核最终 PNG，未发现 Playwright 遮罩噪声、后期大 Hero、径向发光背景或
  大面积低信息密度卡片。

这证明当前分支的 Web 功能和 Claude Design 早期视觉恢复已形成直接证据，但不等于
整个 F158 Goal 完成。iOS 真实启动、Claude Design 云端 lineage 清理、Cloudflare
live、最终提交/CI/个人部署仍在后续门内。

## Exact commands

工作目录：
`/Users/connorlu/.codex/worktrees/f158/OctoAgent/octoagent/frontend`

```bash
npm test -- --run
npm run build
npm run check:complexity
npx playwright test
npx playwright test e2e/approval-center.spec.ts
npx playwright test e2e/visual-claude-surfaces.spec.ts --update-snapshots=all
npx playwright test e2e/visual-claude-surfaces.spec.ts
```

结果：

| command | result |
|---|---|
| `npm test -- --run` | 70 files / 598 passed / 0 failed |
| `npm run build` | 199 modules transformed / production build PASS |
| `npm run check:complexity` | PASS |
| `npx playwright test` | 38 passed / 1 conditional skip / 0 failed / retries=0 |
| fresh `approval-center.spec.ts` | 1 passed / 0 skipped / 0 failed |
| visual surface snapshot generation | 10 passed |
| visual surface no-update rerun | 10 passed |

Vitest 仍输出少量既有 React `act(...)` 警告；它们未造成失败，但作为测试债保留，
不把本轮结果描述为“零告警”。

## Browser 功能场景

`e2e/f158-surface-scenarios.spec.ts` 在同一真实 L1 runtime 中验证：

1. Approvals 普通语言空态与三类待办概览；
2. Tasks 筛选与真实运行任务可达；
3. Automation 暂停、恢复及测试后状态恢复；
4. Settings F150 远程访问刷新、Advanced 诊断且不硬编码 `maojiwang.work`；
5. Agent 创建模板入口与安全取消；
6. Memory 筛选、Advanced、Escape 与焦点归还；
7. Files 任务产物/工作区版本可逆切换；
8. Skill 安装对话框焦点闭环且取消不写入；
9. MCP 手动添加、Advanced 边界、Escape 与焦点归还。

既有 Browser L1 同时覆盖真实 Chat 工具写盘、Task SSE/工件/剪贴板、401/403
边界、Skill 安装、Access 登录/过期/登出/恢复、F150 remote access 与 390px
浏览器健壮性。fresh approval fixture 进一步证明 UI 接受 → REST → 行为文件真实
落盘 → pending 归零。

## 确定性状态矩阵

Browser L1 验收正常路径和代表性认证/断线边界；每个 surface 的完整确定性状态由
对应 L4 UI test 覆盖，避免为了截图复制生产状态机：

| surface | covered states |
|---|---|
| Approvals | loading、统一 empty、三来源、按源降级、403/404/409、accept/reject/conflict/pending |
| Tasks | loading、empty、recoverable error、403、filter、pending |
| Task Detail | loading、recoverable error、403、404、disconnected、unknown/history Advanced |
| Automation | empty、pause/resume、409/rejected、recoverable error、403、cron 文案 |
| Settings | provider empty/add/save、secret replace、auth、Advanced、F150 remote access |
| Agents | loading、empty、recoverable error、403、create/edit/delete、Advanced |
| Memory | loading、double-empty、recoverable error、403、filter、edit/archive/restore、Advanced |
| Files | loading、empty、recoverable error、403、binary/large/same/first version、race |
| Skills | loading、empty、recoverable error、403、409 install、uninstall、Advanced |
| MCP | loading、empty、recoverable error、403、secret keep/replace/clear、Advanced |

## Claude Design 早期视觉合同

Production styles：

- `octoagent/frontend/src/styles/claude-workbench.css`
  - SHA-256 `d64d79715de805b8d37756e1c9521fc5ae63da264987b52b281cb04ab515744a`
- `octoagent/frontend/src/styles/claude-surfaces.css`
  - SHA-256 `3fca66a813486497f8f2362a6ef3f93f8f548efe464d3a0bef7153600c655d87`

视觉合同冻结：

- desktop 1440×900 / dark；
- 页头 title 24–30px、padding 16–24px、radius ≤18px；
- 业务页禁止径向渐变；
- 紧凑低亮度连续卡片，不恢复后期大 Hero/高辐射 glow；
- 动态时间通过测试样式隐藏，不使用洋红遮罩污染基线；
- 真实任务详情与业务页使用同一暗色、细边框和单一绿色强调。

### 主工作台像素基线

| snapshot | SHA-256 |
|---|---|
| sidebar brand | `24c36c726256ddc1fedd8f35e14391a5596b1852774fc1af424465491acec8bc` |
| navigation row | `4bfc0ffdb0a9fd3d9889ca05f152f6a9fec12bfa4e6d4be50f1b965985c56679` |
| composer | `382a1b13f2de7fbdf3858a7a901a99fbb453252627e41a0f1817443c11cae2cb` |
| run panel head | `cc510fc04ab9a731a92fa47afa8cc47ac6b4d3d584b17e4de26ab2bb5c88ff24` |

### 业务 surface 像素基线

| snapshot | SHA-256 |
|---|---|
| agents | `62e5044ecf0dd92381f44e2288c00354667c6253968caf7ae0e43f23e9377764` |
| approvals | `8b360edc602279d5ded471bad402ba46ae6b7dc69dff85dd76fc7e8e4cad3978` |
| automation | `4566681c83eaf6569b828cbb4e41701df4c8f9cdab7a2afedb44a9899787abdb` |
| files | `26bc4b11dee301f1c1338684a08ee01a1b4d096b14ce09699cba6472702a01cf` |
| mcp | `abb58fa9f38cd754390ffb4f07f5ac7f556651a9184c9ead66b58af470312d0f` |
| memory | `3d36a4dc505b89a3698ea1e98bcd385022e41a3c1300470f7553d3248e5b3be2` |
| settings | `55d0ed0728ce2ff3647789f41a887bcc1e721cebc9c73415cd3d64c18c050359` |
| skills | `47dbea43dda7b96b27d92a84b05f5efb6ba7cdfa752fc6d3e8b611d25f327b46` |
| task detail | `d62122b9458b0262fc923f0a12dd1047e5e6a70e81be80f3297149f12d7f595d` |
| tasks | `4734ef1cab7f7dc88f516afd2fedf1e7de495b5193ded5eeecec68cbb3bd87c1` |

这些 PNG 位于：

- `octoagent/frontend/e2e/__snapshots__/visual-claude-baseline.spec.ts/`
- `octoagent/frontend/e2e/__snapshots__/visual-claude-surfaces.spec.ts/`

## 未完成边界

- Claude Design 云端后期不佳 frame/variant 尚未完成最终 lineage 清理与不可变导出；
- iOS 本机缺可用 Simulator runtime，App 尚未真实启动，视觉 E2E 为 0；
- `maojiwang.work` 只属于个人部署，Cloudflare live 尚未完成；
- 当前分支尚未提交、CI、个人部署或合并；
- error/403/409 已由 deterministic L4 UI 合同覆盖，后续 completion audit 仍需决定
  是否为选定的高风险状态增加独立 pixel baselines，不能把正常态 PNG 冒充所有状态。
