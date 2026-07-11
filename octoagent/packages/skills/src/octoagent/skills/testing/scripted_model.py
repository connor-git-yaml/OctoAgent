"""ScriptedModelClient——可编程脚本脑（FunctionModel 等价，F138 拍板②）。

从 ``packages/skills/tests/conftest.py`` 的 ``QueueModelClient`` 上提改名，
**实现逻辑零变更**（F138 Phase B；conftest 侧以 re-export 别名保持既有
消费者零改动）。

用途：按 deque 顺序返回预置 ``SkillOutputEnvelope``（可含 ``tool_calls``，
可为 Exception 触发异常路径），精确编排多步决策环——"第 1 轮调 A、第 2 轮
调 B、第 3 轮返 complete"。是 L3（不打真 LLM）驱动**真** ``SkillRunner`` →
**真** ``tool_broker.execute`` → **真**回写链路的确定性脚本脑。

协议契约（``StructuredModelClientProtocol``）：单方法 ``generate``；
``clear_history`` / ``token_usage`` 均为 runner 侧可选探测（getattr/hasattr），
本件不实现即 no-op。
"""

from __future__ import annotations

from collections import deque
from typing import Any

from ..manifest import SkillManifest
from ..models import SkillExecutionContext, SkillOutputEnvelope


class ScriptedModelClient:
    """按队列返回输出/异常的模型客户端。

    Args:
        items: 预置脚本队列。元素为 ``SkillOutputEnvelope``（本轮决策输出，
            可携带 tool_calls）或 ``Exception``（本轮 raise，驱动重试/降级路径）。

    Attributes:
        calls: ``generate`` 被调用次数（测试断言"决策环真跑了 N 轮"）。
    """

    def __init__(self, items: list[SkillOutputEnvelope | Exception]) -> None:
        self._queue = deque(items)
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
        if not self._queue:
            return SkillOutputEnvelope(content="default", complete=True)
        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item


__all__ = ["ScriptedModelClient"]
