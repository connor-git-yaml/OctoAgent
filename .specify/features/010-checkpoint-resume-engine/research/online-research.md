---
required: true
mode: full
points_count: 4
tools:
  - perplexity/sonar-pro-search
  - official-docs-web-search
queries:
  - "Temporal activity definition idempotent requirement"
  - "Temporal workflow definition deterministic constraint"
  - "Pydantic AI durable execution overview Temporal DBOS Prefect"
  - "SQLite WAL mode checkpoint and concurrency"
skip_reason: ""
---

# 在线调研证据（Feature 010）

## Findings

1. **Workflow / Activity 需要区分：副作用代码必须幂等**
- Temporal 官方文档明确 Activity Definition 中“activity code should be idempotent”。
- 含义：恢复重试时，副作用步骤必须可重入或去重，不能依赖“不会重试”的假设。
- 参考: https://docs.temporal.io/activity-definition

2. **恢复要建立在确定性 workflow 上**
- Temporal Workflow Definition 文档强调 workflow 代码必须 deterministic（同输入同历史，结果一致）。
- 含义：010 的 resume 逻辑不能引入非确定性分支（如基于 wall-clock 随机切换路径）。
- 参考: https://docs.temporal.io/workflow-definition

3. **Pydantic AI Durable Execution 已把可恢复执行作为一等能力**
- Pydantic 官方文档说明 Durable Execution 支持 Temporal/DBOS/Prefect，并强调跨故障恢复与长流程可靠性。
- 含义：OctoAgent 在 M1.5 先做 SQLite 本地耐久恢复，与后续演进到更强编排器并不冲突。
- 参考: https://ai.pydantic.dev/durable_execution/

4. **SQLite WAL 下 checkpoint 与并发写需要显式策略**
- SQLite 官方文档解释了 WAL checkpoint 行为、单写者模型与并发读取语义。
- 含义：010 需要避免多恢复流程并发写 checkpoint；应设置租约/锁并控制 checkpoint 触发点。
- 参考: https://sqlite.org/wal.html

## impacts_on_design

- 设计决策 D1：把“副作用幂等键”纳入 checkpoint/resume 契约，而非仅纳入工具层。
- 设计决策 D2：恢复流程采用确定性状态机（pending -> running -> success/error），禁止隐式跳步。
- 设计决策 D3：持久化层使用 SQLite WAL + 任务级恢复互斥（lease/lock）避免双写冲突。

## 结论

在线证据与本地源码调研一致，支持 Feature 010 采用“节点级 checkpoint + 确定性 resume + 副作用幂等防重放”的最小可行方案。
