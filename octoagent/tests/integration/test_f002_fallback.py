"""Feature 002 降级与恢复集成测试 -- T044

模拟场景：
1. Proxy 不可达 -> 降级 Echo -> 任务仍 SUCCEEDED + is_fallback=True
2. Proxy 恢复后 -> 自动恢复 LiteLLM（lazy probe）
3. FallbackManager 通过 LLMService -> TaskService 的完整链路
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.provider import (
    AliasRegistry,
    EchoMessageAdapter,
    FallbackManager,
    LiteLLMClient,
)


def _make_mock_response(content="Recovered from LiteLLM"):
    """构造模拟的 litellm acompletion 响应"""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    resp.model = "gpt-4o"
    resp._hidden_params = {"response_cost": 0.001}
    return resp


@pytest_asyncio.fixture
async def fallback_app(tmp_path: Path):
    """创建可控降级的测试 app"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()

    # 创建可控的 mock acompletion
    mock_acomp = AsyncMock()

    with patch("octoagent.provider.client.acompletion", mock_acomp):
        litellm_client = LiteLLMClient(
            proxy_base_url="http://mock-proxy:4000",
            proxy_api_key="test-key",
            timeout_s=5,
        )
        echo_adapter = EchoMessageAdapter()
        fallback_manager = FallbackManager(
            primary=litellm_client,
            fallback=echo_adapter,
        )
        alias_registry = AliasRegistry()
        llm_service = LLMService(
            fallback_manager=fallback_manager,
            alias_registry=alias_registry,
        )

        app.state.llm_service = llm_service
        app.state.litellm_client = litellm_client
        app.state.alias_registry = alias_registry

        yield app, mock_acomp

    await store_group.conn.close()
    for key in ["OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE"]:
        os.environ.pop(key, None)


class TestF002Fallback:
    """Feature 002: 降级与恢复"""

    async def test_proxy_unreachable_falls_back_to_echo(self, fallback_app):
        """Proxy 不可达时降级到 Echo，任务仍 SUCCEEDED"""
        app, mock_acomp = fallback_app
        # 模拟 Proxy 不可达
        mock_acomp.side_effect = ConnectionError("Connection refused")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/message",
                json={"text": "Fallback test", "idempotency_key": "f002-fallback-001"},
            )
            assert resp.status_code == 201
            task_id = resp.json()["task_id"]

            await asyncio.sleep(0.5)

            resp = await client.get(f"/api/tasks/{task_id}")
            data = resp.json()

            # 任务仍然成功
            assert data["task"]["status"] == "SUCCEEDED"

            # 验证降级标记
            completed = [
                e for e in data["events"] if e["type"] == "MODEL_CALL_COMPLETED"
            ]
            assert len(completed) == 1
            payload = completed[0]["payload"]
            assert payload["is_fallback"] is True
            assert payload["provider"] == "echo"

            # Artifact 包含 Echo 内容
            artifacts = data["artifacts"]
            assert len(artifacts) >= 1
            part = artifacts[0]["parts"][0]
            assert "Echo:" in (part["content"] or "")
            assert "Fallback test" in (part["content"] or "")

    async def test_proxy_recovery_lazy_probe(self, fallback_app):
        """Proxy 恢复后 lazy probe 自动使用 LiteLLM"""
        app, mock_acomp = fallback_app

        # 第一次调用：Proxy 不可达，降级 Echo
        mock_acomp.side_effect = ConnectionError("Connection refused")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/message",
                json={"text": "Before recovery", "idempotency_key": "f002-recovery-001"},
            )
            task_id_1 = resp.json()["task_id"]
            await asyncio.sleep(0.5)

            resp = await client.get(f"/api/tasks/{task_id_1}")
            data_1 = resp.json()
            assert data_1["task"]["status"] == "SUCCEEDED"
            completed_1 = [
                e for e in data_1["events"] if e["type"] == "MODEL_CALL_COMPLETED"
            ]
            assert completed_1[0]["payload"]["is_fallback"] is True

            # 第二次调用：Proxy 恢复
            mock_acomp.side_effect = None
            mock_acomp.return_value = _make_mock_response("Recovered response")

            resp = await client.post(
                "/api/message",
                json={"text": "After recovery", "idempotency_key": "f002-recovery-002"},
            )
            task_id_2 = resp.json()["task_id"]
            await asyncio.sleep(0.5)

            resp = await client.get(f"/api/tasks/{task_id_2}")
            data_2 = resp.json()
            assert data_2["task"]["status"] == "SUCCEEDED"
            completed_2 = [
                e for e in data_2["events"] if e["type"] == "MODEL_CALL_COMPLETED"
            ]
            # Lazy probe 恢复后，不再是 fallback
            assert completed_2[0]["payload"]["is_fallback"] is False
            assert completed_2[0]["payload"]["response_summary"] == "Recovered response"

    async def test_event_chain_complete_on_fallback(self, fallback_app):
        """降级时事件链仍然完整"""
        app, mock_acomp = fallback_app
        mock_acomp.side_effect = ConnectionError("Timeout")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/message",
                json={"text": "Chain test", "idempotency_key": "f002-chain-001"},
            )
            task_id = resp.json()["task_id"]
            await asyncio.sleep(0.5)

            resp = await client.get(f"/api/tasks/{task_id}")
            data = resp.json()

            event_types = [e["type"] for e in data["events"]]
            # 完整事件链
            assert "TASK_CREATED" in event_types
            assert "USER_MESSAGE" in event_types
            assert "MODEL_CALL_STARTED" in event_types
            assert "MODEL_CALL_COMPLETED" in event_types
            assert "ARTIFACT_CREATED" in event_types

            # 状态流转正确
            state_transitions = [
                e for e in data["events"] if e["type"] == "STATE_TRANSITION"
            ]
            assert any(
                t["payload"]["to_status"] == "RUNNING" for t in state_transitions
            )
            assert any(
                t["payload"]["to_status"] == "SUCCEEDED" for t in state_transitions
            )
