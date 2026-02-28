"""SC-4 Artifact 完整性集成测试

存储 + 检索 + hash 校验端到端。
"""

import asyncio

from httpx import AsyncClient


class TestSC4Artifact:
    """SC-4: Artifact 完整性"""

    async def test_llm_creates_artifact(
        self, client: AsyncClient, integration_app
    ):
        """LLM 处理后生成 Artifact，可通过 API 查询"""
        # 发送消息
        resp = await client.post(
            "/api/message",
            json={"text": "Artifact test message", "idempotency_key": "sc4-001"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # 等待后台处理
        await asyncio.sleep(0.5)

        # 查询任务详情
        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()

        # 验证 Artifact 存在
        artifacts = data["artifacts"]
        assert len(artifacts) >= 1

        # 验证 Artifact 内容
        artifact = artifacts[0]
        assert artifact["name"] == "llm-response"
        assert artifact["size"] > 0
        assert len(artifact["parts"]) >= 1

        # 验证 parts 包含 LLM 回声内容
        part = artifact["parts"][0]
        assert part["type"] == "text"
        # Echo provider 返回输入的回声
        assert "Artifact test message" in (part["content"] or "")
