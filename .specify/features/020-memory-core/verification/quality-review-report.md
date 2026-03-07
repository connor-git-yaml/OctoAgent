# Feature 020 Quality Review Report

**日期**: 2026-03-07

## 优点

- `packages/memory` 按现有 workspace package 形态落地，边界清晰，没有把逻辑继续塞进 `core`。
- SoR current 唯一约束下沉到 SQLite partial unique index，避免仅靠业务代码自觉。
- `search_memory()` / `get_memory()` 保持两段式读取，减少上下文污染。
- `MemoryBackend` / `MemUBackend` adapter 位已经落地，M2 可以在不改 governance contract 的前提下接入 MemU。
- review 中暴露的 3 个 P1 已补齐：store 自动设置 `row_factory`、commit 阶段重验版本、敏感分区默认搜索屏蔽。
- 测试覆盖模型、schema、store、service、backend、durability 六类关键路径。

## 风险与改进

- 当前 `search_memory()` 的模糊查询仍是 SQLite `LIKE`，与 blueprint 里的 LanceDB 目标相比是有意降级；后续可在不破坏 contract 的前提下补向量检索。
- `MemoryService` 目前没有统一事务桥接到 `core` Event Store；如果后续接入 task 级审计，建议新增 memory transaction helper 统一写 proposal + memory state + event。
- 敏感分区当前通过安全摘要落 SoR、Vault skeleton 留引用，后续需要真实密文/secret backend 承载 `content_ref`。
- 当前 `MemUBackend` 仍是 adapter contract，不含真实 bridge/client；进入 021 或 M3 时需要补可观测的 bridge 实现、重试策略与同步幂等。
