# Research Summary: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

本特性采用 `full` 调研模式。

核心结论：

1. **025-B 必须建立在 025-A 已交付的 `Project / Workspace / ProjectBinding` 基线之上**。本阶段不再重做 migration，而是把 secret、wizard、project CLI 主路径挂到既有 canonical model 上。
2. **026-A 已冻结的 `WizardSessionDocument`、`ConfigSchemaDocument`、`ProjectSelectorDocument` 是上游真相源**。025-B 只能消费和落地这些 contract，不能另起一套语义。
3. **Secret Store 的 canonical 层应是 `SecretRef + project-scoped binding + runtime short-lived injection summary`**，而不是新的明文本地配置文件。
4. **普通用户路径必须从 env-first 切到 `project + wizard + secret lifecycle`**；`env/file/exec` 保留为高级路径，但不能再是默认路径。
5. **025-B 应直接复用 024 的 managed runtime / ops / recovery 基线完成 `reload`**；对 unmanaged runtime 则显式降级，而不是伪装成功。
6. **025-B 与 026-B 的边界要先切清**：本阶段交付 CLI 主路径和状态面，完整 Web 配置中心、Session Center、Scheduler、Runtime Console 留到 026-B。

详细依据见：

- `research/tech-research.md`
- `research/product-research.md`
- `research/online-research.md`
- `research/research-synthesis.md`
