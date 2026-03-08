# Feature 027 技术调研：Memory Console + Vault Authorized Retrieval

**日期**: 2026-03-08  
**调研模式**: full / tech  
**核心参考**:
- `docs/m3-feature-split.md` Feature 027 / Feature 028
- `docs/blueprint.md` M3 产品化约束
- `.specify/features/020-memory-core/spec.md`
- `.specify/features/020-memory-core/verification/verification-report.md`
- `.specify/features/025-project-workspace-migration/spec.md`
- `.specify/features/026-control-plane-contract/spec.md`
- `octoagent/packages/memory/src/octoagent/memory/service.py`
- `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py`
- `octoagent/packages/memory/src/octoagent/memory/store/sqlite_init.py`
- `octoagent/packages/core/src/octoagent/core/models/control_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/control_plane.py`

## 1. 设计约束

Feature 027 必须满足以下硬约束：

1. **复用 020 治理内核**：任何权威事实仍然只能经 `WriteProposal -> validate -> commit_memory()` 落盘，027 不能直接旁路写 SoR/Vault。
2. **复用 026 control plane**：Memory 必须作为 canonical resource/action 接入现有 `/api/control/*` 与现有 Web 控制台，不得新造第二套 console framework。
3. **遵守 025-B project/workspace 语义**：Memory 浏览与权限表达必须能挂到 project/workspace/selector 语义上，不能继续只暴露裸 `scope_id`。
4. **Vault default deny 不变**：没有授权记录时，`VaultRecord` 的原文仍不可检索；027 只能补“授权链和审计面”，不能削弱默认 deny。
5. **不偷带 028**：允许为 MemU 预留 integration points，但不在 027 中引入深度 recall/索引/多模态治理新语义。

## 2. 当前代码基盘点

### 2.1 已有可复用内核

- `packages/memory` 已具备 `FragmentRecord`、`SorRecord`、`VaultRecord`、`WriteProposal` 与 `MemoryAccessPolicy`。
- `MemoryService` 已提供：
  - `validate_proposal()`
  - `commit_memory()`
  - `search_memory()`
  - `get_memory()`
  - `before_compaction_flush()`
- `SqliteMemoryStore` 已支持：
  - `list_sor_history(scope_id, subject_key)`
  - `list_fragments(scope_id, partition, ...)`
  - `list_vault(scope_id, partition, ...)`
  - `get_current_sor()` / `get_next_sor_version()`
- 020 的验证已经覆盖 `current` 唯一约束、Vault default deny、proposal validate/commit 闭环，可以继续作为 027 的 contract 基线。

### 2.2 当前缺口

- 没有 Memory 的 canonical control-plane document，`/api/control/snapshot` 也没有 memory resource。
- `MemorySearchHit` 只覆盖基础摘要，不足以表达 project/workspace、subject history、proposal audit、vault authorization state。
- `MemoryAccessPolicy` 只有 `allow_vault/include_history` 两个布尔位，尚未形成 operator-facing 权限模型。
- 没有持久化的 Vault 授权请求/决议/检索结果记录。
- 没有 `memory export / inspect / restore` 的 operator-facing 校验入口。
- Web 控制台当前 `SectionId` 只有 `dashboard/projects/sessions/operator/automation/diagnostics/config/channels`，没有 Memory 分区页面。

## 3. 关键技术判断

### 3.1 027 需要的是“投影层 + 审计层”，不是重写 memory engine

020 已经把写路径和读取 contract 冻结好了。027 的正确技术落点应是：

- 增加 query/projection contract
- 增加 Vault 授权记录与检索审计
- 增加 WriteProposal 审计投影
- 将这些投影发布到 control plane

而不是：

- 重新定义 memory schema
- 直接对 UI 暴露原始 SQLite 表
- 把 Memory Console 变成新的写入入口

### 3.2 需要新的“控制面文档”，但不需要新的控制面框架

026 的 `ControlPlaneDocument` 体系已经足够承载 027，只需扩展新的 canonical resources，例如：

- `MemoryConsoleDocument`
- `MemorySubjectHistoryDocument`
- `VaultAuthorizationDocument`
- `WriteProposalAuditDocument`

同时在现有：

- `/api/control/snapshot`
- `/api/control/resources/*`
- `/api/control/actions`
- `/api/control/events`

上继续扩展，而不是新建平行 `memory-console API family`。

### 3.3 project/workspace 需要通过 projection bridge 表达

020 的底层实体核心维度还是 `scope_id + partition + layer`。027 不应改写这层主键，而应在 projection 层补：

- `scope -> project/workspace` 解析
- 反向 filter：从 active project/workspace 派生 scope filter
- 无法解析时的 `orphan_scope / unbound_scope` 明示

这保证 025-B 的 project 语义与 020 的 memory core 可以兼容，而无需重做存储模型。

### 3.4 Vault 授权必须持久化为正式记录

单靠 `MemoryAccessPolicy(allow_vault=True)` 不足以支撑产品化授权链。027 需要至少三类持久对象：

- 授权申请
- 授权决议/生效记录
- 授权检索执行记录

这样才能满足：

- operator 审计
- 证据链追踪
- 后续 restore/export 校验
- 026 现有 action/event 路径复用

### 3.5 WriteProposal 审计应该使用投影，而不是让前端自己拼

`memory_write_proposals` 表已经有足够原始字段，但 UI 需要的是：

- proposal 来源
- validate 结果
- commit 结果
- 关联的 fragment/sor/vault ids
- evidence refs
- 相关 project/workspace/scope

因此 027 应新增 proposal projection/service，把原始表 + memory store 查询收束成 control-plane document，而不是让前端直接查 proposal 表。

## 4. 方案对比

| 维度 | 方案 A：前端直读 memory tables | 方案 B：Gateway 侧 memory control-plane projection | 方案 C：单独 memory service / 新 API |
|------|-------------------------------|---------------------------------------------------|--------------------------------------|
| 与 020 契约兼容 | 差，前端会依赖存储细节 | 高，只消费治理内核产出的稳定投影 | 中，需要再维护一层平行 API |
| 与 026 控制面兼容 | 差，会绕开 canonical resources | 高，可直接扩展 snapshot/resources/actions/events | 中，容易再次分叉 |
| 审计与权限实现 | 弱，难以集中表达 | 最好，gateway 能统一 actor/action/audit 语义 | 中，需要额外桥接 |
| 对 028 预留空间 | 差，前端容易绑死底层 | 高，只需在 projection 层添加 backend info | 中 |
| 推荐度 | 不推荐 | 推荐 | 不推荐 |

**推荐**：方案 B。继续以 Gateway control plane 作为 canonical producer，在 provider/memory/core 侧补 projection store 与 audit/authorization primitives。

## 5. 推荐技术落点

### 5.1 新增 canonical resources

推荐至少新增四类 control-plane document：

1. `MemoryConsoleDocument`
   - 总览当前 project/workspace 下的 SoR/Fragment/Vault/Proposal 摘要、filters、capabilities、degraded 状态
2. `MemorySubjectHistoryDocument`
   - 针对单个 `subject_key` 的 current/superseded 历史、evidence refs、latest proposal refs
3. `VaultAuthorizationDocument`
   - 授权申请、授权决议、生效范围、过期时间、最近检索记录
4. `WriteProposalAuditDocument`
   - proposal 列表、validate/commit 状态、关联落盘结果和 evidence refs

### 5.2 新增 actions

推荐 action registry 至少覆盖：

- `memory.query`
- `memory.subject.inspect`
- `memory.export.inspect`
- `memory.restore.verify`
- `vault.access.request`
- `vault.access.resolve`
- `vault.retrieve`
- `memory.proposal.inspect`

其中：

- `vault.access.resolve` 必须走 operator/policy 审计链
- `vault.retrieve` 必须返回 redact/authorized 两种明确结果码
- `memory.export.inspect` / `memory.restore.verify` 只做校验入口，不直接执行恢复写入

### 5.3 持久化建议

推荐在现有 `project_root/data/control-plane/` 或 provider DX store 下新增 memory control-plane state/audit store，用于：

- vault 授权申请与授权记录
- 检索执行审计
- export/restore 校验快照

但不复制 020 的权威事实表；权威数据仍以 `packages/memory` SQLite schema 为事实源。

### 5.4 权限模型建议

027 不需要企业级 RBAC，但至少要定义 operator-facing permission matrix：

- 谁可看 SoR 摘要
- 谁可看 superseded/history
- 谁可申请 Vault
- 谁可批准/拒绝 Vault 访问
- 谁可执行 export inspect / restore verify

该权限模型应保持：

- project-scoped
- default deny for Vault
- surface-agnostic（Web/Telegram/CLI 共语义）

## 6. 与 028 的边界

027 可以为 028 预留：

- `backend_id / retrieval_backend / index_health` 字段
- `integration points` 和 degraded metadata
- 后续高级 recall 结果的 ref slot

但 027 不能交付：

- MemU 驱动的高级 recall/分类/ToM
- 多模态 ingest
- consolidation/compaction 智能执行链
- 绕过 `WriteProposal` 的自动事实落盘

## 7. 技术风险

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | 直接把 Vault 原文塞进 control-plane resource | 中 | 高 | 资源层只返回 summary/redacted view，明细走授权检索动作 |
| 2 | project/workspace 过滤与底层 scope 不一致 | 中 | 高 | 在 projection 层固定 scope 解析规则，并对 orphan scope 显式 degraded |
| 3 | proposal 审计和 SoR 历史分开实现后前端自行拼接 | 中 | 中 | 后端直接提供 canonical documents 和 refs |
| 4 | Vault 授权仅落内存状态，重启后丢失 | 中 | 高 | 授权申请/决议/检索都必须落盘，纳入 control-plane audit |
| 5 | 为 028 预留字段时提前引入 engine 依赖 | 低 | 中 | 只留 metadata/integration points，不在本 Feature 引入新 backend 逻辑 |

## 8. 结论

027 的正确实现方式是：

1. 保持 020 为唯一 memory 治理内核；
2. 扩展 026 control plane，新增 memory/vault/proposal canonical resources 与 actions；
3. 通过持久化授权记录和 proposal 审计，把 Vault default deny 与 evidence chain 做成正式产品能力；
4. 为 028 只预留 integration points，不提前引入 MemU 深度引擎语义。
