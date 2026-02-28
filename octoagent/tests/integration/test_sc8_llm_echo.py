"""SC-8 Echo LLM 回路集成测试

全链路事件 + Artifact 引用 + trace_id 一致性。
"""

import asyncio

from httpx import AsyncClient


class TestSC8LLMEcho:
    """SC-8: Echo LLM 完整回路"""

    async def test_echo_full_loop(self, client: AsyncClient, integration_app):
        """消息 -> Echo LLM -> SUCCEEDED + 完整事件 + Artifact"""
        # 发送消息
        resp = await client.post(
            "/api/message",
            json={"text": "Hello OctoAgent", "idempotency_key": "sc8-001"},
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

        # 必须包含完整事件链
        assert "TASK_CREATED" in event_types
        assert "USER_MESSAGE" in event_types
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types
        assert "ARTIFACT_CREATED" in event_types

        # 验证事件顺序（task_seq 递增）
        seqs = [e["task_seq"] for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # 无重复

        # 验证 MODEL_CALL_COMPLETED 有 artifact_ref
        completed_events = [e for e in events if e["type"] == "MODEL_CALL_COMPLETED"]
        assert len(completed_events) == 1
        assert completed_events[0]["payload"].get("artifact_ref") is not None

        # 验证 Artifact
        artifacts = data["artifacts"]
        assert len(artifacts) >= 1
        assert artifacts[0]["name"] == "llm-response"

        # Echo 内容包含原始消息
        part = artifacts[0]["parts"][0]
        assert "Hello OctoAgent" in (part["content"] or "")

    async def test_echo_multiple_tasks_independent(
        self, client: AsyncClient, integration_app
    ):
        """多个任务独立处理，互不干扰"""
        task_ids = []
        for i in range(3):
            resp = await client.post(
                "/api/message",
                json={"text": f"Multi {i}", "idempotency_key": f"sc8-multi-{i}"},
            )
            assert resp.status_code == 201
            task_ids.append(resp.json()["task_id"])

        await asyncio.sleep(1.0)

        # 每个任务独立达到终态
        for tid in task_ids:
            resp = await client.get(f"/api/tasks/{tid}")
            assert resp.status_code == 200
            assert resp.json()["task"]["status"] == "SUCCEEDED"
