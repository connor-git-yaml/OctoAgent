# Requirements Checklist: Feature 027 — Memory Console + Vault Authorized Retrieval

## Governance Boundary

- [x] 明确要求复用 020 的 `WriteProposal -> validate -> commit` 治理内核
- [x] 明确禁止旁路写 SoR / Vault 权威事实
- [x] 明确 Vault 默认 deny 不被削弱
- [x] 明确 028 只能消费 integration points，不能反向重定义 027 语义

## Control Plane Integration

- [x] 明确要求复用 026 现有 control plane 与 Web 控制台
- [x] 明确要求通过 canonical resources / actions / events 暴露 Memory/Vault 能力
- [x] 明确禁止新造平行 console framework 或前端直读底层表
- [x] 明确 Memory 视图需要进入现有 control-plane 导航

## Product Scope

- [x] Memory 浏览器（按 `project/workspace/partition/scope/layer`）已纳入范围
- [x] `subject_key` current / superseded 历史与 evidence refs 已纳入范围
- [x] Vault 授权申请 / 记录 / 检索结果与证据链已纳入范围
- [x] WriteProposal 审计视图已纳入范围
- [x] export inspect / restore verify 校验入口已纳入范围
- [x] MemU 深度引擎、多模态 ingest、consolidation pipeline 已明确排除

## Security / Privacy

- [x] canonical resources 不得暴露未经授权的 Vault 原文
- [x] memory 权限模型已覆盖查看、申请、批准、检索与校验入口
- [x] 所有 Vault 授权与检索动作必须进入审计链
- [x] project/workspace 过滤与 orphan scope 风险已在 spec 中显式表达

## Testing

- [x] 单元测试范围已覆盖 memory projection / permission / authorization primitives
- [x] API / integration 测试已覆盖 Vault 授权与 proposal 审计
- [x] Web integration / control-plane 测试已列为必选
- [x] 关键 e2e 测试已列为必选
