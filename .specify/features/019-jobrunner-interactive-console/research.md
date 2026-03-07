# Research Summary: Feature 019 — Interactive Execution Console + Durable Input Resume

本特性采用 `tech-only` 调研模式。

核心结论：

1. **执行面必须先冻结 contract 再接线 UI/worker**。当前仓库已经有 `TaskRunner` / `WorkerRuntime` / `EventStore` / `ArtifactStore`，缺少的是 execution console contract，而不是另一套任务主链。
2. **实时控制不应发明第二套传输协议**。仓库已有 task SSE；019 更合理的做法是把 execution 事实落回任务事件链，然后让 Web/Telegram/调试工具复用同一事实源。
3. **先冻结 execution contract，再决定是否演进到独立 docker driver**。当前 019 以控制台语义、输入恢复、审批 gate 为主，不额外引入第二套执行主链。
4. **人工输入必须走最小权限 gate**。现有 `ApprovalManager` 已能提供 allow-once / allow-always 语义，019 只需要把 attach input 接入状态 gate 和可选审批 gate。

详细依据见 `research/tech-research.md`。
