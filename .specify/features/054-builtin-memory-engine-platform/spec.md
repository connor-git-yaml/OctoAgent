---
feature_id: "054"
title: "Builtin Memory Engine & Shared Retrieval Platform"
milestone: "M4"
status: "Planned"
created: "2026-03-15"
updated: "2026-03-15"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §8.7 Memory；docs/blueprint.md §8.7.4 语义检索集成；FR-MEM-4 文档知识库；Feature 028（MemU deep integration）；Feature 038（agent memory recall optimization）；Feature 045（Memory panel clarity）"
predecessor: "Feature 020、027、028、038、045"
parallel_dependency: "未来知识库/文档导入 feature 必须复用本 Feature 定义的 retrieval platform、EmbeddingProfile 与 IndexGeneration contract。"
---

# Feature Specification: Builtin Memory Engine & Shared Retrieval Platform

**Feature Branch**: `codex/054-builtin-memory-engine-platform`  
**Created**: 2026-03-15  
**Updated**: 2026-03-15  
**Status**: Planned  
**Input**: 把当前 `local_only / memu(command) / memu(http)` 的用户心智收口为“内建 Memory Engine + 平台级 retrieval models + 可视化 embedding 迁移”。MemU 风格能力继续承担记忆加工、扩写、embedding、rerank、召回与事实/Vault 候选生成，但 SoR / Vault / proposal / audit / durability 的最终治理权仍留在 OctoAgent。未来知识库与文档导入必须复用同一套 embedding/index 平台。

## Problem Statement

当前 Memory 架构在底层已经具备 `MemUBackend + SQLite governance + LanceDB projection` 的雏形，但产品心智和配置面仍然不合理：

1. **`local_only` 仍像弱 fallback，不像真正可用的内建记忆引擎**  
   当前用户看到的是“基础模式 vs MemU bridge”，而不是“系统内建可工作的记忆能力”。这会把普通用户推回 deployment / bridge / transport 语义，偏离产品主路径。

2. **用户被迫理解 `command/http/bridge` 等实现细节**  
   Feature 045 和当前 Settings 仍把 `local_only / memu(command) / memu(http)` 当正式配置语义。这对普通用户没有价值，也会让后续 Memory 与知识库重复建设两套 retrieval 配置面。

3. **加工智能与治理边界还没有在产品层说清楚**  
   当前方向里，MemU 既承担 recall 与 indexing，又可能参与 facts / Vault 加工。但系统没有明确把“智能加工”和“最终治理”分开，后续很容易退回 case-by-case 硬策略或让外部引擎越权落盘。

4. **embedding 切换缺少正式的无缝迁移产品面**  
   用户未来一定会从“默认内建 embedding”升级到“绑定更强 embedding alias”，也可能遇到 embedding 模型失效。现在系统还没有 `active generation / target generation / backfill / catch-up / cutover / cancel / rollback` 的正式 contract 与 UI。

5. **Memory 与未来知识库没有统一 retrieval platform**  
   blueprint 已经明确 LanceDB 既服务 Memory 也服务知识库，但目前还没有共享的 `EmbeddingProfile / IndexGeneration / IndexBuildJob` 抽象。若继续按 Memory 特例推进，后面知识库一定会重复造轮子。

因此，054 的目标不是“给当前 local_only 再补几条说明”，而是：

> 把 Memory 升级成真正的内建记忆引擎，把向量检索抽象成平台级可重建投影，并用同一套 retrieval platform 同时服务 Memory 与未来知识库。

## Product Goal

交付一条新的长期主链：

- 用户默认看到的是**内建 Memory Engine**，而不是 `memu bridge` 部署拓扑
- 只要平台已配置 `main` LLM，Memory 就能完成最基础的加工与回忆
- 系统内置一个低门槛双语 embedding 作为默认层，做到开箱可用
- 用户可在 `Settings > Memory` 绑定 `reasoning / expand / embedding / rerank` model aliases，以升级质量
- facts / Vault 的加工候选可以走 MemU 风格引擎，但最终 commit / approval / audit 仍由 OctoAgent 控制
- 向量索引只是 **derived projection**，Memory 记录本体永远保留在 canonical store
- embedding 升级必须是**可视化、可取消、不中断服务**的后台迁移
- 未来知识库与文档导入直接复用同一 retrieval platform，而不是再造一套 embedding/index 流程

## Design Direction

### 054-A 架构原则

1. **Canonical Store Only Once**
   - `fragment / fact / vault / proposal / audit / evidence` 只保留一份 canonical 数据
   - canonical store 继续由 OctoAgent 本地治理层控制
   - 任何 embedding/index 变化都不迁移这些记录本体

2. **Vector Index is Projection, Not Source of Truth**
   - LanceDB 继续承担 Memory 与未来知识库的向量 projection
   - projection 可以重建、并行多代、延迟清理
   - projection 失效只会影响语义召回质量，不应影响事实真源与治理链

3. **MemU Handles Intelligence, OctoAgent Handles Governance**
   - MemU 风格引擎负责：扩写、改写、embedding、rerank、candidate 召回、facts/Vault 候选生成
   - OctoAgent 负责：proposal、SoR commit、Vault grant / approval / audit、durability、replay、恢复
   - 系统不得退回为 facts / Vault 加工写一堆硬策略；硬规则只保留 gate 与 fallback

4. **Builtin First, Custom Upgrade Second**
   - 默认主路径先让 Memory 可用
   - 自定义 embedding / rerank / expand / reasoning 是质量升级，而不是功能前提
   - 普通用户主路径不再暴露 `bridge_transport / bridge_url / bridge_command`

5. **Shared Retrieval Platform**
   - Memory 与知识库必须复用同一套 `EmbeddingProfile / IndexGeneration / IndexBuildJob / CorpusKind`
   - 差异只体现在 chunking、metadata 与 retrieval policy，不再体现在 embedding/index 生命周期

### 054-B 模型绑定与 fallback

本 Feature 新增平台级 retrieval model bindings：

- `memory_reasoning`
- `memory_expand`
- `memory_embedding`
- `memory_rerank`
- 未来可扩到 `knowledge_embedding / knowledge_rerank`，但第一阶段优先复用平台默认

默认行为：

1. `memory_reasoning` 未配置时，回退到 `main`
2. `memory_expand` 未配置时，回退到 `main`
3. `memory_embedding` 未配置时，优先回退到**内建 `Qwen3-Embedding-0.6B` 默认层**
4. 若内建 embedding 当前不可用，再降级到 lexical / metadata recall
5. `memory_rerank` 未配置时，回退到 heuristic rerank

这意味着：

- Memory 不再依赖“先有外部 embedding provider 才能工作”
- 外部 alias 是升级层，不是启动门槛
- 降级时系统应诚实表达“当前在基础召回/基础 rerank”，而不是假装仍在向量检索

### 054-C Index Generation 生命周期

embedding 变更不再被视为“改一个字段立即生效”，而是一条正式后台作业：

1. 创建新的 `IndexGeneration`
2. 旧 `active generation` 继续服务查询
3. 后台执行：
   - `queued`
   - `scanning`
   - `embedding`
   - `writing_projection`
   - `catching_up`
   - `validating`
   - `ready_to_cutover`
4. 校验通过后执行原子 `cutover`
5. 切换成功后，旧 generation 进入保留窗口
6. 保留窗口内允许 `rollback`
7. `cancel` 仅允许发生在 cutover 前；取消后继续使用旧 generation

核心要求：

- 迁移期间，线上查询仍使用旧 embedding profile 与旧 projection
- 用户能看到进度、剩余量、阶段、最近错误
- 用户不能删除正在服务的 active profile
- 用户可以取消“新 generation 构建”，但不能把系统切成半迁移状态

### 054-D UI / CLI 主路径

#### Settings > Memory

普通用户主路径只回答：

- 当前是基础记忆还是增强记忆
- 现在正在用哪些 retrieval models
- 如需提升质量，下一步去绑定哪些 aliases
- 如果 embedding 正在迁移，进度到哪一步了

普通用户主路径不再展示：

- `memu + command`
- `memu + http`
- `bridge_url`
- `bridge_command`
- `bridge_api_key_env`

这些 transport 细节仅允许保留在 Advanced/operator 兼容入口，供迁移期诊断，不再作为产品主路径。

#### Work / Background Jobs

index build / catch-up / cutover 必须进入统一后台作业视图，方便用户和 operator 观察：

- 哪个 corpus 在迁移
- 当前 generation 与目标 generation
- 百分比、ETA、最近错误
- `cancel` / `rollback` 行为边界

#### Future Knowledge Base

未来知识库页面不得再定义第二套 embedding/indexing UI。它必须直接复用：

- EmbeddingProfile picker
- progress card
- generation status
- cutover / rollback 语义

### 054-E 治理边界

本 Feature 明确以下边界不可下放给 MemU：

- SoR 最终 commit
- Vault 进入受保护层的判定
- grant / approval / audit
- replay / recovery / event trace
- write proposal 的审计链

但以下内容应优先交给 MemU 风格引擎，而不是手写硬策略：

- facts 候选抽取
- Vault 候选整理
- fragment 整理
- recall candidate 召回
- expanded query 生成
- rerank
- derived memory 候选

### 054-F 与现有 blueprint / Feature 的关系

本 Feature 明确替代当前“用户配置 Memory = 选择 transport + 补 bridge 字段”的主心智。

需要在后续实施中回写：

- `docs/blueprint.md`
- `Feature 045` 中关于 `local_only / memu(command/http)` 的用户入口表述
- `README` 和 CLI help 中关于 Memory 配置的说明

兼容策略：

- 迁移期内部仍可保留 `command/http` transport 以支持已有实例
- 但这些 transport 只属于实现兼容层，不再属于普通用户的正式产品面

## Scope Alignment

### In Scope

- 内建 Memory Engine 的产品语义和配置模型
- 平台级 retrieval model binding
- 内建 `Qwen3-Embedding-0.6B` 默认层与 hash fallback 的协同设计
- canonical store / vector projection 分层 contract
- `EmbeddingProfile / IndexGeneration / IndexBuildJob / CorpusKind`
- embedding 重建、切换、取消、回滚的状态机
- Memory 与未来知识库共用 retrieval platform 的产品/技术 contract
- Settings / Work / Memory 页面上的迁移可视化与状态表达
- transport 配置从普通用户主路径移除的迁移策略

### Out of Scope

- 本轮直接交付完整知识库产品页面
- 本轮重写 facts / Vault 数据模型
- 本轮更换 LanceDB 为其他向量数据库
- 本轮直接删除所有兼容 transport 代码
- 把 governance 交给 MemU 或其他外部引擎

## User Scenarios & Testing

### User Story 1 - 用户只配好主模型，也能先把 Memory 用起来 (Priority: P1)

作为普通用户，我希望系统默认就能开始记住和回忆内容，而不是先理解 `bridge`、`transport`、`embedding provider`。

**Independent Test**: 在只配置 `main` model alias 的新实例中进入 Chat 和 Memory，验证系统能生成基础记忆、基础 recall，并清楚说明当前仍是默认 retrieval 质量层。

**Acceptance Scenarios**:

1. **Given** 用户已完成最低配置并有 `main` alias，**When** 进入 Chat 并产生对话，**Then** Memory 能完成基础加工与回忆，不要求先配 embedding provider。
2. **Given** 用户尚未绑定自定义 retrieval aliases，**When** 打开 `Settings > Memory`，**Then** 页面说明当前使用默认内建 retrieval 层，并告诉用户升级质量的下一步。

---

### User Story 2 - 用户可以升级 Memory 质量，但不必理解部署拓扑 (Priority: P1)

作为用户，我希望在 `Settings > Memory` 里直接绑定 `embedding / rerank / expand / reasoning` 的模型，而不是理解 MemU command/http 的差别。

**Independent Test**: 打开 `Settings > Memory`，验证用户可以先配置 Providers 与 model aliases，再在 Memory 页面绑定 retrieval models，而不需要填写 bridge 字段。

**Acceptance Scenarios**:

1. **Given** 用户已配置多个 Providers 和 model aliases，**When** 进入 `Settings > Memory`，**Then** 页面允许分别绑定 `memory_reasoning / memory_expand / memory_embedding / memory_rerank`。
2. **Given** 用户没配 `memory_rerank`，**When** 保存 Memory 配置，**Then** 系统允许保存并说明会回退到 heuristic rerank。

---

### User Story 3 - embedding 变更是后台迁移，不会中断现网检索 (Priority: P1)

作为用户，我希望切换 embedding 模型时，现有检索继续工作，并且我能看到迁移进度。

**Independent Test**: 将 active embedding 从 profile A 切到 profile B，验证旧 generation 在迁移期间继续服务查询；任务完成后再切到新 generation；取消迁移后旧 generation 保持不变。

**Acceptance Scenarios**:

1. **Given** 旧 embedding generation 正在服务查询，**When** 用户选择新的 embedding alias，**Then** 系统创建新的 build job，并保持旧 generation 为 active。
2. **Given** 新 generation 仍在 `scanning / embedding / catching_up`，**When** 用户继续使用 Chat 或 Memory 搜索，**Then** 查询仍基于旧 generation 返回结果。
3. **Given** 用户在 cutover 前点击 `cancel`，**When** 任务停止，**Then** 系统继续使用旧 generation，并把新 generation 标记为 cancelled。
4. **Given** 新 generation 已 cutover 成功，**When** 后续验证发现严重问题，**Then** operator 可以对保留窗口内的旧 generation 执行 rollback。

---

### User Story 4 - facts 与 Vault 的加工可以更智能，但治理权仍在本地 (Priority: P2)

作为系统设计者，我希望 facts / Vault 的候选整理可以走 MemU 风格引擎，但不能绕过本地 proposal / approval / audit。

**Independent Test**: 触发 facts / Vault 加工后，验证外部/内建引擎只能产生候选与 evidence；最终写入仍需经过现有治理链。

**Acceptance Scenarios**:

1. **Given** 引擎生成了一批 fact candidates，**When** 系统准备写回，**Then** 写入仍经过 proposal / commit，而不是直接落盘到 current facts。
2. **Given** 引擎生成了一批 Vault candidates，**When** 权限不足或审批未通过，**Then** 内容不得进入受保护层。

---

### User Story 5 - 未来知识库直接复用这套 retrieval platform (Priority: P2)

作为产品设计者，我希望未来文档导入和知识库检索直接复用 Memory 的 embedding/index 基础设施，而不是分裂成两套系统。

**Independent Test**: 在同一平台上同时存在 `memory` 与 `knowledge_base` corpus 时，验证两者共享 `EmbeddingProfile` 与 `IndexGeneration` 管理，但允许有不同的 chunking / metadata policy。

**Acceptance Scenarios**:

1. **Given** 平台已有 active embedding profile，**When** 新建知识库 corpus，**Then** 它可以直接复用该 profile 或选择新的 generation，而无需新造一套设置页。
2. **Given** 用户切换平台默认 embedding，**When** Memory 和知识库都依赖该 profile，**Then** 系统分别创建各自的 generation/build job，并在各自 cutover 后生效。

## Edge Cases

- 用户在迁移期间删除 active embedding alias 或停用其 provider，系统必须拒绝或先强制改为明确降级态
- 新 generation 完成全量 backfill 期间，新的聊天记忆或新导入文档仍会持续写入 canonical store；系统必须通过 watermark + catch-up 追平增量
- 内建 embedding 不可用时，系统不能伪装成仍然有语义检索，必须诚实降级为 lexical / metadata recall
- 不同 corpus 使用不同 chunker/version 时，generation 切换必须带 `chunker_version` 与 `pipeline_version`
- facts / Vault 候选加工失败不应阻塞普通 fragment ingest 主链

## Functional Requirements

- **FR-001**: 产品主路径 MUST 将 Memory 表达为内建 Memory Engine，而不是要求普通用户理解 `memu bridge`、`command`、`http` 等部署术语。
- **FR-002**: `Settings > Memory` MUST 提供平台级 retrieval model binding，至少覆盖 `memory_reasoning / memory_expand / memory_embedding / memory_rerank`。
- **FR-003**: 系统 MUST 提供一个默认内建 `Qwen3-Embedding-0.6B` 层，并在本机 runtime 不可用时自动回退到 hash embedding，使用户在未绑定自定义 embedding alias 时仍可获得基础语义召回。
- **FR-004**: `memory_reasoning` 与 `memory_expand` 未绑定时 MUST 回退到 `main` alias；`memory_rerank` 未绑定时 MUST 回退到 heuristic；`memory_embedding` 未绑定时 MUST 回退到内建 embedding 或 lexical fallback。
- **FR-005**: canonical memory records MUST 继续由 OctoAgent 本地治理层保存；embedding/index 变化 MUST NOT 迁移 `fragment / fact / vault / proposal / audit / evidence` 本体。
- **FR-006**: LanceDB 或后续向量后端 MUST 被建模为 derived projection，并支持 generation/version 并存。
- **FR-007**: 系统 MUST 引入 `EmbeddingProfile / IndexGeneration / IndexBuildJob / CorpusKind` 等正式实体，并覆盖 `memory | knowledge_base` 至少两类 corpus。
- **FR-008**: embedding 切换 MUST 采用后台迁移模型；在新 generation cutover 前，旧 generation MUST 保持 active 并继续服务查询。
- **FR-009**: 系统 MUST 提供迁移进度可视化，包括阶段、已处理数量、总数量、百分比、ETA 和最近错误。
- **FR-010**: `cancel` MUST 只取消未 cutover 的新 generation；取消后系统 MUST 继续使用旧 generation。
- **FR-011**: 系统 SHOULD 提供 cutover 后的 rollback 保留窗口；rollback 后 MUST 可恢复旧 generation 为 active。
- **FR-012**: MemU 风格引擎 MAY 参与 facts / Vault 候选加工，但最终写入 MUST 仍受 proposal / approval / audit / grant 治理。
- **FR-013**: 系统 MUST 避免为 facts / Vault 加工追加新的硬编码业务策略；硬规则只保留 fallback、gate 与 durability 边界。
- **FR-014**: transport 相关字段在迁移完成后 MUST 从普通用户主路径移除；如需保留，必须仅出现在 Advanced/operator 兼容入口。
- **FR-015**: Memory 与未来知识库 MUST 复用同一 retrieval platform contract，不得各自定义独立的 embedding/index 生命周期。
- **FR-016**: Work / Background Jobs SHOULD 暴露 index build、catch-up、cutover、rollback 等后台任务，供用户与 operator 观察。
- **FR-017**: 文档、CLI、Web 表述 MUST 对齐新心智，并显式标记旧 `local_only / memu(command/http)` 语义进入迁移/兼容状态。

## Key Entities

- **RetrievalModelBinding**: 平台级 retrieval 绑定，描述 `reasoning / expand / embedding / rerank` 当前使用的 alias、fallback 与来源。
- **EmbeddingProfile**: 一组可用于向量投影的 embedding 配置，包含 alias、model id、dim、pipeline version、chunker version 与 backend capability。
- **IndexGeneration**: 某个 corpus 在某个 `EmbeddingProfile` 下生成的一代 projection，包含状态、watermark、统计信息与切换信息。
- **IndexBuildJob**: 驱动 `IndexGeneration` 从 `queued` 到 `completed/cancelled/failed` 的后台作业。
- **CorpusKind**: projection 目标类型，第一阶段至少支持 `memory` 与 `knowledge_base`。
- **MemoryCandidateBatch**: MemU 风格引擎返回的一批 fragment / fact / vault / derived candidates，带 evidence refs，但不自动越过治理边界。
- **CutoverWindow**: 新旧 generation 共存时的切换窗口，支持 `cancel`、`cutover` 与 `rollback` 规则。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 普通用户主路径的 `Settings > Memory` 不再要求填写 `bridge_url / bridge_command / bridge_transport`。
- **SC-002**: 只配置 `main` alias 的新实例也能完成基础 Memory 加工与基础 recall。
- **SC-003**: 用户切换 embedding 时，迁移进度可视化可见，且迁移期间查询不中断。
- **SC-004**: 用户在 cutover 前取消迁移后，active generation 保持旧版本且结果稳定。
- **SC-005**: facts / Vault 加工可走 MemU 风格引擎，但没有任何路径绕过现有 governance chain。
- **SC-006**: 后续知识库 feature 无需重新设计 embedding/index 生命周期，只需复用本 Feature 产出的 retrieval platform。
