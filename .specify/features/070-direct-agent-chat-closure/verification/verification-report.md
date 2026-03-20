# Verification Report

## Feature

- `070-direct-agent-chat-closure`

## Result

- 状态：`passed`

## Verified Paths

1. direct worker session 创建不再伪装成 `BUTLER_MAIN`
2. worker profile 的 `model_alias` 可以穿透到首条 direct message 执行
3. `session_id / thread_id` 会在 direct session 首条消息里稳定传递并持久化
4. 用户会话投影会过滤 `worker_internal` 与内部 runtime session
5. `ChatWorkbench` route session 恢复优先级与首条 direct message Web 链路保持一致

## Commands

```bash
cd octoagent
uv run --group dev pytest apps/gateway/tests/test_chat_send_route.py -q
uv run --group dev pytest apps/gateway/tests/test_control_plane_api.py::TestControlPlaneApi::test_session_new_can_prepare_explicit_agent_session_entry -q
uv run --group dev pytest apps/gateway/tests/test_control_plane_api.py::TestControlPlaneApi::test_session_create_with_project_returns_projected_session_id_and_thread_seed apps/gateway/tests/test_control_plane_api.py::TestControlPlaneApi::test_snapshot_returns_control_plane_resources_and_registry -q

cd frontend
npm test -- --run src/pages/ChatWorkbench.test.tsx
npm run build
```

## Notes

- Python 侧 `ruff --select I,F` 在当前仓库仍会命中若干历史债务，主要集中在 `agent_context.py`、`control_plane.py` 与大体量测试文件；这次 direct-agent chat 修复没有新增新的功能性 lint blocker。
- 还未做 live `~/.octoagent` 手工复测；如需最终验收，应在真实实例上确认：
  - 非主 Agent 新建 direct session 可直接回复
  - 老的 direct session 能继续恢复
  - `/api/control/resources/sessions` 不再泄漏内部 worker session
