"""SC-3 Projection Rebuild 一致性集成测试

重建后与原始状态完全一致。
"""

import asyncio

from httpx import AsyncClient
from octoagent.core.projection import rebuild_all


class TestSC3Projection:
    """SC-3: Projection 重建一致性"""

    async def test_rebuild_preserves_task_state(
        self, client: AsyncClient, integration_app
    ):
        """创建多个任务 -> 重建 Projection -> 状态一致"""
        # 创建多个任务
        task_ids = []
        for i in range(3):
            resp = await client.post(
                "/api/message",
                json={"text": f"Rebuild test {i}", "idempotency_key": f"sc3-{i}"},
            )
            assert resp.status_code == 201
            task_ids.append(resp.json()["task_id"])

        # 等待后台处理
        await asyncio.sleep(1.0)

        # 记录重建前状态
        original_states = {}
        for tid in task_ids:
            resp = await client.get(f"/api/tasks/{tid}")
            assert resp.status_code == 200
            original_states[tid] = resp.json()["task"]["status"]

        # 执行 Projection 重建
        sg = integration_app.state.store_group
        event_count = await rebuild_all(sg.conn, sg.event_store, sg.task_store)
        assert event_count > 0

        # 验证重建后状态一致
        for tid in task_ids:
            resp = await client.get(f"/api/tasks/{tid}")
            assert resp.status_code == 200
            rebuilt_status = resp.json()["task"]["status"]
            assert rebuilt_status == original_states[tid], (
                f"Task {tid}: expected {original_states[tid]}, got {rebuilt_status}"
            )
