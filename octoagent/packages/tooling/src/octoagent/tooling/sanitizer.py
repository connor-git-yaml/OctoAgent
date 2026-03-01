"""敏感数据脱敏 -- Feature 004 Tool Contract + ToolBroker

对齐 spec FR-015。在事件生成前对参数和输出进行脱敏处理。

脱敏规则:
1. 文件路径中的 $HOME / 用户目录 -> ~
2. 环境变量值 -> [ENV:VAR_NAME]（预留，当前仅做模式匹配）
3. 凭证模式（token=*, password=*, secret=*, key=*）-> [REDACTED]
4. 键名包含敏感词（password/secret/token/key）的值 -> [REDACTED]
"""

from __future__ import annotations

import os
import re
from typing import Any

# 敏感键名模式（不区分大小写匹配）
_SENSITIVE_KEY_PATTERNS = re.compile(r"(password|secret|token|key)", re.IGNORECASE)

# 凭证值模式：匹配 token=xxx, password=xxx, secret=xxx, key=xxx
_CREDENTIAL_VALUE_PATTERN = re.compile(r"(token|password|secret|key)\s*=\s*\S+", re.IGNORECASE)

# $HOME 路径（运行时获取一次）
_HOME_DIR = os.path.expanduser("~")


def sanitize_for_event(data: dict[str, Any]) -> dict[str, Any]:
    """对事件 payload 进行脱敏处理

    递归处理嵌套 dict 和 list，对字符串值应用脱敏规则。
    不修改原始 dict，返回脱敏后的副本。

    Args:
        data: 原始事件 payload

    Returns:
        脱敏后的 payload 副本
    """
    return _sanitize_dict(data, parent_key=None)


def _sanitize_dict(data: dict[str, Any], parent_key: str | None) -> dict[str, Any]:
    """递归脱敏 dict"""
    result: dict[str, Any] = {}
    for key, value in data.items():
        result[key] = _sanitize_value(value, key=key)
    return result


def _sanitize_value(value: Any, key: str | None = None) -> Any:
    """脱敏单个值

    Args:
        value: 待脱敏的值
        key: 当前键名（用于敏感键名检测）

    Returns:
        脱敏后的值
    """
    if isinstance(value, dict):
        return _sanitize_dict(value, parent_key=key)

    if isinstance(value, list):
        return [_sanitize_value(item, key=None) for item in value]

    if isinstance(value, str):
        # 规则 4: 键名包含敏感词 -> 整个值替换为 [REDACTED]
        if key is not None and _SENSITIVE_KEY_PATTERNS.search(key):
            return "[REDACTED]"

        # 规则 3: 凭证值模式（token=xxx 等）-> 替换匹配部分
        sanitized = _redact_credentials(value)

        # 规则 1: $HOME 路径替换为 ~
        sanitized = _replace_home_dir(sanitized)

        return sanitized

    # 非字符串类型（int、float、bool、None 等）不变
    return value


def _replace_home_dir(text: str) -> str:
    """将文件路径中的 $HOME 替换为 ~"""
    if _HOME_DIR and _HOME_DIR in text:
        return text.replace(_HOME_DIR, "~")
    return text


def _redact_credentials(text: str) -> str:
    """替换凭证模式为 [REDACTED]"""
    return _CREDENTIAL_VALUE_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
