# Verification Report: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

**Date**: 2026-03-09  
**Status**: PASS  
**Branch**: `codex/feat-032-openclaw-builtin-tool-suite`

## Scope

验证 032 的四条主链：

1. built-in tool catalog 与 availability truth
2. child task / subagent spawn / split-merge 生命周期
3. `pydantic_graph` graph runtime 真执行
4. control plane / 前端对 availability 与 runtime truth 的展示

## Commands

### Backend targeted regression

```bash
cd octoagent
uv run pytest \
  apps/gateway/tests/test_capability_pack_tools.py \
  apps/gateway/tests/test_worker_runtime.py \
  apps/gateway/tests/test_delegation_plane.py \
  apps/gateway/tests/test_control_plane_api.py \
  -q
```

Result: `30 passed`

### Python lint

```bash
cd octoagent
uv run ruff check \
  apps/gateway/src/octoagent/gateway/services/capability_pack.py \
  apps/gateway/src/octoagent/gateway/services/control_plane.py \
  apps/gateway/src/octoagent/gateway/services/delegation_plane.py \
  apps/gateway/src/octoagent/gateway/services/execution_context.py \
  apps/gateway/src/octoagent/gateway/services/task_runner.py \
  apps/gateway/src/octoagent/gateway/services/task_service.py \
  apps/gateway/src/octoagent/gateway/services/worker_runtime.py \
  apps/gateway/src/octoagent/gateway/main.py \
  packages/core/src/octoagent/core/models/capability.py \
  packages/core/src/octoagent/core/models/control_plane.py \
  apps/gateway/tests/test_capability_pack_tools.py \
  apps/gateway/tests/test_worker_runtime.py \
  apps/gateway/tests/test_delegation_plane.py \
  apps/gateway/tests/test_control_plane_api.py
```

Result: `All checks passed`

### Compile sanity

```bash
cd octoagent
uv run python -m compileall \
  apps/gateway/src/octoagent/gateway/services \
  packages/core/src/octoagent/core/models
```

Result: `PASS`

### Frontend targeted tests

```bash
cd octoagent/frontend
npm test -- src/pages/ControlPlane.test.tsx
```

Result: `7 passed`

### Frontend build

```bash
cd octoagent/frontend
npm run build
```

Result: `PASS`

### Review fix addendum

```bash
cd octoagent
uv run pytest \
  apps/gateway/tests/test_capability_pack_tools.py \
  apps/gateway/tests/test_worker_runtime.py \
  apps/gateway/tests/test_delegation_plane.py \
  apps/gateway/tests/test_control_plane_api.py \
  apps/gateway/tests/test_task_service_hardening.py \
  -q
```

Result: `40 passed`

```bash
cd octoagent
uv run ruff check \
  apps/gateway/src/octoagent/gateway/services/worker_runtime.py \
  apps/gateway/src/octoagent/gateway/services/task_service.py \
  apps/gateway/src/octoagent/gateway/services/capability_pack.py \
  apps/gateway/tests/test_worker_runtime.py \
  apps/gateway/tests/test_task_service_hardening.py \
  apps/gateway/tests/test_capability_pack_tools.py
```

Result: `All checks passed`

## Verified Behaviors

- capability pack 现在输出 15+ built-in tools，且包含 `availability / install_hint / entrypoints / runtime_kinds`
- `subagents.spawn` 与 `work.split` 会创建真实 child task，并落成 durable child work / session
- child work 在创建瞬间就能保留显式 `requested_worker_type / target_kind / parent_work_id`
- child task 在 follow-up turn 后仍能保留 parent/runtime metadata，不会退化成顶层任务
- `work.merge` 能在 child work 完成后回写父 work merge 状态
- `graph_agent` 现在真实走 `pydantic_graph` backend，而不是只写 metadata label
- 当 `docker_mode=required` 时，`graph_agent` 会 fail-closed，不会绕过 Docker 隔离策略
- `subagents.spawn(title=...)` 会把 title 作为 label 元数据保留，但 child prompt 仍然使用真实 objective
- control plane snapshot / work actions / 会话投影能展示 runtime truth、child work 关系与 tool availability
- 前端 Control Plane 能显示 availability 状态、entrypoints、runtime kinds、split/merge 动作与 child runtime 信息

## Known Gaps

- 本轮没有重跑整仓全量 `pytest`
- browser / TTS 等工具族当前只验证 availability truth 与控制面接线，没有做真实外部依赖的在线 smoke test
- 032 明确未包含 channel action packs、remote nodes、companion surfaces
