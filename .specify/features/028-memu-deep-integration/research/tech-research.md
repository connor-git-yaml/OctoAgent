# Feature 028 技术调研：MemU Deep Integration

**日期**: 2026-03-08  
**调研模式**: full  
**核心参考**:
- `docs/m3-feature-split.md` Feature 028 / Feature 027
- `docs/blueprint.md` §8.7.4 / M3 产品化约束
- `.specify/features/020-memory-core/contracts/memory-api.md`
- `octoagent/packages/memory/src/octoagent/memory/service.py`
- `octoagent/packages/memory/src/octoagent/memory/backends/protocols.py`
- `octoagent/packages/memory/src/octoagent/memory/backends/memu_backend.py`
- `octoagent/packages/memory/src/octoagent/memory/imports/service.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `_research/16-R4-design-MemU-vNext记忆体系与微信插件化.md`
- `_references/opensource/openclaw/docs/reference/session-management-compaction.md`
- `_references/opensource/agent-zero/python/helpers/memory.py`
- `research/online-research.md`

## 1. 设计约束

Feature 028 必须同时满足以下硬约束：

1. `MemoryService` 仍然是治理面 owner，`WriteProposal -> validate -> commit` 不得外包给 MemU。
2. `MemUBackend` 可以接管检索、索引、增量同步、派生智能层，但不能旁路写 SoR / Vault。
3. 所有高级结果只能落为：
   - `Fragments`
   - 派生索引
   - `WriteProposal`
4. 所有高级结果必须带 artifact / fragment evidence chain。
5. MemU 不可用时必须自动退回 `SQLite-only search + local store` 最小路径。
6. 028 只补齐并兼容扩展 027 可消费的 query/projection/integration contract，不实现新的详细 UI。
7. 026 control plane 只能消费 diagnostics / action hooks，不增加新的 canonical control-plane resource。

## 2. 现有代码基盘点

### 2.1 已有可复用对象

- `MemoryService` 已冻结核心治理接口：`propose_write()`、`validate_proposal()`、`commit_memory()`、`search_memory()`、`get_memory()`、`before_compaction_flush()`。
- SQLite store 已经提供：
  - SoR current 唯一约束
  - Fragments append-only
  - Vault default deny
  - proposal 审计状态
- `ChatImportProcessor` 已有导入窗口、去重、cursor、artifact refs 输入结构，可作为 multimodal ingest 的上游。
- Feature 025-B 已提供 project/workspace、wizard、secret store 主路径，可承接 MemU bridge 配置。
- Feature 026 已提供 diagnostics summary 与 action registry 的控制面接缝，可承接 memory health / maintenance hooks。

### 2.2 当前缺口

- `MemoryBackend` 只有 `search + sync_*`，缺少：
  - 结构化健康诊断
  - 批量/幂等同步
  - 多模态 ingest
  - 派生层查询
  - maintenance / flush / compaction 执行
  - failback/retry/backoff 状态
- `MemUBridge` 还没有真实 HTTP/local-process/plugin bridge。
- `search_memory()` 只有单向降级，没有标准化恢复策略。
- memory 相关 diagnostics 还没有接到 026 control plane。
- 025-B 的 secret / wizard 还没覆盖 MemU target kind，无法正式配置 MemU endpoint/profile/API key。
- 027 已经交付 canonical resource，但 028 仍需补齐 engine-side query/projection/integration contract，避免后续深度集成重新分叉 DTO。

## 3. 外部参考实现收敛

### 3.1 MemU 官方能力面

可借鉴：

- 多模态输入统一进入同一层级 memory 结构
- 同时支持快速检索和更深层语义检索
- Category / Item / Resource 分层天然适合作为“派生层 + 证据链”模型
- retrieve / memorize 两类 API 明确区分“写入/抽取”和“查询/召回”

不应照搬：

- MemU 的 Resource/Item/Category 分层不是 OctoAgent 的治理事实源
- MemU 的 categories/items 不能直接映射成 SoR current
- MemU UI/server 不是 028 范围
- 不能假定 MemU bridge 原生吃下所有媒体；生产形态更接近 `artifact -> extractor/sidecar text -> MemU ingest`

### 3.2 OpenClaw memory + compaction

可借鉴：

- `memory_search` / `memory_get` 两段式读取
- compaction 前 `memory flush` 是显式执行链，而不是隐式副作用
- 索引、watch、fallback 与 session memory 可并行存在

不应照搬：

- OpenClaw 的 Markdown 文件就是事实源；OctoAgent 的事实源仍是 SoR/Vault/store
- OpenClaw 的 session transcript 文件模型不能替代 OctoAgent 的 WriteProposal 审计流

### 3.3 Agent Zero memory dashboard / project isolation

可借鉴：

- project-scoped memory isolation
- conversation / knowledge / learning 等不同 memory 区域
- dashboard 与引擎解耦，先有 memory engine，再做浏览器

不应照搬：

- Agent Zero 允许直接编辑 memory entry；OctoAgent 的 SoR 仍需仲裁
- dashboard 产品面属于 027，不属于 028

## 4. 架构方案对比

| 维度 | 方案 A：MemU 直接接管 memory | 方案 B：治理面留本地，MemU 做 engine plane | 方案 C：维持当前薄 adapter |
|---|---|---|---|
| SoR / Vault 治理一致性 | 差 | 高 | 高 |
| 高级能力覆盖 | 高 | 高 | 低 |
| 与 020 契约兼容 | 差 | 高 | 中 |
| 与 027 并行开发友好度 | 中 | 高 | 低 |
| 故障降级复杂度 | 高 | 中 | 低 |
| 长期演进性 | 中 | 最好 | 差 |

**推荐**：方案 B。

结论：

- OctoAgent 自己持有 governance plane
- MemU 负责 engine plane
- 通过 bridge + protocol + diagnostics + maintenance contract 接入

## 5. 推荐架构

### 5.1 三层结构

1. **Governance Plane**
   - `MemoryService`
   - `WriteProposal`
   - SoR / Vault / Fragments 规则
2. **Memory Engine Plane**
   - `MemUBackend`
   - retrieval / indexing / ingest / derivation / maintenance
3. **Surface / Product Plane**
   - Feature 027 的 Memory Console
   - Feature 026 的 diagnostics / actions hook

### 5.2 `MemoryBackend` 扩展方向

建议把协议扩展为以下能力族：

- `availability / diagnostics`
- `search / retrieve / evidence resolution`
- `sync / index / replay / resume`
- `multimodal ingest`
- `derived layer query`
- `maintenance / consolidation / compaction / flush`

关键点：

- 所有 bridge 调用必须显式返回结构化状态，而不是只有 `bool`
- 所有 ingest/sync/maintenance 都必须支持幂等 key 与批量执行
- 所有高级结果都必须返回 evidence refs / artifact refs

### 5.3 查询与投影 contract

028 需要先补齐并兼容扩展 027 当前消费的 5 组对象：

- `MemoryQueryRequest`
- `MemoryQueryProjection`
- `MemoryEvidenceProjection`
- `MemoryDerivedProjection`
- `MemoryBackendDiagnosticsProjection`

设计原则：

- 仍复用 `search_memory()` / `get_memory()` 作为 canonical read gate
- projection 是面向 027/026 的消费对象，不是替代治理模型的新事实源
- query/projection 可以展开 derived layers，但不能自动暴露敏感原文

### 5.4 多模态 ingest

推荐 ingest 统一使用：

- `artifact ref` 作为原始输入锚点
- `extractor output / sidecar text` 作为图片、音频、文档进入 memory engine 的标准 handoff
- `FragmentRecord` 作为可检索摘要/证据层
- `DerivedMemoryRecord` 作为 entity/relation/category/tom 派生层
- `WriteProposal` 作为潜在 SoR 更新入口

这样文本 / 图片 / 音频 / 文档都能走同一证据链：

`artifact -> fragment -> derived -> proposal(optional) -> validate -> commit`

### 5.5 compaction / consolidation / flush

推荐把 maintenance 做成独立、可审计执行链：

- `MemoryMaintenanceCommand`
- `MemoryMaintenanceRun`
- `MaintenanceArtifactRef`
- `MaintenanceProposalRef`

原则：

- `before_compaction_flush()` 仍只生成 fragment/proposal 草案
- 真正的 consolidation / compaction executor 负责调度、落审计、调用 backend
- 任何 maintenance 都不能静默改变 SoR

### 5.6 健康、降级与回切

推荐把 backend 状态显式建模为：

- `healthy`
- `degraded`
- `unavailable`
- `recovering`

并至少暴露：

- `last_success_at`
- `last_failure_at`
- `failure_code`
- `retry_after`
- `sync_backlog`
- `pending_replay_count`
- `active_backend`

`MemoryService.search_memory()` 依据该状态自动选择：

- MemU primary
- SQLite fallback
- 恢复探测后自动回切 primary

## 6. 与 025-B / 026 / 027 的落点建议

### 6.1 Feature 025-B

028 应明确依赖 project/workspace + secret refs，但不在本阶段实现 wizard/UI：

- 新增 memory/MemU bridge target kind
- project-scoped endpoint/profile/binding
- reload/materialize hook

### 6.2 Feature 026

028 不新增 canonical control-plane resource；只输出：

- diagnostics contribution
- 可选 memory maintenance action definitions
- event refs / deep refs

### 6.3 Feature 027

027 必须继续以其已交付 canonical resources 为产品面，同时使用 028 补齐的 query/projection contract 作为 backend integration 扩展面：

- UI 不得自行发明 memory DTO
- UI 不得绕过 evidence / authorization 规则
- Vault 授权检索仍由 027/020 治理层控制

## 7. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---|---|---|---|
| 1 | 把 derived layer 当成事实层 | 中 | 高 | 明确 `derived != SoR`，所有事实更新都经 proposal |
| 2 | bridge 健康状态只有 bool | 高 | 高 | 设计结构化 diagnostics contract |
| 3 | 027 没有先拿到稳定 projection contract | 高 | 高 | 在 028 设计阶段先冻结 query/projection/integration contract |
| 4 | 多模态 ingest 直接写 SoR | 中 | Critical | 强制 `artifact -> fragment -> proposal` 路径 |
| 5 | MemU 故障后只降不回 | 中 | 中 | 在状态机里定义恢复探测与自动回切 |
| 6 | control plane 想直接吃 memory raw data | 中 | 中 | 026 仅消费 diagnostics/actions/deep refs，不消费 raw memory details |

## 8. 结论

028 的正确实现路径不是“让 MemU 变成新的 Memory Core”，而是：

1. 扩展 `MemUBackend` 为高级 engine contract。
2. 保持治理面继续由 OctoAgent 控制。
3. 让 027 和 026 基于统一 projection/integration contract 并行消费。

这样可以同时得到 MemU 的高级能力、OctoAgent 的治理一致性，以及 M3 所需的产品化演进空间。
