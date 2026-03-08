# Feature 028 产研汇总：MemU Deep Integration

**日期**: 2026-03-08  
**输入**: `product-research.md` + `tech-research.md` + `online-research.md`

## 交叉分析矩阵

| 维度 | 产品结论 | 技术结论 | Feature 028 决策 |
|---|---|---|---|
| 高级能力定位 | 用户需要更强 recall、多模态、derived insights | MemU 适合作为 engine plane，不适合作为治理事实源 | 保留本地 governance，扩展 `MemUBackend` |
| 权威事实保护 | “聪明”不能破坏“可信” | SoR/Vault/WriteProposal 必须继续由 `MemoryService` 控制 | 高级结果只允许落 `Fragments` / 派生索引 / `WriteProposal` |
| 多模态 ingest | 结果必须可回溯到原始材料 | 统一 `artifact -> fragment -> derived -> proposal(optional)` 路径最稳 | ingest 与 derivation 全部强制 evidence chain |
| Compaction / flush | 用户需要知道系统何时做了 consolidation | OpenClaw 证明 flush/compaction 应是显式执行链 | 定义 `maintenance run` 与审计状态，不静默改 SoR |
| 降级体验 | MemU down 不能让核心记忆失能 | 当前代码只有单向 degrade，缺 failback/diagnostics | 冻结 `healthy/degraded/unavailable/recovering` 状态机 |
| 027 并行开发 | 028 不应顺手做 UI | 027 当前没有稳定上游 contract | 在 028 先冻结 query/projection/integration contract |
| 026 集成 | 控制台只需入口级集成 | 026 canonical resources 不包含 memory route | 028 只输出 diagnostics / action hooks / deep refs |
| 025-B 绑定 | Memory engine 应绑定 project/secret | 当前 025-B 还没覆盖 MemU target kind | 在 028 spec 中明确补齐 project-scoped bridge binding |

## 推荐方案

采用“治理面保留本地，MemU 作为高级 engine plane 深度接入”的方案。

核心结构：

1. `MemoryService` 继续负责治理与事实写入。
2. `MemUBackend` 升级为真正的 bridge-backed engine。
3. 028 冻结 027 可消费的 query/projection/integration contract。
4. 026 只消费 diagnostics/action hooks，不接 detailed memory browsing。

## 本阶段必须冻结的 contract

- 扩展后的 `MemoryBackend` / `MemUBridge`
- `MemoryQueryRequest`
- `MemoryQueryProjection`
- `MemoryEvidenceProjection`
- `MemoryDerivedProjection`
- `MemoryBackendDiagnosticsProjection`
- `MemoryMaintenanceCommand` / `MemoryMaintenanceRun`
- 026 可消费的 memory diagnostics / action hooks

## 明确不在本阶段做的内容

- Memory Console / Vault UI
- 直接旁路 SoR 的高级事实写入
- 把 MemU server / UI 直接嵌入 OctoAgent

## 风险矩阵

| 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|
| MemU 接管事实层 | Critical | 破坏 020 治理模型 | 用 contract 明确禁止 direct SoR/Vault writes |
| 没有稳定 027 consumption contract | High | 后续 UI 自造 DTO | 在 028 设计阶段先冻结 projection contract |
| MemU 故障不可恢复 | High | 违反 graceful degradation | 设计健康状态机 + 自动回切 |
| 多模态结果无证据链 | High | 用户无法审计 | 强制 artifact / fragment evidence refs |
| 028 侵入 026/027 产品面 | Medium | 范围失控 | 仅输出 integration hooks，不做 UI |
