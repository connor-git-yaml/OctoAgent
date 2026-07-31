# F155 Product / Design / Tasks Gate Review

- 日期：2026-07-29
- 审查者：F158 Milestone closure
- `GATE_RESEARCH=true`
- `GATE_PRODUCT_DECISION=false`
- `GATE_DESIGN=false`
- `GATE_TASKS=false`
- `GATE_IMPLEMENT=false`

## 判定

Research PASS；其余 HARD STOP。

Apple 官方资料确认 iOS 没有 calendar read-only 权限。读取 events 必须申请 full
access，而系统能力同时允许 create/edit/delete。F152 已明确要求用户在 F155 实施前
作出产品决定；不能由 broad Goal 或一般执行授权推定同意。

## 等待的唯一产品决定

- 方案 A：保留 F155，接受系统 full access，同时要求 Octo 代码层 read-only；
- 方案 B：从 M12 移除 F155，不请求日历权限。

## 即使选择 A 仍关闭 Implement

F153/F154 Verify 尚未完成。当前不得添加 EventKit usage description、production
Swift/Python、Gateway route、capability 或行为 RED。

## Resume fallback

本 worktree 没有 `.specify/.spec-driver-path`、driver config 或
`plugins/spec-driver/scripts/*`，因此本轮按 `spec-driver-resume` 的人工 fallback
恢复，并在这里记录缺失，而没有伪造 orchestration run。
