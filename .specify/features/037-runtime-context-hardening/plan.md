# Implementation Plan: Runtime Control Context Hardening

**Branch**: `037-runtime-context-hardening` | **Date**: 2026-03-10 | **Spec**: `.specify/features/037-runtime-context-hardening/spec.md`
**Input**: `.specify/features/037-runtime-context-hardening/spec.md` + `research/*.md` + Feature 030/033/034 代码基线

## Summary

本 Feature 以最小侵入方式收敛运行态 contract：新增 `RuntimeControlContext`，把 delegation/runtime/task/context resolver 串成单一 lineage；同时让 `AgentContextService` 消费 `ContextResolveRequest` 并优先使用冻结 snapshot，修复 delayed execution 的 scope 漂移风险。

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic, aiosqlite, pytest  
**Storage**: SQLite WAL + artifact store  
**Testing**: pytest + ruff  
**Target Platform**: Gateway backend / local single-user runtime  
**Project Type**: Python monorepo  
**Constraints**: 不破坏 030/033/034 现有运行链；保持 legacy metadata 兼容  

## Constitution Check

- Durability First: 使用既有 `Work` / `SessionContextState` / `ContextFrame` 持久层承载 lineage，满足
- Everything is an Event: 不改事件主链语义，满足
- Degrade Gracefully: runtime snapshot 缺失时回退到 legacy 路径，满足

## Project Structure

### Documentation

```text
.specify/features/037-runtime-context-hardening/
├── spec.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   └── research-synthesis.md
├── contracts/
│   └── runtime-context-contract.md
├── plan.md
├── tasks.md
└── verification/
    └── verification-report.md
```

### Source Code

```text
octoagent/packages/core/src/octoagent/core/models/
octoagent/apps/gateway/src/octoagent/gateway/services/
octoagent/apps/gateway/tests/
octoagent/tests/integration/
```

**Structure Decision**: 不新增 app/package；本次实现聚焦 core model contract、gateway runtime services 和回归测试。
