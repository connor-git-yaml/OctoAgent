# Requirements Checklist: Feature 028 — MemU Deep Integration

## Governance Integrity

- [x] 明确要求治理面继续由 `MemoryService` / `WriteProposal` / SoR / Vault 控制
- [x] 明确禁止 MemU 直接写 SoR / Vault
- [x] 明确要求高级结果只能落为 `Fragments`、派生索引或 `WriteProposal`
- [x] 明确要求所有高级结果必须带 artifact / fragment evidence chain

## MemU Engine Contract

- [x] 扩展后的 `MemoryBackend` / `MemUBridge` 能力边界已明确
- [x] 健康诊断、降级、回切状态机已列为必需能力
- [x] sync / ingest / derivation / maintenance 的批量与幂等约束已明确
- [x] project/workspace scoped bridge binding 已在 spec 中明确

## Multimodal / Derived Layers

- [x] 文本 / 图片 / 音频 / 文档 ingest 已纳入范围
- [x] Category / relation / entity / ToM 派生层已纳入范围
- [x] derived layer 非事实层的边界已明确
- [x] 派生结果触发 SoR 变更时必须通过 `WriteProposalDraft`

## Maintenance / Audit

- [x] consolidation / compaction / flush 的可审计执行链已纳入范围
- [x] `before_compaction_flush()` 只生成草案的约束已保留
- [x] maintenance run / diagnostic refs / replay 状态已列入 contract

## Integration Boundaries

- [x] 027 继续使用既有 canonical resources，并通过 028 contract 获取 backend integration 扩展，不得自造 canonical DTO
- [x] 026 只消费 diagnostics / action hooks，不新增 control-plane canonical memory resource
- [x] 025-B project/secret/wizard 作为 MemU bridge 上游绑定已明确
- [x] Memory Console 详细 UI 明确保留在范围外

## Testing

- [x] backend 协议测试已列为必选
- [x] fallback / failback / degraded 状态测试已列为必选
- [x] multimodal ingest / evidence chain 测试已列为必选
- [x] derived layer / maintenance / integration contract 测试已列为必选
