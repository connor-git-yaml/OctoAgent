# Feature 028 产品调研：MemU Deep Integration

**日期**: 2026-03-08  
**范围**: 高级 memory engine 的产品价值、治理边界、027/026 集成方式  
**参考**:
- `docs/m3-feature-split.md` Feature 027 / Feature 028
- `docs/blueprint.md` §8.7.4 Memory 治理约束
- `.specify/features/020-memory-core/contracts/memory-api.md`
- `.specify/features/026-control-plane-contract/spec.md`
- `_research/16-R4-design-MemU-vNext记忆体系与微信插件化.md`
- `_references/opensource/openclaw/docs/reference/session-management-compaction.md`
- `_references/opensource/agent-zero/python/helpers/memory.py`

## 结论摘要

Feature 028 的产品目标不是“把 MemU 接进来就结束”，而是让 OctoAgent 获得一个真正可用的高级记忆引擎，同时不破坏已经在 Feature 020 冻结的治理边界。

产品上必须同时满足 4 件事：

1. 检索与索引明显强于 SQLite-only fallback，但用户仍然只能信任经过仲裁的 SoR。
2. 多模态输入、Category/relation/entity/ToM 等高级结果必须能追溯到 artifact / fragment 证据。
3. MemU 故障时主对话、核心搜索、当前事实回答不能整体失效。
4. 028 只提供 027 可消费的 query/projection/integration contract，不提前实现 Memory Console UI。

## 用户视角需求

### 1. “更聪明的记忆”不能破坏“可信的记忆”

对用户来说，MemU Deep Integration 的价值不是多一个向量库，而是：

- 相同 query 能召回更相关的内容
- 支持文本以外的图片、音频、文档
- 能从大量材料里形成 category / relation / entity / ToM 等高级视角

但这些都不能直接替代权威事实层。

因此 028 的产品边界必须非常清楚：

- `SoR` 仍是当前权威事实
- `Fragments` 仍是过程证据与摘要
- `WriteProposal` 仍是唯一事实变更入口
- `MemU` 产出的高级结果只能作为“候选事实、检索索引、派生视角”

### 2. 多模态 ingest 必须以 evidence chain 为中心

M3 明确要求文本 / 图片 / 音频 / 文档进入统一 memory 体系，但用户真正需要的是“可解释”而不是“自动很强”。

因此 ingest 的产品语义必须是：

- 先形成 artifact refs
- 对图片/音频/文档先经过 extractor 或 sidecar text/metadata 提取
- 再形成 fragments / derived indexes
- 最后如需改变 SoR，只能形成 `WriteProposal`

用户未来在 027 中看到的每个高级结果，都必须能回答：

- 来源是什么 artifact？
- 经过了哪些 fragment / consolidation？
- 为什么形成了这个 entity / relation / ToM 判断？

### 3. 027 和 028 必须解耦并行

Feature 027 的产品目标是“让用户看懂记忆”；Feature 028 的产品目标是“让系统产出更强的记忆引擎结果”。

因此 028 不应直接做：

- Memory Console 详细列表/详情页
- Vault 授权面板
- SoR 历史浏览 UI
- 审计可视化页面

028 应提供的是 027 可直接消费的 backend contract：

- query / list / inspect / evidence projection
- derived layer projection
- backend diagnostics / degrade state
- maintenance / flush / compaction status

### 4. 自动降级本身是产品要求，不是技术细节

如果 MemU 是高级 engine，那么它失效时系统表现必须可预测：

- 主聊天不应因为 MemU down 而不可用
- `search_memory()` 仍要返回最小可用结果
- diagnostics 必须明确告诉用户当前是 `healthy / degraded / unavailable / recovering`
- 027/026 看到的不是“空白页”，而是“高级引擎不可用，当前正在 fallback”

这决定了健康诊断、错误分类、同步积压、回切状态都必须是正式产品对象。

## 028 与 027/026/025-B 的产品边界

### 与 027 的边界

028 提供：

- query/projection/integration contract
- derived layers
- evidence chain
- backend diagnostics
- maintenance status

027 提供：

- 浏览和授权 UI
- proposal 审计 UI
- Vault retrieval UI
- export / inspect / restore 入口

### 与 026 的边界

028 不新增 control-plane canonical resource。  
026 只消费 028 暴露的：

- diagnostics contribution
- 可选 action registry entries
- event/integration refs

Memory detailed view 仍不放进 026。

### 与 025-B 的边界

025-B 已经提供 project/workspace、wizard、secret store 主路径。  
028 必须基于它来绑定：

- MemU bridge endpoint / profile / API key 引用
- project-scoped memory isolation
- project-scoped health / reindex / maintenance

但 028 不重新设计 secret store 或 wizard 产品面。

## 产品决策建议

### 必须纳入 028

- `MemUBackend` 从薄 adapter 升级为高级 engine contract
- bridge 健康诊断与自动降级/回切
- 多模态 ingest 管线
- derived layers：Category / relation / entity / ToM
- compaction / consolidation / flush 的可审计执行链
- 027 可消费的 query/projection/integration contract

### 明确不纳入 028

- Memory Console 详细 UI
- Vault 授权产品面
- 直接旁路 SoR 的高级事实写入
- 把 MemU 变成唯一事实源

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 把 MemU 当作新的事实源 | 直接破坏 020 的治理模型 | spec 中硬性禁止直接写 SoR / Vault |
| 高级结果没有证据链 | 027 无法做可信浏览 | 所有高级结果强制携带 artifact / fragment evidence |
| 028 顺手把 027 UI 一起做掉 | 范围膨胀，里程碑失控 | 只冻结 query/projection/integration contract |
| MemU 故障即整体失能 | 违反 Constitution 6 | 健康状态、fallback、failback 进入正式 contract |
