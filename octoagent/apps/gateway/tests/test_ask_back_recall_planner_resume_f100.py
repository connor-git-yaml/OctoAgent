"""F100 Phase F: ask_back resume 路径 recall planner 行为单测。

实测背景（phase-f-resume-trace.md）：
- ask_back 触发 WAITING_INPUT；user attach_input 唤醒后 task_runner._run_job 调用
  service.get_latest_user_metadata(task_id) 取 metadata，该 metadata 走
  TASK_SCOPED_CONTROL_KEYS allowlist，**不含 runtime_context_json**。
- 因此 turn N+1 派发时 orchestrator 收到的 metadata 不含 runtime_context_json，
  runtime_context_from_metadata(metadata) 返回 None。
- is_recall_planner_skip(None) → return False（full recall）= 预期行为（AC-F1 / N-H1 选 C）。

F112：两 helper 已删除 metadata 形参，决议完全基于 runtime_context。原"helper 忽略 resume
metadata 中 is_caller_worker_signal"的 compat 用例随形参删除在结构上得到保证（helper 不再
接触 metadata），N-H1 信号链路的端到端验证见 services/test_f101_phase_f_acceptance.py。
本文件保留 helper 层 resume 行为断言（None / unspecified → False）。
"""

from __future__ import annotations

from octoagent.core.models import RuntimeControlContext
from octoagent.gateway.services.runtime_control import (
    is_recall_planner_skip,
    is_single_loop_main_active,
)


class TestAskBackResumeRecallPlannerSkip:
    """ask_back resume 后 turn N+1 调 helper 的行为。

    实测调用链：task_runner.attach_input → _spawn_job → _run_job →
    orchestrator.dispatch(metadata=get_latest_user_metadata)。
    get_latest_user_metadata 走 TASK_SCOPED_CONTROL_KEYS allowlist，
    不含 runtime_context_json → orchestrator 接收 None runtime_context。
    """

    def test_runtime_context_none_returns_false(self) -> None:
        """resume 最常见情况：runtime_context 丢失（None）→ helper 返回 False（full recall）。"""
        assert is_recall_planner_skip(None) is False
        assert is_single_loop_main_active(None) is False

    def test_unspecified_rc_returns_false(self) -> None:
        """若 caller 显式构造 unspecified RuntimeControlContext（非 None），
        helper 仍 return False（v0.3：不 fallback；F112：无 metadata 形参）。
        """
        unspecified_rc = RuntimeControlContext(task_id="resume-task-1")  # default unspecified
        assert is_recall_planner_skip(unspecified_rc) is False
        assert is_single_loop_main_active(unspecified_rc) is False
