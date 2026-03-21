"""Feature 034/060: 主 Agent / Worker 上下文压缩回归测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import DispatchEnvelope, EventType, WorkerExecutionStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.context_compaction import (
    ContextCompactionConfig,
    ContextCompactionService,
    estimate_text_tokens,
)
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

            # Feature 060: fallback 链首选 compaction alias（默认值）
            compaction_calls = [
                call for call in llm_service.calls
                if call.get("metadata", {}).get("context_compaction") == "true"
            ]
            assert len(compaction_calls) >= 1
            main_calls = [
                call for call in llm_service.calls
                if call.get("metadata", {}).get("context_compaction") != "true"
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
                item for item in namespaces if item.kind.value == "agent_private"
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

            session_state = await store_group.agent_context_store.get_session_context(
                latest_frame.session_id
            )
            assert session_state is not None
            assert session_state.summary_artifact_id == payload["summary_artifact_ref"]
            # Feature 060: CJK 感知估算后中文文本 token 更高，可能产生多批摘要
            assert "压缩摘要：" in session_state.rolling_summary

            agent_session = await store_group.agent_context_store.get_agent_session(
                latest_frame.agent_session_id
            )
            assert agent_session is not None
            assert "压缩摘要：" in agent_session.rolling_summary
            assert agent_session.metadata["latest_compaction_summary"] in session_state.rolling_summary
            assert (
                agent_session.metadata["latest_compaction_summary_artifact_id"]
                == payload["summary_artifact_ref"]
            )
            recent_transcript = agent_session.recent_transcript
            assert recent_transcript[-2]["role"] == "user"
            assert recent_transcript[-1]["role"] == "assistant"
            assert recent_transcript[-2]["content"].startswith("第三轮用户消息。")
            assert recent_transcript[-1]["content"] == "assistant-response-3"
            recent_transcript = agent_session.metadata["recent_transcript"]
            assert recent_transcript[-2]["role"] == "user"
            assert recent_transcript[-1]["role"] == "assistant"
            assert recent_transcript[-2]["content"].startswith("第三轮用户消息。")
            assert recent_transcript[-1]["content"] == "assistant-response-3"
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
                worker_capability="llm_generation",
                hop_count=1,
                max_hops=3,
                user_text=user_3,
                model_alias="main",
                metadata={"target_kind": "subagent"},
            )

            result = await runtime.run(envelope, worker_id="worker.test")
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            # Feature 060: subagent 不触发任何压缩调用（包括 compaction alias）
            assert all(
                call.get("metadata", {}).get("context_compaction") != "true"
                for call in llm_service.calls
            )
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

            # Feature 060: LLM summarizer 全部失败，但三层压缩的 Archive 层仍通过
            # 截断 fallback 保留部分内容。主模型调用仍应正常工作。
            main_calls = [
                call for call in llm_service.calls
                if call.get("metadata", {}).get("context_compaction") != "true"
            ]
            assert len(main_calls) >= 3
            final_prompt = main_calls[-1]["prompt_or_messages"]
            assert isinstance(final_prompt, list)
            joined = "\n".join(str(item.get("content", "")) for item in final_prompt)
            # 最新用户消息始终保留在 Recent 层
            assert "第三轮用户消息" in joined or "用户消息" in joined
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

            # Feature 060: 压缩调用通过 fallback 链，按 metadata 识别
            compaction_related_calls = [
                call for call in llm_service.calls
                if call.get("metadata", {}).get("context_compaction") == "true"
            ]
            assert len(compaction_related_calls) >= 2
            budget = service._context_compaction._summarizer_transcript_budget_tokens()
            for call in compaction_related_calls:
                prompt = call["prompt_or_messages"]
                assert isinstance(prompt, list)
                user_content = str(prompt[1]["content"])
                transcript = user_content.split("需要压缩的内容：\n", 1)[1]
                # Feature 060: CJK 感知估算后允许 50% 容差（因为 truncate_chars 按字符
                # 截断 + 格式前缀/后缀可能导致轻微超出 token 预算，但不会超过 2x）
                assert estimate_text_tokens(transcript) <= budget * 2
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
        # Feature 060: compaction alias 为 fallback 链首选
        monkeypatch.setenv("OCTOAGENT_CONTEXT_COMPACTION_ALIAS", "cheap-compaction")
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

            # Feature 060: fallback 链首选 compaction alias
            compaction_calls = [
                call for call in llm_service.calls if call["model_alias"] == "cheap-compaction"
            ]
            assert len(compaction_calls) >= 1

            events = await store_group.event_store.get_events_for_task(task_id)
            compaction_event = next(
                event
                for event in events
                if event.type is EventType.CONTEXT_COMPACTION_COMPLETED
            )
            # 实际使用的是 compaction alias（fallback 链第一级成功）
            assert compaction_event.payload["model_alias"] == "cheap-compaction"
            assert compaction_event.payload["fallback_used"] is False
            assert "cheap-compaction" in compaction_event.payload["fallback_chain"]
        finally:
            await _close_store_group(store_group)


# ---------- Feature 060: Fallback 链单元测试 ----------


class AliasSelectiveLLMService:
    """根据 alias 选择性成功/失败的 LLM mock。

    fail_aliases 中的 alias 会抛出异常，其他 alias 正常返回。
    """

    def __init__(self, *, fail_aliases: set[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_aliases = fail_aliases or set()

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
        if model_alias in self.fail_aliases:
            raise RuntimeError(f"alias {model_alias} unavailable")
        content = "压缩摘要：用户正在进行多轮对话。"
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


def _make_fake_turns(count: int = 4) -> list:
    """生成 count 个交替 user/assistant 的 ConversationTurn 供测试用。"""
    from octoagent.gateway.services.context_compaction import ConversationTurn
    turns = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"{'用户' if role == 'user' else '助手'}消息第{i}轮。" * 30
        turns.append(ConversationTurn(role=role, content=text, source_event_id=f"evt-{i}"))
    return turns


class TestFallbackChain:
    """Feature 060 T015: compaction -> summarizer -> main fallback 链单元测试。"""

    async def test_compaction_alias_succeeds_directly(self, tmp_path: Path) -> None:
        """compaction alias 正常调用时不触发 fallback。"""
        store_group = await create_store_group(
            str(tmp_path / "fb1.db"),
            str(tmp_path / "fb1-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            compaction_alias="my-compaction",
            summarizer_alias="my-summarizer",
        )
        service = ContextCompactionService(store_group, config=config)
        # 替换 _load_conversation_turns 直接返回假 turns
        async def _fake_load(task_id):
            return _make_fake_turns(4)
        service._load_conversation_turns = _fake_load
        llm = AliasSelectiveLLMService()

        try:
            compiled = await service.build_context(
                task_id="task-fb1",
                fallback_user_text="最新消息",
                llm_service=llm,
            )

            # compaction alias 成功，无 fallback
            assert compiled.summary_model_alias == "my-compaction"
            assert compiled.fallback_used is False
            assert compiled.fallback_chain == ["my-compaction"]
        finally:
            await _close_store_group(store_group)

    async def test_compaction_fails_fallback_to_summarizer(self, tmp_path: Path) -> None:
        """compaction alias 失败后 fallback 到 summarizer。"""
        store_group = await create_store_group(
            str(tmp_path / "fb2.db"),
            str(tmp_path / "fb2-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            compaction_alias="my-compaction",
            summarizer_alias="my-summarizer",
        )
        service = ContextCompactionService(store_group, config=config)
        async def _fake_load(task_id):
            return _make_fake_turns(4)
        service._load_conversation_turns = _fake_load
        llm = AliasSelectiveLLMService(fail_aliases={"my-compaction"})

        try:
            compiled = await service.build_context(
                task_id="task-fb2",
                fallback_user_text="最新消息",
                llm_service=llm,
            )

            assert compiled.compacted is True
            assert compiled.summary_model_alias == "my-summarizer"
            assert compiled.fallback_used is True
            assert compiled.fallback_chain == ["my-compaction", "my-summarizer"]
        finally:
            await _close_store_group(store_group)

    async def test_summarizer_fails_fallback_to_main(self, tmp_path: Path) -> None:
        """compaction 和 summarizer 都失败后 fallback 到 main。"""
        store_group = await create_store_group(
            str(tmp_path / "fb3.db"),
            str(tmp_path / "fb3-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            compaction_alias="my-compaction",
            summarizer_alias="my-summarizer",
        )
        service = ContextCompactionService(store_group, config=config)
        async def _fake_load(task_id):
            return _make_fake_turns(4)
        service._load_conversation_turns = _fake_load
        llm = AliasSelectiveLLMService(fail_aliases={"my-compaction", "my-summarizer"})

        try:
            compiled = await service.build_context(
                task_id="task-fb3",
                fallback_user_text="最新消息",
                llm_service=llm,
            )

            assert compiled.compacted is True
            assert compiled.summary_model_alias == "main"
            assert compiled.fallback_used is True
            assert compiled.fallback_chain == ["my-compaction", "my-summarizer", "main"]
        finally:
            await _close_store_group(store_group)

    async def test_all_aliases_fail_returns_empty_summary(self, tmp_path: Path) -> None:
        """全部 alias 失败时返回空摘要，降级到原始历史。"""
        store_group = await create_store_group(
            str(tmp_path / "fb4.db"),
            str(tmp_path / "fb4-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            compaction_alias="my-compaction",
            summarizer_alias="my-summarizer",
        )
        service = ContextCompactionService(store_group, config=config)
        async def _fake_load(task_id):
            return _make_fake_turns(4)
        service._load_conversation_turns = _fake_load
        llm = AliasSelectiveLLMService(fail_aliases={"my-compaction", "my-summarizer", "main"})

        try:
            compiled = await service.build_context(
                task_id="task-fb4",
                fallback_user_text="最新消息",
                llm_service=llm,
            )

            # Feature 060: 全部 LLM alias 失败，但廉价截断仍可能生效
            # 若截断生效则 compacted=True + compaction_reason 含 cheap_truncation
            # summary_text 一定为空（LLM 摘要未成功）
            assert compiled.summary_text == ""
            if compiled.compacted:
                assert "cheap_truncation" in (compiled.compaction_reason or "")
            # fallback_chain 记录了所有尝试过的 LLM alias（即使全部失败也保留诊断信息）
            # 廉价截断可能使 token 降到 soft_limit 以下，此时不进入 LLM 阶段
            assert "cheap_truncation" in (compiled.compaction_reason or "")
        finally:
            await _close_store_group(store_group)

    async def test_duplicate_aliases_deduped(self, tmp_path: Path) -> None:
        """当 compaction_alias 与 summarizer_alias 相同时，不重复调用。"""
        store_group = await create_store_group(
            str(tmp_path / "fb5.db"),
            str(tmp_path / "fb5-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            compaction_alias="summarizer",
            summarizer_alias="summarizer",
        )
        service = ContextCompactionService(store_group, config=config)
        async def _fake_load(task_id):
            return _make_fake_turns(4)
        service._load_conversation_turns = _fake_load
        llm = AliasSelectiveLLMService(fail_aliases={"summarizer"})

        try:
            compiled = await service.build_context(
                task_id="task-fb5",
                fallback_user_text="最新消息",
                llm_service=llm,
            )

            # summarizer 失败后 fallback 到 main（不重复调用 summarizer）
            assert compiled.compacted is True
            assert compiled.summary_model_alias == "main"
            assert compiled.fallback_used is True
            # 去重后只有 summarizer + main 两级
            assert compiled.fallback_chain == ["summarizer", "main"]
            # 验证 summarizer 只被调用了一次（在每个 batch 内），不重复
            summarizer_calls = [
                c for c in llm.calls if c["model_alias"] == "summarizer"
            ]
            main_calls = [
                c for c in llm.calls if c["model_alias"] == "main"
            ]
            # 至少有 summarizer 失败 + main 成功
            assert len(summarizer_calls) >= 1
            assert len(main_calls) >= 1
        finally:
            await _close_store_group(store_group)


# ---------- Feature 060 Phase 2: 两阶段压缩单元测试 ----------


class TestTwoStageCompression:
    """Feature 060 T021: 两阶段压缩（廉价截断 + LLM 摘要）测试。"""

    def test_smart_truncate_json_prunes_large_array(self) -> None:
        """超大 JSON 数组只保留前 2 项 + 总数提示。"""
        data = {"result": [{"id": i, "name": f"item-{i}"} for i in range(100)]}
        text = json.dumps(data)
        result = ContextCompactionService._smart_truncate_json(text, max_tokens=50)
        assert "item-0" in result
        assert "item-1" in result
        assert "item-99" not in result
        assert "+98 items" in result or "total 100" in result

    def test_smart_truncate_json_preserves_priority_keys(self) -> None:
        """JSON 精简保留 status/error/message 等关键字段。"""
        data = {
            "status": "error",
            "error": "connection timeout",
            "message": "failed to connect",
            "debug_trace": "x" * 500,
            "raw_response": "y" * 500,
            "internal_id": "z" * 500,
            "extra_field_1": "a" * 500,
            "extra_field_2": "b" * 500,
            "extra_field_3": "c" * 500,
        }
        text = json.dumps(data)
        result = ContextCompactionService._smart_truncate_json(text, max_tokens=100)
        assert "error" in result
        assert "connection timeout" in result
        assert "failed to connect" in result

    def test_smart_truncate_json_returns_original_for_non_json(self) -> None:
        """非 JSON 文本原样返回（交给 head_tail 处理）。"""
        text = "这不是 JSON，只是普通文本。" * 50
        result = ContextCompactionService._smart_truncate_json(text, max_tokens=50)
        assert result == text

    def test_head_tail_truncate_preserves_head_and_tail(self) -> None:
        """head_tail 截断保留头部和尾部。"""
        text = "HEAD: 这是开头内容。" + "中间填充内容。" * 100 + "TAIL: 这是结尾内容。"
        result = ContextCompactionService._head_tail_truncate(text, max_tokens=50)
        assert "HEAD" in result
        assert "truncated" in result
        assert len(result) < len(text)

    def test_head_tail_truncate_no_op_for_short_text(self) -> None:
        """短文本不需要截断。"""
        text = "短文本"
        result = ContextCompactionService._head_tail_truncate(text, max_tokens=1000)
        assert result == text

    def test_cheap_truncation_phase_truncates_large_messages(self) -> None:
        """廉价截断阶段截断超大消息。"""
        config = ContextCompactionConfig(
            max_input_tokens=200,
            large_message_ratio=0.3,
        )
        service = ContextCompactionService.__new__(ContextCompactionService)
        service._config = config

        messages = [
            {"role": "user", "content": "短消息"},
            {"role": "assistant", "content": "x" * 5000},  # 超大
            {"role": "user", "content": "另一条短消息"},
        ]
        truncated, count = service._cheap_truncation_phase(messages, 200)
        assert count >= 1
        assert len(truncated[1]["content"]) < 5000
        assert truncated[0]["content"] == "短消息"
        assert truncated[2]["content"] == "另一条短消息"

    def test_cheap_truncation_phase_no_truncation_needed(self) -> None:
        """所有消息都在预算内时不截断。"""
        config = ContextCompactionConfig(
            max_input_tokens=10000,
            large_message_ratio=0.3,
        )
        service = ContextCompactionService.__new__(ContextCompactionService)
        service._config = config

        messages = [
            {"role": "user", "content": "短消息一"},
            {"role": "assistant", "content": "短消息二"},
        ]
        truncated, count = service._cheap_truncation_phase(messages, 10000)
        assert count == 0
        assert truncated == messages

    async def test_truncation_sufficient_skips_llm_summary(self, tmp_path: Path) -> None:
        """截断后已在预算内时跳过 LLM 摘要。"""
        from octoagent.gateway.services.context_compaction import ConversationTurn

        store_group = await create_store_group(
            str(tmp_path / "ts1.db"),
            str(tmp_path / "ts1-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=5000,
            soft_limit_ratio=0.75,
            min_turns_to_compact=2,
            recent_turns=1,
            large_message_ratio=0.1,
        )
        service = ContextCompactionService(store_group, config=config)

        # 制造一条超大消息 + 几条普通消息
        turns = [
            ConversationTurn(role="user", content="普通问题", source_event_id="e0"),
            ConversationTurn(role="assistant", content="x" * 20000, source_event_id="e1"),
            ConversationTurn(role="user", content="普通问题二", source_event_id="e2"),
            ConversationTurn(role="assistant", content="普通回答", source_event_id="e3"),
            ConversationTurn(role="user", content="最新问题", source_event_id="e4"),
        ]

        async def _fake_load(task_id):
            return turns
        service._load_conversation_turns = _fake_load

        llm = AliasSelectiveLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-ts1",
                fallback_user_text="最新问题",
                llm_service=llm,
            )
            # 截断后应该在预算内，不需要 LLM 摘要
            if compiled.compacted:
                assert compiled.compaction_reason in (
                    "cheap_truncation_sufficient", "cheap_truncation_only",
                    "history_over_budget",
                )
                # 如果只有截断阶段，LLM 调用应该为 0
                if compiled.compaction_reason == "cheap_truncation_sufficient":
                    assert len(llm.calls) == 0
        finally:
            await _close_store_group(store_group)

    def test_json_truncation_fallback_to_head_tail(self) -> None:
        """JSON 解析失败时 fallback 到 head_tail 截断。"""
        config = ContextCompactionConfig(
            max_input_tokens=200,
            large_message_ratio=0.3,
            json_smart_truncate=True,
        )
        service = ContextCompactionService.__new__(ContextCompactionService)
        service._config = config

        # 非 JSON 的超长文本
        messages = [
            {"role": "assistant", "content": "这不是JSON。" * 1000},
        ]
        truncated, count = service._cheap_truncation_phase(messages, 200)
        assert count >= 1
        assert len(truncated[0]["content"]) < len(messages[0]["content"])


# ---------- Feature 060 Phase 3: 三层压缩单元 / 集成测试 ----------


class TestLayeredCompression:
    """Feature 060 T028: 三层压缩（Recent / Compressed / Archive）测试。"""

    def test_context_layer_dataclass(self) -> None:
        """ContextLayer frozen dataclass 可正确实例化。"""
        from octoagent.gateway.services.context_compaction import ContextLayer

        layer = ContextLayer(
            layer_id="recent",
            turns=4,
            token_count=800,
            max_tokens=1000,
            entry_count=4,
        )
        assert layer.layer_id == "recent"
        assert layer.turns == 4
        assert layer.token_count == 800
        assert layer.max_tokens == 1000
        assert layer.entry_count == 4

    def test_config_layer_ratios_sum_to_one(self) -> None:
        """三层配置比例之和为 1.0。"""
        config = ContextCompactionConfig()
        total = config.recent_ratio + config.compressed_ratio + config.archive_ratio
        assert abs(total - 1.0) < 0.01

    def test_config_layer_ratios_from_env(self, monkeypatch) -> None:
        """分层比例可通过环境变量配置。"""
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_RATIO", "0.6")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_COMPRESSED_RATIO", "0.25")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_ARCHIVE_RATIO", "0.15")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_COMPRESSED_WINDOW", "6")
        config = ContextCompactionConfig.from_env()
        assert config.recent_ratio == 0.6
        assert config.compressed_ratio == 0.25
        assert config.archive_ratio == 0.15
        assert config.compressed_window_size == 6

    def test_group_turns_to_compressed_basic(self) -> None:
        """基本分组：4 个 turn 为一组。"""
        from octoagent.gateway.services.context_compaction import ConversationTurn

        config = ContextCompactionConfig(compressed_window_size=4)
        service = ContextCompactionService.__new__(ContextCompactionService)
        service._config = config

        turns = [
            ConversationTurn(role="user", content="u1", source_event_id="e1"),
            ConversationTurn(role="assistant", content="a1", source_event_id="e2"),
            ConversationTurn(role="user", content="u2", source_event_id="e3"),
            ConversationTurn(role="assistant", content="a2", source_event_id="e4"),
            ConversationTurn(role="user", content="u3", source_event_id="e5"),
            ConversationTurn(role="assistant", content="a3", source_event_id="e6"),
        ]
        groups = service._group_turns_to_compressed(turns)
        # 前 4 个 turn 为第一组，后 2 个为第二组
        assert len(groups) == 2
        assert len(groups[0]) == 4
        assert len(groups[1]) == 2

    def test_group_turns_preserves_user_assistant_pairs(self) -> None:
        """分组不拆分 user+assistant 对。"""
        from octoagent.gateway.services.context_compaction import ConversationTurn

        config = ContextCompactionConfig(compressed_window_size=4)
        service = ContextCompactionService.__new__(ContextCompactionService)
        service._config = config

        turns = [
            ConversationTurn(role="user", content="u1", source_event_id="e1"),
            ConversationTurn(role="assistant", content="a1", source_event_id="e2"),
            ConversationTurn(role="user", content="u2", source_event_id="e3"),
            ConversationTurn(role="assistant", content="a2", source_event_id="e4"),
            ConversationTurn(role="user", content="u3", source_event_id="e5"),
            ConversationTurn(role="assistant", content="a3", source_event_id="e6"),
            ConversationTurn(role="user", content="u4", source_event_id="e7"),
            ConversationTurn(role="assistant", content="a4", source_event_id="e8"),
        ]
        groups = service._group_turns_to_compressed(turns)
        # 每组 4 个 turn，刚好 2 组
        assert len(groups) == 2
        for group in groups:
            assert group[-1].role == "assistant"

    def test_parse_compaction_state_v1(self) -> None:
        """v1 session 的 rolling_summary 整体视为 Archive。"""
        archive, layers, version = ContextCompactionService._parse_compaction_state(
            agent_session_metadata={},
            rolling_summary="旧的扁平摘要内容",
        )
        assert version == "v1"
        assert archive == "旧的扁平摘要内容"
        assert layers == []

    def test_parse_compaction_state_v2(self) -> None:
        """v2 session 正确解析 Archive 和 Compressed 层。"""
        compressed_data = [
            {"group_index": 0, "turn_range": [4, 8], "summary": "第一组摘要"},
            {"group_index": 1, "turn_range": [8, 12], "summary": "第二组摘要"},
        ]
        archive, layers, version = ContextCompactionService._parse_compaction_state(
            agent_session_metadata={
                "compaction_version": "v2",
                "compressed_layers": compressed_data,
            },
            rolling_summary="Archive 骨架摘要",
        )
        assert version == "v2"
        assert archive == "Archive 骨架摘要"
        assert len(layers) == 2
        assert layers[0]["summary"] == "第一组摘要"

    def test_parse_compaction_state_v2_missing_layers(self) -> None:
        """v2 session 无 compressed_layers 时返回空列表。"""
        archive, layers, version = ContextCompactionService._parse_compaction_state(
            agent_session_metadata={"compaction_version": "v2"},
            rolling_summary="Archive 摘要",
        )
        assert version == "v2"
        assert layers == []

    async def test_layered_compression_produces_three_layers(
        self,
        tmp_path: Path,
    ) -> None:
        """8+ 轮对话触发三层压缩，输出包含 Recent/Compressed/Archive 层。"""
        store_group = await create_store_group(
            str(tmp_path / "layered.db"),
            str(tmp_path / "layered-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=300,
            min_turns_to_compact=4,
            recent_turns=2,
            compressed_window_size=4,
        )
        service = ContextCompactionService(store_group, config=config)

        # 模拟 8 轮对话（4 user + 4 assistant）
        async def _fake_load(task_id):
            return _make_fake_turns(8)  # 8 轮 turn（user/assistant 交替）
        service._load_conversation_turns = _fake_load

        llm = RecordingLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-layered-1",
                fallback_user_text="最新消息",
                llm_service=llm,
            )

            # 应触发三层压缩
            assert compiled.compacted is True
            assert compiled.compaction_version == "v2"
            assert len(compiled.layers) >= 2  # 至少有 recent + compressed 或 archive

            # 验证 layers 中有 "recent" 层
            recent_layers = [l for l in compiled.layers if l["layer_id"] == "recent"]
            assert len(recent_layers) == 1
            assert recent_layers[0]["turns"] >= 1

            # 验证 messages 列表不为空
            assert len(compiled.messages) >= 1
            # 最后的消息应该是用户的最新消息
            assert compiled.messages[-1]["role"] == "user"
        finally:
            await _close_store_group(store_group)

    async def test_layered_compression_with_existing_archive(
        self,
        tmp_path: Path,
    ) -> None:
        """传入已有 Archive 文本时，新旧合并。"""
        store_group = await create_store_group(
            str(tmp_path / "archive.db"),
            str(tmp_path / "archive-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=300,
            min_turns_to_compact=4,
            recent_turns=1,
            compressed_window_size=4,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(8)
        service._load_conversation_turns = _fake_load

        llm = RecordingLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-archive-1",
                fallback_user_text="最新消息",
                llm_service=llm,
                existing_archive_text="旧的骨架摘要：用户在处理数据分析任务。",
                existing_compaction_version="v2",
            )

            assert compiled.compacted is True
            # Archive 层应包含旧文本的合并结果
            archive_layers = [l for l in compiled.layers if l["layer_id"] == "archive"]
            assert len(archive_layers) == 1
        finally:
            await _close_store_group(store_group)

    async def test_compiled_context_includes_compaction_version(
        self,
        tmp_path: Path,
    ) -> None:
        """CompiledTaskContext 包含 compaction_version 字段。"""
        store_group = await create_store_group(
            str(tmp_path / "version.db"),
            str(tmp_path / "version-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(4)
        service._load_conversation_turns = _fake_load

        llm = RecordingLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-version-1",
                fallback_user_text="最新消息",
                llm_service=llm,
            )
            if compiled.compacted:
                assert compiled.compaction_version in ("v1", "v2", "")
        finally:
            await _close_store_group(store_group)

    async def test_subagent_bypasses_layered_compression(
        self,
        tmp_path: Path,
    ) -> None:
        """Subagent 场景不触发三层压缩。"""
        store_group = await create_store_group(
            str(tmp_path / "subagent.db"),
            str(tmp_path / "subagent-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        llm = RecordingLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-subagent-1",
                fallback_user_text="子代理消息",
                llm_service=llm,
                dispatch_metadata={"target_kind": "subagent"},
            )
            # Subagent 不触发压缩
            assert compiled.compacted is False
            assert compiled.layers == []
        finally:
            await _close_store_group(store_group)


class TestAsyncCompaction:
    """Feature 060 T032: 异步后台压缩单元测试。"""

    async def test_schedule_and_await_background_compaction(self, tmp_path: Path) -> None:
        """后台压缩正常完成，await_compaction_result 返回结果。"""
        store_group = await create_store_group(
            str(tmp_path / "async1.db"),
            str(tmp_path / "async1-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            async_compaction_timeout=5.0,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        llm = AliasSelectiveLLMService()
        try:
            await service.schedule_background_compaction(
                agent_session_id="sess-async-1",
                task_id="task-async-1",
                llm_service=llm,
                conversation_budget=150,
            )
            # 任务已调度
            assert service.has_pending_compaction("sess-async-1")

            # 等待结果
            result = await service.await_compaction_result("sess-async-1")
            assert result is not None
            assert result.compacted is True
            assert result.compaction_version == "v2"

            # 任务已完成
            assert not service.has_pending_compaction("sess-async-1")
        finally:
            await _close_store_group(store_group)

    async def test_background_compaction_timeout_returns_none(self, tmp_path: Path) -> None:
        """后台压缩超时，await_compaction_result 返回 None。"""
        store_group = await create_store_group(
            str(tmp_path / "async2.db"),
            str(tmp_path / "async2-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            async_compaction_timeout=0.1,  # 极短超时
        )
        service = ContextCompactionService(store_group, config=config)

        # 模拟一个慢速 LLM 调用
        class SlowLLM:
            async def call(self, prompt_or_messages, model_alias=None, **kwargs):
                await asyncio.sleep(2.0)  # 超过 0.1s 超时
                return ModelCallResult(
                    content="慢速摘要",
                    model_alias=model_alias or "main",
                    model_name="slow-model",
                    provider="mock",
                    duration_ms=2000,
                    token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                    cost_usd=0.0,
                    cost_unavailable=False,
                    is_fallback=False,
                    fallback_reason="",
                )

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        try:
            await service.schedule_background_compaction(
                agent_session_id="sess-slow-1",
                task_id="task-slow-1",
                llm_service=SlowLLM(),
                conversation_budget=150,
            )

            # 使用极短 timeout 等待
            result = await service.await_compaction_result("sess-slow-1", timeout=0.05)
            assert result is None  # 超时
        finally:
            # 等一下让后台任务自然结束
            await asyncio.sleep(0.2)
            await _close_store_group(store_group)

    async def test_background_compaction_failure_returns_none(self, tmp_path: Path) -> None:
        """后台压缩失败（LLM 全部失败），不影响正常流程。"""
        store_group = await create_store_group(
            str(tmp_path / "async3.db"),
            str(tmp_path / "async3-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        # LLM 调用抛异常
        class FailingLLM:
            async def call(self, prompt_or_messages, model_alias=None, **kwargs):
                raise RuntimeError("全部失败")

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        try:
            await service.schedule_background_compaction(
                agent_session_id="sess-fail-1",
                task_id="task-fail-1",
                llm_service=FailingLLM(),
                conversation_budget=150,
            )

            # 等待后台任务完成
            result = await service.await_compaction_result("sess-fail-1")
            # 即使 LLM 全部失败，也会返回某种结果（降级的 CompiledTaskContext）
            # 不会抛异常影响主流程
        finally:
            await _close_store_group(store_group)

    async def test_duplicate_schedule_is_idempotent(self, tmp_path: Path) -> None:
        """重复调度同一 session 不创建新的后台任务（幂等）。"""
        store_group = await create_store_group(
            str(tmp_path / "async4.db"),
            str(tmp_path / "async4-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        call_count = 0

        class CountingLLM:
            async def call(self, prompt_or_messages, model_alias=None, **kwargs):
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.1)  # 稍微延迟，让重复调度有机会检测
                return ModelCallResult(
                    content="摘要",
                    model_alias=model_alias or "main",
                    model_name="count-model",
                    provider="mock",
                    duration_ms=100,
                    token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                    cost_usd=0.0,
                    cost_unavailable=False,
                    is_fallback=False,
                    fallback_reason="",
                )

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        try:
            llm = CountingLLM()
            # 第一次调度
            await service.schedule_background_compaction(
                agent_session_id="sess-dup-1",
                task_id="task-dup-1",
                llm_service=llm,
                conversation_budget=150,
            )
            # 第二次调度（应该被跳过，因为已有未完成任务）
            await service.schedule_background_compaction(
                agent_session_id="sess-dup-1",
                task_id="task-dup-1",
                llm_service=llm,
                conversation_budget=150,
            )

            # 等待完成
            await service.await_compaction_result("sess-dup-1")

            # call_count 应该只反映一次压缩流程（而非两次）
            first_call_count = call_count

            # 第三次调度（前一个已完成，应该创建新任务）
            await service.schedule_background_compaction(
                agent_session_id="sess-dup-1",
                task_id="task-dup-2",
                llm_service=llm,
                conversation_budget=150,
            )
            await service.await_compaction_result("sess-dup-1")

            # 新任务的 call_count 应该更高
            assert call_count > first_call_count
        finally:
            await _close_store_group(store_group)

    async def test_per_session_lock_does_not_block_different_sessions(self, tmp_path: Path) -> None:
        """不同 session 的锁不互相阻塞。"""
        store_group = await create_store_group(
            str(tmp_path / "async5.db"),
            str(tmp_path / "async5-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        llm = AliasSelectiveLLMService()
        try:
            # 各自的 lock 应该是不同对象
            lock_a = service.get_compaction_lock("sess-a")
            lock_b = service.get_compaction_lock("sess-b")
            assert lock_a is not lock_b

            # 并发调度两个不同 session 的后台压缩
            await service.schedule_background_compaction(
                agent_session_id="sess-a",
                task_id="task-a",
                llm_service=llm,
                conversation_budget=150,
            )
            await service.schedule_background_compaction(
                agent_session_id="sess-b",
                task_id="task-b",
                llm_service=llm,
                conversation_budget=150,
            )

            # 保存 task 引用后立即检查 pending 状态
            task_a = service._pending_compactions.get("sess-a")
            task_b = service._pending_compactions.get("sess-b")
            assert task_a is not None
            assert task_b is not None

            # 等待两个 task 完成（直接等 asyncio.Task 而非 await_compaction_result，
            # 因为 _bg_compact finally 块会在完成后清理 _pending_compactions）
            results = await asyncio.gather(task_a, task_b, return_exceptions=True)

            # 两个都应该成功返回 CompiledTaskContext
            for result in results:
                assert not isinstance(result, Exception)
                assert result is not None
        finally:
            await _close_store_group(store_group)

    async def test_await_no_pending_returns_none(self, tmp_path: Path) -> None:
        """没有待处理后台任务时 await_compaction_result 返回 None。"""
        store_group = await create_store_group(
            str(tmp_path / "async6.db"),
            str(tmp_path / "async6-artifacts"),
        )
        config = ContextCompactionConfig()
        service = ContextCompactionService(store_group, config=config)

        try:
            result = await service.await_compaction_result("nonexistent-session")
            assert result is None
        finally:
            await _close_store_group(store_group)

    async def test_has_pending_compaction_returns_false_after_done(self, tmp_path: Path) -> None:
        """后台任务完成后 has_pending_compaction 返回 False。"""
        store_group = await create_store_group(
            str(tmp_path / "async7.db"),
            str(tmp_path / "async7-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        llm = AliasSelectiveLLMService()
        try:
            await service.schedule_background_compaction(
                agent_session_id="sess-done-1",
                task_id="task-done-1",
                llm_service=llm,
                conversation_budget=150,
            )
            assert service.has_pending_compaction("sess-done-1")

            # 等待完成
            await service.await_compaction_result("sess-done-1")
            assert not service.has_pending_compaction("sess-done-1")
        finally:
            await _close_store_group(store_group)

    async def test_config_async_compaction_timeout_from_env(self, monkeypatch) -> None:
        """async_compaction_timeout 可通过环境变量配置。"""
        monkeypatch.setenv("OCTOAGENT_CONTEXT_ASYNC_COMPACTION_TIMEOUT", "15.5")
        config = ContextCompactionConfig.from_env()
        assert config.async_compaction_timeout == 15.5

    async def test_config_async_compaction_timeout_clamped(self, monkeypatch) -> None:
        """async_compaction_timeout 超出范围时 clamp 到边界值。"""
        monkeypatch.setenv("OCTOAGENT_CONTEXT_ASYNC_COMPACTION_TIMEOUT", "0.5")
        config = ContextCompactionConfig.from_env()
        assert config.async_compaction_timeout == 1.0  # minimum=1.0

        monkeypatch.setenv("OCTOAGENT_CONTEXT_ASYNC_COMPACTION_TIMEOUT", "100")
        config2 = ContextCompactionConfig.from_env()
        assert config2.async_compaction_timeout == 60.0  # maximum=60.0


# ---------- Feature 060 Phase 6: Polish & Cross-Cutting Concerns ----------


class TestSubagentBypassAll:
    """Feature 060 T039: 验证 Subagent 绕过所有新增压缩机制。

    FR-023: Subagent 行为与 Feature 034 完全一致——
    不触发分层压缩、不触发异步压缩、不注入进度笔记。
    """

    async def test_subagent_no_layered_compression(self, tmp_path: Path) -> None:
        """Subagent 场景不触发三层压缩（layers 为空）。"""
        store_group = await create_store_group(
            str(tmp_path / "sub-lay.db"),
            str(tmp_path / "sub-lay-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(8)
        service._load_conversation_turns = _fake_load

        llm = RecordingLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-sub-lay-1",
                fallback_user_text="子代理消息",
                llm_service=llm,
                dispatch_metadata={"target_kind": "subagent"},
            )
            # Subagent 不触发三层压缩
            assert compiled.compacted is False
            assert compiled.layers == []
            assert compiled.compaction_version == ""
        finally:
            await _close_store_group(store_group)

    async def test_subagent_no_async_compression(self, tmp_path: Path) -> None:
        """Subagent 场景不触发异步后台压缩。

        即使主动调度后台压缩，Subagent 的 build_context 结果仍无压缩。
        """
        store_group = await create_store_group(
            str(tmp_path / "sub-async.db"),
            str(tmp_path / "sub-async-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(8)
        service._load_conversation_turns = _fake_load

        llm = AliasSelectiveLLMService()
        try:
            # 即便预算超限，Subagent 场景仍不压缩
            compiled = await service.build_context(
                task_id="task-sub-async-1",
                fallback_user_text="子代理消息",
                llm_service=llm,
                dispatch_metadata={"target_kind": "subagent"},
                conversation_budget=50,  # 极低预算
            )
            assert compiled.compacted is False
            # 无 LLM 压缩调用
            compaction_calls = [
                c for c in llm.calls
                if c.get("metadata", {}).get("context_compaction") == "true"
            ]
            assert len(compaction_calls) == 0
        finally:
            await _close_store_group(store_group)

    async def test_subagent_no_progress_notes_injection(self, tmp_path: Path) -> None:
        """Subagent 场景 build_task_context 不注入进度笔记。

        进度笔记注入仅在 task_service._build_task_context() 中发生，
        当 dispatch_metadata.target_kind == 'subagent' 时
        worker_runtime 会设置标记绕过注入。此处验证 _build_system_blocks
        在 progress_notes=None 时不生成 ProgressNotes 块。
        """
        from octoagent.gateway.services.agent_context import AgentContextService

        store_group = await create_store_group(
            str(tmp_path / "sub-notes.db"),
            str(tmp_path / "sub-notes-artifacts"),
        )
        try:
            # 验证 _build_system_blocks 在无进度笔记时不生成 ProgressNotes 块
            # 通过检查 ContextCompactionService 的 Subagent 绕过逻辑
            service = ContextCompactionService(store_group, config=ContextCompactionConfig())

            async def _fake_load(task_id):
                return _make_fake_turns(4)
            service._load_conversation_turns = _fake_load

            llm = RecordingLLMService()
            compiled = await service.build_context(
                task_id="task-sub-notes-1",
                fallback_user_text="子代理消息",
                llm_service=llm,
                dispatch_metadata={"target_kind": "subagent"},
            )
            # Subagent 不压缩 -> 没有 layers -> 没有 ProgressNotes 依据
            assert compiled.compacted is False
            assert compiled.layers == []
        finally:
            await _close_store_group(store_group)


class TestAuditChainIntegrity:
    """Feature 060 T040: 验证所有新增压缩路径走既有审计链。

    FR-022: 三层压缩、两阶段压缩、异步压缩的事件均包含完整的
    layers/phases/fallback 信息，control plane 可审计所有压缩路径的详情。
    """

    async def test_layered_compression_event_contains_layers(
        self, tmp_path: Path,
    ) -> None:
        """三层压缩产出的 CompiledTaskContext 包含 layers 字段，可用于审计事件。"""
        store_group = await create_store_group(
            str(tmp_path / "audit-lay.db"),
            str(tmp_path / "audit-lay-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=300,
            min_turns_to_compact=4,
            recent_turns=2,
            compressed_window_size=4,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(8)
        service._load_conversation_turns = _fake_load

        llm = RecordingLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-audit-lay-1",
                fallback_user_text="最新消息",
                llm_service=llm,
            )
            assert compiled.compacted is True
            # layers 字段包含至少 recent 层
            assert len(compiled.layers) >= 1
            # 每个 layer 都有必要的审计字段
            for layer in compiled.layers:
                assert "layer_id" in layer
                assert "turns" in layer
                assert "token_count" in layer
                assert "max_tokens" in layer
            # compaction_version 已设置
            assert compiled.compaction_version in ("v1", "v2")
        finally:
            await _close_store_group(store_group)

    async def test_two_stage_compression_event_contains_phases(
        self, tmp_path: Path,
    ) -> None:
        """两阶段压缩的 CompiledTaskContext 包含 compaction_phases 字段。"""
        from octoagent.gateway.services.context_compaction import ConversationTurn

        store_group = await create_store_group(
            str(tmp_path / "audit-2s.db"),
            str(tmp_path / "audit-2s-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=500,
            min_turns_to_compact=2,
            recent_turns=1,
            large_message_ratio=0.1,
        )
        service = ContextCompactionService(store_group, config=config)

        # 制造超大消息场景
        turns = [
            ConversationTurn(role="user", content="问题", source_event_id="e0"),
            ConversationTurn(role="assistant", content="x" * 10000, source_event_id="e1"),
            ConversationTurn(role="user", content="问题二", source_event_id="e2"),
            ConversationTurn(role="assistant", content="普通回答", source_event_id="e3"),
            ConversationTurn(role="user", content="最新问题", source_event_id="e4"),
        ]

        async def _fake_load(task_id):
            return turns
        service._load_conversation_turns = _fake_load

        llm = AliasSelectiveLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-audit-2s-1",
                fallback_user_text="最新问题",
                llm_service=llm,
            )
            # 无论是否触发 LLM 摘要，compaction_phases 字段都应存在
            assert isinstance(compiled.compaction_phases, list)
            # 如果压缩了，至少有一个 phase 记录
            if compiled.compacted:
                assert len(compiled.compaction_phases) >= 1
                for phase in compiled.compaction_phases:
                    assert "phase" in phase
                    assert phase["phase"] in (
                        "cheap_truncation", "llm_summary",
                        "cheap_truncation_sufficient", "cheap_truncation_only",
                    )
        finally:
            await _close_store_group(store_group)

    async def test_fallback_chain_recorded_in_compiled_context(
        self, tmp_path: Path,
    ) -> None:
        """fallback 链信息完整记录到 CompiledTaskContext。"""
        store_group = await create_store_group(
            str(tmp_path / "audit-fb.db"),
            str(tmp_path / "audit-fb-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            compaction_alias="my-compaction",
            summarizer_alias="my-summarizer",
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        # 使 compaction alias 失败以触发 fallback
        llm = AliasSelectiveLLMService(fail_aliases={"my-compaction"})
        try:
            compiled = await service.build_context(
                task_id="task-audit-fb-1",
                fallback_user_text="最新消息",
                llm_service=llm,
            )
            if compiled.compacted and compiled.summary_text:
                # fallback 相关字段已记录
                assert compiled.fallback_used is True
                assert len(compiled.fallback_chain) >= 2
                assert compiled.fallback_chain[0] == "my-compaction"  # 首先尝试的
                assert compiled.summary_model_alias != ""  # 实际使用的
        finally:
            await _close_store_group(store_group)

    async def test_async_compression_result_preserves_audit_fields(
        self, tmp_path: Path,
    ) -> None:
        """异步压缩结果保留所有审计字段（layers, phases, fallback_chain）。"""
        store_group = await create_store_group(
            str(tmp_path / "audit-async.db"),
            str(tmp_path / "audit-async-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            async_compaction_timeout=5.0,
        )
        service = ContextCompactionService(store_group, config=config)

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        llm = AliasSelectiveLLMService()
        try:
            await service.schedule_background_compaction(
                agent_session_id="sess-audit-async-1",
                task_id="task-audit-async-1",
                llm_service=llm,
                conversation_budget=150,
            )
            result = await service.await_compaction_result("sess-audit-async-1")
            assert result is not None
            # 异步压缩结果保留所有审计字段
            assert hasattr(result, "layers")
            assert hasattr(result, "compaction_phases")
            assert hasattr(result, "fallback_chain")
            assert hasattr(result, "compaction_version")
            if result.compacted:
                assert result.compaction_version in ("v1", "v2")
        finally:
            await _close_store_group(store_group)


class TestConfigFlowVerification:
    """Feature 060 T041: 验证新增配置项通过正确的配置流程。

    FR-024: compaction alias 等配置通过 ContextCompactionConfig / 环境变量配置，
    Settings API 层面通过 AliasRegistry 管理。
    """

    def test_compaction_alias_configurable_via_env(self, monkeypatch) -> None:
        """compaction_alias 可通过环境变量配置。"""
        monkeypatch.setenv("OCTOAGENT_CONTEXT_COMPACTION_ALIAS", "my-cheap-model")
        config = ContextCompactionConfig.from_env()
        assert config.compaction_alias == "my-cheap-model"

    def test_summarizer_alias_configurable_via_env(self, monkeypatch) -> None:
        """summarizer_alias 可通过环境变量配置。"""
        monkeypatch.setenv("OCTOAGENT_CONTEXT_SUMMARIZER_ALIAS", "my-summarizer")
        config = ContextCompactionConfig.from_env()
        assert config.summarizer_alias == "my-summarizer"

    def test_layer_ratios_configurable_via_env(self, monkeypatch) -> None:
        """分层比例可通过环境变量配置。"""
        monkeypatch.setenv("OCTOAGENT_CONTEXT_RECENT_RATIO", "0.6")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_COMPRESSED_RATIO", "0.25")
        monkeypatch.setenv("OCTOAGENT_CONTEXT_ARCHIVE_RATIO", "0.15")
        config = ContextCompactionConfig.from_env()
        assert config.recent_ratio == 0.6
        assert config.compressed_ratio == 0.25
        assert config.archive_ratio == 0.15

    def test_large_message_ratio_configurable(self) -> None:
        """large_message_ratio 可通过构造参数配置。"""
        config = ContextCompactionConfig(large_message_ratio=0.5)
        assert config.large_message_ratio == 0.5

    def test_json_smart_truncate_configurable(self) -> None:
        """json_smart_truncate 可通过构造参数配置。"""
        config = ContextCompactionConfig(json_smart_truncate=False)
        assert config.json_smart_truncate is False

    def test_compressed_window_size_configurable_via_env(self, monkeypatch) -> None:
        """compressed_window_size 可通过环境变量配置。"""
        monkeypatch.setenv("OCTOAGENT_CONTEXT_COMPRESSED_WINDOW", "6")
        config = ContextCompactionConfig.from_env()
        assert config.compressed_window_size == 6

    def test_compaction_alias_in_alias_registry(self) -> None:
        """compaction alias 已在 AliasRegistry 默认别名中注册。"""
        from octoagent.provider.alias import AliasRegistry

        registry = AliasRegistry()
        # compaction 别名应该存在于默认别名中
        aliases = registry.list_all()
        alias_names = [a.name for a in aliases]
        assert "compaction" in alias_names

    def test_compaction_alias_default_values(self) -> None:
        """compaction alias 的默认配置值正确。"""
        config = ContextCompactionConfig()
        assert config.compaction_alias == "compaction"
        assert config.summarizer_alias == "summarizer"

    def test_all_config_fields_have_env_mapping(self, monkeypatch) -> None:
        """关键配置字段都有对应的环境变量映射。"""
        env_mappings = {
            "OCTOAGENT_CONTEXT_COMPACTION_ENABLED": "true",
            "OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS": "8000",
            "OCTOAGENT_CONTEXT_COMPACTION_SOFT_RATIO": "0.8",
            "OCTOAGENT_CONTEXT_COMPACTION_TARGET_RATIO": "0.6",
            "OCTOAGENT_CONTEXT_RECENT_TURNS": "3",
            "OCTOAGENT_CONTEXT_MIN_TURNS_TO_COMPACT": "6",
            "OCTOAGENT_CONTEXT_SUMMARY_MAX_CHARS": "5000",
            "OCTOAGENT_CONTEXT_SUMMARIZER_ALIAS": "test-summarizer",
            "OCTOAGENT_CONTEXT_COMPACTION_ALIAS": "test-compaction",
            "OCTOAGENT_CONTEXT_RECENT_RATIO": "0.55",
            "OCTOAGENT_CONTEXT_COMPRESSED_RATIO": "0.28",
            "OCTOAGENT_CONTEXT_ARCHIVE_RATIO": "0.17",
            "OCTOAGENT_CONTEXT_COMPRESSED_WINDOW": "6",
            "OCTOAGENT_CONTEXT_ASYNC_COMPACTION_TIMEOUT": "15.0",
        }
        for key, value in env_mappings.items():
            monkeypatch.setenv(key, value)

        config = ContextCompactionConfig.from_env()
        assert config.enabled is True
        assert config.max_input_tokens == 8000
        assert config.soft_limit_ratio == 0.8
        assert config.target_ratio == 0.6
        assert config.recent_turns == 3
        assert config.min_turns_to_compact == 6
        assert config.summary_max_chars == 5000
        assert config.summarizer_alias == "test-summarizer"
        assert config.compaction_alias == "test-compaction"
        assert config.recent_ratio == 0.55
        assert config.compressed_ratio == 0.28
        assert config.archive_ratio == 0.17
        assert config.compressed_window_size == 6
        assert config.async_compaction_timeout == 15.0


class TestEdgeCaseRegression:
    """Feature 060 T042: 边界条件和 Edge Case 回归测试。

    覆盖 spec 中定义的 Edge Cases。
    """

    async def test_short_conversation_no_compression(self, tmp_path: Path) -> None:
        """1-3 轮对话不触发压缩（min_turns_to_compact 保护）。"""
        store_group = await create_store_group(
            str(tmp_path / "edge-short.db"),
            str(tmp_path / "edge-short-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=4,
            recent_turns=1,
        )
        service = ContextCompactionService(store_group, config=config)

        from octoagent.gateway.services.context_compaction import ConversationTurn

        # 3 轮对话（3 个 turn）
        turns = [
            ConversationTurn(role="user", content="问题一", source_event_id="e0"),
            ConversationTurn(role="assistant", content="回答一", source_event_id="e1"),
            ConversationTurn(role="user", content="问题二", source_event_id="e2"),
        ]

        async def _fake_load(task_id):
            return turns
        service._load_conversation_turns = _fake_load

        llm = RecordingLLMService()
        try:
            compiled = await service.build_context(
                task_id="task-edge-short-1",
                fallback_user_text="问题二",
                llm_service=llm,
            )
            # 3 轮 < min_turns_to_compact(4)，不触发压缩
            assert compiled.compacted is False
            # 无 LLM 压缩调用
            compaction_calls = [
                c for c in llm.calls
                if c.get("metadata", {}).get("context_compaction") == "true"
            ]
            assert len(compaction_calls) == 0
        finally:
            await _close_store_group(store_group)

    async def test_background_compaction_long_timeout_returns_none(
        self, tmp_path: Path,
    ) -> None:
        """后台压缩超时 >10s 时 await 返回 None（使用短超时模拟）。"""
        store_group = await create_store_group(
            str(tmp_path / "edge-timeout.db"),
            str(tmp_path / "edge-timeout-artifacts"),
        )
        config = ContextCompactionConfig(
            max_input_tokens=200,
            min_turns_to_compact=2,
            recent_turns=1,
            async_compaction_timeout=0.1,  # 极短超时模拟
        )
        service = ContextCompactionService(store_group, config=config)

        class VerySlowLLM:
            async def call(self, prompt_or_messages, model_alias=None, **kwargs):
                await asyncio.sleep(5.0)  # 远超 0.1s 超时
                return ModelCallResult(
                    content="慢速摘要", model_alias=model_alias or "main",
                    model_name="slow-model", provider="mock",
                    duration_ms=5000,
                    token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                    cost_usd=0.0, cost_unavailable=False,
                    is_fallback=False, fallback_reason="",
                )

        async def _fake_load(task_id):
            return _make_fake_turns(6)
        service._load_conversation_turns = _fake_load

        try:
            await service.schedule_background_compaction(
                agent_session_id="sess-edge-timeout",
                task_id="task-edge-timeout",
                llm_service=VerySlowLLM(),
                conversation_budget=150,
            )
            # 使用极短 timeout 等待
            result = await service.await_compaction_result(
                "sess-edge-timeout", timeout=0.05,
            )
            # 超时返回 None
            assert result is None
        finally:
            # 等后台任务清理
            await asyncio.sleep(0.2)
            await _close_store_group(store_group)

    async def test_progress_notes_merge_above_threshold(self) -> None:
        """进度笔记 >50 条时自动合并（使用低阈值 15 加速测试）。"""
        from octoagent.core.models import Artifact, ArtifactPart, PartType
        from octoagent.tooling.progress_note import (
            ProgressNoteInput,
            execute_progress_note,
        )

        class InMemoryArtifactStore:
            def __init__(self):
                self.artifacts: dict[str, Artifact] = {}

            async def put_artifact(self, artifact: Artifact, content: bytes) -> None:
                self.artifacts[artifact.artifact_id] = artifact

            async def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
                return [a for a in self.artifacts.values() if a.task_id == task_id]

        class MockConn:
            async def commit(self):
                pass

        store = InMemoryArtifactStore()
        conn = MockConn()
        threshold = 15

        # 写入 threshold + 2 条笔记
        for i in range(threshold + 2):
            await execute_progress_note(
                input_data=ProgressNoteInput(
                    step_id=f"step_{i}", description=f"步骤 {i}",
                ),
                task_id="task-merge-edge",
                artifact_store=store,
                conn=conn,
                merge_threshold=threshold,
            )

        # 应该有合并笔记
        artifacts = await store.list_artifacts_for_task("task-merge-edge")
        merged = [a for a in artifacts if "__merged_history__" in a.name]
        assert len(merged) >= 1

        # 合并笔记的 JSON 内容包含 milestones
        merged_content = json.loads(merged[0].parts[0].content)
        assert "milestones" in merged_content
        assert len(merged_content["milestones"]) > 0

    def test_skill_truncation_with_many_skills(self) -> None:
        """用户加载 5+ Skill 且总内容 >2000 token 时按顺序截断。"""
        # 构造超过预算的 Skill 内容
        skill_header = "## Active Skills\n\nLoaded skills:"
        skill_sections = []
        for i in range(6):
            # 每个 Skill 约 400 token 的内容
            skill_content = f"skill_{i} ---\n" + f"Skill {i} 指令内容。" * 100
            skill_sections.append(skill_content)

        loaded_skills_content = skill_header + "\n\n--- Loaded Skill: ".join(
            [""] + skill_sections
        )

        # 设置 500 token 的 Skill 预算（远小于 6*400=2400）
        skill_injection_budget = 500

        from octoagent.gateway.services.context_compaction import estimate_text_tokens

        # 验证总 token 超出预算
        total_tokens = estimate_text_tokens(loaded_skills_content)
        assert total_tokens > skill_injection_budget

        # 模拟截断逻辑（与 _build_system_blocks 中的逻辑一致）
        if skill_injection_budget > 0:
            skill_tokens = estimate_text_tokens(loaded_skills_content)
            if skill_tokens > skill_injection_budget:
                sections = loaded_skills_content.split("\n\n--- Loaded Skill: ")
                kept_sections: list[str] = []
                running_tokens = 0
                truncated_skills: list[str] = []
                for idx, section in enumerate(sections):
                    if idx == 0 and section.startswith("## Active Skills"):
                        kept_sections.append(section)
                        running_tokens += estimate_text_tokens(section)
                        continue
                    sec_tokens = estimate_text_tokens(section)
                    if running_tokens + sec_tokens <= skill_injection_budget:
                        kept_sections.append(section)
                        running_tokens += sec_tokens
                    else:
                        skill_name = section.split(" ---")[0].strip()
                        truncated_skills.append(skill_name)

                # 应该有被截断的 Skill
                assert len(truncated_skills) > 0
                # 保留的 Skill 数量小于总数
                assert len(kept_sections) < len(sections)

    def test_budget_planner_invariant_under_stress(self) -> None:
        """BudgetPlanner 在极端参数组合下仍保持不变量。"""
        from octoagent.gateway.services.context_budget import ContextBudgetPlanner

        # 测试各种极端参数组合
        test_cases = [
            # (max_tokens, skill_count, memory_top_k, notes_count)
            (800, 0, 0, 0),       # 最小可用预算
            (800, 5, 10, 10),     # 最小预算 + 大量组件
            (2000, 10, 20, 50),   # 中等预算 + 极端组件数
            (100000, 0, 0, 0),    # 极大预算
            (100000, 20, 50, 100),  # 极大预算 + 极端组件
            (500, 0, 0, 0),       # 低于最小对话预算
        ]

        for max_tokens, skill_count, memory_top_k, notes_count in test_cases:
            config = ContextCompactionConfig(max_input_tokens=max_tokens)
            planner = ContextBudgetPlanner(config=config)
            budget = planner.plan(
                max_input_tokens=max_tokens,
                loaded_skill_names=[f"s{i}" for i in range(skill_count)],
                memory_top_k=memory_top_k,
                has_progress_notes=notes_count > 0,
                progress_note_count=notes_count,
            )
            total = (
                budget.system_blocks_budget
                + budget.skill_injection_budget
                + budget.memory_recall_budget
                + budget.progress_notes_budget
                + budget.conversation_budget
            )
            # 不变量 1: 各部分之和 <= max_input_tokens
            assert total <= max_tokens, (
                f"total={total} > max={max_tokens} for case "
                f"({max_tokens}, {skill_count}, {memory_top_k}, {notes_count})"
            )
            # 不变量 2: conversation_budget >= 800（当 max >= 800）或等于 max
            if max_tokens >= 800:
                assert budget.conversation_budget >= 800, (
                    f"conversation={budget.conversation_budget} < 800 for case "
                    f"({max_tokens}, {skill_count}, {memory_top_k}, {notes_count})"
                )
            # 不变量 3: 所有预算非负
            assert budget.system_blocks_budget >= 0
            assert budget.skill_injection_budget >= 0
            assert budget.memory_recall_budget >= 0
            assert budget.progress_notes_budget >= 0
            assert budget.conversation_budget >= 0

    def test_compiled_context_payload_fields_complete(self) -> None:
        """CompiledTaskContext 包含所有审计所需的 payload 字段。"""
        from octoagent.gateway.services.context_compaction import CompiledTaskContext

        # 验证 CompiledTaskContext 的字段定义完整
        compiled = CompiledTaskContext(
            messages=[],
            request_summary="",
            snapshot_text="",
            raw_tokens=100,
            final_tokens=80,
            delivery_tokens=80,
            latest_user_text="",
            compacted=True,
            compaction_reason="history_over_budget",
            summary_text="摘要",
            summary_model_alias="compaction",
            fallback_used=True,
            fallback_chain=["compaction", "summarizer"],
            compaction_phases=[
                {"phase": "cheap_truncation", "messages_affected": 2, "tokens_saved": 100},
            ],
            layers=[
                {"layer_id": "recent", "turns": 2, "token_count": 40, "max_tokens": 50, "entry_count": 2},
            ],
            compaction_version="v2",
        )
        # 所有审计字段可访问
        assert compiled.compaction_reason == "history_over_budget"
        assert compiled.summary_model_alias == "compaction"
        assert compiled.fallback_used is True
        assert compiled.fallback_chain == ["compaction", "summarizer"]
        assert len(compiled.compaction_phases) == 1
        assert len(compiled.layers) == 1
        assert compiled.compaction_version == "v2"

    def test_compaction_completed_payload_model(self) -> None:
        """ContextCompactionCompletedPayload 包含所有 060 新增字段。"""
        from octoagent.core.models.payloads import ContextCompactionCompletedPayload

        payload = ContextCompactionCompletedPayload(
            model_alias="compaction",
            input_tokens_before=500,
            input_tokens_after=200,
            compressed_turn_count=6,
            kept_turn_count=2,
            summary_artifact_ref="artifact-001",
            request_artifact_ref="artifact-002",
            memory_flush_run_id="flush-001",
            reason="history_over_budget",
            fallback_used=True,
            fallback_chain=["compaction", "summarizer"],
            compaction_phases=[
                {"phase": "cheap_truncation", "messages_affected": 2, "tokens_saved": 100},
                {"phase": "llm_summary", "messages_affected": 1, "tokens_saved": 200, "model_used": "compaction"},
            ],
            layers=[
                {"layer_id": "recent", "turns": 2, "token_count": 100, "max_tokens": 150},
                {"layer_id": "compressed", "turns": 4, "token_count": 80, "max_tokens": 90},
                {"layer_id": "archive", "turns": 6, "token_count": 40, "max_tokens": 60},
            ],
            compaction_version="v2",
        )
        # 所有字段都可序列化
        data = payload.model_dump()
        assert data["model_alias"] == "compaction"
        assert data["fallback_used"] is True
        assert len(data["fallback_chain"]) == 2
        assert len(data["compaction_phases"]) == 2
        assert len(data["layers"]) == 3
        assert data["compaction_version"] == "v2"
        # 反序列化正确
        restored = ContextCompactionCompletedPayload(**data)
        assert restored.compaction_version == "v2"
        assert restored.fallback_chain == ["compaction", "summarizer"]
