---
feature_id: "072"
title: "Document Current Codebase Architecture"
milestone: "M4"
status: "Completed"
created: "2026-03-21"
updated: "2026-03-21"
predecessor: "docs/blueprint.md / README.md / octoagent/README.md / Feature 071"
blueprint_ref: "docs/blueprint.md §6 §8 §9 §12.9"
research_mode: "codebase-scan"
---

# Feature Specification: Document Current Codebase Architecture

**Feature Branch**: `072-document-codebase-architecture`  
**Created**: 2026-03-21  
**Status**: Completed  
**Input**: 用户要求“使用 codebase scan 的模式通过 feature 完成：更新最新的 origin master，并详细总结 OctoAgent 当前的架构和技术文档；先总结整个项目大模块分类，再按模块写二级文档，并把每个模块里最核心类和核心函数的实现逻辑讲清楚”

## Problem Statement

当前仓库已经积累了大量设计与实现资产，但维护者在阅读时会遇到三个结构性问题：

1. **蓝图架构与当前落地架构容易混淆**  
   `docs/blueprint.md` 仍然描述了目标态的分层，例如 `apps/kernel`、`workers/*`、`packages/plugins`、`packages/observability`，但当前主线实现实际收敛在 `apps/gateway`、若干 `packages/*` 和 `frontend/`。如果没有一份“当前实现扫描文档”，新维护者会把目标结构误认成现状。

2. **现有技术文档缺少“地图”和“信任层级”**  
   仓库里既有 `docs/blueprint.md`、`docs/agent-runtime-refactor-plan.md`、`docs/llm-provider-config-architecture.md`，又有多个 milestone split 文档和 `.specify/features/*` 制品，但没有一份文档明确说明：哪些是产品蓝图、哪些是当前实现、哪些是历史规划、哪些是特定专题的深挖。

3. **模块文档缺少“实现级解释”**  
   现有 README 和 blueprint 更擅长讲产品叙事、路线图和高层设计，但对于维护者真正要读的内容，例如：
   - `StoreGroup` 和事务辅助函数如何保证 Task / Event / Checkpoint 原子一致
   - `TaskService` 如何把消息、记忆召回、上下文压缩、LLM 调用串成一条 durable 主链
   - `ControlPlaneService` 为什么是当前控制面的聚合器，以及 `get_snapshot()` / `execute_action()` / `setup.*` 的职责边界
   - `AliasRegistry`、`LiteLLMClient`、`SkillRunner`、`PolicyEngine`、`MemoryService`、`useWorkbenchData()` 这些关键类是如何连接起来的  
   仓库中还没有一套“按模块拆分、按类和函数解释”的系统文档。

本 feature 的目标不是改动运行时行为，而是基于最新 `origin/master` 做一次完整 codebase scan，把“当前真实代码结构”和“现有技术文档体系”系统化沉淀出来，形成一套可长期维护的架构导览。

## User Scenarios & Testing

### User Story 1 - 维护者能先看懂当前代码的大模块分类 (Priority: P1)

维护者进入仓库后，应该先看到一份“当前实现总览”，明确当前 OctoAgent 的真实模块边界、主入口、模块之间的数据/控制流，以及哪些结构仍处于 blueprint 目标态而尚未拆分成独立模块。

**Why this priority**: 没有总览索引，后续所有模块文档都会失去坐标系，阅读者仍然容易把目标结构和现状混为一谈。

**Independent Test**: 仅阅读总览文档，就能回答“当前主入口在哪里、主要模块有哪些、哪些 blueprint 模块还未物理拆分”。

**Acceptance Scenarios**:

1. **Given** 维护者第一次打开新文档，**When** 阅读总览索引，**Then** 能看到当前实现模块表、主要入口和 blueprint 目标态与当前实现的区分
2. **Given** 维护者只关心当前真实代码骨架，**When** 阅读总览文档，**Then** 不会把 `apps/kernel`、`workers/*` 误认成已独立存在的当前目录

---

### User Story 2 - 维护者能知道仓库里的技术文档各自讲什么 (Priority: P1)

维护者需要一份“文档地图”，明确 `README.md`、`octoagent/README.md`、`docs/blueprint.md`、各 milestone split、专题文档和 `.specify/features/*` 在整个仓库中的定位与权威层级。

**Why this priority**: 当前文档很多，但没有统一导览。没有文档地图，维护者很容易在规划文档、专题文档和当前实现文档之间来回迷路。

**Independent Test**: 仅读文档地图，就能回答“要看产品目标、当前产品使用说明、当前代码架构、LLM Provider 专题、里程碑拆解”分别该去哪里。

**Acceptance Scenarios**:

1. **Given** 维护者想找当前产品蓝图，**When** 阅读文档地图，**Then** 能明确知道 `docs/blueprint.md` 是目标架构和设计依据
2. **Given** 维护者想找当前实现级的代码架构说明，**When** 阅读文档地图，**Then** 能明确跳转到新的 codebase architecture 文档集，而不是只看到 milestone split

---

### User Story 3 - 维护者能按模块理解核心类和核心函数 (Priority: P2)

维护者需要分模块阅读文档，并在每个模块文档中看到“核心类是什么、核心函数负责什么、函数内部的关键流程如何串起来”，而不是只有文件树或概念名词。

**Why this priority**: 这决定了文档能不能真正用于维护、review 和架构演进，而不只是做目录索引。

**Independent Test**: 单独阅读任意一个模块文档，就能描述该模块里最核心的几个类及其主流程。

**Acceptance Scenarios**:

1. **Given** 维护者阅读 Gateway 模块文档，**When** 关注 `TaskService`、`TaskRunner`、`OrchestratorService`、`ControlPlaneService`，**Then** 能理解它们在 durable task runtime 里的具体职责和调用关系
2. **Given** 维护者阅读 Provider / Tooling / Memory / Frontend 文档，**When** 查看核心类与函数说明，**Then** 能获得实现级解释，而不是只看到“这是配置层”“这是前端层”这类抽象描述

---

### User Story 4 - 新文档必须与当前代码和已有 canonical 文档一致 (Priority: P2)

这批架构文档必须以当前代码扫描结果为准，同时与 `docs/blueprint.md`、`README.md`、`octoagent/README.md`、`docs/llm-provider-config-architecture.md` 协调，不得制造第二套事实源。

**Why this priority**: 如果新文档和旧文档冲突，只会进一步增加维护成本。

**Independent Test**: 抽查各模块文档中的类/函数描述，对照当前代码与已有 canonical 文档，不能出现明显错误或虚构模块。

**Acceptance Scenarios**:

1. **Given** 文档提到某个已实现模块，**When** 维护者对照当前代码路径，**Then** 必须能找到对应类和函数
2. **Given** 文档提到 blueprint 中尚未拆分的模块，**When** 维护者阅读说明，**Then** 文档必须把它们标记为目标态或逻辑责任，而不是当前物理目录

## Edge Cases

- Blueprint 中存在但当前尚未物理拆分的模块必须明确标记为“目标态 / 逻辑角色”，不能混写成当前代码目录
- 超大聚合文件（如 `control_plane.py`）不能被文档简单概括成“控制面逻辑”，必须解释它当前为什么大、实际聚合了哪些子域
- 已有专题深挖文档（如 LLM Provider 架构文档）不应被重复复制；新文档应以索引和模块边界为主，并链接到专题文档
- 当前实现中仍保留少量历史兼容层或 M0/M1 时代类型（如 `LLMService` 内的 `EchoProvider`/`MockProvider`），文档应注明其定位，避免被误认成推荐扩展点
- 文档必须基于当前最新 `origin/master` 的代码扫描结果，而不是只复述旧 blueprint 里的目录建议

## Requirements

### Functional Requirements

- **FR-001**: 系统必须基于最新同步后的 `origin/master` 完成一次 codebase scan，并将结果沉淀到正式 feature 072 制品中
- **FR-002**: 必须新增一份“当前代码架构总览”文档，先给出整个项目的大模块分类
- **FR-003**: 总览文档必须明确区分“当前真实实现的模块结构”和“blueprint 目标态的分层结构”
- **FR-004**: 必须新增一份“当前技术文档地图”，说明现有 README、blueprint、专题文档、里程碑文档和 feature 制品的定位与使用场景
- **FR-005**: 必须按模块拆分二级文档，覆盖当前主要实现面：Gateway、Core、Provider、Tooling/Policy/Skills、Memory/Protocol、Frontend
- **FR-006**: 每个模块文档都必须解释该模块中最核心的类与核心函数的实现逻辑，而不只是列文件名
- **FR-007**: 模块文档必须描述关键主数据流或控制流，例如“消息如何进入 task runtime”“alias 如何从配置进入运行时”“审批如何介入工具调用”“workbench 如何拉取 snapshot 并驱动页面”
- **FR-008**: 新文档不得虚构当前不存在的物理模块；对于 blueprint 中尚未拆分的逻辑层，必须明确标注为目标态
- **FR-009**: 新文档必须与 `docs/blueprint.md`、`README.md`、`octoagent/README.md`、`docs/llm-provider-config-architecture.md` 保持一致，不制造新的 canonical 事实源
- **FR-010**: Feature 072 必须包含正式 `spec.md`、`research.md`、`plan.md`、`tasks.md`，说明本次 codebase-scan 文档化工作的范围与验收标准
- **FR-011**: 根 README 的 Documentation Map 必须能发现这批新文档，避免文档被淹没在 `docs/` 目录中

### Key Entities

- **Current Architecture Overview**: 基于当前代码扫描结果的总体架构索引，先说明大模块分类，再给出阅读路径
- **Documentation Map**: 现有仓库技术文档的用途、权威性和适用场景地图
- **Module Architecture Document**: 针对单个模块的实现级说明文档，包含职责、核心类、核心函数与主流程
- **Blueprint Target vs Current Implementation**: 对目标态逻辑分层与当前物理落地结构的区分说明

## Success Criteria

### Measurable Outcomes

- **SC-001**: `docs/codebase-architecture/README.md` 能明确列出当前主要实现模块与代表入口
- **SC-002**: `docs/codebase-architecture/current-doc-map.md` 能覆盖当前主要仓库文档，并说明其定位与权威层级
- **SC-003**: 至少 6 份模块级文档完成，覆盖当前主要实现模块面
- **SC-004**: 每份模块文档都包含“核心类 / 核心函数 / 实现逻辑”三个层次，而不只是目录树
- **SC-005**: 文档中引用的关键模块、类和函数都能在当前代码路径中实际找到
- **SC-006**: 根 README 的 Documentation Map 能直接跳转到新的 codebase architecture 文档集
