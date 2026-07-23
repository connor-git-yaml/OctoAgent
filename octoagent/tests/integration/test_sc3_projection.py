"""SC-3 Projection Rebuild 一致性集成测试

重建后与原始状态完全一致。
"""

from httpx import AsyncClient
from octoagent.core.projection import rebuild_all

from tests.integration.conftest import make_task_succeeded_checker, poll_until


class TestSC3Projection:
    """SC-3: Projection 重建一致性"""

    async def test_rebuild_preserves_task_state(
        self, full_client: AsyncClient, full_integration_app
    ):
        """创建多个任务 -> 重建 Projection -> 状态一致"""
        store_group = full_integration_app.state.store_group

        # 逐个创建任务并等待后台事务稳定，再创建下一项。
        task_ids = []
        for i in range(3):
            resp = await full_client.post(
                "/api/message",
                json={"text": f"Rebuild test {i}", "idempotency_key": f"sc3-{i}"},
            )
            assert resp.status_code == 201
            task_id = resp.json()["task_id"]
            task_ids.append(task_id)
            await poll_until(
                make_task_succeeded_checker(task_id, store_group),
                timeout_s=10.0,
            )

        # 记录重建前状态
        original_states = {}
        for tid in task_ids:
            resp = await full_client.get(f"/api/tasks/{tid}")
            assert resp.status_code == 200
            original_states[tid] = resp.json()["task"]["status"]

        # 执行 Projection 重建
        event_count = await rebuild_all(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
        )
        assert event_count > 0

        # 验证重建后状态一致
        for tid in task_ids:
            resp = await full_client.get(f"/api/tasks/{tid}")
            assert resp.status_code == 200
            rebuilt_status = resp.json()["task"]["status"]
            assert rebuilt_status == original_states[tid], (
                f"Task {tid}: expected {original_states[tid]}, got {rebuilt_status}"
            )
