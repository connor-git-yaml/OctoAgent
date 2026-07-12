"""SC-1 端到端集成测试

POST /api/message -> Task 创建 -> Event 落盘 -> SSE 推送完整链路
"""

import asyncio

import pytest
from httpx import AsyncClient

# F142 件5a：xdist 分组——本文件含时序敏感断言（固定 sleep 窗口/性能阈值/状态机
# 竞态，F083 归档债），`--dist=loadgroup` 下同组钉同一 worker 串行执行，
# 解锁其余测试 `-n auto` 并行（本地全量与 CI 双提速）。
pytestmark = pytest.mark.xdist_group("integration_timing")


class TestSC1EndToEnd:
    """SC-1: 消息接收到任务完成全链路"""

    async def test_message_creates_task_and_events(
        self, client: AsyncClient, integration_app
    ):
        """发送消息 -> 任务创建 -> 事件落盘 -> 状态推进到 SUCCEEDED"""
        # 1. 发送消息
        resp = await client.post(
            "/api/message",
            json={"text": "Hello E2E", "idempotency_key": "sc1-001"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # 2. 等待后台 LLM 处理完成
        await asyncio.sleep(0.5)

        # 3. 查询任务详情
        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()

        # 4. 验证任务状态
        assert data["task"]["status"] == "SUCCEEDED"

        # 5. 验证事件数量（TASK_CREATED + USER_MESSAGE + STATE_TRANSITION x2
        #    + MODEL_CALL_STARTED + MODEL_CALL_COMPLETED + ARTIFACT_CREATED）
        events = data["events"]
        assert len(events) >= 6
        event_types = [e["type"] for e in events]
        assert "TASK_CREATED" in event_types
        assert "USER_MESSAGE" in event_types
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types

    async def test_task_list_shows_created_task(
        self, client: AsyncClient, integration_app
    ):
        """创建任务后在列表中可见"""
        # 创建任务
        resp = await client.post(
            "/api/message",
            json={"text": "List test", "idempotency_key": "sc1-list-001"},
        )
        assert resp.status_code == 201

        await asyncio.sleep(0.3)

        # 查询列表
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) >= 1
