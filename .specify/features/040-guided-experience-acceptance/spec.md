---
feature_id: "040"
title: "M4 Guided Experience Integration Acceptance"
milestone: "M4"
status: "In Progress"
created: "2026-03-10"
updated: "2026-03-10"
research_mode: "full"
blueprint_ref: "docs/blueprint.md M4/M5；docs/m4-feature-split.md；Feature 035 / 036 / 039；OpenClaw onboarding/dashboard/approvals；Agent Zero architecture/memory/UI integration"
predecessor: "Feature 035（Guided Workbench）、Feature 036（Guided Setup Governance）、Feature 037（Runtime Context Hardening）、Feature 039（Supervisor Worker Governance）"
parallel_dependency: "Feature 033 仍是 degraded gate；040 只能显式暴露其缺口，不能假装已经闭环。"
---

# Feature Specification: M4 Guided Experience Integration Acceptance

**Feature Branch**: `codex/040-guided-experience-acceptance`  
**Created**: 2026-03-10  
**Updated**: 2026-03-10  
**Status**: In Progress  
**Input**: 在确认 M4 仍需要一个“串联全部功能”的 feature 之后，启动 Feature 040，把 035（workbench）、036（setup governance）和 039（supervisor/worker governance）组织成真正可走通的用户旅程，并形成整体验收入口。  

## Problem Statement

当前仓库已经完成或部分完成了三块关键能力：

1. **035 已经有 workbench shell 和主页面骨架**  
   但 `Home / Settings / Work` 还没有把 036/039 的新资源和动作真正接进去，页面仍然偏“有壳但缺主线”。

2. **036 已经有 canonical setup resources/review**  
   但 frontend 还没有消费 `setup-governance / policy-profiles / skill-governance`，用户在 Home/Settings 看不到真正的 setup readiness，也无法在图形化路径中先 review 再 apply。

3. **039 已经有 worker review/apply 能力**  
   但 Work 页面还不能展示 plan，也不能让用户在工作台里 approve worker plan 并继续派工，导致 supervisor 能力仍停留在 control-plane action 层。

因此，040 不是新能力 feature，而是：

> 把 035/036/039 的正式接口收成同一条 workbench 用户旅程，并用测试证明这条路径是真实可用的。

## Product Goal

交付一条最小但真实的 M4 用户旅程：

- `Home` 直接展示 setup readiness 与当前阻塞项
- `Settings` 在保存前先执行 `setup.review`，把风险、blocking reasons、next actions 说清楚
- `Work` 支持 `worker.review -> worker.apply`，让用户在图形化界面里批准 supervisor 方案
- 上述流程全部复用 canonical control-plane resources/actions
- 形成一组 frontend/backend acceptance tests，证明这不是“页面写了、主链没接”

## Scope Alignment

### In Scope

- frontend 类型与 resource 映射补齐：
  - `setup-governance`
  - `policy-profiles`
  - `skill-governance`
  - `context-continuity`
- `Home` 增加 setup readiness 卡片与阻塞信息
- `SettingsCenter` 增加 `setup.review` 面板，并将“保存”改为 `setup.review -> setup.apply`
- `WorkbenchBoard` 增加 `worker.review / worker.apply` 的 plan 展示与批准流程
- backend `setup.apply`
- `ChatWorkbench` 显式消费 `context_continuity`，在 033 未完成时显示 degraded state
- frontend integration tests
- backend e2e / acceptance regression
- spec / plan / tasks / verification / milestone 文档回写

### Out of Scope

- 完成 033 的 context continuity 领域逻辑
- 实现 036 的 `skills.selection.save`
- CLI `octo init / octo onboard` 与 Web 设置向同一 setup 状态机完全汇流
- `memory -> operator -> export/recovery` 的整条 release-gate 验收
- 新增新的 backend 资源、私有 REST 或新的产品对象
- 重做聊天、memory、advanced 全量页面

## Functional Requirements

- **FR-001**: Workbench MUST 能消费 `setup-governance / policy-profiles / skill-governance`，不能继续只识别旧 snapshot 资源。
- **FR-002**: `Home` MUST 显示 setup readiness、blocking reasons 与 next actions。
- **FR-003**: `SettingsCenter` MUST 在保存配置前先执行 `setup.review`，并把 review summary 呈现给用户。
- **FR-004**: 当 `setup.review` 返回 blocking reasons 时，`SettingsCenter` MUST 阻止直接保存，并解释原因。
- **FR-005**: `WorkbenchBoard` MUST 支持 `worker.review`，并显示 assignments / warnings / tool_profile。
- **FR-006**: `WorkbenchBoard` MUST 支持 `worker.apply`，让用户在图形化工作台里批准 supervisor 派工方案。
- **FR-007**: 040 MUST 以 acceptance tests 证明 `setup -> workbench -> worker plan` 是一条真实链路。
- **FR-008**: 033 未完成时，040 MUST 用显式 degraded 表达当前上下文连续性缺口，而不是静默省略。
- **FR-009**: backend MUST 提供 `setup.apply`，并复用 036/026 已有 canonical actions/resources，而不是新增私有设置写入接口。

## Success Criteria

- **SC-001**: 普通用户进入 `Home` 后能直接看到 setup 是否 ready、卡在哪里、下一步做什么。
- **SC-002**: 用户在 `SettingsCenter` 修改配置时，会先看到 setup risk/blocking summary，再决定是否继续。
- **SC-003**: 用户在 `Work` 页面可以直接 review/apply worker plan，而不必回退到原始 control-plane 资源页。
- **SC-004**: frontend/backend acceptance tests 能覆盖上述主链，并通过。
