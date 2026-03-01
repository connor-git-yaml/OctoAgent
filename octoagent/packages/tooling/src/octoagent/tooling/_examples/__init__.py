"""示例工具 -- Feature 004 Tool Contract + ToolBroker

提供两个参考实现：
- echo_tool: side_effect_level=none 最简示例
- file_write_tool: side_effect_level=irreversible 示例

用途：端到端测试 fixture + 最佳实践参考。
"""

from .echo_tool import echo
from .file_write_tool import file_write

__all__ = ["echo", "file_write"]
