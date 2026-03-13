"""Feature 034: 主 Agent / Worker 上下文压缩回归测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import DispatchEnvelope, EventType, WorkerExecutionStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.context_compaction import estimate_text_tokens
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.worker_runtime import WorkerRuntime, WorkerRuntimeConfig
from octoagent.provider.models import ModelCallResult, TokenUsage


class RecordingLLMService:
    """记录主模型 / summarizer 调用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._main_counter = 0

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        **kwargs,
    ) -> ModelCallResult:
        metadata = kwargs.get("metadata", {})
        self.calls.append(
            {
                "prompt_or_messages": prompt_or_messages,
                "model_alias": model_alias,
                **kwargs,
            }
        )
        if metadata.get("context_compaction") == "true":
            content = (
                "压缩摘要：用户希望继续同一任务；保留已确认事实、上一轮回答和当前待处理问题。"
            )
        else:
            self._main_counter += 1
            content = f"assistant-response-{self._main_counter}"
        return ModelCallResult(
            content=content,
            model_alias=model_alias or "main",
            model_name="mock-model",
            provider="mock",
            duration_ms=5,
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class FailingSummarizerLLMService(RecordingLLMService):
    """模拟 summarizer 不可用，主模型仍应继续工作。"""

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        **kwargs,
    ) -> ModelCallResult:
        if kwargs.get("metadata", {}).get("context_compaction") == "true":
            self.calls.append(
                {
                    "prompt_or_messages": prompt_or_messages,
                    "model_alias": model_alias,
                    **kwargs,
                }
            )
            raise RuntimeError("summarizer unavailable")
        return await super().call(
            prompt_or_messages,
            model_alias=model_alias,
            **kwargs,
        )


class CrashBeforeModelStartedCheckpointTaskService(TaskService):
    """模拟 compaction 完成后、model_call_started checkpoint 前崩溃。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fail_once = True
        self._compaction_recorded = False

    async def _write_checkpoint(
        self,
        task_id: str,
        node_id: str,
        trace_id: str,
        state_snapshot: dict[str, object],
    ) -> str:
        if (
            node_id == "model_call_started"
            and self._compaction_recorded
            and self._fail_once
        ):
            self._fail_once = False
            raise RuntimeError("inject crash before model_call_started checkpoint")
        return await super()._write_checkpoint(
            task_id=task_id,
            node_id=node_id,
            trace_id=trace_id,
            state_snapshot=state_snapshot,
        )

    async def _record_context_compaction_once(
        self,
        *,
        task_id: str,
        trace_id: str,
        compiled,
        llm_call_idempotency_key: str,
        compaction_idempotency_key: str,
        request_artifact_id: str,
        session_id: str | None,
        worker_capability: str | None,
    ) -> None:
        await super()._record_context_compaction_once(
            task_id=task_id,
            trace_id=trace_id,
            compiled=compiled,
            llm_call_idempotency_key=llm_call_idempotency_key,
            compaction_idempotency_key=compaction_idempotency_key,
            request_artifact_id=request_artifact_id,
            session_id=session_id,
            worker_capability=worker_capability,
        )
        self._compaction_recorded = True

    async def _handle_llm_failure(
        self,
        task_id: str,
        trace_id: str,
        model_alias: str,
        error: Exception,
    ) -> None:
        return None


async def _close_store_group(store_group) -> None:
    await store_group.conn.close()
    await asyncio.sleep(0)


@pytest_asyncio.fixture
async def route_app(tmp_path: Path):
    from fastapi import FastAPI
    from octoagent.gateway.routes import chat, tasks

    store_group = await create_store_group(
        str(tmp_path / "route.db"),
        str(tmp_path / "artifacts"),
    )
    llm_service = RecordingLLMService()
    sse_hub = SSEHub()
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=llm_service,
        timeout_seconds=60,
        monitor_interval_seconds=0.05,
    )
    await task_runner.startup()

    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(tasks.router)
    app.state.store_group = store_group
    app.state.sse_hub = sse_hub
    app.state.llm_service = llm_service
    app.state.task_runner = task_runner

    yield app, llm_service

    await task_runner.shutdown()
    await _close_store_group(store_group)


@pytest_asyncio.fixture
async def route_client(route_app) -> AsyncClient:
    app, _ = route_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


class TestContextCompaction:
    async def test_chat_continue_request_reuses_prior_history(
        self,
        route_app,
        route_client: AsyncClient,
    ) -> None:
        app, llm_service = route_app

        first = await route_client.post("/api/chat/send", json={"message": "第一轮问题"})
        assert first.status_code == 200
        task_id = first.json()["task_id"]

        await asyncio.sleep(0.4)

        second = await route_client.post(
            "/api/chat/send",
            json={"message": "第二轮追问", "task_id": task_id},
        )
        assert second.status_code == 200

        await asyncio.sleep(0.4)

        assert len(llm_service.calls) >= 2
        followup_call = llm_service.calls[-1]
        prompt = followup_call["prompt_or_messages"]
        assert isinstance(prompt, list)
        joined = "\n".join(str(item.get("content", "")) for item in prompt)
        assert "第一轮问题" in joined
        assert "assistant-response-1" in joined
        assert "第二轮追问" in joined

        events = await app.state.store_group.event_store.get_events_for_task(task_id)
        latest_user_event = [event for event in events if event.type == "USER_MESSAGE"][-1]
        assert latest_user_event.payload["text"] == "第二轮追问"

    async def test_task_service_compacts_history_and_flushes_memory(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", "120")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT", "4")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_TURNS", "1")

        store_group = await create_store_group(
            str(tmp_path / "compaction.db"),
            str(tmp_path / "compaction-artifacts"),
        )
        service = TaskService(store_group, SSEHub())
        llm_service = RecordingLLMService()
        try:
            user_1 = "第一轮用户消息。" * 40
            user_2 = "第二轮用户消息。" * 40
            user_3 = "第三轮用户消息。" * 40

            task_id, created = await service.create_task(
                NormalizedMessage(text=user_1, idempotency_key="f034-compaction-1")
            )
            assert created is True
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_1,
                llm_service=llm_service,
            )

            await service.append_user_message(task_id, user_2)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_2,
                llm_service=llm_service,
            )

            await service.append_user_message(task_id, user_3)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_3,
                llm_service=llm_service,
            )

            summarizer_calls = [
                call for call in llm_service.calls if call["model_alias"] == "summarizer"
            ]
            assert len(summarizer_calls) >= 1
            main_calls = [
                call for call in llm_service.calls if call["model_alias"] != "summarizer"
            ]
            assert len(main_calls) >= 3

            events = await store_group.event_store.get_events_for_task(task_id)
            compaction_events = [
                event for event in events if event.type == "CONTEXT_COMPACTION_COMPLETED"
            ]
            assert len(compaction_events) == 1
            payload = compaction_events[0].payload
            assert payload["input_tokens_before"] > payload["input_tokens_after"]
            assert payload["summary_artifact_ref"]
            assert payload["request_artifact_ref"]
            assert payload["memory_flush_run_id"]

            artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
            artifact_names = {artifact.name for artifact in artifacts}
            assert "context-compaction-summary" in artifact_names
            assert "llm-request-context" in artifact_names

            cursor = await store_group.conn.execute(
                "SELECT COUNT(*) FROM memory_maintenance_runs"
            )
            maintenance_count = (await cursor.fetchone())[0]
            assert maintenance_count >= 1
            frames = await store_group.agent_context_store.list_context_frames(
                task_id=task_id,
                limit=5,
            )
            latest_frame = frames[0]
            namespaces = []
            for namespace_id in latest_frame.memory_namespace_ids:
                namespace = await store_group.agent_context_store.get_memory_namespace(
                    namespace_id
                )
                if namespace is not None:
                    namespaces.append(namespace)
            private_namespace = next(
                item for item in namespaces if item.kind.value == "butler_private"
            )
            cursor = await store_group.conn.execute(
                """
                SELECT scope_id
                FROM memory_maintenance_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
            latest_flush_scope_id = (await cursor.fetchone())[0]
            assert latest_flush_scope_id == private_namespace.memory_scope_ids[0]

            cursor = await store_group.conn.execute("SELECT COUNT(*) FROM memory_fragments")
            fragment_count = (await cursor.fetchone())[0]
            assert fragment_count >= 1
        finally:
            await _close_store_group(store_group)

    async def test_worker_runtime_skips_compaction_for_subagent_target(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", "120")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT", "4")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_TURNS", "1")

        store_group = await create_store_group(
            str(tmp_path / "worker.db"),
            str(tmp_path / "worker-artifacts"),
        )
        sse_hub = SSEHub()
        service = TaskService(store_group, sse_hub)
        warmup_llm = RecordingLLMService()
        try:
            user_1 = "主链第一轮消息。" * 40
            user_2 = "主链第二轮消息。" * 40
            user_3 = "子代理第三轮消息。" * 40

            task_id, created = await service.create_task(
                NormalizedMessage(text=user_1, idempotency_key="f034-worker-1")
            )
            assert created is True
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_1,
                llm_service=warmup_llm,
            )
            await service.append_user_message(task_id, user_2)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_2,
                llm_service=warmup_llm,
            )
            await service.append_user_message(task_id, user_3)

            llm_service = RecordingLLMService()
            runtime = WorkerRuntime(
                store_group=store_group,
                sse_hub=sse_hub,
                llm_service=llm_service,
                config=WorkerRuntimeConfig(docker_mode="disabled"),
            )
            envelope = DispatchEnvelope(
                dispatch_id="dispatch-f034-subagent",
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                contract_version="1.0",
                route_reason="test-subagent-bypass",
                worker_capability="research",
                hop_count=1,
                max_hops=3,
                user_text=user_3,
                model_alias="main",
                metadata={"target_kind": "subagent"},
            )

            result = await runtime.run(envelope, worker_id="worker.test")
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            assert all(call["model_alias"] != "summarizer" for call in llm_service.calls)
        finally:
            await _close_store_group(store_group)

    async def test_compaction_degrades_to_raw_history_when_summarizer_fails(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", "120")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT", "4")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_TURNS", "1")

        store_group = await create_store_group(
            str(tmp_path / "degraded.db"),
            str(tmp_path / "degraded-artifacts"),
        )
        service = TaskService(store_group, SSEHub())
        llm_service = FailingSummarizerLLMService()

        try:
            user_1 = "第一轮用户消息。" * 40
            user_2 = "第二轮用户消息。" * 40
            user_3 = "第三轮用户消息。" * 40

            task_id, created = await service.create_task(
                NormalizedMessage(text=user_1, idempotency_key="f034-degraded-1")
            )
            assert created is True
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_1,
                llm_service=llm_service,
            )
            await service.append_user_message(task_id, user_2)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_2,
                llm_service=llm_service,
            )
            await service.append_user_message(task_id, user_3)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_3,
                llm_service=llm_service,
            )

            assert any(call["model_alias"] == "summarizer" for call in llm_service.calls)
            main_calls = [
                call for call in llm_service.calls if call["model_alias"] != "summarizer"
            ]
            final_prompt = main_calls[-1]["prompt_or_messages"]
            assert isinstance(final_prompt, list)
            joined = "\n".join(str(item.get("content", "")) for item in final_prompt)
            assert "第一轮用户消息" in joined
            assert "assistant-response-1" in joined
            assert "第二轮用户消息" in joined
            assert "assistant-response-2" in joined
            assert "第三轮用户消息" in joined

            events = await store_group.event_store.get_events_for_task(task_id)
            assert all(event.type != "CONTEXT_COMPACTION_COMPLETED" for event in events)
        finally:
            await _close_store_group(store_group)

    async def test_compaction_resume_does_not_repeat_side_effects(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", "120")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT", "4")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_TURNS", "1")

        store_group = await create_store_group(
            str(tmp_path / "resume.db"),
            str(tmp_path / "resume-artifacts"),
        )
        service = CrashBeforeModelStartedCheckpointTaskService(store_group, SSEHub())
        llm_service = RecordingLLMService()

        try:
            user_1 = "第一轮用户消息。" * 40
            user_2 = "第二轮用户消息。" * 40
            user_3 = "第三轮用户消息。" * 40

            task_id, created = await service.create_task(
                NormalizedMessage(text=user_1, idempotency_key="f034-resume-1")
            )
            assert created is True
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_1,
                llm_service=llm_service,
            )
            await service.append_user_message(task_id, user_2)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_2,
                llm_service=llm_service,
            )
            await service.append_user_message(task_id, user_3)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_3,
                llm_service=llm_service,
            )

            latest_checkpoint = await store_group.checkpoint_store.get_latest_success(task_id)
            assert latest_checkpoint is not None
            assert latest_checkpoint.node_id == "state_running"

            events_before = await store_group.event_store.get_events_for_task(task_id)
            compaction_count_before = sum(
                1 for event in events_before if event.type is EventType.CONTEXT_COMPACTION_COMPLETED
            )
            started_count_before = sum(
                1 for event in events_before if event.type is EventType.MODEL_CALL_STARTED
            )
            cursor = await store_group.conn.execute(
                "SELECT COUNT(*) FROM memory_maintenance_runs"
            )
            maintenance_before = (await cursor.fetchone())[0]
            cursor = await store_group.conn.execute("SELECT COUNT(*) FROM memory_fragments")
            fragments_before = (await cursor.fetchone())[0]

            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_3,
                llm_service=llm_service,
                resume_from_node="state_running",
                resume_state_snapshot=latest_checkpoint.state_snapshot,
            )

            events_after = await store_group.event_store.get_events_for_task(task_id)
            compaction_count_after = sum(
                1 for event in events_after if event.type is EventType.CONTEXT_COMPACTION_COMPLETED
            )
            started_count_after = sum(
                1 for event in events_after if event.type is EventType.MODEL_CALL_STARTED
            )
            cursor = await store_group.conn.execute(
                "SELECT COUNT(*) FROM memory_maintenance_runs"
            )
            maintenance_after = (await cursor.fetchone())[0]
            cursor = await store_group.conn.execute("SELECT COUNT(*) FROM memory_fragments")
            fragments_after = (await cursor.fetchone())[0]

            assert compaction_count_before == 1
            assert compaction_count_after == 1
            assert started_count_after == started_count_before
            assert maintenance_after == maintenance_before
            assert fragments_after == fragments_before
        finally:
            await _close_store_group(store_group)

    async def test_compaction_bounds_summarizer_transcript_for_long_history(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", "120")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT", "4")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_TURNS", "1")

        store_group = await create_store_group(
            str(tmp_path / "bounded.db"),
            str(tmp_path / "bounded-artifacts"),
        )
        service = TaskService(store_group, SSEHub())
        llm_service = RecordingLLMService()

        try:
            messages = [
                "第一轮超长用户消息。" * 50,
                "第二轮超长用户消息。" * 50,
                "第三轮超长用户消息。" * 50,
                "第四轮超长用户消息。" * 50,
            ]
            task_id, created = await service.create_task(
                NormalizedMessage(text=messages[0], idempotency_key="f034-bounded-1")
            )
            assert created is True
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=messages[0],
                llm_service=llm_service,
            )
            for message in messages[1:]:
                await service.append_user_message(task_id, message)
                await service.process_task_with_llm(
                    task_id=task_id,
                    user_text=message,
                    llm_service=llm_service,
                )

            summarizer_calls = [
                call for call in llm_service.calls if call["model_alias"] == "summarizer"
            ]
            assert len(summarizer_calls) >= 2
            budget = service._context_compaction._summarizer_transcript_budget_tokens()
            for call in summarizer_calls:
                prompt = call["prompt_or_messages"]
                assert isinstance(prompt, list)
                user_content = str(prompt[1]["content"])
                transcript = user_content.split("需要压缩的内容：\n", 1)[1]
                assert estimate_text_tokens(transcript) <= budget
        finally:
            await _close_store_group(store_group)

    async def test_compaction_event_uses_configured_summarizer_alias(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", "120")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT", "4")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_TURNS", "1")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_SUMMARIZER_ALIAS", "cheap-summary")

        store_group = await create_store_group(
            str(tmp_path / "alias.db"),
            str(tmp_path / "alias-artifacts"),
        )
        service = TaskService(store_group, SSEHub())
        llm_service = RecordingLLMService()

        try:
            user_1 = "第一轮用户消息。" * 40
            user_2 = "第二轮用户消息。" * 40
            user_3 = "第三轮用户消息。" * 40

            task_id, created = await service.create_task(
                NormalizedMessage(text=user_1, idempotency_key="f034-alias-1")
            )
            assert created is True
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_1,
                llm_service=llm_service,
            )
            await service.append_user_message(task_id, user_2)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_2,
                llm_service=llm_service,
            )
            await service.append_user_message(task_id, user_3)
            await service.process_task_with_llm(
                task_id=task_id,
                user_text=user_3,
                llm_service=llm_service,
            )

            summarizer_calls = [
                call for call in llm_service.calls if call["model_alias"] == "cheap-summary"
            ]
            assert len(summarizer_calls) >= 1

            events = await store_group.event_store.get_events_for_task(task_id)
            compaction_event = next(
                event
                for event in events
                if event.type is EventType.CONTEXT_COMPACTION_COMPLETED
            )
            assert compaction_event.payload["model_alias"] == "cheap-summary"
        finally:
            await _close_store_group(store_group)
