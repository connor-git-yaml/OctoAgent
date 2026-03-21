"""Wave 2: A2A store 持久化测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    A2AConversation,
    A2AConversationStatus,
    A2AMessageDirection,
    A2AMessageRecord,
    RequesterInfo,
    RiskLevel,
    Task,
    TaskPointers,
    TaskStatus,
)
from octoagent.core.store import create_store_group


async def test_a2a_store_roundtrip(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "a2a.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.task_store.create_task(
        Task(
            task_id="task-weather",
            title="weather",
            thread_id="thread-weather",
            scope_id="scope-weather",
            requester=RequesterInfo(channel="web", sender_id="owner"),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            status=TaskStatus.CREATED,
            risk_level=RiskLevel.LOW,
            pointers=TaskPointers(),
        )
    )
    conversation = A2AConversation(
        a2a_conversation_id="work-weather",
        task_id="task-weather",
        work_id="work-weather",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        source_agent_runtime_id="runtime-butler-alpha",
        source_agent_session_id="session-butler-alpha",
        target_agent_runtime_id="runtime-worker-research",
        target_agent_session_id="session-worker-research",
        source_agent="agent://butler.main",
        target_agent="agent://worker.llm.default",
        context_frame_id="context-frame-alpha",
        request_message_id="message-1",
        latest_message_id="message-2",
        latest_message_type="RESULT",
        status=A2AConversationStatus.COMPLETED,
        message_count=2,
        trace_id="trace-task-weather",
        metadata={"worker_capability": "llm_generation"},
    )
    task_message = A2AMessageRecord(
        a2a_message_id="message-1",
        a2a_conversation_id=conversation.a2a_conversation_id,
        message_seq=1,
        task_id=conversation.task_id,
        work_id=conversation.work_id,
        project_id=conversation.project_id,
        workspace_id=conversation.workspace_id,
        source_agent_runtime_id=conversation.source_agent_runtime_id,
        source_agent_session_id=conversation.source_agent_session_id,
        target_agent_runtime_id=conversation.target_agent_runtime_id,
        target_agent_session_id=conversation.target_agent_session_id,
        direction=A2AMessageDirection.OUTBOUND,
        message_type="TASK",
        protocol_message_id="dispatch-weather",
        from_agent=conversation.source_agent,
        to_agent=conversation.target_agent,
        idempotency_key="task-weather:dispatch-weather:task",
        payload={"user_text": "深圳今天天气怎么样？"},
        trace={"trace_id": conversation.trace_id},
        metadata={"route_reason": "freshness"},
        raw_message={"type": "TASK"},
    )
    result_message = A2AMessageRecord(
        a2a_message_id="message-2",
        a2a_conversation_id=conversation.a2a_conversation_id,
        message_seq=2,
        task_id=conversation.task_id,
        work_id=conversation.work_id,
        project_id=conversation.project_id,
        workspace_id=conversation.workspace_id,
        source_agent_runtime_id=conversation.target_agent_runtime_id,
        source_agent_session_id=conversation.target_agent_session_id,
        target_agent_runtime_id=conversation.source_agent_runtime_id,
        target_agent_session_id=conversation.source_agent_session_id,
        direction=A2AMessageDirection.INBOUND,
        message_type="RESULT",
        protocol_message_id="dispatch-weather-result",
        from_agent=conversation.target_agent,
        to_agent=conversation.source_agent,
        idempotency_key="task-weather:dispatch-weather:result",
        payload={"summary": "深圳晴天，21C。"},
        trace={"trace_id": conversation.trace_id},
        metadata={"backend": "inline"},
        raw_message={"type": "RESULT"},
    )

    await store_group.a2a_store.save_conversation(conversation)
    await store_group.a2a_store.save_message(task_message)
    await store_group.a2a_store.save_message(result_message)
    await store_group.conn.commit()

    stored_conversation = await store_group.a2a_store.get_conversation(
        conversation.a2a_conversation_id
    )
    stored_for_work = await store_group.a2a_store.get_conversation_for_work(conversation.work_id)
    stored_messages = await store_group.a2a_store.list_messages(
        a2a_conversation_id=conversation.a2a_conversation_id
    )
    next_seq = await store_group.a2a_store.get_next_message_seq(conversation.a2a_conversation_id)

    assert stored_conversation is not None
    assert stored_conversation.status == A2AConversationStatus.COMPLETED
    assert stored_conversation.request_message_id == "message-1"
    assert stored_for_work is not None
    assert stored_for_work.a2a_conversation_id == conversation.a2a_conversation_id
    assert [item.a2a_message_id for item in stored_messages] == ["message-1", "message-2"]
    assert stored_messages[0].direction == A2AMessageDirection.OUTBOUND
    assert stored_messages[1].direction == A2AMessageDirection.INBOUND
    assert next_seq == 3

    await store_group.conn.close()


async def test_a2a_store_append_message_assigns_unique_seq_under_concurrency(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "a2a-concurrency.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.task_store.create_task(
        Task(
            task_id="task-concurrency",
            title="concurrency",
            thread_id="thread-concurrency",
            scope_id="scope-concurrency",
            requester=RequesterInfo(channel="web", sender_id="owner"),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            status=TaskStatus.CREATED,
            risk_level=RiskLevel.LOW,
            pointers=TaskPointers(),
        )
    )
    conversation = A2AConversation(
        a2a_conversation_id="conversation-concurrency",
        task_id="task-concurrency",
        work_id="work-concurrency",
        source_agent="agent://butler.main",
        target_agent="agent://worker.test",
    )
    await store_group.a2a_store.save_conversation(conversation)
    await store_group.conn.commit()

    async def append_message(message_id: str) -> A2AMessageRecord:
        return await store_group.a2a_store.append_message(
            conversation.a2a_conversation_id,
            lambda message_seq: A2AMessageRecord(
                a2a_message_id=message_id,
                a2a_conversation_id=conversation.a2a_conversation_id,
                message_seq=message_seq,
                task_id=conversation.task_id,
                work_id=conversation.work_id,
                direction=A2AMessageDirection.OUTBOUND,
                message_type="HEARTBEAT",
                protocol_message_id=f"{message_id}-protocol",
                from_agent=conversation.source_agent,
                to_agent=conversation.target_agent,
                idempotency_key=f"{message_id}-idempotency",
                payload={"message_id": message_id},
                trace={"trace_id": "trace-concurrency"},
                metadata={},
                raw_message={"type": "HEARTBEAT"},
            ),
        )

    first, second = await asyncio.gather(
        append_message("message-concurrency-1"),
        append_message("message-concurrency-2"),
    )
    await store_group.conn.commit()

    stored_messages = await store_group.a2a_store.list_messages(
        a2a_conversation_id=conversation.a2a_conversation_id
    )

    assert sorted([first.message_seq, second.message_seq]) == [1, 2]
    assert [item.message_seq for item in stored_messages] == [1, 2]

    await store_group.conn.close()
