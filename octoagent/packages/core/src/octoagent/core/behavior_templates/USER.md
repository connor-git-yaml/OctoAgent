## 用户画像

此文件维护高频引用的用户偏好摘要，作为每次对话的快速参考。

**重要存储边界**: 稳定事实应通过 Memory 服务写入持久化存储，此文件仅保留需要每次对话快速参考的核心偏好摘要。不要把大量用户事实堆积在此文件中。

### 基本信息

- **称呼**: （待引导时填写——用户希望被称呼的名字或昵称）
- **时区/地点**: （待引导时填写——影响时间相关回复的准确性）
- **主要语言**: 中文
- **职业/领域**: （待了解后补充——帮助调整专业术语的使用深度）

### 沟通偏好

- **回复风格**: （简洁直接 / 详细解释 / 轻松随意 / 其他——待引导或对话中了解）
- **信息组织**: 优先回答——现在发生了什么、对用户有什么影响、下一步做什么。避免冗长的背景铺垫
- **确认偏好**: （用户倾向于你直接执行还是先确认再动手——待了解）

### 工作习惯

- **活跃时段**: （待了解后补充——帮助安排异步任务的通知时机）
- **常用工具/平台**: （待了解后补充——帮助选择合适的集成方式）
- **任务偏好**: （偏好一步到位还是渐进迭代——待了解后补充）

### 通知偏好

<!-- F101 Phase C FR-B4：机器可读字段，供 NotificationService quiet hours 解析使用 -->
<!-- 格式：active_hours: "HH:MM-HH:MM"（24 小时制，左闭右开 [start, end)，支持跨 midnight） -->
<!-- 示例：active_hours: "09:00-23:00" 表示每天 9 点到 23 点为活跃时段，23:00~09:00 为 quiet hours -->
<!-- 未填写或格式非法时，系统默认全时段推送所有通知（含低优先级） -->
<!-- CRITICAL 级别通知（如等待审批）始终推送，不受 quiet hours 影响 -->
- **active_hours**: （格式："HH:MM-HH:MM"，如 "09:00-23:00"——设置后 quiet hours 内只推送紧急通知）

### Daily Routine 偏好

<!-- F102 Proactive Followup FR-D1：机器可读字段，供 DailyRoutineService 解析 -->
<!-- daily_summary_time: "HH:MM"，每日 active hours 内自动推送昨日 Worker 摘要的时间，默认 08:30 -->
<!-- routine_active: "true" / "false"，是否启用 daily routine，默认 true -->
<!-- summary_channels: 逗号分隔 "telegram" / "web" / "telegram,web"，默认全渠道 -->
<!-- 注意：daily_summary_time 落在 quiet hours 内时通知会被 discard 不补发，建议落在 active hours 内 -->
- **daily_summary_time**: "08:30"
- **routine_active**: "true"
- **summary_channels**: "telegram,web"

---

*更新原则*: 当对话中获得新的用户偏好信息时，先判断信息稳定性——稳定事实（如姓名、时区）应优先写入 Memory 服务持久化；高频参考的简要偏好（如回复风格）可同步更新本文件。用户偏好应来自真实交互中的了解，而不是临时猜测。
