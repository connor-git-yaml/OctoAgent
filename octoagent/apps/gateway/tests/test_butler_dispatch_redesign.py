"""Feature 064 Phase 1: Butler Dispatch Redesign 测试。

验证 Butler Direct Execution 路径：
- _resolve_butler_decision() 跳过 model decision
- _should_butler_direct_execute() 资格判断
- _dispatch_butler_direct_execution() 完整链路
- Event 链完整性
- 回归安全（天气/位置路径、Worker 路径不受影响）
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from octoagent.core.models import (
    ButlerDecisionMode,
    TaskStatus,
    WorkerExecutionStatus,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.orchestrator import OrchestratorService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.provider import ModelCallResult, TokenUsage


# ---------------------------------------------------------------------------
# 测试用 LLM 服务 Mock
# ---------------------------------------------------------------------------


class _DirectExecutionLLMService:
    """模拟支持 single loop executor 的 LLM 服务。

    对所有请求返回固定文本回复，记录调用详情。
    """

    supports_single_loop_executor = True
    supports_butler_decision_phase = False

    def __init__(self, content: str = "Butler 直接回复。") -> None:
        self._content = content
        self.calls: list[dict[str, object]] = []

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
    ) -> ModelCallResult:
        self.calls.append(
            {
                "model_alias": model_alias,
                "metadata": dict(metadata or {}),
                "tool_profile": tool_profile,
                "worker_capability": worker_capability,
            }
        )
        return ModelCallResult(
            content=self._content,
            model_alias=model_alias or "main",
            model_name="test-direct-model",
            provider="tests",
            duration_ms=10,
            token_usage=TokenUsage(
                prompt_tokens=20,
                completion_tokens=15,
                total_tokens=35,
            ),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class _NoSingleLoopLLMService:
    """不支持 single loop executor 的 LLM 服务。"""

    supports_single_loop_executor = False
    supports_butler_decision_phase = False

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
    ) -> ModelCallResult:
        return ModelCallResult(
            content="fallback reply",
            model_alias=model_alias or "main",
            model_name="test-no-single-loop",
            provider="tests",
            duration_ms=5,
            token_usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=8,
                total_tokens=18,
            ),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


async def _build_test_context(
    tmp_path: Path,
    llm_service=None,
):
    """构建测试用 store + task_service + orchestrator。"""
    store_group = await create_store_group(
        str(tmp_path / "dispatch-redesign.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    resolved_llm = llm_service or _DirectExecutionLLMService()
    task_service = TaskService(store_group, sse_hub)
    orchestrator = OrchestratorService(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=resolved_llm,
    )
    return store_group, task_service, orchestrator, resolved_llm


async def _create_and_dispatch(
    tmp_path: Path,
    user_text: str,
    llm_service=None,
    metadata: dict | None = None,
) -> tuple:
    """创建任务并 dispatch，返回 (store_group, result, llm_service)。"""
    store_group, task_service, orchestrator, llm = await _build_test_context(
        tmp_path, llm_service=llm_service
    )
    msg = NormalizedMessage(
        text=user_text,
        idempotency_key=f"f064-{user_text[:10]}",
    )
    task_id, _ = await task_service.create_task(msg)
    result = await orchestrator.dispatch(
        task_id=task_id,
        user_text=user_text,
        metadata=metadata,
    )
    return store_group, result, llm, task_id


# ---------------------------------------------------------------------------
# Phase 3 US1: 核心路径测试
# ---------------------------------------------------------------------------


class TestResolveButlerDecisionSkipsModelDecision:
    """T003.1: 验证 _resolve_butler_decision() 仅使用规则决策，无 LLM 预路由调用。"""

    async def test_resolve_butler_decision_skips_model_decision(
        self, tmp_path: Path
    ) -> None:
        store_group, task_service, orchestrator, llm = await _build_test_context(
            tmp_path
        )
        msg = NormalizedMessage(text="你好", idempotency_key="f064-skip-model-001")
        task_id, _ = await task_service.create_task(msg)

        from octoagent.core.models import OrchestratorRequest

        request = OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="你好",
            worker_capability="llm_generation",
            contract_version="1.0",
            hop_count=0,
            max_hops=3,
            tool_profile="standard",
            runtime_context=None,
            metadata={},
        )

        decision, metadata_updates = await orchestrator._resolve_butler_decision(
            request
        )

        # 规则决策返回 DIRECT_ANSWER → (None, {})，无 model decision LLM 调用
        assert decision is None
        assert metadata_updates == {}
        await store_group.conn.close()


class TestShouldButlerDirectExecute:
    """T003.2 + T003.3: 验证 _should_butler_direct_execute() 资格判断。"""

    async def test_eligible_normal_request(self, tmp_path: Path) -> None:
        _, _, orchestrator, _ = await _build_test_context(tmp_path)
        from octoagent.core.models import OrchestratorRequest

        request = OrchestratorRequest(
            task_id="task-001",
            trace_id="trace-001",
            user_text="Hello",
            worker_capability="llm_generation",
            contract_version="1.0",
            hop_count=0,
            max_hops=3,
            tool_profile="standard",
            runtime_context=None,
            metadata={},
        )
        assert orchestrator._should_butler_direct_execute(request) is True

    async def test_ineligible_subtask(self, tmp_path: Path) -> None:
        _, _, orchestrator, _ = await _build_test_context(tmp_path)
        from octoagent.core.models import OrchestratorRequest

        request = OrchestratorRequest(
            task_id="task-002",
            trace_id="trace-002",
            user_text="Hello",
            worker_capability="llm_generation",
            contract_version="1.0",
            hop_count=0,
            max_hops=3,
            tool_profile="standard",
            runtime_context=None,
            metadata={"parent_task_id": "task-001"},
        )
        assert orchestrator._should_butler_direct_execute(request) is False

    async def test_ineligible_spawned(self, tmp_path: Path) -> None:
        _, _, orchestrator, _ = await _build_test_context(tmp_path)
        from octoagent.core.models import OrchestratorRequest

        request = OrchestratorRequest(
            task_id="task-003",
            trace_id="trace-003",
            user_text="Hello",
            worker_capability="llm_generation",
            contract_version="1.0",
            hop_count=0,
            max_hops=3,
            tool_profile="standard",
            runtime_context=None,
            metadata={"spawned_by": "worker-001"},
        )
        assert orchestrator._should_butler_direct_execute(request) is False

    async def test_ineligible_no_single_loop(self, tmp_path: Path) -> None:
        _, _, orchestrator, _ = await _build_test_context(
            tmp_path, llm_service=_NoSingleLoopLLMService()
        )
        from octoagent.core.models import OrchestratorRequest

        request = OrchestratorRequest(
            task_id="task-004",
            trace_id="trace-004",
            user_text="Hello",
            worker_capability="llm_generation",
            contract_version="1.0",
            hop_count=0,
            max_hops=3,
            tool_profile="standard",
            runtime_context=None,
            metadata={},
        )
        assert orchestrator._should_butler_direct_execute(request) is False


class TestDispatchRoutesToButlerDirectExecution:
    """T003.4: 验证 dispatch() 路由到 _dispatch_butler_direct_execution。"""

    async def test_dispatch_routes_to_butler_direct_execution(
        self, tmp_path: Path
    ) -> None:
        store_group, result, llm, task_id = await _create_and_dispatch(
            tmp_path, "你好"
        )
        assert result.status == WorkerExecutionStatus.SUCCEEDED
        assert result.dispatch_id.startswith("butler-direct:")
        # LLM 应被调用（直接执行路径）
        assert len(llm.calls) >= 1
        await store_group.conn.close()


class TestButlerDirectExecutionMetadata:
    """T003.5 + T003.6: 验证 trivial/standard 请求的 metadata 标记。"""

    async def test_trivial_metadata(self, tmp_path: Path) -> None:
        store_group, result, llm, task_id = await _create_and_dispatch(
            tmp_path, "你好"
        )
        assert result.status == WorkerExecutionStatus.SUCCEEDED
        # 验证 LLM 调用的 metadata
        assert len(llm.calls) >= 1
        call_metadata = llm.calls[0]["metadata"]
        assert call_metadata.get("butler_execution_mode") == "direct"
        assert call_metadata.get("butler_is_trivial") is True
        await store_group.conn.close()

    async def test_standard_metadata(self, tmp_path: Path) -> None:
        store_group, result, llm, task_id = await _create_and_dispatch(
            tmp_path, "Python 的 GIL 是什么？"
        )
        assert result.status == WorkerExecutionStatus.SUCCEEDED
        # 验证 LLM 调用的 metadata
        assert len(llm.calls) >= 1
        call_metadata = llm.calls[0]["metadata"]
        assert call_metadata.get("butler_execution_mode") == "direct"
        assert call_metadata.get("butler_is_trivial") is False
        await store_group.conn.close()


# ---------------------------------------------------------------------------
# Phase 4 US2: Event 链完整性验证
# ---------------------------------------------------------------------------


class TestButlerDirectExecutionEventChain:
    """T010: 验证 Butler Direct Execution 路径生成完整 Event 链。"""

    async def test_event_chain_completeness(self, tmp_path: Path) -> None:
        store_group, result, llm, task_id = await _create_and_dispatch(
            tmp_path, "你好"
        )
        assert result.status == WorkerExecutionStatus.SUCCEEDED

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [e.type for e in events]

        # 验证核心 Event 链存在
        assert "ORCH_DECISION" in event_types
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types
        assert "ARTIFACT_CREATED" in event_types

        # 验证 ORCH_DECISION 的 route_reason
        orch_events = [e for e in events if e.type == "ORCH_DECISION"]
        assert len(orch_events) >= 1
        orch_payload = orch_events[0].payload
        assert "butler_direct_execution" in orch_payload.get("route_reason", "")

        await store_group.conn.close()

    async def test_event_metadata_contains_butler_execution_mode(
        self, tmp_path: Path
    ) -> None:
        """T011: 验证 metadata 中 butler_execution_mode 字段被传递。"""
        store_group, result, llm, task_id = await _create_and_dispatch(
            tmp_path, "你好"
        )
        assert result.status == WorkerExecutionStatus.SUCCEEDED

        # 验证 LLM 调用 metadata 包含 butler_execution_mode
        assert len(llm.calls) >= 1
        call_metadata = llm.calls[0]["metadata"]
        assert "butler_execution_mode" in call_metadata
        assert call_metadata["butler_execution_mode"] == "direct"

        await store_group.conn.close()


# ---------------------------------------------------------------------------
# Phase 5 US3: 回归安全测试
# ---------------------------------------------------------------------------


class TestRegressionSafety:
    """T012: 回归测试——确认现有路径不受影响。"""

    async def test_worker_dispatch_fallback_for_subtask(
        self, tmp_path: Path
    ) -> None:
        """子任务（有 parent_task_id）不应走 Butler Direct Execution。"""
        store_group, task_service, orchestrator, llm = await _build_test_context(
            tmp_path
        )
        msg = NormalizedMessage(text="子任务请求", idempotency_key="f064-subtask-001")
        task_id, _ = await task_service.create_task(msg)

        result = await orchestrator.dispatch(
            task_id=task_id,
            user_text="子任务请求",
            metadata={"parent_task_id": "parent-001"},
        )
        # 子任务不走 butler-direct，走其他路径
        assert not result.dispatch_id.startswith("butler-direct:")
        await store_group.conn.close()

    async def test_inline_butler_decision_still_works(
        self, tmp_path: Path
    ) -> None:
        """非 DIRECT_ANSWER 的规则决策仍走 _dispatch_inline_butler_decision()。

        验证 _resolve_butler_decision 返回非 None 决策（如 ASK_ONCE）时，
        不进入 Butler Direct Execution 路径，而走 inline butler decision。
        """
        store_group, task_service, orchestrator, llm = await _build_test_context(
            tmp_path
        )
        msg = NormalizedMessage(text="今天天气怎么样", idempotency_key="f064-inline-001")
        task_id, _ = await task_service.create_task(msg)

        # 直接 mock _resolve_butler_decision 返回非 DIRECT_ANSWER 决策
        # 因为 _prepare_single_loop_butler_request 会设置 single_loop_executor=True
        # 导致 _resolve_butler_decision 短路返回 (None, {})
        from octoagent.core.models import ButlerDecision

        mock_decision = ButlerDecision(
            mode=ButlerDecisionMode.ASK_ONCE,
            category="weather_location",
            rationale="需要确认位置",
            reply_prompt="你想查哪个城市的天气？",
        )

        with patch.object(
            orchestrator,
            "_resolve_butler_decision",
            new_callable=AsyncMock,
            return_value=(mock_decision, {}),
        ):
            result = await orchestrator.dispatch(
                task_id=task_id,
                user_text="今天天气怎么样",
            )

        # 应走 inline butler decision 路径
        assert result.dispatch_id.startswith("butler-clarification:")
        assert not result.dispatch_id.startswith("butler-direct:")
        await store_group.conn.close()
