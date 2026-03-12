# Implementation Plan: Feature 041 Dynamic Root Agent Profiles + Profile Studio

**Branch**: `codex/feat-041-dynamic-root-agent-profiles` | **Date**: 2026-03-12 | **Spec**: `.specify/features/041-dynamic-root-agent-profiles/spec.md`
**Input**: `.specify/features/041-dynamic-root-agent-profiles/spec.md` + research/* + Feature 026 / 030 / 035 / 036 / 039 基线 + Agent Zero / OpenClaw 本地参考

## Summary

把固定 `WorkerType` 驱动的 worker 管理，完整演进成 `singleton Root Agent + Profile Studio` 产品模式：在 canonical control plane 中提供正式 `WorkerProfile / WorkerProfileRevision / effective lineage` 主链，把 Root Agent 的静态配置、动态运行上下文、review/apply、publish/archive、spawn/extract 全部挂到现有 control-plane resources/actions 上，并接到 `AgentCenter` / `ControlPlane`。第一阶段仍不拆独立多实例 runtime，但要把“正式 profile 资产 + 单例运行上下文 + runtime lineage” 一次打通。

## Technical Context

**Language/Version**: Python 3.12+, TypeScript / React 18  
**Primary Dependencies**: FastAPI, Pydantic, SQLite WAL, React, Vite  
**Storage**: SQLite（control-plane / task / work / agent context）  
**Testing**: pytest, frontend vitest  
**Target Platform**: Web control plane + localhost runtime  
**Project Type**: Web application（backend + frontend monorepo）  
**Performance Goals**: snapshot/resource projection 保持当前控制面量级，无额外长链查询  
**Constraints**: 不重做 026 backend contract；不绕过 ToolBroker/policy；不破坏 035/036/039 已有主链  
**Scale/Scope**: 第一阶段单例 Root Agent；支持内建 archetype 与后续正式 profile resource 扩展

## Constitution Check

- **Durability First**: 第一阶段新增 projection/contract 可先从既有 capability pack + delegation 派生，不强求先落完整新表；但任何新增 profile lineage 字段不得只存在前端内存。
- **Everything is an Event**: 第一阶段只增加资源投影与展示，不允许前端私有状态冒充正式 Root Agent 对象。
- **Tools are Contracts**: `worker_profiles` 只汇聚 capability pack / delegation / policy 的正式事实，不允许 profile 文本自由创造工具。
- **User-in-Control**: 第一期 UI 必须同时展示静态配置和动态上下文，让用户理解当前 Root Agent 在做什么。
- **Observability is a Feature**: `worker_profiles` 必须能回答“它是谁”“它现在在干什么”“它用了哪些工具”。

## Project Structure

### Documentation (this feature)

```text
.specify/features/041-dynamic-root-agent-profiles/
├── spec.md
├── plan.md
├── data-model.md
├── contracts/
│   └── worker-profiles.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   └── research-synthesis.md
└── tasks.md
```

### Source Code (repository root)

```text
octoagent/
├── apps/gateway/
│   ├── src/octoagent/gateway/routes/control_plane.py
│   ├── src/octoagent/gateway/services/control_plane.py
│   └── tests/test_control_plane_api.py
├── packages/core/
│   └── src/octoagent/core/models/
│       ├── control_plane.py
│       └── __init__.py
└── frontend/
    └── src/
        ├── pages/AgentCenter.tsx
        ├── pages/ControlPlane.tsx
        ├── types/index.ts
        └── index.css
```

**Structure Decision**: 不引入新 app / package。第一阶段只扩 canonical model、gateway projection 和现有前端页面，把 041 收敛成“正式资源 + 现有页面演进”的最小接缝方案。

## Design Decisions

### 1. 第一阶段按 Singleton 模式落地

- 一个 Root Agent profile 对应一个单例运行槽位
- UI 把 profile 当主对象展示
- 动态状态作为 `dynamic_context` 挂在 profile 旁边

### 2. 正式对象优先，Editor 必须接 canonical actions

- `WorkerProfile` 不能继续停留在 projection 层，必须有正式 store、revision 与 action
- `AgentCenter` / `Profile Studio` 只能通过 canonical actions 改状态，不能靠前端本地草稿伪造发布结果
- `worker_profiles` resource 负责展示当前事实；`worker_profile.*` actions 负责创建、review、publish、archive、spawn

### 3. 兼容优先，不硬删 legacy 字段

- 保留 `capability_pack.pack.worker_profiles`
- 保留 `delegation.works[].selected_worker_type`
- 新 UI 优先读 `worker_profiles`，旧逻辑继续兼容

### 4. UI 走 Data-Dense Agent Console，而不是大卡片营销风

基于 `ui-ux-pro-max` 的结果，采用：

- `Data-Dense Dashboard` 作为整体风格
- 强调静态配置 / 动态上下文并排
- 交互上保持 `Container/Presentational split`
- 不引入花哨 hero 动效，不破坏现有工作台语言

## Implementation Phases

### Phase 1: Singleton Root Agent Contract

- 扩展 core control-plane models，新增 `WorkerProfilesDocument`
- 定义 `static_config` / `dynamic_context` 结构
- 补 `contracts/worker-profiles.md`

### Phase 2: Backend Projection

- `ControlPlaneService.get_worker_profiles_document()`
- snapshot 与 resource route 接入
- 由 capability pack + delegation 派生单例 Root Agent 视图

### Phase 3: Frontend Agent Console

- 扩展 TS types / snapshot shape
- 在 `AgentCenter` 新增 Root Agent 区块
- 展示静态配置与动态上下文
- 在 `ControlPlane` 增加对应 lens

### Phase 4: Backend Profile Registry + Revision

- 新增 `WorkerProfile` / `WorkerProfileRevision` domain model 与 SQLite 存储
- control plane 新增 profile revision resource
- 新增 `worker_profile.create/update/clone/archive/review/apply/publish` actions

### Phase 5: Runtime Lineage + Spawn / Extract

- `Work` / delegation / runtime truth 增加 profile lineage 字段
- 新增 `worker.spawn_from_profile`
- 新增 `worker.extract_profile_from_runtime`

### Phase 6: Frontend Profile Library + Profile Studio

- AgentCenter 新增 `Profile Library` 与 `Profile Studio`
- 基于 canonical actions 完成 create / review / publish / archive / spawn / extract
- ControlPlane 新增 work runtime lineage lens 与 revision inspector 入口

### Phase 7: Verification

- backend API snapshot/resource/action regression
- frontend UI smoke / vitest
- 手动检查 AgentCenter / ControlPlane 主要路径

## Risks

- 若直接把新 resource 强行替换旧 `capability_pack.worker_profiles`，会打断现有 Worker 管理 UI；第一阶段必须双轨兼容。
- 若 `dynamic_context` 字段设计过少，后续 UI 仍会退回读取 delegation 原始 work 列表；因此本轮要一次性把关键运行上下文字段补齐。
- 若前端继续用大卡片 + 大留白表达 Root Agent，会掩盖控制信息；因此本轮 UI 必须走更高信息密度的控制面表达。
- 若 profile revision 与 work lineage 没有真正落盘，041 会退化成“可看不可审计”；因此本轮必须补齐 durability 侧接缝。
