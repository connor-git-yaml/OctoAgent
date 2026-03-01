"""echo 示例工具 -- side_effect_level=none

最简单的工具实现参考：无副作用、minimal profile、system 分组。
用于端到端测试和最佳实践演示。
"""

from __future__ import annotations

from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import SideEffectLevel, ToolProfile


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_profile=ToolProfile.MINIMAL,
    tool_group="system",
)
async def echo(text: str) -> str:
    """回显输入文本。

    Args:
        text: 要回显的文本内容
    """
    return text
