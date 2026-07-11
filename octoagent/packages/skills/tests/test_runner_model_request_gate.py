"""F137 硬闸：SkillRunner 模型调用路径对 ModelRequestsNotAllowedError re-raise。

AC-3（SkillRunner 面）：gate=deny 下漏网真调用经决策环路径（ProviderModelClient →
SkillRunner）必须直接向上炸——不得进入 retry/backoff（拖时间）、不得转
REPEAT_ERROR failed result（掩埋信号）。
"""

from __future__ import annotations

import pytest
from octoagent.provider import ModelRequestsNotAllowedError
from octoagent.skills.models import SkillOutputEnvelope, SkillRunStatus
from octoagent.skills.runner import SkillRunner

from .conftest import QueueModelClient


async def test_runner_reraises_model_requests_not_allowed(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """gate 异常从 model_client.generate 直接穿透 runner.run（不转 failed result）。"""
    client = QueueModelClient([ModelRequestsNotAllowedError("leak")])
    runner = SkillRunner(
        model_client=client,
        tool_broker=tool_broker,
        event_store=event_store,
    )

    with pytest.raises(ModelRequestsNotAllowedError):
        await runner.run(
            manifest=echo_manifest,
            execution_context=execution_context,
            skill_input={"text": "hi"},
            prompt="prompt",
        )
    # 不 retry：第一次 generate 撞闸后立即炸，无第二次调用
    assert client.calls == 1


async def test_runner_ordinary_error_still_retries_and_converts(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """对照组：普通异常保持 baseline 行为——retry 后成功仍 SUCCEEDED（合法自愈不受影响）。"""
    client = QueueModelClient(
        [
            RuntimeError("transient"),
            SkillOutputEnvelope(content="ok", complete=True),
        ]
    )
    runner = SkillRunner(
        model_client=client,
        tool_broker=tool_broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )
    assert result.status == SkillRunStatus.SUCCEEDED
    assert client.calls == 2
