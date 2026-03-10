# Requirements Checklist: Feature 036 Guided Setup Governance

- [x] 明确 036 不是新造 setup backend，而是扩既有 control-plane / wizard / onboarding 主链
- [x] 明确 Provider / Channel / Agent Profile / 权限 / Tools / Skills 必须进入同一 setup-governance canonical flow
- [x] 明确 `front_door`、Telegram `dm_policy/group_policy`、allowlists 等安全字段必须进入主路径
- [x] 明确 Agent Profile、policy profile、tool_profile 不能再只靠静默默认值生效
- [x] 明确 Tools / Skills / MCP 必须以 readiness / missing requirements / install hint 方式暴露
- [x] 明确 Web 与 CLI 必须复用同一套 document / action / review / apply 语义
- [x] 明确 setup review 必须由后端生成风险摘要，前端只负责呈现
- [x] 明确 setup apply 仍必须保持 secrets refs-only，不得泄露明文
- [x] 明确 036 必须与 Feature 015 / 025 / 026 / 030 / 035 正式接线，不得平行实现
- [x] 明确需要 backend regression、CLI integration、frontend integration 和 e2e 的非伪实现测试矩阵
