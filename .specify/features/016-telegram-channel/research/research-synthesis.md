# 产研汇总：Feature 016 Telegram Channel + Pairing + Session Routing

**特性分支**: `codex/feat-016-telegram-channel`
**汇总日期**: 2026-03-07
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md)
**执行者**: 主编排器

## 1. 产品 × 技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合判断 | 建议 |
|---|---|---|---|---|---|
| Telegram 入站消息进入 Task 链路 | P1 | 高 | 中 | 高 | 纳入 MVP |
| DM pairing / allowlist | P1 | 中高 | 中 | 高 | 纳入 MVP |
| 群聊 / topic / reply routing | P1 | 高 | 中 | 高 | 纳入 MVP |
| Telegram 出站文本 / 审批提示 / 错误提示 | P1 | 中 | 中 | 中高 | 纳入 MVP |
| webhook / polling 运行与诊断 | P1 | 高 | 中 | 高 | 纳入 MVP |
| 统一 operator inbox | P2 | 中 | 高 | 低 | 延后到 017 |

## 2. 可行性评估

### 技术可行性

- 仓库已经有成熟的 `NormalizedMessage -> TaskService -> Event/SSE` 闭环
- 015 已经交付 `channel verifier` contract，016 只需补真实 Telegram verifier
- 官方 Bot API 与 aiogram 都支持 webhook / polling、reply/thread 参数和外部 Web framework 集成

### 资源评估

- **预估工作量**: 中等偏大，涉及 config、gateway runtime、onboarding/doctor、tests 四条线
- **关键技能需求**: FastAPI 生命周期、aiogram、SQLite durability、任务幂等设计
- **外部依赖**: Telegram Bot API、bot token、Webhook HTTPS 环境（生产）

### 约束与限制

- 016 不能吞并 017 的 operator inbox
- 016 必须从 `master` 基线推进，并保持 WebChannel 无回归
- 016 必须遵守 fail-closed 默认值，不能为了“先跑起来”而放松授权边界

## 3. 风险评估

| # | 风险 | 来源 | 概率 | 影响 | 缓解策略 | 状态 |
|---|---|---|---|---|---|---|
| 1 | webhook / polling 切换重复投递 | 技术 | 高 | 高 | 冻结 Telegram 幂等键规则，并补重复 update 测试 | 待监控 |
| 2 | pairing / allowlist 状态不 durable | 技术 | 中 | 高 | 使用持久化存储与审计记录 | 待监控 |
| 3 | 路由规则不稳定导致串会话 | 产品/技术 | 中 | 高 | 先冻结 `scope_id/thread_id` contract，再编码 | 待监控 |
| 4 | 群聊权限模型漂移到 017 | 产品 | 中 | 中 | spec 中明确边界：016 只做基础 outbound action/result，不做统一 inbox | 待监控 |

## 4. 最终推荐方案

### 推荐架构

1. 在 `octoagent.yaml` 中新增 `channels.telegram` 作为单一事实源
2. 由 Gateway 生命周期启动 Telegram runtime，支持 webhook 与 polling
3. 入站 update 在 Gateway 中完成去重、授权判定、规范化，然后进入现有 `TaskService`
4. 015 的 `octo onboard --channel telegram` 与 `octo doctor` 复用同一套 Telegram verifier / readiness 结果
5. Telegram 出站统一使用一份 outbound contract，至少覆盖文本回复、审批提示、错误提示与重试结果

### 推荐实施路径

1. **Phase 1 (MVP)**: 配置模型、Gateway transport、Telegram normalizer、pairing/allowlist、真实 verifier、基础 outbound、核心集成测试
2. **Phase 2**: 更丰富的 group policy / mention policy / callback action 收口
3. **Phase 3**: 多 bot account、统一 operator inbox、复杂媒体和 dashboard 协同

## 5. MVP 范围界定

### 最终 MVP 范围

**纳入**:
- Telegram 渠道配置与健康检查
- webhook / polling 双模式
- DM pairing / allowlist 与群组 allowlist
- DM / 群聊 / topic / reply thread 的稳定 session routing
- 文本回复、审批提示、错误提示、重试结果的 Telegram 回传
- 015 onboarding/doctor 的真实 verifier 闭环

**排除**:
- 统一 operator inbox
- 多 bot account / 多租户 Telegram 账户
- 高级媒体、poll、文件回传
- Telegram 之外的其它移动端控制面

## 6. 结论

### 综合判断

Feature 016 技术可行且产品价值高，是 M2 “从可演示到可日用”的关键门槛。当前仓库已经具备通用任务基础设施和 onboarding 接缝，最需要补的是 Telegram transport、durable 授权边界和稳定 routing contract。建议按推荐架构进入需求规范，不再拖延。

### 置信度

| 维度 | 置信度 | 说明 |
|---|---|---|
| 产品方向 | 高 | 蓝图、里程碑拆解和 015 并行边界都很清晰 |
| 技术方案 | 高 | 现有代码基础扎实，外部参考和官方文档都支持该路径 |
| MVP 范围 | 高 | 纳入项与排除项边界清晰，不必吞并 017 |

