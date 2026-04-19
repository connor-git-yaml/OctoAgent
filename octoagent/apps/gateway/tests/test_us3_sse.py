"""US-3 SSE 集成测试 -- T042

测试内容：
1. SSE 连接建立成功
2. 历史事件接收
3. 任务不存在时返回 404
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import ActorType, Event, EventType, TaskStatus
from octoagent.core.models.payloads import StateTransitionPayload
from octoagent.core.store import create_store_group
from octoagent.core.store.transaction import append_event_and_update_task, append_event_only
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    """创建测试用 FastAPI app"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from fastapi import FastAPI
    from octoagent.gateway.routes import message, stream

    app = FastAPI()
    app.include_router(message.router)
    app.include_router(stream.router)

    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = None

    yield app

    await store_group.conn.close()
    os.environ.pop("OCTOAGENT_DB_PATH", None)
    os.environ.pop("OCTOAGENT_ARTIFACTS_DIR", None)
    os.environ.pop("LOGFIRE_SEND_TO_LOGFIRE", None)


@pytest_asyncio.fixture
async def client(test_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestSSE:
    """US-3: SSE 实时事件推送"""

    async def test_sse_404_for_nonexistent_task(self, client: AsyncClient):
        """任务不存在时返回 404"""
        resp = await client.get("/api/stream/task/01JNONEXISTENT0000000000")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "TASK_NOT_FOUND"

    async def test_sse_receives_history_for_terminal_task(
        self, client: AsyncClient, test_app
    ):
        """已终态的任务：连接后接收所有历史事件并关闭"""
        # 创建任务
        resp = await client.post(
            "/api/message",
            json={
                "text": "SSE terminal 测试",
                "idempotency_key": "sse-terminal-001",
            },
        )
        task_id = resp.json()["task_id"]

        # 手动推进到终态
        store_group = test_app.state.store_group
        from ulid import ULID

        now = datetime.now(UTC)

        # STATE_TRANSITION: CREATED -> RUNNING
        event_3 = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            event_3,
            "RUNNING",
        )

        # STATE_TRANSITION: RUNNING -> SUCCEEDED
        event_4 = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=4,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            event_4,
            "SUCCEEDED",
        )

        # SSE 连接 -- 应该收到所有历史事件后关闭
        events_received = []
        async with client.stream(
            "GET", f"/api/stream/task/{task_id}"
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[len("data:"):].strip())
                    events_received.append(data)

        # 验证收到了所有事件
        assert len(events_received) == 4
        assert events_received[0]["type"] == "TASK_CREATED"
        assert events_received[1]["type"] == "USER_MESSAGE"
        assert events_received[3]["type"] == "STATE_TRANSITION"
        assert events_received[3]["final"] is True

    async def test_sse_after_event_id_skips_replayed_history(
        self, client: AsyncClient, test_app
    ):
        """续聊游标：只回放指定事件之后的新历史，避免把旧回复重放给新一轮对话。"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "SSE cursor 测试",
                "idempotency_key": "sse-cursor-001",
            },
        )
        task_id = resp.json()["task_id"]

        store_group = test_app.state.store_group
        from ulid import ULID

        now = datetime.now(UTC)
        event_3 = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            event_3,
            "RUNNING",
        )

        event_4 = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=4,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            event_4,
            "SUCCEEDED",
        )

        events_received = []
        async with client.stream(
            "GET",
            f"/api/stream/task/{task_id}?after_event_id={event_3.event_id}",
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[len("data:"):].strip())
                    events_received.append(data)

        assert len(events_received) == 1
        assert events_received[0]["event_id"] == event_4.event_id
        assert events_received[0]["type"] == "STATE_TRANSITION"
        assert events_received[0]["final"] is True

    async def test_sse_realtime_push(self, client: AsyncClient, test_app):
        """实时推送：先连接 SSE，再写入新事件"""
        # 创建任务
        resp = await client.post(
            "/api/message",
            json={
                "text": "SSE realtime 测试",
                "idempotency_key": "sse-realtime-001",
            },
        )
        task_id = resp.json()["task_id"]
        sse_hub = test_app.state.sse_hub

        # 通过 SSEHub 直接测试广播
        queue = await sse_hub.subscribe(task_id)

        now = datetime.now(UTC)
        from ulid import ULID

        broadcast_event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await sse_hub.broadcast(task_id, broadcast_event)

        # 从队列中接收
        received = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert received.event_id == broadcast_event.event_id
        assert received.type == EventType.STATE_TRANSITION

        await sse_hub.unsubscribe(task_id, queue)

    async def test_sse_hub_subscribe_unsubscribe(self, test_app):
        """SSEHub 订阅/取消订阅"""
        sse_hub = test_app.state.sse_hub

        queue = await sse_hub.subscribe("test-task-sub")
        assert "test-task-sub" in sse_hub._subscribers
        assert queue in sse_hub._subscribers["test-task-sub"]

        await sse_hub.unsubscribe("test-task-sub", queue)
        assert "test-task-sub" not in sse_hub._subscribers

    async def test_sse_dedups_events_present_in_history_and_queue(
        self, client: AsyncClient, test_app
    ):
        """回归：历史快照与订阅队列重叠的 event 只能推送一次。

        修复 publish-before-subscribe 竞态后，stream 层改为先订阅再读历史，
        一旦 broadcast 发生在读历史期间，同一个 event 可能同时出现在
        历史快照和订阅队列里。验证 dedup 生效，不会向前端推两次。
        """
        from ulid import ULID

        resp = await client.post(
            "/api/message",
            json={
                "text": "SSE dedup 测试",
                "idempotency_key": "sse-dedup-001",
            },
        )
        task_id = resp.json()["task_id"]
        store_group = test_app.state.store_group
        sse_hub = test_app.state.sse_hub

        now = datetime.now(UTC)
        running_event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            running_event,
            "RUNNING",
        )

        # 模拟一个在订阅建立之后、读历史之前 broadcast 的事件：
        # 它已经写进 store（会出现在历史快照里），又会通过 sse_hub 进订阅队列。
        overlap_event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=4,
            ts=now,
            type=EventType.MODEL_CALL_COMPLETED,
            actor=ActorType.SYSTEM,
            payload={"response_summary": "dedup 测试回复"},
            trace_id=f"trace-{task_id}",
        )
        await append_event_only(
            store_group.conn,
            store_group.event_store,
            overlap_event,
        )

        succeeded_event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=5,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )

        events_received: list[dict] = []

        async def reader() -> None:
            async with client.stream(
                "GET", f"/api/stream/task/{task_id}"
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data = json.loads(line[len("data:") :].strip())
                        events_received.append(data)

        read_task = asyncio.create_task(reader())
        # 给 SSE 连接、subscribe、读历史一点窗口
        await asyncio.sleep(0.1)

        # 订阅建立后主动广播 overlap_event：它此刻也已经在历史里，dedup 应只推一次
        await sse_hub.broadcast(task_id, overlap_event)

        # 推进到终态触发 final
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            succeeded_event,
            "SUCCEEDED",
        )
        await sse_hub.broadcast(task_id, succeeded_event)

        await asyncio.wait_for(read_task, timeout=5.0)

        overlap_hits = [
            e for e in events_received if e["event_id"] == overlap_event.event_id
        ]
        assert len(overlap_hits) == 1, (
            f"overlap event should be yielded exactly once, got {len(overlap_hits)}"
        )
        assert overlap_hits[0]["type"] == "MODEL_CALL_COMPLETED"

        succeeded_hits = [
            e for e in events_received if e["event_id"] == succeeded_event.event_id
        ]
        assert len(succeeded_hits) == 1
        assert succeeded_hits[0]["final"] is True

    async def test_sse_marks_final_when_terminal_event_landed_after_snapshot(
        self, client: AsyncClient, test_app
    ):
        """回归 Codex 指出的终态 overlap bug：

        task 在进入 SSE 路由时状态是 RUNNING（入口快照非终态），但在
        subscribe 之后、读历史期间终态 STATE_TRANSITION 落库。新路由必须
        按事件本身判断 final，否则终态事件会被标成 final=false 并在订阅
        侧 drain 时被 dedup 跳过，前端永远收不到 final=true。
        """
        from ulid import ULID

        resp = await client.post(
            "/api/message",
            json={
                "text": "SSE terminal overlap 测试",
                "idempotency_key": "sse-terminal-overlap-001",
            },
        )
        task_id = resp.json()["task_id"]
        store_group = test_app.state.store_group

        now = datetime.now(UTC)
        running_event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            running_event,
            "RUNNING",
        )

        # 关键：只 append event、不更新 task.status，模拟 task projection
        # 还没来得及落表就被 SSE 路由读了"旧"快照的竞态。
        succeeded_event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=4,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_only(
            store_group.conn,
            store_group.event_store,
            succeeded_event,
        )

        events_received: list[dict] = []

        async def reader() -> None:
            async with client.stream(
                "GET", f"/api/stream/task/{task_id}"
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        events_received.append(
                            json.loads(line[len("data:") :].strip())
                        )

        # 若 SSE 层没正确处理终态 overlap，reader 会永远挂着心跳，
        # 这里用超时守住回归
        await asyncio.wait_for(reader(), timeout=3.0)

        terminal_hits = [
            e for e in events_received if e["event_id"] == succeeded_event.event_id
        ]
        assert len(terminal_hits) == 1
        assert terminal_hits[0]["type"] == "STATE_TRANSITION"
        assert terminal_hits[0]["final"] is True

    async def test_sse_default_hub_queue_size_absorbs_burst(self):
        """默认 SSEHub 的 queue 容量足以缓冲一次典型失败 task 的 event 爆发。

        历史 bug：queue_maxsize=100 时，失败链路喷几百个 event 瞬间塞满队列，
        sse_hub 直接把订阅者 discard，前端永远等不到终态事件。
        """
        hub = SSEHub()
        task_id = "01JTESTSSEHUBBURSTDEFAULT01"
        queue = await hub.subscribe(task_id)

        now = datetime.now(UTC)
        events = [
            Event(
                event_id=f"01JTESTSSEEVT{idx:015d}",
                task_id=task_id,
                task_seq=idx + 1,
                ts=now,
                type=EventType.TOOL_CALL_STARTED,
                actor=ActorType.SYSTEM,
                payload={"tool_name": f"tool_{idx}"},
                trace_id=f"trace-{task_id}",
            )
            for idx in range(500)
        ]
        for event in events:
            await hub.broadcast(task_id, event)

        assert queue in hub._subscribers.get(task_id, set()), (
            "500 个 event 突发下订阅者不应被踢掉，"
            "证明 queue_maxsize 默认值足以缓冲短时爆发"
        )
        assert queue.qsize() == 500

        await hub.unsubscribe(task_id, queue)

    async def test_sse_hub_removes_full_queue_subscriber(self):
        """慢订阅者队列满时自动移除，避免内存膨胀"""
        sse_hub = SSEHub(queue_maxsize=1)
        task_id = "01JTESTSSEQUEUEFULL000000001"
        queue = await sse_hub.subscribe(task_id)

        first_event = Event(
            event_id="01JTESTSSEEVT00000000000001",
            task_id=task_id,
            task_seq=1,
            ts=datetime.now(UTC),
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            payload={},
            trace_id=f"trace-{task_id}",
        )
        second_event = Event(
            event_id="01JTESTSSEEVT00000000000002",
            task_id=task_id,
            task_seq=2,
            ts=datetime.now(UTC),
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            payload={},
            trace_id=f"trace-{task_id}",
        )

        await sse_hub.broadcast(task_id, first_event)
        await sse_hub.broadcast(task_id, second_event)

        assert task_id not in sse_hub._subscribers or queue not in sse_hub._subscribers[task_id]
