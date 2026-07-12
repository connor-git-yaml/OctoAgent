"""F140 L1 场景脚本脑：prompt-marker 路由（memU FakeChatClient 范式）。

与 F138 ``octoagent.skills.testing.ScriptedModelClient``（队列版）实现同一
``StructuredModelClientProtocol``，但**按输入路由而非按序出队**——L1 服务器
长驻、跨多条 Playwright 测试消费，队列会被后台/额外 LLM 调用 desync；
prompt-marker 让「哪条消息得到哪个脚本」与调用次序解耦（确定性属于消息本身）。

轮次判定用 ``feedback``（决策环第 2 轮携带工具执行 feedback，spike 实测）而非
内部计数——同因：不依赖跨请求共享状态。

场景表（marker 出现在用户消息里即命中）：

- ``L1-WRITE``：第 1 轮调 ``filesystem.write_text`` 写 ``l1_e2e/note.md``
  （内容 = :data:`L1_WRITE_FILE_CONTENT`），第 2 轮回 :data:`L1_WRITE_REPLY`。
- 其余消息：直接回 :data:`L1_DEFAULT_REPLY`（完成）。

常量被 Playwright 侧断言复用语义（TS 侧有对应字面量），改动需同步
``frontend/e2e/support.ts``。
"""

from __future__ import annotations

from typing import Any

from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    ToolCallSpec,
)

# --- 场景契约常量（TS 侧 frontend/e2e/support.ts 同步字面量） ---
L1_WRITE_MARKER = "L1-WRITE"
L1_WRITE_FILE_RELPATH = "l1_e2e/note.md"
L1_WRITE_FILE_CONTENT = "F140-L1-MARKER：这行内容由脚本决策环真实写盘"
L1_WRITE_REPLY = "文件已写好（L1 场景①）"
L1_DEFAULT_REPLY = "L1 默认回复"


class L1ScenarioModelClient:
    """按 prompt marker 路由的确定性脚本脑。

    Attributes:
        calls: ``generate`` 被调用总次数（launcher 日志用，不做断言依赖——
            长驻 server 上后台调用会使其跨场景不可预测）。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
        feedback: list[Any],
        attempt: int,
        step: int,
    ) -> SkillOutputEnvelope:
        self.calls += 1
        if L1_WRITE_MARKER in prompt:
            if not feedback:
                return SkillOutputEnvelope(
                    content="",
                    tool_calls=[
                        ToolCallSpec(
                            tool_name="filesystem.write_text",
                            arguments={
                                "path": L1_WRITE_FILE_RELPATH,
                                "content": L1_WRITE_FILE_CONTENT,
                            },
                        )
                    ],
                )
            return SkillOutputEnvelope(content=L1_WRITE_REPLY, complete=True)
        return SkillOutputEnvelope(content=L1_DEFAULT_REPLY, complete=True)


__all__ = [
    "L1ScenarioModelClient",
    "L1_WRITE_MARKER",
    "L1_WRITE_FILE_RELPATH",
    "L1_WRITE_FILE_CONTENT",
    "L1_WRITE_REPLY",
    "L1_DEFAULT_REPLY",
]
