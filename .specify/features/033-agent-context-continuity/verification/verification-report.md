# Verification Report: Feature 033 Agent Profile + Bootstrap + Context Continuity

## 状态

- 阶段：主链实现完成，review 阻塞项已修复
- 日期：2026-03-11

## 本次验证内容

1. 已补齐 `AgentProfile`、`OwnerProfile`、`OwnerProfileOverlay`、`BootstrapSession`、`SessionContextState`、`ContextFrame` 的正式模型、SQLite store、schema migration 与 store group 导出。
2. 已把 `Project.default_agent_profile_id`、`Work.agent_profile_id/context_frame_id`、`AutomationJob.agent_profile_id/context_frame_id` 接入持久化模型。
3. 已新增 `AgentContextService`，在 `TaskService.process_task_with_llm()` 前真实装配：
   - project/workspace 作用域
   - agent profile
   - owner profile / overlay
   - bootstrap session
   - durable recent summary
   - `MemoryService.search_memory()` retrieval hits
   - durable `ContextFrame`
4. 已把 context assembly 结果真实接进主模型输入链；`llm-request-context` artifact 现在会记录 context frame 与 system blocks。
5. 已把 `context_frame_id / agent_profile_id` 继承到 delegation `Work` 与 dispatch metadata。
6. 已为 control plane 新增只读资源：
   - `agent_profiles`
   - `owner_profile`
   - `bootstrap_session`
   - `context_continuity`
7. 已修复 review 阶段发现的 3 个 correctness 问题：
   - `SessionContextState` 改为 scope-aware session key，避免同 `thread_id` 在跨 project/channel/workspace 下串用上下文
   - system blocks 注入后会重新估算真实 prompt tokens，并在超预算时裁剪 `RecentSummary / MemoryHits / RuntimeContext`
   - control plane 的 `context_frames` 查询已把 `workspace_id` 条件下推到 store 层，再应用 `limit`

## 已执行测试

执行命令：

```bash
uv run --group dev pytest packages/core/tests/test_project_store.py packages/core/tests/test_work_store.py packages/core/tests/test_agent_context_store.py apps/gateway/tests/test_context_compaction.py apps/gateway/tests/test_task_service_context_integration.py tests/integration/test_f033_agent_context_continuity.py -q
uv run --group dev pytest apps/gateway/tests/test_control_plane_api.py apps/gateway/tests/test_delegation_plane.py apps/gateway/tests/test_task_service_context_integration.py tests/integration/test_f033_agent_context_continuity.py -q
uv run --group dev pytest apps/gateway/tests/test_task_runner.py apps/gateway/tests/test_worker_runtime.py -q
uv run --group dev pytest packages/core/tests/test_agent_context_store.py apps/gateway/tests/test_task_service_context_integration.py tests/integration/test_f033_agent_context_continuity.py apps/gateway/tests/test_delegation_plane.py apps/gateway/tests/test_control_plane_api.py apps/gateway/tests/test_context_compaction.py apps/gateway/tests/test_task_runner.py apps/gateway/tests/test_worker_runtime.py -q
```

结果：

- 目标回归与 033 专项测试全部通过
- 本轮累计验证：`63 passed`

新增/更新的关键断言：

- `packages/core/tests/test_agent_context_store.py`
  - 验证 profile/bootstrap/session/frame roundtrip
- `apps/gateway/tests/test_task_service_context_integration.py`
  - 直接断言主模型输入真实包含 profile/bootstrap/recent summary/memory hits
  - 验证 legacy session 会迁移到 scope-aware key
  - 验证 system blocks 注入后会记录真实 prompt token，并在超预算时降级裁剪
- `tests/integration/test_f033_agent_context_continuity.py`
  - 验证重启后 continuity 不丢失
  - 验证同 `thread_id` 跨 project/scope 时 profile/summary/memory 不串用
- `apps/gateway/tests/test_delegation_plane.py`
  - 验证 delegation 继承 `agent_profile_id/context_frame_id`
  - 验证同 `thread_id` 不同 scope 使用不同 session key 继承正确 frame
- `apps/gateway/tests/test_control_plane_api.py`
  - 验证新 control-plane 资源已对外暴露
  - 验证 `context_frames` 在 workspace 维度先过滤后截断

## 剩余缺口 / Deferred

- bootstrap 仍是 durable object + fail-soft guidance，尚未补齐完整的问答驱动 runtime、跨 surface resume/action 语义
- control plane 目前提供只读 projection，尚未补齐 `profile switch / bootstrap resume / context refresh` 操作
- frontend 还未接入 033 新资源的专用展示
- 当前剩余项已经不再阻塞 M3/M4 gate，同步文档仅用于持续保持事实一致

## 结论

- 033 的核心验收门禁已经关闭：主 Agent 现在真实消费 canonical context chain，而不再只看当前一句话
- review 阶段指出的 session 串用、prompt token 低估、workspace frame 误过滤问题已全部修复并有回归覆盖
- delegation 与 control plane 已具备最小继承与可观测能力
- 若要继续做体验增强，后续重点应放在 bootstrap operator UX、frontend 暴露与更细粒度 context evidence 展示，而不是再补运行时主链
