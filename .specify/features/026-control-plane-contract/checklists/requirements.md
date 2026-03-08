# Requirements Checklist: Feature 026 — Control Plane Delivery

## Contract Integrity

- [x] 明确要求消费 026-A frozen contract，而不是重新定义 canonical resource / action semantics
- [x] 六类 mandatory resources 均被保留为 canonical document
- [x] `ActionRegistryDocument`、`ActionRequestEnvelope`、`ActionResultEnvelope`、`ControlPlaneEvent` 均在 spec 中继续作为强制对象
- [x] `degraded/unavailable` 语义在所有资源中被明确要求

## Backend / Frontend Boundary

- [x] backend canonical producer 与 frontend consumer 边界已明确
- [x] frontend 不得直接拼旧 route DTO 的限制已明确
- [x] Telegram 仅作为 surface alias consumer 的限制已明确

## Product Scope

- [x] Web 正式 control plane shell 在范围内
- [x] Session Center、Automation、Diagnostics、Config、Channels、Operator 面在范围内
- [x] unified entry for approvals/retry/cancel/backup/restore/import/update 在范围内
- [x] Memory/Vault detailed view 明确留在范围外，只保留统一入口
- [x] Secret Store 实值存储与 Wizard 详细实现明确留在范围外

## Testing

- [x] backend API / projection 测试已列为必选
- [x] Telegram / Web shared action semantics 测试已列为必选
- [x] frontend integration 测试已列为必选
- [x] e2e 测试已列为必选

## 025 Compatibility

- [x] default project / workspace fallback 已在 spec 中明确
- [x] 025-B Secret Store / Wizard 预留已在 spec 中明确
- [x] secret values 不入 YAML / control-plane docs / frontend cache 的约束已明确
