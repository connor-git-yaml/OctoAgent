---
feature_id: "029"
title: "WeChat Import + Multi-source Import Workbench"
milestone: "M3"
status: "Implemented"
created: "2026-03-08"
research_mode: "full"
blueprint_ref: "docs/m3-feature-split.md Feature 029；docs/blueprint.md M3 产品化约束；Feature 021/025/026/027/028 已交付基线"
predecessor: "Feature 021（Chat Import Core，已交付） / Feature 025-B（Project/Workspace/Wizard，已交付） / Feature 026（Control Plane，已交付） / Feature 027（Memory Console，已交付） / Feature 028（MemU integration point，已交付）"
parallel_dependency: "Feature 031 只消费本 Feature 暴露的 import workbench、reports、resume、memory effect contract；029 不得提前吞并 M3 最终验收"
---

# Feature Specification: WeChat Import + Multi-source Import Workbench

**Feature Branch**: `codex/feat-029-wechat-import-workbench`  
**Created**: 2026-03-08  
**Status**: Implemented  
**Input**: 落实 M3 Feature 029：WeChat Import + Multi-source Import Workbench。范围包含 WeChat 导入插件与 source-specific adapter、多源导入工作台、dry-run/mapping/dedupe/cursor-resume、附件进入 artifact/fragment/MemU 管线、导入提案与 Memory proposal/commit 打通，以及导入报告/warnings/errors/resume 入口接入现有 control plane。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

Feature 021 已经交付了通用 Chat Import Core，Feature 025/026/027/028 也已经把 project/workspace、control plane、memory console 和 MemU integration point 铺好，但导入能力仍停留在“工程内核 + 简易触发”的状态：

1. 021 当前只支持 `normalized-jsonl` 输入，要求用户先自行把外部数据转成内部格式，普通用户无法直接使用。
2. 026 目前只有一个 `import.run` action 和简易 `input_path + source_format` 表单，没有 source-specific 检测、mapping、dedupe 结果和 resume 入口。
3. 027 已经把 Memory / Proposal / Vault 做成可视产品对象，但导入结果还不能自然解释“本次导入会写入哪些 artifact、fragment、proposal、SoR/Vault ref”。
4. 028 已经给 MemU 留出 integration point，但多源附件目前没有稳定进入 artifact/fragment/MemU 的统一管线。
5. M3 的目标是让普通用户 Ready；若 029 仍要求用户先理解内部 `ImportedChatMessage` 和 `normalized-jsonl`，就没有真正把导入产品化。

因此，029 的目标不是“给 021 再加一个 adapter 函数”，而是：

- 让用户可以直接用 WeChat 等来源的本地导出物进行导入；
- 让导入在执行前可预览、可修正、可恢复；
- 让附件、多媒体和事实提取进入统一 artifact / fragment / Memory / MemU 治理链；
- 让导入结果在现有 Control Plane 中可见、可继续、可审计。

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 021：Chat Import Core | 已交付 | 029 必须复用 batch/cursor/dedupe/report/proposal 基线，不得重做导入内核 |
| Feature 025-B：Project / Workspace / Wizard / Secret | 已交付 | 029 的 source mapping 与 resume 必须以 project/workspace 为主语义 |
| Feature 026：Control Plane | 已交付 | 029 必须接入现有 canonical resources / actions / events 与 Web 控制台 |
| Feature 027：Memory Console + Proposal/Vault 视图 | 已交付 | 029 的导入结果应能引用/跳转到既有 memory/proposal/vault 视图 |
| Feature 028：MemU integration point | 已交付 | 029 可把附件与 fragment 送入 MemU integration point，但不能重定义治理模型 |

前置约束：

- 029 **必须** 复用 021 的 `ImportBatch / ImportCursor / ImportReport / dedupe / proposal` 内核，不得新建第二套生产导入主流程。
- 029 **必须** 复用 025 的 project/workspace 与 selector 语义，不得绕开 project 直接做全局导入。
- 029 **必须** 复用 026 的 control plane 与 Web 控制台，不得新造平行导入后台。
- 029 **必须** 复用 020/027/028 的 Memory 治理边界：任何权威事实写入都必须经 `WriteProposal -> validate -> commit`，附件/MemU 只能作为 artifact/fragment/integration point。
- 029 **不得** 把 M3 最终集成验收、全量样本库和跨 Feature acceptance 规范偷带进来；那属于 Feature 031。

## Scope Boundaries

### In Scope

- WeChat source adapter
- 多源 import adapter contract
- Import Workbench（dry-run、mapping、dedupe、warnings/errors、cursor/resume）
- source conversation -> project/workspace/scope 的 mapping/preview
- 多源附件进入 artifact / fragment / MemU 管线
- 导入 proposal 与 Memory proposal/commit 打通
- Control Plane 中的导入报告、recent runs、resume 入口、warning/error 展示
- 单元测试、关键 API/integration 测试与必要 e2e

### Out of Scope

- 在线拉取 WeChat 服务器聊天历史
- 重写 021 Chat Import Core
- 绕过 WriteProposal/SoR/Vault 治理直接落权威事实
- 重做 Control Plane 框架或独立导入后台
- 031 的最终 M3 用户验收矩阵、整套 acceptance bundle 与跨 Feature 总体验收脚本

## User Scenarios & Testing

### User Story 1 - 我可以直接导入 WeChat 导出物，并在执行前看到完整 dry-run 预览 (Priority: P1)

作为 owner，我希望直接选择 WeChat 的本地导出目录、HTML、JSON 或等价离线导出物，系统能先做 source detection、mapping preview 和 dry-run，告诉我会导入哪些会话、多少消息、多少附件、多少重复和 warnings，这样我不用先学内部格式再碰运气执行。

**Why this priority**: 这是 029 把 021 从“工程内核”推进到“普通用户 Ready 导入路径”的核心价值；没有 source-specific preview，029 不成立。

**Independent Test**: 准备一份 WeChat 导出样本，进入 workbench 执行 detect + preview，不做真实导入；验证系统输出会话列表、scope 映射、消息/附件计数、重复数量、warnings/errors，且不产生 Memory/Artifact 副作用。

**Acceptance Scenarios**:

1. **Given** 用户提供可识别的 WeChat 导出物，**When** 执行 detect/preview，**Then** 系统能识别 source metadata、conversation 列表和附件目录，并生成 dry-run 预览结果。
2. **Given** 导出物缺少附件目录或消息字段不完整，**When** 执行 preview，**Then** 系统清楚展示 warnings/errors，而不是在真实导入时才失败。
3. **Given** 用户尚未确认 mapping，**When** 只执行 dry-run，**Then** 系统不得产生 artifact、fragment、proposal 或 event 副作用。

---

### User Story 2 - 我可以在工作台里修正 mapping、查看 dedupe，并从中断点继续导入 (Priority: P1)

作为 operator，我希望在导入工作台里看到 source conversation 到 project/workspace/scope 的映射、重复消息/附件结果、cursor 与 resume 入口；如果某次导入失败或中断，我可以修正映射或源路径后继续，而不是从头再来。

**Why this priority**: 多源导入天然是长流程；没有 mapping/dedupe/resume 的正式产品面，批量导入就不可用。

**Independent Test**: 准备一个含 2 个 conversation、部分重复消息、一个中途中断批次的样本；验证 workbench 可展示 mapping、dedupe 结果和 recent runs，并在 resume 后只处理未完成部分。

**Acceptance Scenarios**:

1. **Given** source 包含多个 conversation，**When** 用户编辑 mapping，**Then** 系统能预览每个 conversation 会落到哪个 project/workspace/scope。
2. **Given** 同一 source 之前已经导入过一部分消息，**When** 用户执行 preview 或 resume，**Then** 系统能展示 dedupe 结果和 cursor 位置，而不是重复导入全部消息。
3. **Given** 某次导入因附件缺失或 parse warning 终止，**When** 用户修复输入后点击 resume，**Then** 系统从上次 checkpoint/cursor 继续，并保留原始 warnings/errors 审计。

---

### User Story 3 - 我导入的附件和事实提案会进入统一的 artifact / fragment / Memory / MemU 治理链 (Priority: P1)

作为知识系统维护者，我希望多源附件进入 artifact store，并生成带 provenance 的 fragment/ref；如果导入内容能形成稳定事实，它必须继续通过 Memory proposal/commit 进入 SoR/Vault；如果 MemU 可用，附件和 fragment 还应进入其 integration point，但绝不能绕过治理边界。

**Why this priority**: 029 的价值不只是“把消息搬进来”，而是把导入数据纳入系统已有的长期治理模型。

**Independent Test**: 准备带图片/语音/文档附件和 `fact_hints` 的导入样本，执行真实导入；验证附件被 artifact 化、fragment/proposal 记录保留 provenance、Memory commit 仍经 validate/commit，而 MemU unavailable 时导入会优雅降级。

**Acceptance Scenarios**:

1. **Given** 导入源含图片/语音/文件附件，**When** 执行真实导入，**Then** 系统将附件 materialize 为 artifact，并把摘要/引用送入 fragment 或等价检索层。
2. **Given** 导入消息附带稳定事实候选，**When** 导入流程处理该窗口，**Then** 系统必须通过 `WriteProposal -> validate -> commit` 写入 SoR/Vault，而不是直接写 current。
3. **Given** MemU integration point 当前 unavailable，**When** 导入包含附件或需要索引的 fragment，**Then** 系统以 warning 形式降级为 artifact/fragment-only，而不会让整批导入失败。

---

### User Story 4 - 我可以在 Control Plane 中查看导入报告、错误、warnings 和 resume 入口 (Priority: P2)

作为 operator，我希望在现有 Control Plane 中看到最近的导入批次、dry-run 报告、warnings/errors、resume 入口和相关 Memory effect，这样我不需要回到 CLI 或日志里排障，也不会把导入视为一次性黑箱动作。

**Why this priority**: 029 的“工作台”语义要求导入是正式控制面对象，而不是 fire-and-forget action。

**Independent Test**: 执行一次 preview、一次真实导入和一次失败后 resume，打开 Control Plane；验证可以查看 recent runs、报告详情、errors/warnings、resume action 与关联 resource refs。

**Acceptance Scenarios**:

1. **Given** 最近执行过 preview 和 run，**When** 打开导入工作台，**Then** 系统显示 recent runs、状态、warnings/errors 摘要与下一步动作。
2. **Given** 某次导入需要 resume，**When** 打开 workbench，**Then** 用户能看到 resume 入口和恢复前提，而不需要重新猜 source_id/cursor。
3. **Given** 导入已触发 proposal / memory 变化，**When** 查看报告详情，**Then** 系统能给出 artifact refs、proposal stats 和跳转到 Memory/Proposal 视图的入口。

## Edge Cases

- WeChat 导出物只包含部分媒体目录或只包含 HTML/JSON，不含完整 SQLite 时，系统如何明确降级支持范围？
- 同一 source conversation 被映射到错误 project/workspace 后，系统如何在 execute 前阻止污染错误 scope？
- 导入批次包含大量重复附件但消息文本不同，dedupe 应如何区分“重复消息”和“重复媒体”？
- 某些附件无法 materialize 或 MIME 不可识别时，系统如何保证主消息仍可导入并保留 provenance？
- MemU 不可用、Memory proposal validate rejected、或部分窗口 commit 失败时，报告如何表达 partial success 而不是简单失败？
- source adapter 解析出会话列表，但用户没有提供足够 mapping 信息时，系统如何阻止直接执行真实导入？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 复用 Feature 021 的 Chat Import Core（`ImportBatch / ImportCursor / ImportReport / dedupe / windowing / proposal`），而 MUST NOT 新建平行生产导入主流程。
- **FR-002**: 系统 MUST 定义统一的 multi-source import adapter contract，并至少交付一个 WeChat source adapter。
- **FR-003**: WeChat source adapter MUST 以用户提供的本地导出物为默认输入路径（如导出目录、HTML、JSON、SQLite snapshot 或等价离线产物），而 MUST NOT 依赖在线拉取聊天历史作为主路径。
- **FR-004**: source adapter MUST 能把 source-specific 数据规范化为现有 `ImportedChatMessage`、attachment descriptors、mapping hints、cursor hints 和 warnings/errors。
- **FR-005**: 系统 MUST 提供 Import Workbench canonical resource，用于表达 source detect、mapping、dry-run、recent runs、resume entries、warnings/errors 与 capabilities。
- **FR-006**: workbench MUST 支持 source-specific detect/preview，并展示 conversation/account metadata、附件目录状态、消息/附件计数与 parse warnings。
- **FR-007**: 系统 MUST 提供 mapping 能力，把 source conversation / sender / metadata 映射到 `project_id / workspace_id / scope_id / partition`；未完成 mapping 时，真实导入 MUST fail-closed。
- **FR-008**: mapping 状态 MUST 是 project-scoped durable state，而 MUST NOT 只保存在前端草稿或一次性 action params 中。
- **FR-009**: dry-run 结果 MUST 至少展示新增消息数、重复消息数、窗口数、附件数、proposal 预估、warnings、errors 与目标 scope/mapping 预览。
- **FR-010**: 系统 MUST 支持 dedupe detail 与 cursor/resume 视图，明确哪些内容因历史去重被跳过、哪些内容可继续导入，以及最近一次成功位点。
- **FR-011**: 系统 MUST 支持 resume 动作，并在导入中断后仅从 checkpoint/cursor 之后继续，而不得重复污染已导入的消息、附件或 Memory proposal。
- **FR-012**: workbench MUST 明确区分 `preview`、`ready_to_run`、`running`、`failed`、`action_required`、`resume_available` 等导入状态，而不是只返回成功/失败。
- **FR-013**: 多源附件 MUST 先进入 artifact store，并保留 source provider、conversation、message id、source path/ref、mime、checksum 或等价 provenance 元数据。
- **FR-014**: 附件导入后 MUST 继续进入 fragment / searchable ref 路径；若 MemU integration point 可用，系统 SHOULD 将附件/fragment 送入其索引同步路径。
- **FR-015**: 当 MemU integration point unavailable 时，系统 MUST 优雅降级为 artifact/fragment-only，并把降级原因写入导入报告与 warnings。
- **FR-016**: 由导入产生的稳定事实候选 MUST 继续通过 `WriteProposal -> validate -> commit` 写入 SoR/Vault；029 MUST NOT 直接旁路写 current SoR 或 Vault。
- **FR-017**: 当 evidence 不足、validate rejected 或 attachment materialization 失败时，系统 MUST 支持 fragment-only / artifact-only / partial-success 路径，并在报告中精确表达。
- **FR-018**: Control Plane MUST 提供 recent import runs、report inspect、warnings/errors 展示、resume 入口与相关 resource refs，而 MUST NOT 把导入视为一次性 `import.run` 动作结果。
- **FR-019**: 029 新增的导入资源与动作 MUST 复用现有 `/api/control/*`、action registry、action result 与 control-plane events 语义。
- **FR-020**: 导入报告 MUST 能引用 artifact refs、proposal stats、Memory/Proposal/Vault 相关 resource refs，并允许用户在 027 视图中继续审计导入结果。
- **FR-021**: CLI MUST 保持可用，至少保留对 detect/preview/run/resume 主路径的等价入口；但 Web Control Plane 应成为普通用户的主工作台。
- **FR-022**: 029 MUST 明确排除 Feature 031 的全量 M3 acceptance 范围，只交付本 Feature 的导入能力、工作台资源与验证矩阵。
- **FR-023**: 029 MUST 补齐 unit、adapter integration、control-plane API/integration、frontend integration 与必要 e2e 测试，覆盖 WeChat adapter、mapping、dry-run、dedupe、resume、attachment pipeline 和 Memory proposal 效果。

### Key Entities

- **ImportSourceAdapter**: source-specific 适配器协议，负责 detect、preview、materialize 和 cursor hints。
- **WeChatImportSource**: WeChat 导入源对象，表达导出物路径、账号/会话元数据、媒体根目录与 adapter state。
- **ImportWorkbenchDocument**: Control Plane 中的导入工作台总览文档，聚合 source、recent runs、resume entries、warnings 与 capabilities。
- **ImportMappingProfile**: source conversation / sender / metadata 到 `project/workspace/scope/partition` 的 durable mapping 配置。
- **ImportRunDocument**: 单次 preview/run/resume 的状态、counts、dedupe、warnings/errors、artifact/proposal/resource refs。
- **ImportAttachmentEnvelope**: 附件 materialization 的统一表达，包含 source provenance、mime、checksum、artifact ref 与 indexing state。
- **ImportResumeEntry**: 可恢复的导入入口，表达 source、last cursor、failed phase、blocking reason 与 next action。
- **ImportMemoryEffectSummary**: 导入对 fragment/proposal/commit/vault/memu 的影响摘要。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可以直接用 WeChat 本地导出物进入 detect + preview，不需要先手工生成 `normalized-jsonl`。
- **SC-002**: workbench 能稳定展示 mapping、dedupe、warnings/errors 和 resume 入口，而不是只提供一次性导入按钮。
- **SC-003**: 真实导入后，附件进入 artifact 路径并保留 provenance，且导入结果可追溯到对应 chat scope、artifact refs 与 memory effect。
- **SC-004**: 任何由导入产生的权威事实写入都仍经过 `WriteProposal -> validate -> commit`，不存在 029 直接写 current SoR/Vault 的旁路。
- **SC-005**: 当 MemU 不可用或部分附件失败时，导入仍能以 warning/partial-success 方式完成，并提供可恢复信息。
- **SC-006**: Control Plane 中可以查看 recent import runs、report details、warnings/errors 和 resume 入口；关键路径测试通过。
