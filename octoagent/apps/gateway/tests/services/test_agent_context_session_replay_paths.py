"""Agent context session replay 的窄路径回归测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from octoagent.gateway.services.agent_context import AgentContextService
from octoagent.gateway.services.agent_context_helpers import SessionReplayProjection
from octoagent.gateway.services.agent_context_session_replay import (
    AgentContextSessionReplayMixin,
    ResponseContextStorageRequest,
)
from octoagent.gateway.services.runtime_service_bundle import RuntimeServiceModeError
from octoagent.gateway.services.task_service import TaskService


def test_session_replay_render_and_transcript_deduplication_paths() -> None:
    projection = SessionReplayProjection(
        transcript_entries=[{"role": "user", "content": "hello"}],
        dropped_orphan_tool_calls=1,
        dropped_orphan_tool_results=2,
    )

    rendered = AgentContextSessionReplayMixin.render_agent_session_replay_block(projection)
    assert "dropped_orphan_tool_calls=1" in rendered
    assert "dropped_orphan_tool_results=2" in rendered

    entries = AgentContextSessionReplayMixin._append_session_transcript_entries(
        existing_entries=[],
        task_id="task-1",
        latest_user_text="hello",
        model_response="world",
    )
    assert entries == [
        {"role": "user", "content": "hello", "task_id": "task-1"},
        {"role": "assistant", "content": "world", "task_id": "task-1"},
    ]

    duplicate = AgentContextSessionReplayMixin._append_session_transcript_entries(
        existing_entries=entries,
        task_id="task-1",
        latest_user_text="hello",
        model_response="world",
    )
    assert duplicate == entries

    replaced = AgentContextSessionReplayMixin._append_session_transcript_entries(
        existing_entries=[
            {"role": "user", "content": "old", "task_id": "task-1"},
            {"role": "assistant", "content": "reply", "task_id": "task-1"},
        ],
        task_id="task-1",
        latest_user_text="new",
        model_response="answer",
    )
    assert replaced == [
        {"role": "user", "content": "new", "task_id": "task-1"},
        {"role": "assistant", "content": "answer", "task_id": "task-1"},
    ]


def test_task_service_runtime_guard_and_response_spacing_paths() -> None:
    service = object.__new__(TaskService)
    service._storage_only = True
    service._runtime_services = None

    with pytest.raises(RuntimeServiceModeError):
        service._require_runtime_mode("test")

    sanitized = TaskService._sanitize_user_visible_response(
        '可见内容\n\nto=memory.search\n{"query": "secret"}\n最终内容'
    )
    assert sanitized == "可见内容\n\n最终内容"


async def test_response_storage_missing_task_and_runtime_dependency_rejection() -> None:
    replay = AgentContextSessionReplayMixin()
    replay._stores = SimpleNamespace(task_store=AsyncMock(get_task=AsyncMock(return_value=None)))
    result = await replay.persist_response_context_storage(
        ResponseContextStorageRequest(
            task_id="missing-task",
            context_frame_id="frame",
            request_artifact_id="request",
            response_artifact_id="response",
            latest_user_text="hello",
            model_response="world",
        )
    )
    assert result == (None, None)

    with pytest.raises(RuntimeServiceModeError):
        AgentContextService(
            SimpleNamespace(),
            storage_only=True,
            llm_service=object(),
        )
