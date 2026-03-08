# Verification Report: Feature 027 — Memory Console + Vault Authorized Retrieval

**Feature ID**: `027`  
**Date**: 2026-03-08  
**Branch**: `codex/feat-027-memory-console-vault`  
**Status**: Passed (定向验证通过)

## Verification Scope

- Memory durable schema / store / service
- Gateway control-plane Memory resources / actions / snapshot integration
- Web Control Plane Memory section、subject history、proposal audit、Vault authorization 面板
- Memory export inspect / restore verify 的 control-plane 接线

## Executed Checks

### Backend

```bash
uv run pytest \
  packages/memory/tests/test_memory_store.py \
  packages/memory/tests/test_memory_service.py \
  packages/provider/tests/dx/test_memory_console_service.py \
  apps/gateway/tests/test_control_plane_api.py \
  -q
```

结果：`28 passed`

```bash
uv run --with ruff ruff check \
  apps/gateway/src/octoagent/gateway/main.py \
  apps/gateway/src/octoagent/gateway/routes/control_plane.py \
  apps/gateway/src/octoagent/gateway/services/control_plane.py \
  packages/memory/src/octoagent/memory \
  packages/memory/tests/test_memory_store.py \
  packages/memory/tests/test_memory_service.py \
  apps/gateway/tests/test_control_plane_api.py
```

结果：`All checks passed!`

### Frontend

```bash
npm test -- src/pages/ControlPlane.test.tsx
```

结果：`5 passed`

```bash
npm run build
```

结果：通过

## Covered Paths

- `/api/control/snapshot` 发布 `memory` canonical resource
- `/api/control/resources/memory`
- `/api/control/resources/memory-subjects/{subject_key}`
- `/api/control/resources/memory-proposals`
- `/api/control/resources/vault-authorization`
- `memory.query`
- `vault.access.request / vault.access.resolve / vault.retrieve`
- `memory.export.inspect / memory.restore.verify`
- Web Control Plane Memory section 的 overview / subject history / proposal audit / vault authorization 读取与动作提交
- review 回归点：显式 `grant_id` actor 校验、空 scope 过滤、proposal `source` 过滤、export/restore scope 边界、Memory 刷新保留查询参数

## Residual Notes

- 本轮没有运行真实 browser/manual live e2e；关键路径由 gateway API integration 与 frontend integration 测试覆盖。
- 本轮没有重跑整仓全量 `pytest`；验证范围聚焦于 027 受影响模块。
