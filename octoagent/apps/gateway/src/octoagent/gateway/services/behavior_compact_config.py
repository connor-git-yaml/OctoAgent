"""F111 Behavior Compactor — 配置解析（USER.md 机器可读字段）。

复用 F127 ``consolidation_config`` 的解析范式（强制 key prefix + 负向左边界锚定 +
完整 HTML 注释块剥离 + 非法值 fallback 默认 + WARNING，Constitution C6）。v0.1
**不动 USER.md 模板**（1800 字符预算，memory: project_user_md_template_budget）——
字段缺失时全部走默认值，用户显式在 USER.md 加字段才生效。

USER.md 机器可读字段（FR-14）：
- ``compact_active``（bool，默认 **False**——compact 会改用户行为规则文件（即便人审），
  保守默认关，用户显式开）
- ``compact_time``（HH:MM，默认 ``03:30``——与 F127 consolidation 默认 03:00 错峰，
  避免同分钟两个 nightly 任务争 LLM/调度）

时区复用 F102 ``extract_user_timezone_from_user_md``；通知 channels 复用 F102
``summary_channels``（不新增专属字段——F127 handoff §1.5 纪律）。

**H6 联动**（spec §0.1.3 自查②）：本模块的两个 extractor 同时是发现端 H6 config
parity 护栏的对账函数之一——compact 精简 USER.md 若把 ``compact_active: true``
合并掉，H6 会拦下（否则 compact 静默自我关闭）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import structlog

from .daily_routine_config import (
    DEFAULT_SUMMARY_CHANNELS,
    extract_summary_channels_from_user_md,
    extract_user_timezone_from_user_md,
)

logger = structlog.get_logger(__name__)


# ============================================================
# 默认值（FR-14）
# ============================================================

#: v0.1 保守默认**关闭**——用户必须显式开启 nightly compact。
DEFAULT_COMPACT_ACTIVE: Final[bool] = False
#: 与 F127 consolidation 默认 03:00 错峰。
DEFAULT_COMPACT_TIME: Final[str] = "03:30"


# ============================================================
# 解析正则（复用 F127 负向左边界锚定范式——挡 previous_/last_ 说明性前缀）
# ============================================================

_COMPACT_ACTIVE_PATTERN = re.compile(
    r"""
    (?<![\w])                       # 左边界：key 前不得紧贴标识符字符
    (?:\*\*)?
    compact_active                  # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    (true|false|True|False)
    (?![\w])                        # 右边界：挡 truee/true_x → fallback
    "?
    """,
    re.VERBOSE,
)

_COMPACT_TIME_PATTERN = re.compile(
    r"""
    (?<![\w])
    (?:\*\*)?
    compact_time                    # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    (\d{1,2}:\d{2})                 # HH:MM
    (?!\d)                          # 右边界：挡 3:300 → fallback
    "?
    """,
    re.VERBOSE,
)

#: 完整 HTML 注释块剥离（含多行，F127 finding-G 同款）。
_HTML_COMMENT_BLOCK = re.compile(r"<!--.*?-->", re.DOTALL)


def _config_lines(user_md_content: str) -> list[str]:
    stripped = _HTML_COMMENT_BLOCK.sub("", user_md_content)
    return [
        line for line in stripped.splitlines() if not line.lstrip().startswith("<!--")
    ]


def _validate_time_format(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        hh, mm = int(parts[0]), int(parts[1])
        return 0 <= hh <= 23 and 0 <= mm <= 59
    except ValueError:
        return False


def extract_compact_active_from_user_md(user_md_content: str | None) -> bool:
    """提取 compact_active；缺失/非法 fallback 到 **False**（保守默认关）。"""
    if not user_md_content:
        return DEFAULT_COMPACT_ACTIVE
    for line in _config_lines(user_md_content):
        if "compact_active" not in line:
            continue
        m = _COMPACT_ACTIVE_PATTERN.search(line)
        if m is None:
            continue
        value = m.group(1).lower()
        if value == "true":
            return True
        if value == "false":
            return False
        logger.warning(
            "compact_active value invalid; falling back to default",
            raw_value=value,
            default=DEFAULT_COMPACT_ACTIVE,
        )
        return DEFAULT_COMPACT_ACTIVE
    return DEFAULT_COMPACT_ACTIVE


def extract_compact_time_from_user_md(user_md_content: str | None) -> str:
    """提取 compact_time HH:MM；缺失/非法 fallback 到 ``03:30``。"""
    if not user_md_content:
        return DEFAULT_COMPACT_TIME
    for line in _config_lines(user_md_content):
        if "compact_time" not in line:
            continue
        m = _COMPACT_TIME_PATTERN.search(line)
        if m is None:
            continue
        value = m.group(1)
        if _validate_time_format(value):
            return value
        logger.warning(
            "compact_time format invalid; falling back to default",
            raw_value=value,
            default=DEFAULT_COMPACT_TIME,
        )
        return DEFAULT_COMPACT_TIME
    return DEFAULT_COMPACT_TIME


@dataclass(frozen=True, slots=True)
class BehaviorCompactConfig:
    """compact 运行时配置快照（从 USER.md 解析，frozen 不可变）。

    ``user_timezone`` / ``summary_channels`` 复用 F102 extractors（单一事实源）。
    """

    compact_active: bool
    compact_time: str
    user_timezone: str | None = None
    summary_channels: frozenset[str] = DEFAULT_SUMMARY_CHANNELS

    @classmethod
    def from_user_md(cls, user_md_content: str | None) -> BehaviorCompactConfig:
        return cls(
            compact_active=extract_compact_active_from_user_md(user_md_content),
            compact_time=extract_compact_time_from_user_md(user_md_content),
            user_timezone=extract_user_timezone_from_user_md(user_md_content),
            summary_channels=extract_summary_channels_from_user_md(user_md_content),
        )

    def to_crontab(self) -> str:
        """compact_time ``"HH:MM"`` → crontab ``"MM HH * * *"``。"""
        hh, mm = self.compact_time.split(":")
        return f"{int(mm)} {int(hh)} * * *"


__all__ = [
    "DEFAULT_COMPACT_ACTIVE",
    "DEFAULT_COMPACT_TIME",
    "BehaviorCompactConfig",
    "extract_compact_active_from_user_md",
    "extract_compact_time_from_user_md",
]
