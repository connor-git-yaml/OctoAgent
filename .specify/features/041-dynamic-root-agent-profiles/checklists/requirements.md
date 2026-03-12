# Requirements Checklist: Feature 041 Dynamic Root Agent Profiles

- [x] 明确 041 的目标是 `WorkerProfile` 产品化，而不是继续扩充固定 `WorkerType` 枚举
- [x] 明确 Butler 保持主 Agent / supervisor 角色，动态的是 Root Agent profile，而不是 Butler 本身
- [x] 明确 041 必须复用 026 的 canonical control-plane resources/actions，不新造平行 backend
- [x] 明确 041 必须直接接入 039 的 worker review/apply 治理链，而不是绕开审批和审查
- [x] 明确 `WorkerType` 在 041 中退化为 starter template / base archetype，并保留兼容期
- [x] 明确 `Work` / runtime truth 必须记录 `profile_id / revision / effective snapshot`，不能只剩 `selected_worker_type`
- [x] 明确前端需要拆分 `starter templates / Profile Library / Runtime Workers` 三层对象
- [x] 明确 `Profile Studio` 必须覆盖身份、能力边界、review/publish 三个阶段，而不是单一大表单
- [x] 明确 profile 自定义仍受 ToolBroker / capability pack / MCP / policy 治理，不能自由创造不存在的工具
- [x] 明确 legacy built-in worker 与旧 work 记录需要有兼容迁移路径，避免破坏原有系统接缝
