# Tasks - Feature 051

## T001 - 固化差异矩阵与蓝图边界

- 状态：已完成
- 新建 051 `spec.md / plan.md / tasks.md`
- 回写 `docs/blueprint.md / docs/m4-feature-split.md / docs/agent-runtime-refactor-plan.md`
- 明确 049 只负责 behavior workspace；051 负责 session-native / recall / tool-aware runtime

## T002 - Behavior budget / truncation contract

- 状态：已完成（首版）
- 在 `BehaviorWorkspaceFile / BehaviorPackFile / BehaviorLayer` 增加 budget / truncation 元数据
- 增加 `default -> system -> project -> optional user-local` overlay contract
- 为 behavior files 引入字符预算、截断与 provenance

## T003 - Tool universe hints 接入 ButlerDecision

- 状态：已完成（首版）
- 从实际 tool selection / capability pack 派生 `mounted / blocked / reasons / tool_profile`
- 注入 ButlerDecision preflight prompt 与 artifact
- 补 request metadata / tests

## T004 - Session-native recent conversation

- 状态：已完成（phase 2）
- `AgentSession` 已具备正式 `recent_transcript / rolling_summary` 字段
- `RecentConversation` 已优先从正式字段读取，旧 `metadata.recent_transcript` 仅保留兼容 shadow
- 完成回合后的 `user/assistant` turn 已回写到 `AgentSession.recent_transcript`
- `AgentSessionTurn` 已成为正式 turn/tool-turn store：`user / assistant / tool_call / tool_result / context_summary` 会落到 `agent_session_turns`
- `task/event reconstruction` 已降级为 fallback，并在回放后回写 session transcript
- context compaction 完成后，`rolling_summary / summary_artifact_id / trimmed recent_transcript` 已写回 `SessionContextState + AgentSession`
- `session.export` 底层 payload 已开始携带 `SessionContextState + AgentSession` continuity 信息
- 控制面已新增 `session.new / session.reset`
- Web Chat 已补齐 `开始新对话` 主路径：调用服务端 `session.new` 后，下一次发送会创建新的 task/thread，并回写 `session.focus`
- `session.reset` 现已同步清空 `agent_session_turns`
- Session Center 已补齐 `开始新对话 / 重置 continuity` 入口，并直接走 control action

## T005 - Agent-led recall runtime

- 状态：已完成（Butler 主链）
- 定义 `RecallPlan / RecallEvidenceBundle`
- Butler chat 默认已切到 `agent-led hint-first` memory runtime，不再固定预取详细 recall
- `planner_enabled` profile 下已支持 `RecallPlan` 规划 contract：先由模型生成 recall query，再执行 plan-driven recall
- Butler 现已把 `ButlerDecision + RecallPlan` 收口为统一 `ButlerLoopPlan`
- Butler direct-answer 路径会把 recall 计划作为 `precomputed_recall_plan` 注入主调用，不再额外触发独立 recall planner phase
- `RecallEvidenceBundle` 已写回 `memory_recall` budget，并以 artifact/source refs 保留 planner 审计链
- 当 MemU backend 可用时，plan-driven recall 已验证会优先复用高级 backend search path
- Worker runtime 默认已切到 `hint-first`，仅在显式 profile override 下保留 `detailed_prefetch`
- delayed recall 在 `hint_first / agent_led_hint_first` 下都不再被系统自动调度
- 若本轮已执行 plan-driven recall，delayed recall 会继续按 degraded/backend 状态决定是否补跑
- `MemorySearchOptions` 已把 `expanded_queries / focus_terms / subject_hint / rerank_mode / post_filter_mode / min_keyword_overlap` 下发到 MemU `command/http` recall 执行面

## T006 - Compatibility fallback 收薄

- 状态：已完成
- 已移除排优先级 / 推荐 / 比较的 compatibility tree
- 仅保留天气缺地点边界、天气 follow-up 恢复，以及 guardrail / parse failure / migration compatibility

## T007 - 验证与对标复核

- 状态：已完成
- 已跑 `test_butler_behavior.py + test_orchestrator.py + test_capability_pack_tools.py + test_task_service_context_integration.py`
- 已跑 `test_context_compaction.py + test_backup_service.py`
- 本轮新增已跑：
  - `test_control_plane_api.py + test_task_service_context_integration.py + test_context_compaction.py + test_backup_service.py`：后端 `81 passed`
  - `useChatStream.test.tsx + ChatWorkbench.test.tsx + controlPlaneResources.test.ts + ControlPlane.test.tsx`：前端 `23 passed`
  - `tsc -b`：通过
- 对照 Agent Zero / OpenClaw 重新复核本轮差距并关闭对应实现缺口
- 回写文档与 release note

## T008 - Transcript replay / sanitize 收口

- 状态：已完成
- `AgentSessionTurn` 现已生成正式 `SessionReplay` 投影，而不是只提供 `recent_transcript` cache
- replay 投影会执行 turn 去重、tool call/result pairing 修复、orphan tool call 清理，并把 sanitize notes 暴露到 metadata
- `SessionReplay` 已进入 canonical system block，`RecentConversation` 与 recall planning 也复用同一投影

## T009 - 单循环 Butler 主执行器

- 状态：已完成
- 默认 general Butler 请求现在会进入 `single_loop_executor`
- 主模型调用会直接带着 profile-first 工具集进入 `LLM + SkillRunner` 工具循环
- 这条主路径不再额外触发 `ButlerDecision` 或 `memory_recall_planning` 辅助 phase
- `ButlerDecision / ButlerLoopPlan` 保留给 compatibility / explicit delegation / legacy preflight 路径

## T010 - Phase 2 TODO List: Worker planner-enabled recall

- 状态：已完成
- Worker 默认 mirror profile 现在对 `research / dev / general` archetype 开启 `planner_enabled=true`
- `hint_first` 也允许进入 `RecallPlan` 规划链，不再只有 `agent_led_hint_first` 能走 planner
- 显式 `detailed_prefetch` override 仍可关闭 planner，并回到旧式 prompt 注入 recall

## T011 - Phase 2 TODO List: Session 生命周期产品面

- 状态：已完成
- Session projection 增加 `lane` 与 `summary(total/running/queue/history/focused)` 元数据
- Control Plane 新增 `session.unfocus`
- Session Center 现在提供 `全部 / 运行中 / 队列 / 历史` lane 过滤，以及 `取消聚焦 / 开始新对话 / 重置 continuity`

## T012 - Phase 2 TODO List: Transcript replay budget-driven trim

- 状态：已完成
- `SessionReplay` 不再固定只注入最近 `6/4` 条 dialogue/tool lines
- replay projection 先保留完整 turn-store 投影，再在 `_fit_prompt_budget()` 中按预算逐级裁剪
- replay trim 不再误触发 delayed recall materialization

## T013 - Phase 2 TODO List: single-loop 扩到 worker lens

- 状态：已完成
- `single_loop_executor` 不再只覆盖 general Butler
- 显式 `requested_worker_type=research/dev/ops` 时，Butler 可借对应 worker lens 直接进入单循环工具执行
- `ToolIndexQuery` 与 skill description 已同步使用真实 worker type

## T014 - Phase 2 TODO List: Behavior files edit/diff/apply

- 状态：已完成
- CLI 新增 `octo behavior edit/show/diff/apply`
- `edit` 会 materialize 可编辑目标文件并尽量调用本机编辑器
- `diff` 比较当前 override 相对下层来源的差异
- `apply` 支持把 reviewed proposal 写回目标 behavior file
- Settings 页已同步展示 `edit / diff / apply` CLI 入口
