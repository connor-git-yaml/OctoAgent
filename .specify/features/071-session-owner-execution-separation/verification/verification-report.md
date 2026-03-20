# Verification Report

## Feature

- `071-session-owner-execution-separation`

## Scope

本轮验证确认以下语义已经稳定成立：

- `Profile + Project` 只决定 `session_owner_profile_id`
- 默认主会话仍可走 Butler direct execution
- direct worker 会话可走 owner-self execution
- 显式 delegated worker 会在 control plane / Chat 中正确显示 owner / executor / target
- worker 仅允许 `self / spawn_subagent`，明确禁止 `worker -> worker`
- 历史 `BUTLER_MAIN + worker profile` 污染会话会被标记为 `reset_recommended`

## Regression Matrix

| 场景 | 主要覆盖 |
| --- | --- |
| main session / self | `apps/gateway/tests/test_control_plane_api.py::test_session_projection_exposes_lane_summary_and_unfocus` + `apps/gateway/tests/test_control_plane_api.py::test_legacy_butler_session_with_worker_profile_pollution_is_marked_for_reset` + `apps/gateway/tests/test_orchestrator.py::test_dispatch_single_loop_executor_uses_explicit_singleton_worker_profile` |
| direct worker session / self | `apps/gateway/tests/test_control_plane_api.py::test_session_new_can_prepare_explicit_agent_session_entry` + `apps/gateway/tests/test_control_plane_api.py::test_direct_session_first_message_recovers_owner_profile_from_session_anchor` + `apps/gateway/tests/test_control_plane_api.py::test_direct_session_continue_message_preserves_owner_without_delegation_target` |
| delegated worker | `apps/gateway/tests/test_control_plane_api.py::test_session_projection_reports_delegated_worker_execution` + `apps/gateway/tests/test_orchestrator.py::test_dispatch_prepared_roundtrips_through_a2a_and_restores_runtime_context` |
| spawned subagent | `apps/gateway/tests/test_capability_pack_tools.py::test_subagents_spawn_preserves_freshness_tool_profile_and_lineage` |
| worker -> worker prohibited | `apps/gateway/tests/test_capability_pack_tools.py::test_subagents_spawn_rejects_worker_to_worker_delegation` |
| worker profile observability | `apps/gateway/tests/test_control_plane_api.py::test_worker_profiles_document_includes_owner_self_worker_runs` |

## Feature Compatibility

### Feature 064

- Butler direct execution 仍然保留，且 eligibility 不再被 `session owner` 或 inherited worker profile 误阻断。

### Feature 070

- direct non-main session 仍是一等对象，但其首轮与续聊现在按照 `owner-self execution` 运行，不再把 owner profile 自动提升成 `requested_worker_profile_id`。

## Commands Run

```bash
cd /Users/connorlu/.codex/worktrees/47dc/OctoAgent/octoagent
uv run --group dev pytest \
  apps/gateway/tests/test_chat_send_route.py \
  apps/gateway/tests/test_control_plane_api.py \
  apps/gateway/tests/test_delegation_plane.py \
  apps/gateway/tests/test_orchestrator.py \
  apps/gateway/tests/test_capability_pack_tools.py -q
```

## Result

- 相关定向回归通过
- 文档事实源已同步到 `docs/blueprint.md` 与 `docs/m4-feature-split.md`
- `071` 的 Slice A-F 现已闭合
