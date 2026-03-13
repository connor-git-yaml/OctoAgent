# Requirements Checklist: Feature 043 Module Connection Trust-Boundary Hardening

**Purpose**: 确认 043 的需求边界、实现范围与验收点已经冻结  
**Created**: 2026-03-13  
**Feature**: `.specify/features/043-module-connection-trust-boundary-hardening/spec.md`

## Trust Boundary

- [x] CHK001 已明确区分 `input metadata` 与 `trusted control metadata`
- [x] CHK002 已定义 control metadata 的白名单与 key scope（turn/task）
- [x] CHK003 已定义 control metadata 的显式清除语义
- [x] CHK004 已明确 runtime prompt 只允许使用 sanitizer 后的 control summary

## Execution Semantics

- [x] CHK005 已明确 `/api/chat/send` 的 success/failure 语义
- [x] CHK006 已覆盖 create failure 与 enqueue failure 两类 fail-fast 场景
- [x] CHK007 已明确旧的 `accepted` fail-open 行为必须移除

## Typed Contract

- [x] CHK008 已明确 `DispatchEnvelope / OrchestratorRequest / A2ATaskPayload` metadata 改为 typed canonical contract
- [x] CHK009 已明确 `selected_tools_json / runtime_context_json` 只作为兼容字段保留
- [x] CHK010 已明确 child lineage / retry lineage 仍需保留

## Partial Degrade

- [x] CHK011 已明确 `/api/control/snapshot` 支持 section-level degrade
- [x] CHK012 已明确 fallback document 仍需保留原 resource_type
- [x] CHK013 已明确 snapshot 顶层暴露 `status / degraded_sections / resource_errors`

## Verification

- [x] CHK014 已定义 metadata trust split regression
- [x] CHK015 已定义 chat fail-fast regression
- [x] CHK016 已定义 typed metadata continuity regression
- [x] CHK017 已定义 snapshot partial degrade regression
