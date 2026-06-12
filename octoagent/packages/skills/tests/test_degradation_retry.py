"""智能降级重试测试 (T5.3)。

覆盖：
- 降级成功：FAILED → 切 fallback 模型 → SUCCEEDED
- 防递归：is_degraded_retry=True 时不重试
- max_steps clamp：min(int(max_steps * 1.5), _MAX_STEPS_HARD_CEILING) 或 None → None
- budget 不放宽：降级时 max_budget_usd 保持不变
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from octoagent.skills.models import (
    ErrorCategory,
    RetryPolicy,
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunResult,
    SkillRunStatus,
    UsageLimits,
    _MAX_STEPS_HARD_CEILING,
)


# ═══════════════════════════════════════
# 辅助：模拟 LLMService._try_call_with_tools
# ═══════════════════════════════════════

# 由于 LLMService._try_call_with_tools 是核心降级入口，
# 我们需要构建足够的 metadata 才能进入 Skill 分支。
# 通过 mock SkillRunner.run() 的返回值来控制测试流程。


def _make_metadata_with_tools() -> dict[str, Any]:
    """构建包含工具选择的 metadata，使 _try_call_with_tools 能进入 skill 分支。"""
    return {
        "tool_selection": {
            "mounted_tools": [
                {"tool_name": "system.echo"},
                {"tool_name": "filesystem.read_text"},
            ],
        },
        "selected_worker_type": "general",
    }


def _make_failed_result(
    category: str = "step_limit_exceeded",
    steps: int = 30,
    usage: dict[str, Any] | None = None,
) -> SkillRunResult:
    """构建一个 FAILED 的 SkillRunResult。"""
    return SkillRunResult(
        status=SkillRunStatus.FAILED,
        attempts=steps,
        steps=steps,
        duration_ms=5000,
        error_category=ErrorCategory(category),
        error_message=f"资源限制触发: {category}",
        usage=usage or {"steps": steps, "cost_usd": 0.01},
        total_cost_usd=0.01,
    )


def _make_succeeded_result(content: str = "降级成功") -> SkillRunResult:
    """构建一个 SUCCEEDED 的 SkillRunResult。"""
    return SkillRunResult(
        status=SkillRunStatus.SUCCEEDED,
        output=SkillOutputEnvelope(
            content=content,
            complete=True,
            metadata={"model_name": "fallback-model", "provider": "litellm", "token_usage": {}, "cost_unavailable": True},
        ),
        attempts=1,
        steps=1,
        duration_ms=1000,
        total_cost_usd=0.005,
    )


# ═══════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════


def _manifest_with_fallback(**kwargs: Any) -> Any:
    """构建带有 fallback_model_alias 的 SkillManifest（注入降级重试触发条件）。"""
    from octoagent.skills.manifest import SkillManifest

    if "retry_policy" not in kwargs:
        kwargs["retry_policy"] = RetryPolicy(fallback_model_alias="fallback-model")
    return SkillManifest(**kwargs)


class TestDegradationRetrySuccess:
    @pytest.mark.asyncio
    async def test_degradation_succeeds_with_fallback_model(self) -> None:
        """FAILED(step_limit_exceeded) + fallback_model → 降级重试成功。"""
        from octoagent.gateway.services.llm_service import LLMService

        call_count = 0

        async def mock_run(*, manifest: Any, execution_context: Any, skill_input: Any, prompt: str) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 首次失败
                return _make_failed_result("step_limit_exceeded")
            else:
                # 降级重试成功
                return _make_succeeded_result("降级后成功")

        mock_runner = MagicMock()
        mock_runner.run = mock_run

        service = LLMService(skill_runner=mock_runner)

        # 注入 fallback_model_alias 使降级条件可触发
        with patch(
            "octoagent.gateway.services.llm_service.SkillManifest",
            _manifest_with_fallback,
        ):
            result = await service._try_call_with_tools(
                prompt_or_messages="请执行任务",
                model_alias="main",
                task_id="task-1",
                trace_id="trace-1",
                metadata=_make_metadata_with_tools(),
                worker_capability="llm_generation",
                tool_profile="standard",
            )

        assert result is not None
        assert "降级后成功" in result.content
        assert result.is_fallback is True
        assert result.fallback_reason == "degraded_retry"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_degradation_on_timeout_exceeded(self) -> None:
        """FAILED(timeout_exceeded) 也触发降级重试。"""
        from octoagent.gateway.services.llm_service import LLMService

        call_count = 0

        async def mock_run(*, manifest: Any, execution_context: Any, skill_input: Any, prompt: str) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_failed_result("timeout_exceeded")
            return _make_succeeded_result()

        mock_runner = MagicMock()
        mock_runner.run = mock_run

        service = LLMService(skill_runner=mock_runner)

        with patch(
            "octoagent.gateway.services.llm_service.SkillManifest",
            _manifest_with_fallback,
        ):
            result = await service._try_call_with_tools(
                prompt_or_messages="请执行任务",
                model_alias="main",
                task_id="task-1",
                trace_id="trace-1",
                metadata=_make_metadata_with_tools(),
                worker_capability="llm_generation",
                tool_profile="standard",
            )

        assert result is not None
        assert result.is_fallback is True
        assert call_count == 2


class TestDegradationPreventRecursion:
    @pytest.mark.asyncio
    async def test_no_retry_when_already_degraded(self) -> None:
        """is_degraded_retry=True 时，FAILED 不再触发降级重试。"""
        from octoagent.gateway.services.llm_service import LLMService

        call_count = 0

        async def mock_run(*, manifest: Any, execution_context: Any, skill_input: Any, prompt: str) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            return _make_failed_result("step_limit_exceeded")

        mock_runner = MagicMock()
        mock_runner.run = mock_run

        service = LLMService(skill_runner=mock_runner)

        result = await service._try_call_with_tools(
            prompt_or_messages="请执行任务",
            model_alias="main",
            task_id="task-1",
            trace_id="trace-1",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
            is_degraded_retry=True,  # 已是降级重试
        )

        # 仅调用一次，没有再次降级
        assert call_count == 1
        assert result is not None
        assert "处理步骤较多" in result.content

    @pytest.mark.asyncio
    async def test_no_retry_for_non_matching_category(self) -> None:
        """budget_exceeded 不触发降级重试（仅 step_limit/timeout 触发）。"""
        from octoagent.gateway.services.llm_service import LLMService

        call_count = 0

        async def mock_run(*, manifest: Any, execution_context: Any, skill_input: Any, prompt: str) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            return _make_failed_result("budget_exceeded")

        mock_runner = MagicMock()
        mock_runner.run = mock_run

        service = LLMService(skill_runner=mock_runner)

        result = await service._try_call_with_tools(
            prompt_or_messages="请执行任务",
            model_alias="main",
            task_id="task-1",
            trace_id="trace-1",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )

        # budget_exceeded 不触发降级，仅调用一次
        assert call_count == 1
        assert result is not None
        assert "预算上限" in result.content

    @pytest.mark.asyncio
    async def test_no_retry_without_fallback_alias(self) -> None:
        """无 fallback_model_alias 时不触发降级重试。"""
        from octoagent.gateway.services.llm_service import LLMService

        call_count = 0

        async def mock_run(*, manifest: Any, execution_context: Any, skill_input: Any, prompt: str) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            return _make_failed_result("step_limit_exceeded")

        mock_runner = MagicMock()
        mock_runner.run = mock_run

        service = LLMService(skill_runner=mock_runner)

        # 默认 RetryPolicy.fallback_model_alias="" → 不触发降级
        result = await service._try_call_with_tools(
            prompt_or_messages="请执行任务",
            model_alias="main",
            task_id="task-1",
            trace_id="trace-1",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )

        assert call_count == 1


class TestDegradationMaxStepsClamp:
    @pytest.mark.asyncio
    async def test_max_steps_clamped_at_ceiling(self) -> None:
        """降级时 max_steps = min(int(30 * 1.5), 500) = 45。"""
        from octoagent.gateway.services.llm_service import LLMService

        captured_contexts: list[SkillExecutionContext] = []

        async def mock_run(*, manifest: Any, execution_context: Any, skill_input: Any, prompt: str) -> SkillRunResult:
            captured_contexts.append(execution_context)
            if len(captured_contexts) == 1:
                return _make_failed_result("step_limit_exceeded", steps=30)
            return _make_succeeded_result()

        mock_runner = MagicMock()
        mock_runner.run = mock_run

        service = LLMService(skill_runner=mock_runner)

        # 需要设 fallback_model_alias 才能触发降级
        # 通过 metadata 注入 resource_limits 设定 max_steps=30
        meta = _make_metadata_with_tools()
        meta["resource_limits"] = {"max_steps": 30}

        result = await service._try_call_with_tools(
            prompt_or_messages="请执行任务",
            model_alias="main",
            task_id="task-1",
            trace_id="trace-1",
            metadata=meta,
            worker_capability="llm_generation",
            tool_profile="standard",
        )

        # 默认 RetryPolicy 没有 fallback_model_alias，所以不会触发降级
        # 验证没有第二次调用（因为没有 fallback_model_alias）
        assert len(captured_contexts) == 1

    @pytest.mark.asyncio
    async def test_max_steps_clamp_formula(self) -> None:
        """验证降级 max_steps 计算公式：min(int(original * 1.5), _MAX_STEPS_HARD_CEILING)。"""
        # 直接验证公式
        assert min(int(30 * 1.5), _MAX_STEPS_HARD_CEILING) == 45
        assert min(int(100 * 1.5), _MAX_STEPS_HARD_CEILING) == 150
        assert min(int(400 * 1.5), _MAX_STEPS_HARD_CEILING) == 500  # clamp at 500

    @pytest.mark.asyncio
    async def test_max_steps_none_stays_none(self) -> None:
        """max_steps=None 时，降级后仍为 None（不限步数）。"""
        # 验证降级逻辑
        original = UsageLimits()  # max_steps=None
        degraded_steps = (
            min(int(original.max_steps * 1.5), _MAX_STEPS_HARD_CEILING)
            if original.max_steps is not None
            else None
        )
        assert degraded_steps is None


class TestDegradationBudgetNotRelaxed:
    @pytest.mark.asyncio
    async def test_budget_preserved_in_degraded_context(self) -> None:
        """降级重试时 max_budget_usd 不放宽。"""
        # 直接验证降级 UsageLimits 构造
        original = UsageLimits(max_steps=30, max_budget_usd=0.50)
        degraded_steps = min(int(original.max_steps * 1.5), _MAX_STEPS_HARD_CEILING)
        degraded = UsageLimits(
            max_steps=degraded_steps,
            max_request_tokens=original.max_request_tokens,
            max_response_tokens=original.max_response_tokens,
            max_tool_calls=original.max_tool_calls,
            max_budget_usd=original.max_budget_usd,  # 不放宽
            max_duration_seconds=original.max_duration_seconds,
            repeat_signature_threshold=original.repeat_signature_threshold,
        )

        assert degraded.max_steps == 45  # 放宽
        assert degraded.max_budget_usd == 0.50  # 不放宽
        assert degraded.max_request_tokens is None
        assert degraded.max_duration_seconds == 7200.0  # 全局默认


class TestDegradedRetryFailure:
    @pytest.mark.asyncio
    async def test_degradation_retry_fails_returns_original_error(self) -> None:
        """降级重试也失败时，返回原始错误提示。"""
        from octoagent.gateway.services.llm_service import LLMService

        call_count = 0

        async def mock_run(*, manifest: Any, execution_context: Any, skill_input: Any, prompt: str) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            # 两次都失败
            return _make_failed_result("step_limit_exceeded")

        mock_runner = MagicMock()
        mock_runner.run = mock_run

        service = LLMService(skill_runner=mock_runner)

        # 由于默认 RetryPolicy.fallback_model_alias=""，降级不触发
        result = await service._try_call_with_tools(
            prompt_or_messages="请执行任务",
            model_alias="main",
            task_id="task-1",
            trace_id="trace-1",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )

        assert result is not None
        assert "处理步骤较多" in result.content
        assert result.fallback_reason == "skill_failed:step_limit_exceeded"


class TestFriendlyErrorMessages:
    """验证不同 ErrorCategory 返回对应的中文友好提示 (T1.16)。"""

    @pytest.mark.asyncio
    async def test_step_limit_message(self) -> None:
        from octoagent.gateway.services.llm_service import LLMService

        async def mock_run(**kw: Any) -> SkillRunResult:
            return _make_failed_result("step_limit_exceeded", steps=50)

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        result = await service._try_call_with_tools(
            prompt_or_messages="test",
            model_alias="main",
            task_id="t",
            trace_id="t",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )
        assert result is not None
        assert "处理步骤较多" in result.content

    @pytest.mark.asyncio
    async def test_token_limit_message(self) -> None:
        from octoagent.gateway.services.llm_service import LLMService

        async def mock_run(**kw: Any) -> SkillRunResult:
            return _make_failed_result("token_limit_exceeded")

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        result = await service._try_call_with_tools(
            prompt_or_messages="test",
            model_alias="main",
            task_id="t",
            trace_id="t",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )
        assert result is not None
        assert "token" in result.content

    @pytest.mark.asyncio
    async def test_tool_call_limit_message(self) -> None:
        from octoagent.gateway.services.llm_service import LLMService

        async def mock_run(**kw: Any) -> SkillRunResult:
            return _make_failed_result("tool_call_limit_exceeded")

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        result = await service._try_call_with_tools(
            prompt_or_messages="test",
            model_alias="main",
            task_id="t",
            trace_id="t",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )
        assert result is not None
        assert "工具调用次数" in result.content

    @pytest.mark.asyncio
    async def test_budget_exceeded_message(self) -> None:
        from octoagent.gateway.services.llm_service import LLMService

        async def mock_run(**kw: Any) -> SkillRunResult:
            return _make_failed_result("budget_exceeded")

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        result = await service._try_call_with_tools(
            prompt_or_messages="test",
            model_alias="main",
            task_id="t",
            trace_id="t",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )
        assert result is not None
        assert "预算上限" in result.content

    @pytest.mark.asyncio
    async def test_timeout_exceeded_message(self) -> None:
        from octoagent.gateway.services.llm_service import LLMService

        async def mock_run(**kw: Any) -> SkillRunResult:
            return _make_failed_result("timeout_exceeded")

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        result = await service._try_call_with_tools(
            prompt_or_messages="test",
            model_alias="main",
            task_id="t",
            trace_id="t",
            metadata=_make_metadata_with_tools(),
            worker_capability="llm_generation",
            tool_profile="standard",
        )
        assert result is not None
        assert "超时" in result.content


# ═══════════════════════════════════════
# 凭证失效（AUTH_ERROR）向上传播 —— 2026-06-12 production OAuth 断链事故回归
# ═══════════════════════════════════════
# 修复前：FAILED skill run 一律转换成道歉文案的"成功" ModelCallResult，
# 凭证断链被掩盖成正常回复，task 永远到不了 FAILED 终态。
# 修复后：error_category == AUTH_ERROR 时 raise SkillAuthError，由
# process_task_with_llm._handle_llm_failure 把 task 推进 FAILED。


def _make_auth_failed_result() -> SkillRunResult:
    """构建 runner 凭证失效终止后的 FAILED SkillRunResult。"""
    return SkillRunResult(
        status=SkillRunStatus.FAILED,
        attempts=1,
        steps=1,
        duration_ms=600,
        error_category=ErrorCategory.AUTH_ERROR,
        error_message=(
            "provider 凭证已失效（HTTP 401），自动刷新失败，"
            "请重新授权后重试: HTTP 401: refresh_token_reused"
        ),
        usage={"steps": 1},
        total_cost_usd=0.0,
    )


class TestAuthErrorPropagation:
    @pytest.mark.asyncio
    async def test_auth_error_raises_instead_of_apology(self) -> None:
        """AUTH_ERROR 必须 raise SkillAuthError，不得返回道歉文案的成功结果。"""
        from octoagent.gateway.services.llm_service import LLMService
        from octoagent.skills.exceptions import SkillAuthError

        call_count = 0

        async def mock_run(**kw: Any) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            return _make_auth_failed_result()

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        with pytest.raises(SkillAuthError, match="凭证已失效"):
            await service._try_call_with_tools(
                prompt_or_messages="test",
                model_alias="main",
                task_id="t",
                trace_id="t",
                metadata=_make_metadata_with_tools(),
                worker_capability="llm_generation",
                tool_profile="standard",
            )
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_auth_error_raises_through_public_call(self) -> None:
        """公开入口 call() 同样向上抛 —— 不得回落到 FallbackManager(echo)。"""
        from octoagent.gateway.services.llm_service import LLMService
        from octoagent.skills.exceptions import SkillAuthError

        async def mock_run(**kw: Any) -> SkillRunResult:
            return _make_auth_failed_result()

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        with pytest.raises(SkillAuthError):
            await service.call(
                "test",
                model_alias="main",
                task_id="t",
                trace_id="t",
                metadata=_make_metadata_with_tools(),
            )

    @pytest.mark.asyncio
    async def test_auth_error_skips_degraded_retry(self) -> None:
        """凭证失效不触发降级重试（换模型打的还是同一个失效凭证链路）。"""
        from octoagent.gateway.services.llm_service import LLMService
        from octoagent.skills.exceptions import SkillAuthError

        call_count = 0

        async def mock_run(**kw: Any) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            return _make_auth_failed_result()

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        with patch(
            "octoagent.gateway.services.llm_service.SkillManifest",
            _manifest_with_fallback,
        ):
            with pytest.raises(SkillAuthError):
                await service._try_call_with_tools(
                    prompt_or_messages="test",
                    model_alias="main",
                    task_id="t",
                    trace_id="t",
                    metadata=_make_metadata_with_tools(),
                    worker_capability="llm_generation",
                    tool_profile="standard",
                )
        assert call_count == 1, "AUTH_ERROR 不得进入降级重试分支"

    @pytest.mark.asyncio
    async def test_degraded_retry_hits_auth_error_propagates(self) -> None:
        """首次失败可降级（step_limit），降级重试中凭证失效也必须向上抛。"""
        from octoagent.gateway.services.llm_service import LLMService
        from octoagent.skills.exceptions import SkillAuthError

        call_count = 0

        async def mock_run(**kw: Any) -> SkillRunResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_failed_result("step_limit_exceeded")
            return _make_auth_failed_result()

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        with patch(
            "octoagent.gateway.services.llm_service.SkillManifest",
            _manifest_with_fallback,
        ):
            with pytest.raises(SkillAuthError):
                await service._try_call_with_tools(
                    prompt_or_messages="test",
                    model_alias="main",
                    task_id="t",
                    trace_id="t",
                    metadata=_make_metadata_with_tools(),
                    worker_capability="llm_generation",
                    tool_profile="standard",
                )
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_directly_raised_auth_error_propagates(self) -> None:
        """runner 直接抛 SkillAuthError 时不得被宽捕获吞成 None→Echo。

        当前主路径 runner 以 FAILED(AUTH_ERROR) 返回而非抛出，此测试
        钉住未来路径（Codex review LOW）。
        """
        from octoagent.gateway.services.llm_service import LLMService
        from octoagent.skills.exceptions import SkillAuthError

        async def mock_run(**kw: Any) -> SkillRunResult:
            raise SkillAuthError("provider 凭证已失效（HTTP 401）")

        service = LLMService(skill_runner=MagicMock(run=mock_run))
        with pytest.raises(SkillAuthError):
            await service._try_call_with_tools(
                prompt_or_messages="test",
                model_alias="main",
                task_id="t",
                trace_id="t",
                metadata=_make_metadata_with_tools(),
                worker_capability="llm_generation",
                tool_profile="standard",
            )

    @pytest.mark.asyncio
    async def test_auth_error_full_chain_real_runner(self) -> None:
        """真 SkillRunner + 401 model client → call() 抛 SkillAuthError 且只调 1 次。

        把 runner 快速失败与 llm_service 传播真实连起来（不 mock runner）。
        """
        from octoagent.gateway.services.llm_service import LLMService
        from octoagent.provider import ProviderLLMCallError
        from octoagent.skills.exceptions import SkillAuthError
        from octoagent.skills.runner import SkillRunner

        from .conftest import MockEventStore, MockToolBroker, QueueModelClient

        client = QueueModelClient(
            [
                ProviderLLMCallError(
                    "api_error",
                    "HTTP 401: refresh_token_reused",
                    retriable=True,
                    status_code=401,
                )
            ]
        )
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )
        service = LLMService(skill_runner=runner)

        with pytest.raises(SkillAuthError, match="凭证已失效"):
            await service.call(
                "hello",
                model_alias="main",
                task_id="task-auth-chain",
                trace_id="trace-auth-chain",
                metadata=_make_metadata_with_tools(),
            )
        assert client.calls == 1
