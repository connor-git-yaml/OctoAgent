"""models.py 单元测试。"""

from octoagent.skills.models import (
    ContextBudgetPolicy,
    ErrorCategory,
    LoopGuardPolicy,
    RetryPolicy,
    SkillOutputEnvelope,
    SkillRunResult,
    SkillRunStatus,
    ToolCallSpec,
)


def test_retry_policy_defaults() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.backoff_ms == 500
    assert policy.upgrade_model_on_fail is False


def test_loop_guard_defaults() -> None:
    policy = LoopGuardPolicy()
    assert policy.max_steps == 30
    assert policy.repeat_signature_threshold == 3


def test_context_budget_defaults() -> None:
    policy = ContextBudgetPolicy()
    assert policy.max_chars == 1500
    assert policy.summary_chars == 240


def test_output_envelope_with_tool_call() -> None:
    envelope = SkillOutputEnvelope(
        content="call tool",
        tool_calls=[ToolCallSpec(tool_name="system.echo", arguments={"text": "hi"})],
    )
    assert envelope.complete is False
    assert envelope.tool_calls[0].tool_name == "system.echo"


def test_skill_run_result_failed() -> None:
    result = SkillRunResult(
        status=SkillRunStatus.FAILED,
        attempts=3,
        steps=3,
        duration_ms=100,
        error_category=ErrorCategory.VALIDATION_ERROR,
        error_message="bad output",
    )
    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.VALIDATION_ERROR
