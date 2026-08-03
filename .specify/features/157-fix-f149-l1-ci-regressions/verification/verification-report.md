# F157 验证报告

## 结论

PASS。四项 F149 L1 回归均已恢复，且没有修改页面组件、CSS、Claude Design
输出或产品功能。F151 clean-checkout corrective 已合并到 `master`，对应 exact
commit 的权威 CI 五个 job 全部通过。

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

### 干净检出 corrective

- 权威 run `30197077191`：L1、frontend、architecture、benchmark 通过；
  backend 的唯一失败为 5 项 F151 clean-checkout fixture。
- 五个原失败 exact node 本地复验：`5 passed`。
- 完整 `test_runtime_architecture.py`：`83 passed`。
- 完整 `octoagent/tests/gate`：`206 passed`。
- F151 runtime checker 无 diff；修复只在 gate test 的 Git baseline、临时 tree/JUnit
  fixture、F150 fixture 与 optional raw cross-check。

### 权威 master CI

- commit：
  `db3214fff722c6f969baf99528a76fc03a1e21a1`
- run：
  [30198514576](https://github.com/connor-git-yaml/OctoAgent/actions/runs/30198514576)
- workflow conclusion：`success`
- jobs：benchmark、frontend、l1-playwright、architecture、
  backend-deterministic 全部 `success`
- backend 同次运行已覆盖 deterministic layers、`e2e_scripted` 和 changed-lines
  coverage gate；因此 clean-checkout corrective 不再依赖开发机本地状态。

## 回归闭环

1. Chat 重新收到有界 `response_summary` / `error`，原始 provider、token usage、tool
   payload 和额外字段不穿透。
2. Task Detail 只把 model-call 映射为不含正文的 Advanced phase diagnostic，不驱动任务
   状态、artifact 或普通界面。
3. A-wave 使用场景启动时刻，watchdog 仍启用且完整 L1 中
   `drift_detected_count=0`。
4. 390px 合法只读空状态使用全局 shell 键盘操作作为可达性控制，不增加无意义 CTA。

## 完成状态

- F157 T001-T013 全部完成。
- 本报告的权威终态绑定上述 `master` commit 与 CI run；后续 Milestone 审计可将
  F157 视为已证明，不再标记为文档漂移。
