# Requirements Checklist: Feature 042 Profile-First Tool Universe + Agent Console Reset

- [x] 明确 042 的目标是把默认聊天主链改成 `profile-first tool universe`，而不是针对 weather/news 等单个 case 打补丁
- [x] 明确普通 chat 必须能显式或隐式绑定 `agent_profile_id`，不再只传 `message + task_id`
- [x] 明确 `ToolIndex` 在 042 中降级为 discovery / explainability 能力，而不是默认聊天的主闸门
- [x] 明确 delegation 核心工具需要作为稳定能力暴露，不能继续经常被工具裁剪隐藏
- [x] 明确 `selected_tools_json` 需要兼容保留，但语义升级为“本次挂载给模型的核心工具集”
- [x] 明确需要新增 tool resolution explainability，解释工具为何可用/不可用
- [x] 明确 Agent 页面要做 IA 重组，而不是只在现有页面上继续堆卡片和术语
- [x] 明确 Agent 页面主目标是让用户理解“当前默认 Agent / 当前工作 / 当前能力”，不是暴露所有内部实现名词
- [x] 明确 042 必须复用 041 的 `worker_profiles` canonical resource 和控制面动作，不新造平行 backend
- [x] 明确 legacy `selected_worker_type` / 旧 work 记录必须继续兼容可读
- [x] 明确本 Feature 需要 category-based acceptance matrix，为后续 plan/tasks/verify 提供统一验证边界
