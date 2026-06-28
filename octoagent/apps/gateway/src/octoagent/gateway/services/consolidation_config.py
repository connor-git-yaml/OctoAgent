"""F127 Sleep-Time Memory Consolidation — 配置解析（USER.md 机器可读字段）。

复用 F102 ``daily_routine_config`` 的解析范式（强制 key prefix 正则 + 跳过 HTML 注释行 +
非法值 fallback 默认 + WARNING log，Constitution C6）。v0.1 **不动 USER.md 模板**
（避免 1800 字符预算超限，memory: project_user_md_template_budget）——字段缺失时全部
走默认值，用户显式在 USER.md 加字段才生效。

USER.md 机器可读字段（FR-F1）：
- ``consolidation_active``（bool，默认 **False**——v0.1 保守默认关，用户显式开）
- ``consolidation_time``（HH:MM，默认 ``03:00`` 深夜空闲期）
- ``consolidation_window_days``（int，默认 7）
- ``consolidation_max_facts``（int，默认 50）

时区复用 F102 ``extract_user_timezone_from_user_md``（同一 USER.md user_timezone 字段，
单一事实源不重复解析）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import structlog

logger = structlog.get_logger(__name__)


# ============================================================
# 默认值（FR-F1）
# ============================================================

#: v0.1 保守默认 **关闭**——巩固会改用户既有记忆（即便人审），用户必须显式开启。
DEFAULT_CONSOLIDATION_ACTIVE: Final[bool] = False
DEFAULT_CONSOLIDATION_TIME: Final[str] = "03:00"
DEFAULT_CONSOLIDATION_WINDOW_DAYS: Final[int] = 7
DEFAULT_CONSOLIDATION_MAX_FACTS: Final[int] = 50

# 窗口/事实数合理上界（防 USER.md 误填超大值拖垮巩固运行）
_MAX_WINDOW_DAYS: Final[int] = 365
_MAX_FACTS_CAP: Final[int] = 1000


# ============================================================
# 解析正则（强制 key prefix，复用 F102 H1 BLOCKER 修复范式 + 左边界锚定）
# ============================================================
#
# Codex review（round3）：仅"key MUST 出现"还不够——``search()`` 会把
# ``previous_consolidation_active: true`` / ``last_consolidation_time: 02:00`` 这类用户在
# USER.md 里写的**说明性字段**当真实配置匹配（key 作为子串出现）。consolidation_active 默认
# False 且门控**破坏性**记忆合并，false-positive 误开后果严重（C4/C7）。故每个 pattern 前加
# 负向左边界 ``(?<![\w])``（key 前不能紧贴标识符字符），挡掉 ``previous_`` / ``last_`` 等前缀，
# 仍允许 ``**consolidation_active**`` / 行首裸 key / ``- consolidation_active:``。
# 注：F102 daily_routine_config 同范式有同隐患（但非破坏性、低风险），留独立 follow-up，
# 不在 F127 范围内扩。

_CONSOLIDATION_ACTIVE_PATTERN = re.compile(
    r"""
    (?<![\w])                       # 左边界：key 前不得紧贴标识符字符（挡 previous_ 等）
    (?:\*\*)?
    consolidation_active            # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    (true|false|True|False)
    "?
    """,
    re.VERBOSE,
)

_CONSOLIDATION_TIME_PATTERN = re.compile(
    r"""
    (?<![\w])
    (?:\*\*)?
    consolidation_time              # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    (\d{1,2}:\d{2})                 # HH:MM
    "?
    """,
    re.VERBOSE,
)

_CONSOLIDATION_WINDOW_PATTERN = re.compile(
    r"""
    (?<![\w])
    (?:\*\*)?
    consolidation_window_days       # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    (\d{1,4})
    "?
    """,
    re.VERBOSE,
)

_CONSOLIDATION_MAX_FACTS_PATTERN = re.compile(
    r"""
    (?<![\w])
    (?:\*\*)?
    consolidation_max_facts         # MUST 出现
    (?:\*\*)?
    \s*:\s*
    "?
    (\d{1,5})
    "?
    """,
    re.VERBOSE,
)


def _validate_time_format(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        hh, mm = int(parts[0]), int(parts[1])
        return 0 <= hh <= 23 and 0 <= mm <= 59
    except ValueError:
        return False


def extract_consolidation_active_from_user_md(user_md_content: str | None) -> bool:
    """提取 consolidation_active；缺失/非法 fallback 到 **False**（v0.1 保守默认关）。"""
    if not user_md_content:
        return DEFAULT_CONSOLIDATION_ACTIVE
    for line in user_md_content.splitlines():
        if line.lstrip().startswith("<!--"):
            continue
        if "consolidation_active" not in line:
            continue
        m = _CONSOLIDATION_ACTIVE_PATTERN.search(line)
        if m is None:
            continue
        value = m.group(1).lower()
        if value == "true":
            return True
        if value == "false":
            return False
        logger.warning(
            "consolidation_active value invalid; falling back to default",
            raw_value=value,
            default=DEFAULT_CONSOLIDATION_ACTIVE,
        )
        return DEFAULT_CONSOLIDATION_ACTIVE
    return DEFAULT_CONSOLIDATION_ACTIVE


def extract_consolidation_time_from_user_md(user_md_content: str | None) -> str:
    """提取 consolidation_time HH:MM；缺失/非法 fallback 到 ``03:00``。"""
    if not user_md_content:
        return DEFAULT_CONSOLIDATION_TIME
    for line in user_md_content.splitlines():
        if line.lstrip().startswith("<!--"):
            continue
        if "consolidation_time" not in line:
            continue
        m = _CONSOLIDATION_TIME_PATTERN.search(line)
        if m is None:
            continue
        value = m.group(1)
        if _validate_time_format(value):
            return value
        logger.warning(
            "consolidation_time format invalid; falling back to default",
            raw_value=value,
            default=DEFAULT_CONSOLIDATION_TIME,
        )
        return DEFAULT_CONSOLIDATION_TIME
    return DEFAULT_CONSOLIDATION_TIME


def extract_consolidation_window_days_from_user_md(user_md_content: str | None) -> int:
    """提取 consolidation_window_days；缺失/越界 fallback 到 7（上界 365）。"""
    if not user_md_content:
        return DEFAULT_CONSOLIDATION_WINDOW_DAYS
    for line in user_md_content.splitlines():
        if line.lstrip().startswith("<!--"):
            continue
        if "consolidation_window_days" not in line:
            continue
        m = _CONSOLIDATION_WINDOW_PATTERN.search(line)
        if m is None:
            continue
        try:
            value = int(m.group(1))
        except ValueError:
            continue
        if 1 <= value <= _MAX_WINDOW_DAYS:
            return value
        logger.warning(
            "consolidation_window_days out of range; falling back to default",
            raw_value=value,
            default=DEFAULT_CONSOLIDATION_WINDOW_DAYS,
        )
        return DEFAULT_CONSOLIDATION_WINDOW_DAYS
    return DEFAULT_CONSOLIDATION_WINDOW_DAYS


def extract_consolidation_max_facts_from_user_md(user_md_content: str | None) -> int:
    """提取 consolidation_max_facts；缺失/越界 fallback 到 50（上界 1000）。"""
    if not user_md_content:
        return DEFAULT_CONSOLIDATION_MAX_FACTS
    for line in user_md_content.splitlines():
        if line.lstrip().startswith("<!--"):
            continue
        if "consolidation_max_facts" not in line:
            continue
        m = _CONSOLIDATION_MAX_FACTS_PATTERN.search(line)
        if m is None:
            continue
        try:
            value = int(m.group(1))
        except ValueError:
            continue
        if 1 <= value <= _MAX_FACTS_CAP:
            return value
        logger.warning(
            "consolidation_max_facts out of range; falling back to default",
            raw_value=value,
            default=DEFAULT_CONSOLIDATION_MAX_FACTS,
        )
        return DEFAULT_CONSOLIDATION_MAX_FACTS
    return DEFAULT_CONSOLIDATION_MAX_FACTS


@dataclass(frozen=True, slots=True)
class ConsolidationConfig:
    """巩固运行时配置快照（从 USER.md 解析，frozen 不可变）。

    ``user_timezone`` 复用 F102 ``extract_user_timezone_from_user_md``（同一字段单一
    事实源）；``None`` 表示 USER.md 未提供，由 service 降级 env → UTC。
    """

    consolidation_active: bool
    consolidation_time: str
    consolidation_window_days: int
    consolidation_max_facts: int
    user_timezone: str | None = None

    @classmethod
    def from_user_md(cls, user_md_content: str | None) -> ConsolidationConfig:
        # 复用 F102 时区解析（避免重复造，单一事实源）
        from .daily_routine_config import extract_user_timezone_from_user_md

        return cls(
            consolidation_active=extract_consolidation_active_from_user_md(user_md_content),
            consolidation_time=extract_consolidation_time_from_user_md(user_md_content),
            consolidation_window_days=extract_consolidation_window_days_from_user_md(
                user_md_content
            ),
            consolidation_max_facts=extract_consolidation_max_facts_from_user_md(
                user_md_content
            ),
            user_timezone=extract_user_timezone_from_user_md(user_md_content),
        )

    def to_crontab(self) -> str:
        """consolidation_time ``"HH:MM"`` → crontab ``"MM HH * * *"``。"""
        hh, mm = self.consolidation_time.split(":")
        return f"{int(mm)} {int(hh)} * * *"


__all__ = [
    "DEFAULT_CONSOLIDATION_ACTIVE",
    "DEFAULT_CONSOLIDATION_MAX_FACTS",
    "DEFAULT_CONSOLIDATION_TIME",
    "DEFAULT_CONSOLIDATION_WINDOW_DAYS",
    "ConsolidationConfig",
    "extract_consolidation_active_from_user_md",
    "extract_consolidation_max_facts_from_user_md",
    "extract_consolidation_time_from_user_md",
    "extract_consolidation_window_days_from_user_md",
]
