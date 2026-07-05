# F136 Implementation Plan

> 对应 spec.md。base master `662df4a7`。Phase 顺序 A → B → C → D（先核心后文案，测试紧跟，
> 收口回归 + 双评审）。

## Phase A — 服务端审批 helper + handler 接线（核心）

1. 新建 `apps/gateway/src/octoagent/gateway/services/builtin_tools/write_approval.py`：
   - `WriteApprovalOutcome`（frozen dataclass：decision ∈ {approved, rejected, timeout,
     unavailable} + approval_id + reason）。
   - `async def gate_behavior_write(deps, *, exec_ctx, file_id, resolved, old_content,
     new_content, budget_chars) -> WriteApprovalOutcome`：
     镜像 `ask_back_tools.escalate_permission_handler` 的调用序列（request_approval →
     ApprovalManager.register → mark_waiting_approval → notify_approval_request(CRITICAL) →
     wait_for_decision(300s) → 按 DP-4 条件恢复 RUNNING），差异仅：
     a) 显式拒绝也恢复 RUNNING（spec DP-4，注释说明与 escalate 的差异理由）；
     b) 超时经 `handle.operator == "system_timeout"` 区分，映射 timeout 不恢复；
     c) diff_content = unified_diff（`_DIFF_MAX_CHARS=4000` 截断）；
     d) tool_name="behavior.write_file"，side_effect_level=REVERSIBLE。
   - 模块常量 `BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS = 300.0`（与 task_runner
     approval_timeout / manager expires_at 对齐，注释锚定）。
2. `misc_tools.behavior_write_file`：REVIEW_REQUIRED 且 confirmed=true 时，在 proposal 门与
   `commit_behavior_file_write` 之间插入 `gate_behavior_write`（write.py 模块 docstring 预留的
   "门"位置）；非 approved → 按 FR-4 返回 rejected；approved → 重读 old_content → 既有写序列
   不变（结构化日志加 approval_id）。
3. `BehaviorWriteFileResult` 增 `approval_id: str = ""`（core/models/tool_results.py）。

## Phase B — 语义文案同步（FR-8）

- `agent_decision.py:801-804` 工具指引。
- handler docstring + proposal preview 文案。
- 检查 `llm_service.py:66` deferred 示例列表（F135 后 behavior.write_file 已 Core，若文案过时
  顺手校正，属同一教学面）。

## Phase C — 测试

1. 新 `apps/gateway/tests/test_f136_write_approval.py`（AC-1~AC-7、AC-9）：
   - 复用 test_f135 的 `_capture_behavior_tool` 模式（misc_tools.register + _CaptureBroker +
     bind_execution_context）。
   - 真 `ApprovalGate`（不 mock）+ resolver 协程（等 `_pending_handles` 出现后 resolve）驱动
     approve/reject；timeout 用短 timeout monkeypatch 常量。
   - RUNNING 恢复断言用 Fake console（记录 mark_waiting_approval /
     mark_running_from_waiting_approval 调用）。
2. 适配既有测试（AC-10）：
   - `test_behavior_write_golden.py` 5 处 confirmed=True → 注入 auto-approve gate helper；
     golden 的"两入口写核对齐"断言不变。
   - `test_f135_behavior_tool_exposure.py::test_behavior_write_confirmed_records_version` →
     auto-approve gate + 增断言 approval 发生。
   - `test_write_result_contract.py` 如涉 confirmed 直写同样适配。

## Phase D — 回归 + 双评审 + 文档收口

1. 全量回归（PYTHONPATH 锁 worktree + `uv run --no-sync python -m pytest`）vs `662df4a7`
   0 regression；e2e_smoke 由 pre-commit hook 验证。
2. Codex `codex review --base 662df4a7`（CLI 同步，scoped diff）+ Opus spec-对齐 review；
   finding 闭环（high/medium 全处理，分歧列人裁）。
3. living-docs：`docs/codebase-architecture/harness-and-context.md`（ApprovalGate 消费者 +
   behavior.write_file 治理描述）；completion-report + handoff（含 user_profile.replace 复用
   指引 + limitations）。
4. commit 到 worktree 分支，不 push；归总报告等用户拍板。

## 风险与回退

- 全部改动集中在 LLM 工具入口 + 新 helper + 测试；control_plane / core 写核零改动
  （除结果模型加默认字段）。单 revert `misc_tools.py` + `write_approval.py` 即回退。
- 教学文本变更影响 prompt tokens：+~40 chars，schema 不变（prefix-cache 工具段不动）。
