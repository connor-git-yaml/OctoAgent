# F101 Per-Phase D Codex Review

## Summary
- HIGH: 1
- MEDIUM: 0
- LOW: 1
- 总体评估：FIX_HIGH_FIRST

## 6 维度评估
1. FR-C4 integration test 真实性: PARTIAL。测试确实使用真实 SQLite StoreGroup、EventStore、ExecutionConsoleService，并通过真实 ask_back handler + attach_input 路径验证下游链路；MED-01 spy 也调用原始 TaskService._write_state_transition。问题是 AC-C4 声称的完整事件链没有覆盖 USER_MESSAGE 起点，见 D-H1。
2. FR-C5 guard 完整性: PASS。ask_back / request_input / escalate_permission 三工具均有 is_caller_worker=False 的 else 分支，并对非 RUNNING task 返回对应降级值。
3. M-1 broad-catch: PASS。三处 guard broad-catch 均改为 `except Exception as exc`，且 `log.debug(..., exc_info=True)` 存在。
4. FR-C7 __all__: PASS。`source_kinds.py` 显式列出 11 个符号；直接按文件 import 后执行 star import，实际只导出这 11 个大写符号。
5. 跨 Phase 一致性: PASS。Phase D 只在 escalate_permission 入口增加非 worker guard 和 debug 日志；worker RUNNING 路径下 Phase B v4 的 WAITING_APPROVAL 状态机和 Phase C NotificationService 调用位置未被改动。
6. 测试质量: PARTIAL。8 个测试路径基本独立，mock 边界总体合理；但 AC-C5 只测 ask_back 的非 worker guard，未直接覆盖 request_input / escalate_permission 的新增非 worker 分支，见 D-L1。

## Finding 列表

### D-H1 / HIGH
- 位置: `octoagent/apps/gateway/tests/services/test_f101_ask_back_integration.py:70`
- 位置: `octoagent/apps/gateway/tests/services/test_f101_ask_back_integration.py:121`
- 位置: `octoagent/apps/gateway/tests/services/test_f101_ask_back_integration.py:343`
- 描述: AC-C4 integration test 没有证明声明的完整事件链 `USER_MESSAGE -> CONTROL_METADATA_UPDATED ask_back -> ATTACH_INPUT -> resume`。`_ensure_task()` 直接调用 `task_store.create_task()` 创建 projection，没有走 TaskService 创建入口，也没有写入 USER_MESSAGE；`test_ac_c4_event_chain_completeness` 后续只断言 CONTROL_METADATA_UPDATED / EXECUTION_INPUT_REQUESTED / EXECUTION_INPUT_ATTACHED / STATE_TRANSITION，未断言 USER_MESSAGE 存在或顺序。因此该测试能证明 ask_back 下游等待输入链路，但不能作为完整事件链 gate。
- 推荐方向: 用 TaskService 创建初始 task 或显式追加 USER_MESSAGE，并在 EventStore 查询中断言 USER_MESSAGE、CONTROL_METADATA_UPDATED、EXECUTION_INPUT_ATTACHED、恢复 RUNNING 的 STATE_TRANSITION 的存在和相对顺序。

### D-L1 / LOW
- 位置: `octoagent/apps/gateway/tests/services/test_f101_ask_back_integration.py:490`
- 位置: `octoagent/apps/gateway/tests/services/test_f101_ask_back_integration.py:574`
- 位置: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:306`
- 位置: `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py:421`
- 描述: AC-C5 测试只调用 `worker.ask_back` 的非 worker guard 两个场景；Phase D 同时新增了 `worker.request_input` 和 `worker.escalate_permission` 的非 worker 分支，但新测试没有直接执行这两个 handler。实现代码经人工检查是完整的，所以这是覆盖缺口，不是当前生产 bug。
- 推荐方向: 将 AC-C5 非 worker guard 测试参数化到三工具，分别断言 request_input 返回 `""`、escalate_permission 返回 `"rejected"`，并覆盖 guard exception + task 非 RUNNING 两类路径。

## 整体结论
不可 commit + 进入 Phase E。先修 D-H1，使 AC-C4 测试真正覆盖 USER_MESSAGE 起点的完整事件链；D-L1 可随同补齐，避免三工具 guard 覆盖不对称。
