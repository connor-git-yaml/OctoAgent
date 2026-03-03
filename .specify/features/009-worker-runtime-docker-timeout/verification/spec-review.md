# Spec Review: Feature 009 Worker Runtime + Docker + Timeout/Profile

**特性分支**: `codex/feat-009-worker-runtime`
**审查日期**: 2026-03-03
**审查范围**: FR-001 ~ FR-012

## 结论

- 结论: **PASS**
- 说明: MUST 需求均有实现与测试证据；未发现阻塞性缺口。

## FR 对齐检查

| FR | 状态 | 证据 |
|----|------|------|
| FR-001 | ✅ | `WorkerSession` 模型（core/orchestrator.py） |
| FR-002 | ✅ | `WorkerRuntime.run()` 循环与预算检查（worker_runtime.py） |
| FR-003 | ✅ | backend 模式 `disabled/preferred/required` |
| FR-004 | ✅ | required + docker unavailable 返回 `WorkerBackendUnavailableError` |
| FR-005 | ✅ | privileged gate（`privileged_approved`） |
| FR-006 | ✅ | `WorkerRuntimeConfig` 三层超时字段 |
| FR-007 | ✅ | max_exec timeout -> FAILED + timeout 分类 |
| FR-008 | ✅ | cancel 信号透传（route -> task_runner -> runtime） |
| FR-009 | ✅ | `WorkerResult` 与 `WorkerReturnedPayload` runtime 元数据扩展 |
| FR-010 | ✅ | F008 集成测试继续通过 |
| FR-011 | ✅ | `apps/gateway/tests/test_worker_runtime.py` |
| FR-012 | ✅ | `tests/integration/test_f009_worker_runtime_flow.py` |

## 边界场景检查

- EC-1（max_steps 耗尽）: ✅ runtime 已实现预算耗尽分支。
- EC-2（preferred 降级）: ✅ docker 不可用自动回退 inline。
- EC-3（cancel 竞争）: ✅ cancel 接口对已 CANCELLED 场景返回 200（幂等）。
- EC-4（授权标记错误）: ✅ 统一视为未授权并拒绝。
