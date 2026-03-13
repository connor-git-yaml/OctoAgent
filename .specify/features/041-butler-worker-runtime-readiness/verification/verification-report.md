# Verification Report: Feature 041 — Butler / Worker Runtime Readiness + Ambient Context

**Feature ID**: `041`  
**Date**: 2026-03-12  
**Updated**: 2026-03-13  
**Branch**: `codex/041-butler-worker-runtime-readiness`  
**Status**: Implemented（ambient facts、Butler-owned freshness A2A 主链、缺城市追问、backend unavailable 降级与 surface truth 已全部验证）

## Verification Scope

- ambient current time / timezone / locale 是否已经进入主 Agent 与 child worker 的默认运行面
- Butler 是否会把“今天 / 天气 / 官网 / 最新资料”解释为 freshness query delegation，而不是直接宣称系统没有实时能力
- research / ops worker 的 governed web/browser 路径、`tool_profile` 与 child work lineage 是否可解释且可审计
- Workbench / Control Plane 是否已经把 freshness runtime truth 与 degraded reason 讲成人能读懂的话
- 041 是否已经具备正式 acceptance matrix 与 release gate report

## Executed Checks

### Backend Lint

```bash
cd octoagent && uv run --group dev ruff check \
  apps/gateway/src/octoagent/gateway/services/agent_context.py \
  apps/gateway/src/octoagent/gateway/services/capability_pack.py \
  apps/gateway/src/octoagent/gateway/services/delegation_plane.py \
  apps/gateway/tests/test_task_service_context_integration.py \
  apps/gateway/tests/test_capability_pack_tools.py
```

结果：`All checks passed!`

### Backend Regression

```bash
cd octoagent && uv run pytest \
  apps/gateway/tests/test_orchestrator.py \
  apps/gateway/tests/test_control_plane_api.py -q
```

结果：`60 passed`

重点覆盖：

- `Butler -> Research child task -> A2AConversation -> WorkerSession -> ButlerReply`
- 天气缺城市时 Butler 显式追问位置，不会误答成系统没有实时能力
- research child 因 web backend 不可用而失败时，Butler 会把限制解释成当前工具后端 / 环境限制
- parent work 回填 `research_a2a_conversation_id / research_worker_agent_session_id / research_a2a_message_count`
- control plane runtime truth contract

### Frontend Regression

```bash
cd octoagent/frontend && npm test -- --run \
  src/pages/ChatWorkbench.test.tsx \
  src/workbench/freshness.test.ts \
  src/pages/ControlPlane.test.tsx \
  src/App.test.tsx
```

结果：`39 passed`

重点覆盖：

- `Chat` 当前任务侧栏能直接看到 Butler -> Worker 内部协作链
- `/work` freshness readiness 卡片
- `Butler -> Research` 路径的人话摘要
- 缺城市追问与 backend unavailable 的路径文案
- `/advanced` dashboard / delegation freshness runtime truth
- degraded reason 的用户可读解释

### Frontend Build

```bash
cd octoagent/frontend && npm run build
```

结果：通过

## Gate Results

| Gate | 结论 | 证据 |
|---|---|---|
| `GATE-041-AMBIENT-RUNTIME` | PASS | `test_task_service_context_integration.py::test_build_ambient_runtime_facts_formats_local_datetime_and_fallbacks` + `agent_context.py` 中 `AmbientRuntime` block |
| `GATE-041-FRESHNESS-DELEGATION` | PASS | `test_capability_pack_tools.py::test_workers_review_uses_standard_profile_for_freshness_queries` + `bootstrap:general / research / ops` |
| `GATE-041-LINEAGE-RUNTIME-TRUTH` | PASS | `test_capability_pack_tools.py::test_subagents_spawn_preserves_freshness_tool_profile_and_lineage` + `delegation_plane.py` / `control_plane.py` runtime summary |
| `GATE-041-WORKBENCH-SURFACE` | PASS | `src/pages/ChatWorkbench.test.tsx::会在侧栏展示当前 Butler 到 Worker 的内部协作链` + `src/workbench/freshness.test.ts::会保留带 Butler-owned freshness 路径的相关 work` + `src/App.test.tsx::Work 看板会把实时问题能力和相关运行真相翻译成可读摘要` + `src/pages/ControlPlane.test.tsx::Dashboard 和 Delegation 会显示实时问题能力与对应 work 路径` |
| `GATE-041-ACCEPTANCE-MATRIX` | PASS | `contracts/freshness-query-acceptance-matrix.md` |
| `GATE-041-RELEASE-REPORT` | PASS | 本报告 + `docs/blueprint.md` + `docs/m4-feature-split.md` |

## Release Decision

**结论**：Feature 041 的 targeted release gates 已全部闭合，现可按 2026-03-13 更新后的 blueprint 标准视为 fully complete。

当前 release 口径应为：

- **041 feature**：Implemented
- **M4 follow-up readiness**：已闭合 Butler-owned freshness runtime 的全部 acceptance

成立前提：

- Butler 仍保持 supervisor-only，不直接持有 web/browser/code 执行面
- freshness query 通过 governed worker/tool 路径处理，而不是无差别放宽主 Agent 权限
- 对 web/browser 环境受限的情况，系统已能把限制解释成当前 runtime/tool backend 限制，而不是宣称系统本质不具备外部事实能力

## Key Release Notes

- 主 Agent 与 child worker 现在都拿到当前本地日期/时间、星期、timezone、locale 与 surface/source 事实。
- 系统新增 `runtime.now` deterministic 能力，避免“今天/现在”类问题继续依赖模型猜测。
- `workers.review`、`subagents.spawn`、`work.split` 现在会把天气、官网、最新资料等 objective 解释成更明确的 worker/tool_profile 路径。
- 当用户只问“今天天气怎么样”而未给城市时，Butler 会先显式补问位置，而不是误答成系统没有实时能力。
- 当 research child 遇到 web/browser backend unavailable 时，Butler 会用同一主链给出环境限制说明，并保留 A2A / WorkerSession 审计事实。
- child work 继续保留 `project_id / workspace_id / requested_worker_type / requested_tool_profile / spawned_by / plan_id` runtime truth。
- parent work 现在会回填 `research_a2a_conversation_id / research_worker_agent_session_id / research_a2a_message_count`，Control Plane 与 Workbench 都能看见这条内部真相。
- Chat / Workbench / Control Plane 已经能直接显示 freshness readiness、相关 degraded reason 与最近一条 `Butler -> Worker` 内部执行链证据。

## Residual Risks

- 当前验证属于 targeted release verification，而非整仓全量 `pytest`；如需 nightly 级别置信度，仍应继续保留更大范围 CI。
- 当前验证已经覆盖一条真实 `Butler -> Research child task -> A2AConversation -> WorkerSession -> ButlerReply` 主链，以及 Butler 显式缺参追问和 backend unavailable 的降级答复。
