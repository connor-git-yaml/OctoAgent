"""凭证脱敏工具 -- 对齐 contracts/auth-adapter-api.md SS4, FR-011

保留前缀和末尾少量字符，中间替换为 ***。
确保凭证值不以明文出现在日志和事件中。
"""

from __future__ import annotations


def mask_secret(
    value: str,
    prefix_len: int = 10,
    suffix_len: int = 3,
) -> str:
    """脱敏凭证值

    规则:
    - 空字符串: 返回 "***"
    - 长度 <= prefix_len + suffix_len: 返回 "***"
    - 否则: 保留前 prefix_len 字符 + "***" + 末尾 suffix_len 字符

    示例:
    - "sk-or-v1-abc123xyz" -> "sk-or-v1-a***xyz"
    - "sk-ant-oat01-longtoken" -> "sk-ant-oat***ken"
    - "short" -> "***"

    Args:
        value: 原始凭证值
        prefix_len: 保留前缀长度
        suffix_len: 保留后缀长度

    Returns:
        脱敏后的字符串
    """
    if not value or len(value) <= prefix_len + suffix_len:
        return "***"
    return f"{value[:prefix_len]}***{value[-suffix_len:]}"
