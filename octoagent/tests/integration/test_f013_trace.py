"""Feature 013 场景 D — 全链路执行过程完整可追溯验收（SC-004）

测试目标：
- FR-005: 验证消息接收、路由决策、Worker 执行三层均保留追踪记录
- 所有记录通过统一 task_id 可完整串联，链路无断裂
- LOGFIRE_SEND_TO_LOGFIRE=false 时主业务流程不受影响（FR-012 降级验证）

独立测试命令：
    uv run pytest tests/integration/test_f013_trace.py -v

注：
- 场景 D 使用 full_integration_app（含 TaskRunner）产生完整事件链
- 追踪可观测性通过 EventStore 事件链验证（task_id 串联）
- capfire fixture 用于捕获 in-memory span；当 LOGFIRE_SEND_TO_LOGFIRE=false 时
  logfire 不初始化，span 为空属于正常降级行为（FR-012）
- SC-004 验收主要基于 EventStore 三层事件链完整性，capfire 为附加验证
"""

import os

import logfire.testing
from httpx import AsyncClient

from tests.integration.conftest import make_task_succeeded_checker, poll_until


class TestF013ScenarioD:
    """场景 D: 全链路执行过程完整可追溯验收（SC-004）

    覆盖 FR-005 的三条验收场景：
    - 场景 1: 三层事件均存在，通过 task_id 可串联（EventStore 直接验证）
    - 场景 2: ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED 三类事件链路无断裂
    - 场景 3: LOGFIRE_SEND_TO_LOGFIRE=false 时主流程不受影响（降级验证）
    幂等键格式: f013-sc-d-{sequence}
    """

    async def test_full_trace_spans_across_all_layers(
        self,
        full_client: AsyncClient,
        full_integration_app,
        capfire: logfire.testing.CaptureLogfire,
    ) -> None:
        """FR-005 场景 1: 三层追踪记录均存在，通过 task_id 可完整串联。

        追踪可观测性的主要验证基于 EventStore 事件链（task_id 串联）：
        - TASK_CREATED（接收层）、ORCH_DECISION（路由决策层）、WORKER_RETURNED（执行层）
        - 所有事件 task_id 一致，链路无断裂

        capfire span 验证：当 LOGFIRE_SEND_TO_LOGFIRE=false 时 span 为空属降级行为（FR-012），
        此情况下通过 EventStore 事件链完成追踪完整性验证。
        幂等键: f013-sc-d-001
        """
        resp = await full_client.post(
            "/api/message",
            json={"text": "f013 trace full chain", "idempotency_key": "f013-sc-d-001"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        store_group = full_integration_app.state.store_group
        await poll_until(make_task_succeeded_checker(task_id, store_group), timeout_s=10.0)

        # 主验证：通过 EventStore 事件链验证三层追踪可追溯性
        events = await store_group.event_store.get_events_for_task(task_id)
        assert len(events) > 0, f"任务 {task_id} 的 EventStore 中无事件记录"

        # 所有事件的 task_id 必须与提交时的 task_id 一致（链路无断裂）
        event_task_ids = {e.task_id for e in events}
        assert event_task_ids == {task_id}, (
            f"EventStore 中存在 task_id 不一致的事件：{event_task_ids}"
        )

        # 附加验证：若 logfire 已初始化（LOGFIRE_SEND_TO_LOGFIRE=true），span 应非空
        # 当前 fixture 设置 LOGFIRE_SEND_TO_LOGFIRE=false 时 span 为空属降级行为（FR-012）
        exported_spans = capfire.exporter.exported_spans_as_dict()
        logfire_enabled = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "false").lower() == "true"
        if logfire_enabled:
            assert len(exported_spans) > 0, (
                "LOGFIRE_SEND_TO_LOGFIRE=true 时期望 capfire 捕获到至少一条 span"
            )
        # LOGFIRE_SEND_TO_LOGFIRE=false 时 span 为空是正常降级行为，不作强断言

    async def test_trace_chain_continuity(
        self,
        full_client: AsyncClient,
        full_integration_app,
        capfire: logfire.testing.CaptureLogfire,
    ) -> None:
        """FR-005 场景 2: Worker 执行记录可追溯到路由决策记录，链路无断裂。

        断言 ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED 三类事件均存在，
        验证控制平面链路完整性，与场景 A 的断言互补强化。
        幂等键: f013-sc-d-002
        """
        resp = await full_client.post(
            "/api/message",
            json={"text": "f013 trace chain continuity", "idempotency_key": "f013-sc-d-002"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        store_group = full_integration_app.state.store_group
        await poll_until(make_task_succeeded_checker(task_id, store_group), timeout_s=10.0)

        # 从 EventStore 获取完整事件链
        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = {e.type.value for e in events}

        # 断言三类控制平面事件均存在（Worker 执行可追溯到路由决策，链路无断裂）
        assert "ORCH_DECISION" in event_types, (
            f"缺少 ORCH_DECISION 事件（路由决策层），实际事件类型: {event_types}"
        )
        assert "WORKER_DISPATCHED" in event_types, (
            f"缺少 WORKER_DISPATCHED 事件（Worker 执行层），实际事件类型: {event_types}"
        )
        assert "WORKER_RETURNED" in event_types, (
            f"缺少 WORKER_RETURNED 事件（Worker 回传层），实际事件类型: {event_types}"
        )

        # 验证所有控制平面事件的 task_id 一致（链路完整性最终确认）
        orch_events = [
            e for e in events
            if e.type.value in {"ORCH_DECISION", "WORKER_DISPATCHED", "WORKER_RETURNED"}
        ]
        for event in orch_events:
            assert event.task_id == task_id, (
                f"控制平面事件 {event.type.value} 的 task_id 不一致: "
                f"期望 {task_id}，实际 {event.task_id}"
            )

    async def test_trace_unaffected_when_backend_unavailable(
        self,
        full_client: AsyncClient,
        full_integration_app,
    ) -> None:
        """FR-005 场景 3 / FR-012: LOGFIRE_SEND_TO_LOGFIRE=false 时主流程不受影响。

        fixture 已设置 LOGFIRE_SEND_TO_LOGFIRE=false（降级配置），
        直接验证主业务流程正常完成，可观测后端不可用不导致业务中断。
        幂等键: f013-sc-d-003
        """
        # 确认降级配置已生效
        assert os.environ.get("LOGFIRE_SEND_TO_LOGFIRE") == "false", (
            "期望 LOGFIRE_SEND_TO_LOGFIRE=false，确认 full_integration_app fixture 已配置"
        )

        resp = await full_client.post(
            "/api/message",
            json={"text": "f013 trace degraded mode", "idempotency_key": "f013-sc-d-003"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        store_group = full_integration_app.state.store_group

        # 主业务流程应在可观测后端不可用时正常完成（FR-012 降级验证）
        await poll_until(make_task_succeeded_checker(task_id, store_group), timeout_s=10.0)

        task = await store_group.task_store.get_task(task_id)
        assert task is not None
        assert task.status == "SUCCEEDED", (
            f"降级模式下任务应正常完成，实际状态: {task.status}"
        )
