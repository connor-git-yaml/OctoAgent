"""F100 Phase F: ask_back resume 路径 recall planner 行为单测。

实测背景（phase-f-resume-trace.md）：
- ask_back 触发 WAITING_INPUT；user attach_input 唤醒后 task_runner._run_job 调用
  service.get_latest_user_metadata(task_id) 取 metadata，该 metadata 走
  TASK_SCOPED_CONTROL_KEYS allowlist，**不含 runtime_context_json**。
- 因此 turn N+1 派发时 orchestrator 收到的 metadata 不含 runtime_context_json，
  runtime_context_from_metadata(metadata) 返回 None。
- is_recall_planner_skip(None, metadata)：
  - F091 baseline：fallback metadata_flag → metadata 缺 single_loop_executor → False
  - F100 v0.3：return False（移除 fallback；与 baseline 等价）

本测试覆盖 AC-5 + AC-N-H1-COMPAT + AC-12（ask_back / source_runtime_kind 不破）的
helper 层行为；不真跑 worker.ask_back 工具（F099 测试已覆盖工具层）。
"""

from __future__ import annotations

import pytest

from octoagent.core.models import RuntimeControlContext
from octoagent.gateway.services.runtime_control import (
    is_recall_planner_skip,
    is_single_loop_main_active,
)


# ============================================================
# AC-5: ask_back resume turn N+1 行为（v0.3 修订后等价于 baseline）
# ============================================================


class TestAskBackResumeRecallPlannerSkip:
    """ask_back resume 后 turn N+1 调 helper 的行为。

    实测调用链：task_runner.attach_input → _spawn_job → _run_job →
    orchestrator.dispatch(metadata=get_latest_user_metadata).
    get_latest_user_metadata 走 TASK_SCOPED_CONTROL_KEYS allowlist，
    不含 runtime_context_json → orchestrator 接收 None runtime_context。
    """

    def test_runtime_context_none_metadata_empty(self) -> None:
        """resume 时最常见情况：metadata 仅含 TASK_SCOPED_CONTROL_KEYS allowlist
        字段（不含 single_loop_executor / runtime_context_json）。helper 返回 False。
        """
        # 模拟 get_latest_user_metadata 返回的 allowlisted metadata
        resume_metadata = {
            "session_id": "session-test-1",
            "thread_id": "thread-test-1",
            "is_caller_worker_signal": "1",  # F099 N-H1 修复
        }
        assert is_recall_planner_skip(None, resume_metadata) is False
        assert is_single_loop_main_active(None, resume_metadata) is False

    def test_unspecified_rc_resume_metadata(self) -> None:
        """若 caller 显式构造 unspecified RuntimeControlContext（非 None），
        helper 仍 return False（v0.3 修订：不 fallback metadata flag）。
        """
        unspecified_rc = RuntimeControlContext(task_id="resume-task-1")  # default unspecified
        resume_metadata = {
            "session_id": "session-test-1",
            "is_caller_worker_signal": "1",
        }
        assert is_recall_planner_skip(unspecified_rc, resume_metadata) is False
        assert is_single_loop_main_active(unspecified_rc, resume_metadata) is False


# ============================================================
# AC-N-H1-COMPAT: F099 is_caller_worker_signal 透传不破
# ============================================================


class TestAskBackIsCallerWorkerSignalCompat:
    """F099 N-H1 修复机制（CONTROL_METADATA_UPDATED 事件 + resume_state_snapshot
    透传 is_caller_worker_signal）与 F100 改动正交。helper 层不读这个字段，
    所以 F100 helper 修改不影响 F099 信号链路。
    """

    def test_helper_ignores_is_caller_worker_signal(self) -> None:
        """F100 helper 不读 is_caller_worker_signal——该信号给 WorkerRuntime 用。"""
        metadata_with_signal = {"is_caller_worker_signal": "1"}
        metadata_without = {}
        # 含 signal vs 不含：helper 返回值一致（F100 helper 与该信号无关）
        assert is_recall_planner_skip(None, metadata_with_signal) is False
        assert is_recall_planner_skip(None, metadata_without) is False
        assert is_single_loop_main_active(None, metadata_with_signal) is False
        assert is_single_loop_main_active(None, metadata_without) is False


# ============================================================
# baseline 等价性：v0.3 行为与 F091 baseline 行为对照
# ============================================================


class TestV03BaselineEquivalence:
    """v0.3 修订后 unspecified/None 路径的行为等价于 baseline metadata_flag 缺失时
    的默认 False。这是 F100 HIGH-3 自动闭环的关键。
    """

    @pytest.mark.parametrize(
        "metadata",
        [
            {},  # 空 metadata
            {"session_id": "s1"},  # 仅 task-scoped key
            {"thread_id": "t1", "is_caller_worker_signal": "1"},  # F099 allowlist
        ],
    )
    def test_none_runtime_context_returns_false(
        self, metadata: dict
    ) -> None:
        """resume 时 metadata 不含 single_loop_executor → return False。"""
        assert is_recall_planner_skip(None, metadata) is False
        assert is_single_loop_main_active(None, metadata) is False
