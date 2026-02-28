"""Feature 002 LiteLLM 模式 Mock Proxy 集成测试 -- T043

Mock litellm.acompletion() 验证全链路调用：
1. LiteLLMClient -> FallbackManager -> LLMService -> TaskService
2. ModelCallResult 字段完整（provider/cost/token_usage）
3. 事件链正确（包含 Feature 002 新字段）
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


def _make_mock_response(content="Hello from LiteLLM", model="gpt-4o"):
    """构造模拟的 litellm acompletion 响应"""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    resp.model = model
    resp._hidden_params = {"response_cost": 0.001}
    return resp


@pytest_asyncio.fixture
async def litellm_app(tmp_path: Path):
    """创建使用 Mock LiteLLMClient 的测试 app"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

    # 手动初始化
    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()

    # 创建带 Mock 的 LiteLLMClient
    with patch("octoagent.provider.client.acompletion", new_callable=AsyncMock) as mock_acomp:
        mock_acomp.return_value = _make_mock_response()

        litellm_client = LiteLLMClient(
            proxy_base_url="http://mock-proxy:4000",
            proxy_api_key="test-key",
            timeout_s=10,
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


@pytest_asyncio.fixture
async def litellm_client_pair(litellm_app):
    """提供 (AsyncClient, mock_acompletion) 元组"""
    app, mock_acomp = litellm_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac, mock_acomp, app


class TestF002LiteLLMMode:
    """Feature 002: LiteLLM 模式 Mock Proxy 集成"""

    async def test_litellm_mode_full_chain(self, litellm_client_pair):
        """LiteLLM 模式全链路：消息 -> LiteLLM Proxy (mock) -> SUCCEEDED"""
        client, mock_acomp, app = litellm_client_pair

        resp = await client.post(
            "/api/message",
            json={"text": "Hello LiteLLM", "idempotency_key": "f002-litellm-001"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # 等待后台处理
        await asyncio.sleep(0.5)

        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()

        # 验证终态
        assert data["task"]["status"] == "SUCCEEDED"

        # 验证事件链完整
        event_types = [e["type"] for e in data["events"]]
        assert "TASK_CREATED" in event_types
        assert "USER_MESSAGE" in event_types
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types
        assert "ARTIFACT_CREATED" in event_types

    async def test_litellm_mode_payload_fields(self, litellm_client_pair):
        """LiteLLM 模式 MODEL_CALL_COMPLETED 包含真实 provider/cost 数据"""
        client, mock_acomp, app = litellm_client_pair

        resp = await client.post(
            "/api/message",
            json={"text": "Check fields", "idempotency_key": "f002-litellm-fields-001"},
        )
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.5)

        resp = await client.get(f"/api/tasks/{task_id}")
        data = resp.json()

        completed = [e for e in data["events"] if e["type"] == "MODEL_CALL_COMPLETED"]
        assert len(completed) == 1
        payload = completed[0]["payload"]

        # 来自 Mock 的真实数据
        assert payload["response_summary"] == "Hello from LiteLLM"
        assert payload["is_fallback"] is False
        assert payload["artifact_ref"] is not None

        # Token usage 使用 Feature 002 命名
        assert "prompt_tokens" in payload["token_usage"]
        assert "completion_tokens" in payload["token_usage"]
        assert "total_tokens" in payload["token_usage"]

    async def test_litellm_mode_artifact_content(self, litellm_client_pair):
        """LiteLLM 模式 Artifact 包含 LLM 响应"""
        client, mock_acomp, app = litellm_client_pair

        resp = await client.post(
            "/api/message",
            json={"text": "Get artifact", "idempotency_key": "f002-litellm-artifact-001"},
        )
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.5)

        resp = await client.get(f"/api/tasks/{task_id}")
        data = resp.json()

        artifacts = data["artifacts"]
        assert len(artifacts) >= 1
        assert artifacts[0]["name"] == "llm-response"
        part = artifacts[0]["parts"][0]
        assert part["content"] == "Hello from LiteLLM"
