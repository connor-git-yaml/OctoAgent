# F152 验证报告

## 结论

本地验证通过。四项 F149 L1 回归均已恢复，且没有修改页面组件、CSS、Claude Design
输出或产品功能。最终通过条件仍包括提交后的权威 `master` CI 全绿。

## TDD 证据

- Gateway finite model-call projection 合同先在旧实现上以
  `F149_TASK_SSE_CONTRACT_MISSING` 见红，随后实现转绿。
- Web Task Detail decoder 合同先在旧实现上以
  `F149_TASK_SSE_FRONTEND_DECODER_MISSING` 见红，随后实现转绿。
- 合同覆盖 started/completed/failed、敏感失败信息净化、额外 raw 字段拒绝、畸形历史
  completed 降级与 event/payload 类型错配。

## 本地验证结果

### Gateway

- F149 Task SSE 合同：`5 passed`；目标模块 statement coverage `90%`。
- Task SSE、secret egress、Web contract、US3 SSE 联合回归：`23 passed`。
- Ruff check / format check：PASS。

### Frontend

- 完整 Vitest：`69 files / 597 tests passed`。
- 完整 Playwright L1：`19 passed`。
- OpenAPI generated contract check：PASS。
- TypeScript + Vite production build：PASS。
- 前端复杂度门：PASS。

### 架构与质量

- `check-runtime-architecture.py all --base-ref origin/master --scope-mode repository`：PASS。
- `git diff --check`：PASS。
- Gateway projector、Chat consumer 与 Task Detail decoder 的共享 SSE 边界已同步到
  `docs/codebase-architecture/modules/06-frontend-workbench.md`。
- 没有新增 endpoint、transport、store、service、registry、兼容层或 UI。

## 回归闭环

1. Chat 重新收到有界 `response_summary` / `error`，原始 provider、token usage、tool
   payload 和额外字段不穿透。
2. Task Detail 只把 model-call 映射为不含正文的 Advanced phase diagnostic，不驱动任务
   状态、artifact 或普通界面。
3. A-wave 使用场景启动时刻，watchdog 仍启用且完整 L1 中
   `drift_detected_count=0`。
4. 390px 合法只读空状态使用全局 shell 键盘操作作为可达性控制，不增加无意义 CTA。

## 待完成

- 提交并推送修复分支。
- fast-forward 合并到 `master` 并推送。
- 等待新的权威 `master` CI 全部 job 通过。
