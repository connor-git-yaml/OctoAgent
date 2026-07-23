"""SC-2 持久性集成测试

进程重启后 tasks 状态完整。
"""

import os
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub

from apps.gateway.tests.runtime_service_fixtures import start_runtime_task_runner


class TestSC2Durability:
    """SC-2: 进程重启后数据完整"""

    async def test_tasks_survive_restart(self, tmp_path: Path):
        """创建任务 -> 关闭 Store -> 重新打开 -> 数据完整"""
        db_path = str(tmp_path / "durable.db")
        artifacts_dir = str(tmp_path / "artifacts")

        os.environ["OCTOAGENT_DB_PATH"] = db_path
        os.environ["OCTOAGENT_ARTIFACTS_DIR"] = artifacts_dir
        os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

        try:
            from octoagent.gateway.main import create_app

            # 第一次启动：创建任务
            app1 = create_app()
            sg1 = await create_store_group(db_path, artifacts_dir)
            sse_hub1 = SSEHub()
            llm_service1 = LLMService()
            runtime_fixture1, task_runner1 = await start_runtime_task_runner(
                sg1,
                sse_hub1,
                llm_service1,
            )
            app1.state.store_group = sg1
            app1.state.sse_hub = sse_hub1
            app1.state.llm_service = llm_service1
            app1.state.runtime_services = runtime_fixture1.bundle
            app1.state.task_runner = task_runner1

            async with AsyncClient(
                transport=ASGITransport(app=app1),
                base_url="http://test",
            ) as c1:
                resp = await c1.post(
                    "/api/message",
                    json={"text": "Durable task", "idempotency_key": "sc2-001"},
                )
                assert resp.status_code == 201
                task_id = resp.json()["task_id"]

            # 关闭连接（模拟进程退出）
            await task_runner1.shutdown()
            await sg1.close()

            # 第二次启动：验证数据完整
            app2 = create_app()
            sg2 = await create_store_group(db_path, artifacts_dir)
            app2.state.store_group = sg2
            app2.state.sse_hub = SSEHub()
            app2.state.llm_service = LLMService()

            async with AsyncClient(
                transport=ASGITransport(app=app2),
                base_url="http://test",
            ) as c2:
                resp = await c2.get(f"/api/tasks/{task_id}")
                assert resp.status_code == 200
                data = resp.json()
                assert data["task"]["task_id"] == task_id
                assert data["task"]["title"] == "Durable task"

                # 事件也完整
                assert len(data["events"]) >= 2

            await sg2.close()
        finally:
            os.environ.pop("OCTOAGENT_DB_PATH", None)
            os.environ.pop("OCTOAGENT_ARTIFACTS_DIR", None)
            os.environ.pop("LOGFIRE_SEND_TO_LOGFIRE", None)
