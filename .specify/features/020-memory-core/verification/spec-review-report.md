# Feature 020 Spec Review Report

**日期**: 2026-03-07  
**范围**: `.specify/features/020-memory-core/spec.md` 与已实现代码对照

## 总体结论

Feature 020 当前实现满足 spec 里定义的最小 Memory Core 目标：SoR current 唯一、WriteProposal 仲裁、Vault default deny、flush 钩子不直接写 SoR。

## FR 对照

| Requirement | 状态 | 证据 |
|---|---|---|
| FR-001 ~ FR-003 | PASS | `packages/memory/src/octoagent/memory/enums.py` + `packages/memory/src/octoagent/memory/models/` |
| FR-004 ~ FR-006 | PASS | `packages/memory/src/octoagent/memory/service.py` + `packages/memory/tests/test_models.py` + `packages/memory/tests/test_memory_service.py` |
| FR-007 ~ FR-009 | PASS | `packages/memory/src/octoagent/memory/store/sqlite_init.py` + `packages/memory/tests/test_sqlite_init.py` |
| FR-010 ~ FR-012 | PASS | `packages/memory/src/octoagent/memory/service.py` + `packages/memory/tests/test_memory_service.py` |
| FR-013 | PASS | `packages/memory/src/octoagent/memory/service.py::before_compaction_flush` |
| FR-014 | PASS | `memory_write_proposals.status/validation_errors/validated_at/committed_at` 持久化 |
| FR-015 | PASS | 未引入 Chat Import / 向量写路径 / Vault UI |

## 残余风险

- 当前 proposal 审计落在 memory 自身表中，尚未接入 `core` Event Store；这不阻塞 020 验收，但仍是后续 021/023 集成时要补的可观测增强。
- Vault 目前是 skeleton：保存安全摘要和 `content_ref` 占位，不包含最终授权检索链路。
