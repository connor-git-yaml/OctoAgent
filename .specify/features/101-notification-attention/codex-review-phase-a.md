# F101 Per-Phase A Codex Review

> Reviewer: Codex GPT-5.4 high
> Date: 2026-05-17
> Phase: A (force_full_recall producer)
> Baseline commit: 182e9ed
> Review input: git diff 182e9ed -- chat.py + test_chat_force_full_recall.py
> Verification: 18 new tests PASS / 11 existing chat tests 0 regression / 3423 total passed / e2e_smoke 8/8

## Summary
- HIGH: 2（必须处理）
- MEDIUM: 1（建议处理）
- LOW: 1（可选改进）
- 总体评估：FIX_HIGH_FIRST
- No finding：注入点位于 `encode_runtime_context` 之前；`> 2000` / `2001` 边界语义符合 spec/plan；第二组跨语言矩阵覆盖中文、英文、代码、JSON、混合 stack trace 五类输入；tracked diff 中 `spec.md` / `plan.md` / `tasks.md` 无变更。

## Finding 列表

### [HIGH] H-A1: 标准 task_runner 路径丢弃 force_full_recall，producer 只在 fallback 路径有效
- 位置: octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:442; octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:493; octoagent/apps/gateway/tests/test_chat_force_full_recall.py:141
- 描述: HARD finding。Phase A 只把 `force_full_recall` 写进临时 `dispatch_metadata`。但真实 `_enqueue_or_run` 在存在 `request.app.state.task_runner` 时走 `task_runner.enqueue(task_id, message, model_alias=model_alias)` 并返回，未传递 `dispatch_metadata`。`TaskRunner.enqueue` 只持久化 `task_id/user_text/model_alias`，后续 `_run_job` 从 `TaskService.get_latest_user_metadata(task_id)` 读取 USER_MESSAGE 的 control metadata。新对话和续对话写入 USER_MESSAGE 时使用的是未包含该 flag 的 `chat_control_metadata`，所以生产默认路径下 orchestrator 收不到 `metadata["force_full_recall"]`。
- Adversarial 理由: 测试在 `test_chat_force_full_recall.py:141` 等位置 patch 掉 `_enqueue_or_run`，只证明路由层构造过 `dispatch_metadata`，没有执行真实 task_runner 入队链。测试 fixture 实际设置了 `app.state.task_runner`，这正是会丢 metadata 的标准路径，但 patch 掩盖了问题。结果是 FR-D1/FR-D2 和 AC-D1/AC-D3 在默认生产路径不成立。
- 推荐方向: 不要只把 flag 放在不会进入 `TaskRunner` 的临时参数里。可选方向：将 `force_full_recall` 写入持久 control metadata，或扩展 `TaskRunner.enqueue` / task_job 存储以保留 `dispatch_metadata`，并新增不 patch `_enqueue_or_run` 的链路测试，断言 `OrchestratorService.dispatch` 或 `_prepare_single_loop_request` 实际收到并 patch 出 `runtime_context.force_full_recall=True`。

### [HIGH] H-A2: LONG_PROMPT_THRESHOLD 被 hardcode，违反 FR-D3 可配置要求
- 位置: octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:43
- 描述: HARD finding。实现为 `LONG_PROMPT_THRESHOLD: int = 2000` 的模块级硬编码常量，没有从 settings/env/USER.md 或其他配置源读取。spec FR-D3 明确要求 `LONG_PROMPT_THRESHOLD MUST 有可配置的默认值 ... 不得 hardcode`。
- Adversarial 理由: 这是硬性 spec 违背，不只是后续调参便利性问题。阈值直接控制 recall planner 运行频率，过低会造成性能回退，过高会漏掉长 context；没有部署级或用户级配置入口时，Phase A 未满足 “configurable default value”。
- 推荐方向: 增加单一配置入口，例如 `OCTOAGENT_LONG_PROMPT_THRESHOLD` 环境变量读取并保留默认 `2000`，或接入项目已有配置/USER.md SoT。测试需覆盖默认值、覆盖值、非法值 fallback，并避免所有测试直接依赖不可覆盖的模块常量。

### [MEDIUM] M-A1: AC-D1 的 orchestrator 处理点没有被 Phase A 测试覆盖
- 位置: octoagent/apps/gateway/tests/test_chat_force_full_recall.py:423
- 描述: HARD verification finding。AC-D1 要求 `dispatch_metadata["force_full_recall"]=True` 经 `orchestrator._prepare_single_loop_request` 后变成 `runtime_context.force_full_recall=True`，并让 `is_recall_planner_skip` 返回 False。当前测试在 `test_chat_force_full_recall.py:423-437` 直接构造 `RuntimeControlContext(force_full_recall=True)` 调 helper，没有从 chat producer 产出的 metadata 经过 orchestrator。
- Adversarial 理由: 直接测 helper 会绕开最容易断裂的集成点：metadata 是否进入 task_runner/orchestrator、`_with_delegation_mode` 是否读取 hint、`RUNTIME_CONTEXT_JSON_KEY` 是否被刷新。H-A1 正是这种链路断裂，现有测试无法捕获。
- 推荐方向: Phase A 至少增加一个 mock-based orchestrator 链路测试：从 chat 请求开始，不 patch `_enqueue_or_run`，或只 spy `OrchestratorService.dispatch/_prepare_single_loop_request`，断言最终 request metadata 或 runtime_context 中的 `force_full_recall` 为 True。helper 真值表测试可以保留，但不能作为 AC-D1 链路证明。

### [LOW] L-A1: 跨语言矩阵第一组参数有死字段和自相矛盾 case，降低测试可读性
- 位置: octoagent/apps/gateway/tests/test_chat_force_full_recall.py:299
- 描述: SOFT finding。`test_cross_language_triggering` 参数包含 `should_trigger`，但测试体不使用该参数，而是重新计算 `actual_should_trigger`。其中代码块 case 文案写 “2001 字符代码块 → 应触发”，实际 lambda 长度约 1213，参数还写成 `False` 占位。
- Adversarial 理由: 第二组 `test_all_cross_language_inputs_trigger_force_full_recall` 已经覆盖 5 类 `len > 2000` 输入，所以这不是验收缺口；但第一组测试会让后续维护者误判 M1 矩阵意图，也可能在复制扩展时把错误 case 当成有效边界样本。
- 推荐方向: 删除第一组冗余参数化测试，或让 `should_trigger` 成为真实断言，并把所有 case 的长度构造写成显式 `LONG_PROMPT_THRESHOLD +/- n`。

## 整体结论
是否可以进入 Phase B：不可。

理由：Phase A 当前在真实 task_runner 标准路径下不会把 `force_full_recall` 传到 orchestrator，核心 producer 功能不成立；同时 FR-D3 的可配置阈值要求未满足。先修 H-A1/H-A2，并补一条不被 `_enqueue_or_run` patch 掩盖的 orchestrator 链路测试，再进入 Phase B。
