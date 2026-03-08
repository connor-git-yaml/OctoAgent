# Requirements Checklist: Feature 029 — WeChat Import + Multi-source Import Workbench

## Upstream Reuse

- [x] 明确要求复用 Feature 021 的 Chat Import Core
- [x] 明确要求复用 Feature 025 的 project/workspace 主路径
- [x] 明确要求复用 Feature 026 的 control plane 与 Web 控制台
- [x] 明确要求复用 Feature 027/028 的 Memory/MemU 治理边界
- [x] 明确禁止重做导入内核或新造平行控制台

## Product Scope

- [x] WeChat source adapter 已纳入范围
- [x] multi-source adapter contract 已纳入范围
- [x] dry-run / mapping / dedupe / cursor / resume 已纳入范围
- [x] warnings / errors / recent runs / resume entry 已纳入范围
- [x] 多源附件进入 artifact / fragment / MemU 管线已纳入范围
- [x] 导入结果与 Memory proposal/commit 打通已纳入范围

## Control Plane Integration

- [x] 明确要求通过 canonical resources / actions / events 交付 import workbench
- [x] 明确要求 `import.run` 不再是唯一导入控制面入口
- [x] 明确要求在现有 Control Plane 中展示报告、warnings/errors、resume
- [x] 明确禁止前端只靠一次性 action result 驱动工作台

## Safety / Governance

- [x] 明确要求真实导入前必须先完成 detect/preview/mapping
- [x] 明确要求未完成 mapping 时真实导入 fail-closed
- [x] 明确要求权威事实写入继续走 `WriteProposal -> validate -> commit`
- [x] 明确要求附件 materialization 保留 provenance
- [x] 明确要求 MemU 不可用时优雅降级

## Boundary Control

- [x] 明确排除在线 WeChat 历史抓取主路径
- [x] 明确排除 Feature 031 的最终 M3 acceptance 范围
- [x] 明确排除绕过 Memory/Vault 治理的快捷写入能力

## Testing

- [x] 单元测试已覆盖 source adapter / mapping / dedupe / attachment contract
- [x] integration 测试已覆盖 preview/run/resume 与 Memory effect
- [x] control-plane API / frontend integration 已列为必选
- [x] 必要 e2e 已列为必选
