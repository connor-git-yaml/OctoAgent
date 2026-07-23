"""Feature 009: Worker Runtime 单元测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from octoagent.core.models import DispatchEnvelope, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.execution_console import ExecutionConsoleService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.worker_runtime import (
    WorkerCancellationRegistry,
    WorkerRuntime,
    WorkerRuntimeConfig,
)
from octoagent.provider.models import ModelCallResult, TokenUsage

from apps.gateway.tests.runtime_service_fixtures import runtime_service_fixture


class SlowLLMService:
    """用于 timeout/cancel 测试的慢速 LLM 服务。"""

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    async def call(self, prompt_or_messages, model_alias: str | None = None) -> ModelCallResult:
        await asyncio.sleep(self._delay_s)
        return ModelCallResult(
            content="slow response",
            model_alias=model_alias or "main",
            model_name="mock-slow",
            provider="mock",
            duration_ms=int(self._delay_s * 1000),
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class CapturingLLMService:
    """记录 WorkerRuntime 下发参数。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        **kwargs,
    ) -> ModelCallResult:
        self.calls.append(
            {
                "prompt_or_messages": prompt_or_messages,
                "model_alias": model_alias,
                **kwargs,
            }
        )
        return ModelCallResult(
            content="captured",
            model_alias=model_alias or "main",
            model_name="mock-capture",
            provider="mock",
            duration_ms=1,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


async def _create_task_with_envelope(tmp_path: Path, key: str) -> tuple:
    store_group = await create_store_group(
        str(tmp_path / f"{key}.db"),
        str(tmp_path / f"{key}-artifacts"),
    )
    sse_hub = SSEHub()
    service = TaskService(store_group, sse_hub, storage_only=True)
    msg = NormalizedMessage(text=f"{key}-task", idempotency_key=key)
    task_id, created = await service.create_task(msg)
    assert created is True
    envelope = DispatchEnvelope(
        dispatch_id=f"dispatch-{key}",
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        contract_version="1.0",
        route_reason="test",
        worker_capability="llm_generation",
        hop_count=1,
        max_hops=3,
        user_text=msg.text,
        model_alias="main",
    )
    return store_group, sse_hub, service, envelope


class TestWorkerRuntime:
    async def test_privileged_profile_requires_explicit_approval(self, tmp_path: Path) -> None:
        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f009-runtime-001"
        )
        envelope.tool_profile = "privileged"

        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(SlowLLMService(delay_s=0.01)).bundle,
            config=WorkerRuntimeConfig(docker_mode="disabled"),
        )
        result = await runtime.run(envelope, worker_id="worker.test")
        assert result.status == TaskStatus.FAILED
        assert result.retryable is False
        assert result.error_type == "WorkerProfileDeniedError"

        await store_group.close()

    async def test_required_docker_backend_fails_when_unavailable(self, tmp_path: Path) -> None:
        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f009-runtime-002"
        )

        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(SlowLLMService(delay_s=0.01)).bundle,
            config=WorkerRuntimeConfig(docker_mode="required"),
            docker_available_checker=lambda: False,
        )
        result = await runtime.run(envelope, worker_id="worker.test")
        assert result.status == TaskStatus.FAILED
        assert result.retryable is False
        assert result.error_type == "WorkerBackendUnavailableError"

        await store_group.close()

    async def test_max_exec_timeout_marks_task_failed(self, tmp_path: Path) -> None:
        store_group, sse_hub, service, envelope = await _create_task_with_envelope(
            tmp_path, "f009-runtime-003"
        )

        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(SlowLLMService(delay_s=0.3)).bundle,
            config=WorkerRuntimeConfig(
                docker_mode="disabled",
                max_execution_timeout_seconds=0.05,
            ),
        )
        result = await runtime.run(envelope, worker_id="worker.test")
        assert result.status == TaskStatus.FAILED
        assert result.error_type == "WorkerRuntimeTimeoutError"

        task = await service.get_task(envelope.task_id)
        assert task is not None
        assert task.status == "FAILED"

        await store_group.close()

    async def test_auth_failure_marks_worker_result_not_retryable(self, tmp_path: Path) -> None:
        """凭证失效导致的 worker FAILED 必须 retryable=False。

        2026-06-12 production OAuth 断链回归：重新授权前重派只会再次快速
        失败，operator inbox 不应展示重试入口（Codex review MED-1）。
        """
        from octoagent.skills import SkillAuthError

        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f-credfail-worker-001"
        )

        class AuthBrokenLLMService:
            async def call(self, prompt_or_messages, model_alias=None, **kwargs):
                raise SkillAuthError(
                    "provider 凭证已失效（HTTP 401），自动刷新失败，"
                    "请重新授权后重试: refresh_token_reused"
                )

        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(AuthBrokenLLMService()).bundle,
            config=WorkerRuntimeConfig(docker_mode="disabled"),
        )
        result = await runtime.run(envelope, worker_id="worker.test")

        assert result.status == TaskStatus.FAILED
        assert result.retryable is False
        assert "凭证已失效" in (result.error_message or "")

        await store_group.close()

    async def test_direct_path_auth_failure_also_not_retryable(self, tmp_path: Path) -> None:
        """直连路径凭证失效（LLMCallError 401，无 tool_selection 时
        FallbackManager re-raise）同样 retryable=False（Codex re-review MEDIUM）。
        """
        from octoagent.provider import ProviderLLMCallError

        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f-credfail-worker-003"
        )

        class DirectAuthBrokenLLMService:
            async def call(self, prompt_or_messages, model_alias=None, **kwargs):
                raise ProviderLLMCallError(
                    "api_error",
                    "HTTP 401: refresh_token_reused",
                    retriable=True,
                    status_code=401,
                )

        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(DirectAuthBrokenLLMService()).bundle,
            config=WorkerRuntimeConfig(docker_mode="disabled"),
        )
        result = await runtime.run(envelope, worker_id="worker.test")

        assert result.status == TaskStatus.FAILED
        assert result.retryable is False
        assert "凭证已失效" in (result.error_message or "")

        await store_group.close()

    async def test_generic_failure_keeps_worker_result_retryable(self, tmp_path: Path) -> None:
        """回归护栏：非 auth 的普通失败保持 retryable=True 原语义。"""
        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f-credfail-worker-002"
        )

        class GenericBrokenLLMService:
            async def call(self, prompt_or_messages, model_alias=None, **kwargs):
                raise RuntimeError("transient upstream error")

        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(GenericBrokenLLMService()).bundle,
            config=WorkerRuntimeConfig(docker_mode="disabled"),
        )
        result = await runtime.run(envelope, worker_id="worker.test")

        assert result.status == TaskStatus.FAILED
        assert result.retryable is True

        await store_group.close()

    async def test_cancel_signal_returns_cancelled_result(self, tmp_path: Path) -> None:
        store_group, sse_hub, service, envelope = await _create_task_with_envelope(
            tmp_path, "f009-runtime-004"
        )

        cancellation_registry = WorkerCancellationRegistry()
        cancellation_registry.ensure(envelope.task_id).set()
        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(SlowLLMService(delay_s=0.2)).bundle,
            config=WorkerRuntimeConfig(docker_mode="disabled"),
            cancellation_registry=cancellation_registry,
        )
        result = await runtime.run(envelope, worker_id="worker.test")
        assert result.status == TaskStatus.CANCELLED
        assert result.retryable is False

        task = await service.get_task(envelope.task_id)
        assert task is not None
        assert task.status == "CANCELLED"

        await store_group.close()

    async def test_runtime_forwards_dispatch_metadata_to_llm_service(self, tmp_path: Path) -> None:
        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f009-runtime-005"
        )
        envelope.worker_capability = "ops"
        envelope.tool_profile = "minimal"
        envelope.metadata = {
            "selected_tools_json": '["runtime.inspect"]',
            "selected_worker_type": "ops",
        }

        llm_service = CapturingLLMService()
        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(llm_service).bundle,
            config=WorkerRuntimeConfig(docker_mode="disabled"),
        )

        result = await runtime.run(envelope, worker_id="worker.test")

        assert result.status == TaskStatus.SUCCEEDED
        task_calls = [call for call in llm_service.calls if "task_id" in call]
        assert len(task_calls) == 1
        call = task_calls[0]
        assert call["task_id"] == envelope.task_id
        assert call["trace_id"] == envelope.trace_id
        assert call["worker_capability"] == "ops"
        assert call["tool_profile"] == "minimal"
        assert call["metadata"]["selected_tools_json"] == envelope.metadata["selected_tools_json"]
        assert call["metadata"]["selected_worker_type"] == envelope.metadata["selected_worker_type"]
        # agent_runtime_id / agent_session_id / context_frame_id 由 AgentContext
        # 注入，在无完整 agent context 的单元测试环境中可能为空或缺失，
        # 此处仅验证 dispatch metadata 的原始字段被正确转发。

        await store_group.close()

    async def test_graph_target_kind_uses_real_graph_backend(self, tmp_path: Path) -> None:
        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f032-runtime-graph"
        )
        envelope.metadata = {
            "target_kind": "graph_agent",
            "work_id": "work-graph-1",
            "selected_worker_type": "dev",
        }

        llm_service = CapturingLLMService()
        execution_console = ExecutionConsoleService(store_group, sse_hub)
        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(llm_service).bundle,
            config=WorkerRuntimeConfig(docker_mode="disabled"),
            execution_console=execution_console,
        )

        result = await runtime.run(envelope, worker_id="worker.graph")

        assert result.status == TaskStatus.SUCCEEDED
        assert result.backend == "graph"
        task_calls = [call for call in llm_service.calls if "task_id" in call]
        assert len(task_calls) == 1

        session = await execution_console.get_session(envelope.task_id)
        assert session is not None
        assert session.metadata["runtime_kind"] == "graph_agent"
        assert session.metadata["work_id"] == "work-graph-1"
        assert session.current_step == "graph.finalize"

        await store_group.close()

    async def test_graph_target_kind_fails_closed_when_docker_is_required(
        self, tmp_path: Path
    ) -> None:
        store_group, sse_hub, _, envelope = await _create_task_with_envelope(
            tmp_path, "f032-runtime-graph-docker-required"
        )
        envelope.metadata = {
            "target_kind": "graph_agent",
            "work_id": "work-graph-2",
            "selected_worker_type": "dev",
        }

        runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(CapturingLLMService()).bundle,
            config=WorkerRuntimeConfig(docker_mode="required"),
            docker_available_checker=lambda: True,
        )

        result = await runtime.run(envelope, worker_id="worker.graph")

        assert result.status == TaskStatus.FAILED
        assert result.error_type == "WorkerBackendUnavailableError"
        assert (
            result.error_message == "graph backend is unavailable when docker isolation is required"
        )

        await store_group.close()
