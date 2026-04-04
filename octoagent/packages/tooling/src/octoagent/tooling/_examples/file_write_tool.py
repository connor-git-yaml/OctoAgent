"""file_write 示例工具 -- side_effect_level=irreversible

有副作用的工具实现参考：不可逆操作、standard profile、filesystem 分组。
实际不执行文件写入（模拟），用于演示 FR-010a 安全保障。
"""

from __future__ import annotations

from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import SideEffectLevel


@tool_contract(
    side_effect_level=SideEffectLevel.IRREVERSIBLE,
    tool_group="filesystem",
)
async def file_write(path: str, content: str) -> str:
    """模拟写入文件内容（不实际执行 IO）。

    Args:
        path: 目标文件路径
        content: 要写入的内容
    """
    # 模拟实现：不实际写入文件
    return f"Written {len(content)} bytes to {path}"
