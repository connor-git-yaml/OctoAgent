# Feature 020 Requirements Checklist

- [x] **FR-001-MODELS** 已定义 `FragmentRecord` / `SorRecord` / `WriteProposal` / `VaultRecord`
- [x] **FR-002-DIMENSIONS** 已区分 `layer/type` 与 `partition/scope`
- [x] **FR-003-ACTION** `WriteAction` 支持 `ADD | UPDATE | DELETE | NONE`
- [x] **FR-004-EVIDENCE** 非 `NONE` proposal 缺证据会被拒绝
- [x] **FR-005-VALIDATE** `validate_proposal()` 覆盖 action/证据/版本校验
- [x] **FR-006-COMMIT-GATE** 未验证 proposal 不能 commit
- [x] **FR-007-CURRENT-UNIQUE** SoR current 唯一约束有测试覆盖
- [x] **FR-008-SUPERSEDE** `UPDATE` 自动将旧版转为 `superseded`
- [x] **FR-009-FRAGMENTS** Fragments append-only 语义有测试覆盖
- [x] **FR-010-SEARCH** 默认 `search_memory()` 不返回 Vault
- [x] **FR-011-GET-VAULT** 未授权 `get_memory()` 拒绝 Vault
- [x] **FR-012-GET** `get_memory()` 支持读取单条 SoR/Fragment/Vault
- [x] **FR-013-FLUSH** `before_compaction_flush()` 不直接改 SoR
- [x] **FR-014-AUDIT** proposal 审计状态可查询
- [x] **FR-015-SCOPE** 020 未实现 Chat Import / 向量写路径 / Vault UI
