# Spec Review: Feature 008 Orchestrator Skeleton

**特性分支**: `codex/feat-008-orchestrator-skeleton`
**审查日期**: 2026-03-02
**审查范围**: FR-001 ~ FR-012
**Rerun**: 2026-03-02（from `GATE_RESEARCH`）

## 结论

- 结论: **PASS**
- 说明: 所有 MUST 级需求均有对应实现与测试证据。
- 重跑差异: 无 FR 变更；仅补充在线调研证据链。

## FR 对齐检查

| FR | 状态 | 证据 |
|----|------|------|
| FR-001 | ✅ | 新增 `packages/core/models/orchestrator.py` |
| FR-002 | ✅ | `DispatchEnvelope` 含 contract/route/capability/hop 字段 |
| FR-003 | ✅ | `SingleWorkerRouter.route()` + `OrchestratorService.dispatch()` |
| FR-004 | ✅ | `TaskRunner` 改为调用 `OrchestratorService.dispatch()` |
| FR-005 | ✅ | `EventType` 新增 `ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED` |
| FR-006 | ✅ | `WorkerResult.retryable` + `WORKER_RETURNED` payload |
| FR-007 | ✅ | `OrchestratorPolicyGate` 高风险拦截 |
| FR-008 | ✅ | hop 保护（next_hop > max_hops 失败） |
| FR-009 | ✅ | 低风险主链路仍保留 `MODEL_CALL_*` / `ARTIFACT_CREATED` |
| FR-010 | ✅ | `apps/gateway/tests/test_orchestrator.py` |
| FR-011 | ✅ | `tests/integration/test_f008_orchestrator_flow.py` |
| FR-012 | ✅ | worker 缺失与异常路径均可解释失败 |

## 边界场景检查

- EC-1（hop 超限）: ✅ 覆盖（unit）
- EC-2（worker 缺失）: ✅ 覆盖（unit）
- EC-3（worker 异常）: ✅ 代码路径覆盖（adapter + orchestrator 防御兜底）
- EC-4（高风险 gate）: ✅ 覆盖（unit）
