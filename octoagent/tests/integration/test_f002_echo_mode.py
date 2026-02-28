"""Feature 002 Echo 模式全链路集成测试 -- T042

验证 LLM_MODE=echo 时全链路行为与 M0 一致：
1. 消息 -> Echo LLM -> SUCCEEDED + 完整事件链
2. 事件 payload 包含 Feature 002 新字段（使用默认值）
3. ModelCallResult 字段完整
"""

import asyncio

from httpx import AsyncClient


class TestF002EchoMode:
    """Feature 002: Echo 模式全链路集成"""

    async def test_echo_mode_full_chain(self, client: AsyncClient, integration_app):
        """Echo 模式全链路：消息 -> Echo -> SUCCEEDED + Feature 002 payload 字段"""
        # 发送消息
        resp = await client.post(
            "/api/message",
            json={"text": "Feature 002 echo test", "idempotency_key": "f002-echo-001"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # 等待后台处理
        await asyncio.sleep(0.5)

        # 查询详情
        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()

        # 验证终态
        assert data["task"]["status"] == "SUCCEEDED"

        # 验证事件链路完整
        events = data["events"]
        event_types = [e["type"] for e in events]
        assert "TASK_CREATED" in event_types
        assert "USER_MESSAGE" in event_types
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types
        assert "ARTIFACT_CREATED" in event_types

        # 验证 STATE_TRANSITION 事件（CREATED->RUNNING, RUNNING->SUCCEEDED）
        state_transitions = [e for e in events if e["type"] == "STATE_TRANSITION"]
        assert len(state_transitions) >= 2
        # 第一个: CREATED -> RUNNING
        assert state_transitions[0]["payload"]["from_status"] == "CREATED"
        assert state_transitions[0]["payload"]["to_status"] == "RUNNING"
        # 最后一个: RUNNING -> SUCCEEDED
        assert state_transitions[-1]["payload"]["from_status"] == "RUNNING"
        assert state_transitions[-1]["payload"]["to_status"] == "SUCCEEDED"

    async def test_echo_mode_payload_has_f002_fields(
        self, client: AsyncClient, integration_app
    ):
        """Echo 模式 MODEL_CALL_COMPLETED payload 包含 Feature 002 新字段"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Check payload fields",
                "idempotency_key": "f002-echo-payload-001",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.5)

        resp = await client.get(f"/api/tasks/{task_id}")
        data = resp.json()
        events = data["events"]

        # 找到 MODEL_CALL_COMPLETED 事件
        completed_events = [e for e in events if e["type"] == "MODEL_CALL_COMPLETED"]
        assert len(completed_events) == 1
        payload = completed_events[0]["payload"]

        # Feature 002 新字段存在且有值
        assert "model_name" in payload
        assert "provider" in payload
        assert "cost_usd" in payload
        assert "cost_unavailable" in payload
        assert "is_fallback" in payload
        assert "artifact_ref" in payload

        # Echo 模式特定值
        assert payload["provider"] == "echo"
        assert payload["model_name"] == "echo"
        assert payload["cost_usd"] == 0.0
        assert payload["is_fallback"] is False

        # M0 已有字段仍然存在
        assert "model_alias" in payload
        assert "response_summary" in payload
        assert "duration_ms" in payload
        assert "token_usage" in payload

    async def test_echo_mode_response_content(
        self, client: AsyncClient, integration_app
    ):
        """Echo 模式响应内容包含原始消息"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Hello from Feature 002",
                "idempotency_key": "f002-echo-content-001",
            },
        )
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.5)

        resp = await client.get(f"/api/tasks/{task_id}")
        data = resp.json()

        # Artifact 中包含 Echo 内容
        artifacts = data["artifacts"]
        assert len(artifacts) >= 1
        part = artifacts[0]["parts"][0]
        assert "Hello from Feature 002" in (part["content"] or "")

        # MODEL_CALL_COMPLETED 的 response_summary 也包含
        completed = [e for e in data["events"] if e["type"] == "MODEL_CALL_COMPLETED"]
        assert "Hello from Feature 002" in completed[0]["payload"]["response_summary"]
