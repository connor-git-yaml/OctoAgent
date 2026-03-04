"""Feature 013 场景 A — 消息路由全链路验收（SC-001）

测试目标：
- FR-002: 验证系统完整处理消息从接收到结果返回的全过程
- 三类 Orchestrator 系统事件均写入 EventStore（ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED）
- 所有事件关联同一 task_id（通过 EventStore 直接验证）
- 任务以 SUCCEEDED 状态结束

独立测试命令：
    uv run pytest tests/integration/test_f013_e2e_full.py -v

注：场景 A 使用 full_integration_app 而非 integration_app，因为需要 TaskRunner
才能产生 ORCH_DECISION / WORKER_DISPATCHED / WORKER_RETURNED 控制平面事件。
"""

from httpx import AsyncClient

from tests.integration.conftest import make_task_succeeded_checker, poll_until


class TestF013ScenarioA:
    """场景 A: 消息路由全链路验收（SC-001）

    验证从消息接收到结果返回的完整处理流程，覆盖 FR-002：
    - 场景 1: ORCH_DECISION / WORKER_DISPATCHED / WORKER_RETURNED 三类事件写入 EventStore
    - 场景 2: 执行结果非空，与实际执行产物一致
    幂等键格式: f013-sc-a-{sequence}
    """

    async def test_message_routing_full_chain(
        self,
        full_client: AsyncClient,
        full_integration_app,
    ) -> None:
        """FR-002 场景 1: 提交消息后，系统完整处理并以 SUCCEEDED 结束。

        断言：
        - ORCH_DECISION、WORKER_DISPATCHED、WORKER_RETURNED 三类 Orchestrator 事件存在
        - 所有 EventStore 中的事件均属于同一 task_id
        - 任务最终状态为 SUCCEEDED
        幂等键: f013-sc-a-001
        """
        resp = await full_client.post(
            "/api/message",
            json={"text": "f013 e2e full chain test", "idempotency_key": "f013-sc-a-001"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # 使用轮询等待替代固定 sleep（FR-009 时序稳定性）
        store_group = full_integration_app.state.store_group
        await poll_until(make_task_succeeded_checker(task_id, store_group), timeout_s=10.0)

        # 验证任务 API 状态
        detail = await full_client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200
        assert detail.json()["task"]["status"] == "SUCCEEDED"

        # 直接从 EventStore 获取完整事件（包含 task_id 字段），API 层已省略该字段
        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = {e.type.value for e in events}

        # 断言三类 Orchestrator 系统事件写入（FR-002，来自 Feature 008）
        assert "ORCH_DECISION" in event_types, (
            f"缺少 ORCH_DECISION 事件，实际事件类型: {event_types}"
        )
        assert "WORKER_DISPATCHED" in event_types, (
            f"缺少 WORKER_DISPATCHED 事件，实际事件类型: {event_types}"
        )
        assert "WORKER_RETURNED" in event_types, (
            f"缺少 WORKER_RETURNED 事件，实际事件类型: {event_types}"
        )

        # 断言所有事件关联同一 task_id（SC-001 核心断言）
        for event in events:
            assert event.task_id == task_id, (
                f"事件 task_id 不一致：期望 {task_id}，实际 {event.task_id}"
            )

    async def test_task_result_non_empty(
        self,
        full_client: AsyncClient,
        full_integration_app,
    ) -> None:
        """FR-002 场景 2: 执行结果非空，与实际执行产物一致。

        断言：任务完成后 artifact_store 中至少存在一条产物记录
        幂等键: f013-sc-a-002
        """
        resp = await full_client.post(
            "/api/message",
            json={"text": "f013 e2e result check", "idempotency_key": "f013-sc-a-002"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        store_group = full_integration_app.state.store_group
        await poll_until(make_task_succeeded_checker(task_id, store_group), timeout_s=10.0)

        # 断言产物列表非空（echo 模式至少产生一个产物）
        artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
        assert len(artifacts) >= 1, (
            f"任务 {task_id} 执行结果为空，期望至少 1 个产物"
        )
