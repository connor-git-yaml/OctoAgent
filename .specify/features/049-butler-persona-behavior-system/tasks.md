# Tasks - Feature 049

## T001 - 收口核心 behavior files

- 状态：已完成
- 定义 `AGENTS.md / USER.md / PROJECT.md / TOOLS.md` 的 schema、可见性和 fallback
- 定义 `SOUL.md / IDENTITY.md / HEARTBEAT.md` 的高级扩展策略
- 明确 `MEMORY.md` 不属于默认行为配置主路径

## T002 - 设计 RuntimeHintBundle

- 状态：已完成
- 定义当前时间、tool availability、recent confirmed facts、user defaults、surface 等 hints
- 明确 hints provenance 与审计要求

## T003 - 设计 ButlerDecision contract

- 状态：已完成
- 定义 `direct_answer / ask_once / delegate_research / delegate_ops / best_effort_answer`
- 明确 assumptions、missing_inputs、boundary_note、tool_intent 字段
- 确定 deterministic guardrails 与 agentic decision 的边界

## T004 - 设计 WorkerContextCapsule

- 状态：已完成
- 明确 Worker 默认可见的文件集合
- 明确 `USER.md` 到 Worker hint 的筛选规则
- 对齐 A2A payload / work metadata / runtime truth

## T005 - 设计 Web / CLI 产品面

- 状态：已完成（当前阶段）
- 已提供 `Settings -> Behavior Files` 只读 operator 视图
- 已提供 `octo behavior ls/show/init`
- 更完整的 edit/diff/review/apply 留作后续增强，不阻塞本轮 049 收口

## T006 - 兼容迁移方案

- 状态：已完成
- 已实现从默认模板 / system / project filesystem 的 effective source chain
- 旧天气/location/clarify 规则已降级为 compatibility fallback，并带 provenance

## T007 - 验收矩阵与回归

- 状态：已完成
- 覆盖天气、推荐、排期、比较、显式 WebSearch 指令
- 覆盖有默认值、会话确认事实、无关键线索、tool 不可用等上下文

## T008 - 文档同步

- 状态：已完成
- 更新 `docs/blueprint.md`
- 更新 `docs/m4-feature-split.md`
- 如实现范围确认后，再决定是否回写 README / 产品文档
