---
required: true
mode: tech-only
points_count: 3
tools:
  - openrouter-perplexity:web_search
queries:
  - best practices autonomous agent worker runtime free loop max steps budget cancellation graceful shutdown
  - docker command execution layered timeout first output between output max execution best practice
  - checkpoint resume idempotency key exactly-once side effects workflow engine design
findings:
  - Free Loop 需要预算合同化（steps/tokens/time）并在每轮前检查，预算耗尽时应优雅退出。
  - Docker 长任务应分层超时而非单一 timeout，至少区分首包超时与总执行超时。
  - 恢复链路应使用稳定 idempotency key，重复执行时以“已执行冲突”视作成功防止副作用重复。
impacts_on_design:
  - 新增 WorkerRuntime 配置与 WorkerSession 状态，显式记录 loop_step/max_steps。
  - TimeoutConfig 采用 first_output/between_output/max_exec 三层，并允许 backend 能力门控。
  - cancel/timeout 失败分类统一为可审计结果，避免任务挂起。
skip_reason: ""
---

## 证据摘要

- 调研点 1（Free Loop）支撑 F009-T01/T02：预算控制和优雅停机必须进入 runtime 主循环。
- 调研点 2（Docker Timeout）支撑 F009-T03/T05：Docker backend 需要可降级策略与多层 timeout。
- 调研点 3（Idempotency）支撑 F009-T06：cancel/timeout/retry 场景需幂等，避免重复副作用。
