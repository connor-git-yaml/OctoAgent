# Verification Report: Feature 029 — WeChat Import + Multi-source Import Workbench

**Feature ID**: `029`  
**Date**: 2026-03-08  
**Branch**: `codex/feat-029-wechat-import-workbench`  
**Status**: Passed (定向验证通过)

## Verification Scope

- WeChat source adapter 与 multi-source adapter registry
- provider DX import workbench state / mapping / preview / run / resume
- attachment -> artifact / fragment / memory effect 链路
- gateway control-plane import resources / actions / snapshot integration
- Web Control Plane Import Workbench section 与 CLI parity

## Executed Checks

### Backend

```bash
uv run pytest \
  packages/core/tests/test_artifact_store.py \
  packages/provider/tests/test_chat_import_service.py \
  packages/provider/tests/test_chat_import_commands.py \
  packages/provider/tests/test_import_workbench_service.py \
  apps/gateway/tests/test_control_plane_api.py \
  -q
```

结果：`26 passed`

```bash
uv run ruff check \
  apps/gateway/src/octoagent/gateway/routes/control_plane.py \
  apps/gateway/src/octoagent/gateway/services/control_plane.py \
  packages/memory/src/octoagent/memory/__init__.py \
  packages/memory/src/octoagent/memory/imports/__init__.py \
  packages/memory/src/octoagent/memory/imports/models.py \
  packages/memory/src/octoagent/memory/imports/service.py \
  packages/memory/src/octoagent/memory/imports/source_adapters/base.py \
  packages/memory/src/octoagent/memory/imports/source_adapters/normalized_jsonl.py \
  packages/memory/src/octoagent/memory/imports/source_adapters/wechat.py \
  packages/memory/src/octoagent/memory/service.py \
  packages/provider/src/octoagent/provider/dx/chat_import_commands.py \
  packages/provider/src/octoagent/provider/dx/chat_import_service.py \
  packages/provider/src/octoagent/provider/dx/import_mapping_store.py \
  packages/provider/src/octoagent/provider/dx/import_source_store.py \
  packages/provider/src/octoagent/provider/dx/import_workbench_models.py \
  packages/provider/src/octoagent/provider/dx/import_workbench_service.py \
  packages/provider/tests/test_chat_import_commands.py \
  packages/provider/tests/test_chat_import_service.py \
  packages/provider/tests/test_import_workbench_service.py \
  apps/gateway/tests/test_control_plane_api.py
```

结果：`All checks passed!`

```bash
python -m compileall \
  apps/gateway/src/octoagent/gateway/routes/control_plane.py \
  apps/gateway/src/octoagent/gateway/services/control_plane.py \
  packages/memory/src/octoagent/memory \
  packages/provider/src/octoagent/provider/dx/chat_import_commands.py \
  packages/provider/src/octoagent/provider/dx/chat_import_service.py \
  packages/provider/src/octoagent/provider/dx/import_mapping_store.py \
  packages/provider/src/octoagent/provider/dx/import_source_store.py \
  packages/provider/src/octoagent/provider/dx/import_workbench_models.py \
  packages/provider/src/octoagent/provider/dx/import_workbench_service.py
```

结果：通过

### Frontend

```bash
npm test -- src/pages/ControlPlane.test.tsx
```

结果：`6 passed`

```bash
npm run build
```

结果：通过

## Covered Paths

- `ImportSourceType / ImportSourceFormat.WECHAT`
- `memory.imports.source_adapters.{base,normalized_jsonl,wechat}`
- `ImportWorkbenchService.detect_source / save_mapping / preview / run / resume / inspect_report`
- `ChatImportService.import_messages` 的 attachment materialization、artifact refs、fragment sync、memory effect 汇总
- review 回归点：小型二进制附件不再 UTF-8 inline 损坏、同 scope 合并消息按时间/游标稳定排序、run 物化异常落 `FAILED` durable record、显式 `mapping_id` 做 source/project/workspace 归属校验
- `/api/control/snapshot` 发布 `imports` canonical resource
- `/api/control/resources/import-workbench`
- `/api/control/resources/import-sources/{source_id}`
- `/api/control/resources/import-runs/{run_id}`
- `import.source.detect / import.mapping.save / import.preview / import.run / import.resume / import.report.inspect`
- Web Control Plane Import Workbench 的 source detail / run detail / refresh
- CLI `octo import detect / mapping-save / preview / run / resume`
- integration 主路径：`detect -> mapping -> preview -> run -> resume`

## Residual Notes

- 本轮没有运行整仓全量 `pytest`；验证范围聚焦于 029 受影响模块。
- WeChat adapter 当前定向覆盖的是离线 JSON 导出主路径；HTML / SQLite fallback 已实现并通过静态编译检查，但未补独立样本回归。
- 本轮没有做真实浏览器手工操作；Web 路径由 frontend integration test 与 gateway API integration test 覆盖。
