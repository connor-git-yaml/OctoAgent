# 技术调研报告: Chat Import Core

**特性分支**: `codex/feat-021-chat-import-core`
**调研日期**: 2026-03-07
**调研模式**: full（离线 references + 在线证据）
**产品调研基础**: [product-research.md](product-research.md)

## 1. 调研目标

**核心问题**:
- 如何在不重写 020 的前提下，把聊天导入接入既有 `WriteProposal -> validate -> commit` contract
- 如何保证增量去重、窗口化摘要、artifact provenance 和 cursor 恢复同时成立
- 如何补一个最小用户入口而不提前绑定微信/Slack 等具体 adapter
- 如何让导入结果被 022 backup/export 和 023 集成验收自然消费

**产品 MVP 范围（来自产品调研）**:
- `octo import chats` + `--dry-run`
- ImportBatch / Cursor / Window / Summary / Report
- 原始聊天 artifact + fragment summary + optional SoR proposal
- 持久化 dedupe ledger 和导入报告

## 2. 当前代码基线

### 已有能力

1. `packages/memory` 已提供完整治理 contract：
   - `propose_write()`
   - `validate_proposal()`
   - `commit_memory()`
   - `search_memory()` / `get_memory()`
   - `before_compaction_flush()`
2. `packages/core` 已提供：
   - `NormalizedMessage`
   - Artifact Store
   - Event Store
   - 单库 `create_store_group()` 工厂
3. `022` 已证明批量运维功能可以通过 CLI + 状态文件 + operational task 审计链交付。

### 明确缺口

1. 仓库中不存在任何 021 的模型、store、service 或 CLI 命令。
2. `020` 的 memory 表目前没有与项目主库初始化打通的产品路径，021 需要给出实际使用方式。
3. 当前没有导入 dedupe ledger，也没有 import cursor / import report 的 durable schema。
4. Artifact Store 强制绑定 `task_id`，因此导入原文审计与生命周期事件必须采用 dedicated operational task 语义。

## 3. 推荐架构

### 方案对比表

| 维度 | 方案 A: 仅做库层导入服务 | 方案 B: 导入内核 + 最小 CLI 入口 | 方案 C: 直接做 Web 导入面板 + 多 adapter |
|------|-------------------------|----------------------------------|----------------------------------------|
| 概述 | 只暴露 Python service | 暴露 service，并提供 `octo import chats` | 同时做完整 UI、API、adapter |
| 用户可用性 | 低 | 高 | 高 |
| 实现复杂度 | 低 | 中 | 高 |
| 与 M2 目标匹配 | 低 | 高 | 中 |
| 与现有项目兼容性 | 中 | 高 | 中 |
| 对 023 验收价值 | 低 | 高 | 中 |

### 推荐方案

**推荐**: 方案 B，导入内核 + 最小 CLI 入口。

**理由**:
1. 它是满足 `M2` “用户可触达稳定入口” 的最小闭环。
2. 它可以复用 020、022 的现有 contract 和 operational task 模式，不需要重开体系。
3. 它避免把具体 source adapter 和 Web 面板提前拉进 021，保持范围收敛。

## 4. 关键技术设计

### 4.1 数据模型

建议新增最小模型集：

- `ImportBatch`
  - 批次级元数据：`batch_id`、`source_kind`、`source_uri`、`channel`、`thread_id`、`scope_id`、`status`、`dry_run`、`started_at`、`completed_at`
- `ImportCursor`
  - 增量位点：`cursor_value`、`last_message_ts`、`last_message_key`、`imported_count`、`duplicate_count`
- `ImportWindow`
  - 窗口摘要：`window_id`、`batch_id`、`message_count`、`first_ts`、`last_ts`、`artifact_ref`、`summary_text`
- `ImportSummary`
  - 本次执行结果：`imported_count`、`duplicate_count`、`window_count`、`proposal_count`、`warning_count`
- `ImportReport`
  - 用户可持久化查看的最终报告：`summary`、`cursor`、`artifact_refs`、`warnings`、`errors`、`next_actions`

### 4.2 幂等与去重

建议分两层：

1. **游标层**：记录 source cursor，支持从上次位置继续；
2. **消息去重层**：持久化 dedupe ledger，唯一键为：
   - 优先：`source_message_id`
   - 回退：`hash(sender_id + timestamp + normalized_text)`

结论：cursor 解决“从哪继续”，dedupe ledger 解决“即使重复扫到也不重复写入”。两者不能互相替代。

### 4.3 scope 与 provenance

- 导入 scope 必须使用 `chat:<channel>:<thread_id>` 或等价 chat scope；
- 原始聊天文本不得直接塞入主上下文，而应按窗口写为 Artifact；
- `FragmentRecord` 保存窗口摘要；
- 若从窗口中提炼出稳定事实，才生成 `WriteProposal`，再走 `validate_proposal()` / `commit_memory()`。

### 4.4 存储与集成方式

推荐 021 使用**项目主 SQLite**，并在同一连接上执行 `core.init_db + memory.init_memory_db + import schema init`：

- 好处 1：022 的 backup/export 可自然覆盖导入元数据与记忆写入；
- 好处 2：不需要引入第二个生产数据库文件；
- 好处 3：Event / Artifact / Memory / Import 可形成统一 durability 边界。

### 4.5 审计事件与 operational task

由于现有 Event Store / Artifact Store 都依赖 `task_id`，021 应采用 dedicated operational task，例如：

- `ops-chat-import`

该任务承载：
- `CHAT_IMPORT_STARTED`
- `CHAT_IMPORT_COMPLETED`
- `CHAT_IMPORT_FAILED`

原始窗口 artifact 也挂在这个 operational task 下，保证导入过程可回放、可追查。

## 5. 对现有契约的影响

### 对 020 的影响

- 021 只消费 `020` 的治理 contract，不新增旁路写入。
- 021 不回改 `search_memory()` / `get_memory()` 行为语义。
- 021 可以补充 import-specific 模型和 schema，但不改变 `WriteProposal` / `SorRecord` / `FragmentRecord` 主 contract。

### 对 022 的影响

- 若 021 使用项目主 SQLite + artifacts 目录，则 022 的 backup/export 路径基本无需重做，只需在后续实现阶段确认导入报告 / artifact 已自然纳入现有 bundle。
- 021 的用户入口建议与 022 风格一致：CLI first，结果写状态文件/报告文件，而不是只靠控制台即时输出。

## 6. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | cursor 存在，但重复扫描仍重复写入 | 中 | 高 | 引入持久化 dedupe ledger，唯一键校验放在 store 层 |
| 2 | 原始聊天直接写入 fragment，导致上下文污染和存储膨胀 | 中 | 高 | 原文只进 artifact，fragment 只保留窗口摘要 |
| 3 | 事实提取质量不稳定，误写 SoR | 中 | 高 | 只允许 proposal 驱动写入；低置信度走 fragment-only 或 `NONE` |
| 4 | 导入元数据单独放新数据库，022 backup 不覆盖 | 中 | 中 | 使用项目主 SQLite，统一 durability 边界 |
| 5 | 导入无 task_id，事件/产物无法审计 | 高 | 中 | dedicated operational task `ops-chat-import` |
| 6 | 过早承诺微信/Slack adapter，导致 021 范围膨胀 | 高 | 中 | 021 只定义 source contract 和 generic CLI 输入，不交付特定 adapter |

## 7. 产品-技术对齐度

### 覆盖评估

| MVP 功能 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| 用户可触达入口 | ✅ 完全覆盖 | 通过 `octo import chats` 提供稳定 CLI 入口 |
| 重复执行安全 | ✅ 完全覆盖 | cursor + dedupe ledger 双层保障 |
| 原文可审计 | ✅ 完全覆盖 | 原文窗口写 artifact，报告保留 refs |
| 不污染主聊天 scope | ✅ 完全覆盖 | 强制 chat scope 隔离 |
| SoR 写入治理 | ✅ 完全覆盖 | 只允许通过 020 contract 提交 |
| 具体微信/Slack 解析 | ❌ 未覆盖 | 明确留给后续 adapter / M3 |

### 扩展性评估

方案天然支持后续：
- 微信导入插件只需产出统一 source message 流；
- Web 导入面板可复用同一 service / report contract；
- 023 可直接围绕 CLI 入口和导入报告做集成验收。

### Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| Durability First | ✅ 兼容 | 批次、cursor、dedupe、report、artifact、event 都需持久化 |
| Everything is an Event | ✅ 兼容 | 导入生命周期通过 operational task 写事件 |
| Side-effect Must be Two-Phase | ✅ 兼容 | 事实写入仍走 proposal -> validate -> commit |
| Degrade Gracefully | ✅ 兼容 | dry-run 和 fragment-only 模式可在部分能力不可用时降级 |
| User-in-Control | ✅ 兼容 | 提供 dry-run、报告、scope 可见性与失败原因 |
| Observability is a Feature | ✅ 兼容 | 导入结果和原文引用可回放、可审计 |

## 8. 结论与建议

### 总结

021 的技术实现应该围绕“统一 durability 边界”展开：导入批次、去重账本、窗口 artifact、fragment 摘要、proposal 审计全部纳入现有 SQLite / Artifact / Event 体系，而不是旁路开新存储。

### 对产研汇总的建议

- 把 `octo import chats + --dry-run + ImportReport` 提升为 021 MVP，而不是可选增强。
- 把“使用项目主 SQLite”定为推荐架构，避免与 022 backup/export 脱节。
- 把“specific adapter out of scope”写死，防止 021 范围膨胀。
