# Verification Report — Feature 034

## Scope

验证 034 的三类核心承诺：

1. 主 Agent / Worker 的真实多轮上下文组装
2. 超预算时的 summarizer compaction + memory flush evidence chain
3. Subagent 绕过与 summarizer 失败降级

## Commands

```bash
cd octoagent
uv run --group dev ruff check \
  apps/gateway/src/octoagent/gateway/services/context_compaction.py \
  apps/gateway/src/octoagent/gateway/services/task_service.py \
  apps/gateway/src/octoagent/gateway/services/control_plane.py \
  apps/gateway/src/octoagent/gateway/services/operator_actions.py \
  apps/gateway/tests/test_context_compaction.py \
  packages/core/src/octoagent/core/models/enums.py \
  packages/core/src/octoagent/core/models/payloads.py

uv run --group dev pytest \
  apps/gateway/tests/test_context_compaction.py \
  apps/gateway/tests/test_chat_send_route.py \
  apps/gateway/tests/test_worker_runtime.py \
  apps/gateway/tests/test_task_service_hardening.py \
  apps/gateway/tests/test_control_plane_api.py \
  apps/gateway/tests/test_operator_actions.py \
  apps/gateway/tests/test_telegram_operator_actions.py -q
```

## Results

- `ruff check`：通过
- `pytest`：`43 passed in 5.10s`

## Coverage Notes

- `test_chat_continue_request_reuses_prior_history`：验证 chat 主链路真的带历史
- `test_task_service_compacts_history_and_flushes_memory`：验证 summarizer、artifact、event、memory flush
- `test_worker_runtime_skips_compaction_for_subagent_target`：验证 Subagent 绕过
- `test_compaction_degrades_to_raw_history_when_summarizer_fails`：验证 graceful degradation
- `test_compaction_resume_does_not_repeat_side_effects`：验证恢复路径不会重复写 compaction event / memory flush
- `test_compaction_bounds_summarizer_transcript_for_long_history`：验证超长历史会按预算分批喂给 summarizer
- `test_compaction_event_uses_configured_summarizer_alias`：验证审计事件记录真实 summarizer alias
- `test_control_plane_api` / `test_operator_actions` / `test_telegram_operator_actions`：验证消费侧优先读取完整 `payload.text`

## Residual Risk

- 当前 token 预算仍是轻量估算，不是 provider 真正 tokenizer；这是 M4 之后可继续增强的精度问题，但不影响 034 的真实接线成立。
