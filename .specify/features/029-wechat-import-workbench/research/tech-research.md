# Feature 029 技术调研：WeChat Import + Multi-source Import Workbench

**日期**: 2026-03-08  
**调研模式**: full / tech  
**核心参考**:
- `docs/m3-feature-split.md` Feature 029 / Feature 031
- `docs/blueprint.md` M3 产品化约束、Chat Import、Memory/MemU 约束
- `.specify/features/021-chat-import-core/spec.md`
- `.specify/features/025-project-workspace-migration/spec.md`
- `.specify/features/026-control-plane-contract/spec.md`
- `.specify/features/027-memory-console-vault-authorized-retrieval/spec.md`
- `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_commands.py`
- `octoagent/packages/memory/src/octoagent/memory/imports/service.py`
- `octoagent/packages/memory/src/octoagent/memory/imports/models.py`
- `octoagent/packages/memory/src/octoagent/memory/backends/memu_backend.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `octoagent/packages/core/src/octoagent/core/models/control_plane.py`

## 1. 设计约束

029 必须同时满足以下硬约束：

1. **复用 021 Import Core**：不得重写 `ImportBatch / ImportCursor / ImportReport / dedupe / windowing / proposal` 主流程。
2. **复用 025 Project/Workspace**：mapping、scope 绑定、resume 入口必须是 project/workspace aware。
3. **复用 026 Control Plane**：导入工作台必须接入既有 control-plane resource/action/event，不得新造平行 console。
4. **复用 027/028 Memory 边界**：附件与导入结果可以进入 artifact/fragment/MemU integration point，但任何权威事实仍经 `WriteProposal -> validate -> commit`。
5. **不偷带 031**：029 只交付导入能力与工作台，不负责定义最终 M3 全量验收矩阵。

## 2. 当前代码基盘点

### 2.1 021 已经交付的内核能力

`ChatImportService` 与 `ChatImportProcessor` 已经提供：

- `normalized-jsonl` 输入加载
- `source_id + scope_id + message_key` 去重
- `cursor/resume`
- 窗口化摘要
- 原始窗口写入 artifact
- fragment 生成
- 基于 `fact_hints` 的 proposal / validate / commit
- `ImportReport` 与 lifecycle audit event

对应文件：

- `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_commands.py`
- `octoagent/packages/memory/src/octoagent/memory/imports/service.py`
- `octoagent/packages/memory/src/octoagent/memory/imports/models.py`

### 2.2 当前导入路径的明确缺口

当前实现仍然只有“内核 + 触发器”，还没有 029 需要的产品层：

- `ImportSourceFormat` 只有 `normalized-jsonl`
- 没有 source adapter registry，也没有 source-specific parse/config step
- Control Plane 只有 `import.run` action，没有 import 资源文档
- 前端只暴露一个 `input_path + source_format` 表单
- 没有 mapping profile、conversation binding、dedupe detail、resume list 的正式投影
- 附件目前只沿 `ImportedChatMessage.attachments` 透传，没有多源附件的统一 materialization / provenance / MemU sync 设计

### 2.3 026 已有可承接的控制面能力

026 已经具备：

- canonical control-plane documents 基类
- per-resource route / snapshot route / action registry / action execution / control-plane events
- Web Control Plane shell
- `import.run` action 已在 registry 中存在

这说明 029 不需要新造 import 后台，只需要在现有 control-plane 模型上增加新的 import resources / actions / UI section。

### 2.4 028 已有可用 integration point

`MemUBackend` 已经提供：

- `search()`
- `sync_fragment()`
- `sync_sor()`
- `sync_vault()`

而 `MemoryService.commit_memory()` 在 commit 后会调用 backend sync。  
这意味着 029 对 MemU 的正确接法不是“导入时直接写高级记忆”，而是：

- 附件/消息先进入 artifact + fragment
- proposal/commit 继续走 020 治理
- MemU 只消费 fragment/sor/vault 的 backend sync 或新增的 import attachment sync integration point

## 3. 关键技术判断

### 3.1 029 需要一个“adapter 层”，而不是重写 import engine

推荐分层：

1. **Source Adapter Layer**
   - 负责解析 WeChat 导出目录/HTML/JSON/SQLite snapshot
   - 输出统一 `ImportedChatMessage`、attachment descriptors、mapping hints、cursor hints
2. **Import Workbench Projection Layer**
   - 负责 dry-run、mapping preview、dedupe summary、resume entries、warnings/errors
3. **021 Import Core**
   - 继续负责 batch/cursor/dedupe/window/proposal/artifact/event

这样可以让 029 的新增代码聚焦在“source normalization + product projection”，而不是破坏 021。

### 3.2 Import Workbench 应该是 control-plane resource，不只是 action result

当前 `import.run` 的结果是一次性 `ActionResultEnvelope`。  
029 要求的内容包括：

- dry-run 结果
- mapping 预览
- dedupe 详情
- warnings/errors
- cursor/resume 入口
- 过去批次报告

这些都不适合作为瞬时 action result，应该形成正式资源，例如：

- `ImportWorkbenchDocument`
- `ImportSourceCatalogDocument`
- `ImportRunDocument` / `ImportReportDocument`

具体命名可在 plan 阶段再冻结，但技术上必须是 canonical resource。

### 3.3 WeChat adapter 的默认路径应是“用户提供本地导出物”

因为官方不提供完整聊天服务端导出，安全且可维护的默认路径应是：

- 用户提供 WeChat 桌面导出目录 / HTML / JSON / SQLite snapshot
- adapter 在本地解析消息与附件引用
- 可选支持社区导出工具产物，但不把“进程注入/在线解密/设备直连”设为默认主路径

这能满足：

- 合规与可维护性
- 可测试性
- 跨平台降级
- 与 024/025 的安装/配置复杂度保持一致

### 3.4 附件管线必须 artifact-first，并保留 provenance

029 不能把附件只是挂成 message metadata。正确路径应是：

- 附件先 materialize 到 artifact store
- artifact 记录 source provider、conversation、message id、checksum、mime、source path ref
- fragment 只保存可检索摘要、转录、caption 或引用
- MemU integration point 只消费 artifact/fragment refs，不直接持有无 provenance 的原始数据

### 3.5 Mapping 需要独立于 source parser 和 import core 的持久对象

工作台里的 mapping 不是 parse 细节，而是产品对象。建议单独持久化：

- source conversation -> project/workspace/scope 绑定
- source sender / participant -> normalized actor hints
- partition / sensitivity defaults
- attachment handling policy

否则每次 dry-run 都得重新推断，无法形成 resume/continue 用户体验。

## 4. 方案对比

| 维度 | 方案 A：直接扩展 021 支持 WeChat 输入 | 方案 B：新增 source adapter + workbench projection，021 保持核心引擎 | 方案 C：重写一套 import workbench service |
|---|---|---|---|
| 对 021 复用程度 | 中 | 高 | 低 |
| 对多源扩展性 | 低 | 高 | 中 |
| 对 Control Plane 接入 | 中 | 高 | 中 |
| 对附件 / MemU 管线表达 | 弱 | 高 | 中 |
| 实施风险 | 中 | 中 | 高 |
| 推荐度 | 不推荐 | 推荐 | 不推荐 |

**推荐**：方案 B。

## 5. 推荐技术落点

### 5.1 Source adapter contract

建议新增统一 source adapter 协议，至少表达：

- `source_type`
- `detect(inputs) -> source-specific metadata`
- `preview(inputs, mapping) -> messages/attachments/counts/warnings/errors`
- `materialize(inputs, mapping) -> ImportedChatMessage stream + attachment refs`
- `resume_key / cursor_hint`

WeChat 作为首个 adapter 实现，未来 Slack/Telegram export 只需复用同一 contract。

### 5.2 Import Workbench canonical resources

建议至少新增：

1. `ImportWorkbenchDocument`
   - 当前 active project/workspace 下的 source candidates、最近 runs、resume entries、global warnings
2. `ImportSourceDocument`
   - 单个 source adapter 的 config requirements、mapping hints、detect result
3. `ImportReportDocument`
   - dry-run / run 的 counts、dedupe detail、warnings/errors、artifact refs、proposal stats、resume refs

### 5.3 Control-plane actions

建议至少新增：

- `import.source.detect`
- `import.preview`
- `import.mapping.save`
- `import.run`
- `import.resume`
- `import.report.inspect`
- `import.report.dismiss_warning` 或等价 ack 能力

### 5.4 持久化建议

建议在现有主 SQLite / provider DX store 中新增 029 所需 durable state：

- source adapter state
- mapping profile
- resume checkpoint projection
- attachment materialization ledger
- import report projection index

但**不复制 021 已有的 batch/cursor/dedupe/report 表**；新层只保存 workbench 与 source-specific state。

### 5.5 与 Memory / MemU 的技术边界

- 事实写入仍只通过 021 + 020 proposal/commit 路径
- 附件进入 artifact + fragment + optional MemU sync
- MemU unavailable 时必须降级到 artifact/fragment-only，不影响导入主流程

## 6. 与相邻 Feature 的边界

### 与 021

- 021 继续是 generic import core
- 029 只在其之上增加 source adapter、workbench projection 和 attachment pipeline

### 与 025

- 029 必须消费 project/workspace 与 secret/wizard 主路径
- 但不在 029 重做 project selector 或 secret 生命周期

### 与 026

- 029 必须复用 control plane shell、resource/action/event 基线
- 但不重做控制台框架

### 与 027

- 029 的报告与导入结果应能跳转/引用到 proposal/memory/vault 视图
- 但不改写 027 的 Memory/Vault 语义

### 与 028

- 029 可以把附件与 fragment 送到 MemU integration point
- 但不能在 029 定义新的 MemU 治理模型

### 与 031

- 029 不负责最终 M3 E2E 验收矩阵
- 029 只需定义本 Feature 的 API/integration/e2e 覆盖

## 7. 技术风险

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---|---|---|---|
| 1 | 直接把 WeChat adapter 写死到 021 service | 中 | 高 | 抽出 source adapter contract，021 仅吃 normalized stream |
| 2 | 附件直接转文本丢失 provenance | 中 | 高 | artifact-first + checksum/source ref |
| 3 | workbench 只靠 action result，不可恢复 | 中 | 高 | 增加 import canonical resources 与持久化 projection |
| 4 | MemU 不可用导致导入整体失败 | 中 | 中 | attachment/memu sync 显式降级为 warning，不阻断主导入 |
| 5 | mapping 只保存在前端草稿 | 中 | 高 | mapping profile 后端持久化并 project-scoped |

## 8. 结论

029 的正确落点是：

1. 在 021 上新增 source adapter 层；
2. 在 026 control plane 上新增 import workbench canonical resources / actions；
3. 用 025 project/workspace 语义承接 source conversation mapping；
4. 用 027/028 的 memory/memu 边界承接附件、proposal 与导入结果；
5. 保持 031 的最终验收边界不被提前吞并。
