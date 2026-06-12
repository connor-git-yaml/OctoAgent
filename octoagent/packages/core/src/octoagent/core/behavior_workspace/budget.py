from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from ._types import BEHAVIOR_FILE_BUDGETS


# ---------------------------------------------------------------------------
# Feature 063: Head/Tail 截断策略
# ---------------------------------------------------------------------------


def truncate_behavior_content(content: str, budget: int) -> str:
    """按 head/tail 策略截断行为文件内容。

    保留 70% 头部 + 20% 尾部 + 中间插入截断标记。
    最小预算 64 字符——低于此阈值返回空字符串。
    """
    content = content.strip()
    if len(content) <= budget:
        return content
    if budget < 64:
        return ""

    # 截断标记本身需要的空间（估算）
    marker_template = (
        "\n\n[... 中间内容已截断（原文 {total} 字符，预算 {budget} 字符），"
        "完整内容请使用 offset/limit 参数分段读取 ...]\n\n"
    )
    marker = marker_template.format(total=len(content), budget=budget)
    marker_len = len(marker)

    usable = budget - marker_len
    if usable < 40:
        # 预算太紧，只保留头部
        return content[:budget]

    head_len = int(usable * 0.7)
    tail_len = int(usable * 0.2)
    # 剩余给 marker
    head = content[:head_len].rstrip()
    tail = content[-tail_len:].lstrip() if tail_len > 0 else ""

    return head + marker + tail


@dataclass(frozen=True, slots=True)
class _BehaviorBudgetResult:
    content: str
    budget_chars: int
    original_char_count: int
    effective_char_count: int
    truncated: bool
    truncation_reason: str


def _budget_for_file(file_id: str) -> int:
    return int(BEHAVIOR_FILE_BUDGETS.get(file_id, 2000))


def _apply_behavior_budget(*, file_id: str, content: str) -> _BehaviorBudgetResult:
    """应用字符预算限制，超出时使用 head/tail 截断策略（Feature 063 T2.3）。"""
    normalized = content.strip()
    original_char_count = len(normalized)
    budget_chars = _budget_for_file(file_id)
    if original_char_count <= budget_chars:
        return _BehaviorBudgetResult(
            content=normalized,
            budget_chars=budget_chars,
            original_char_count=original_char_count,
            effective_char_count=original_char_count,
            truncated=False,
            truncation_reason="",
        )
    # Feature 063: 改用 head/tail 截断（70% 头 + 20% 尾 + 中间标记）
    effective = truncate_behavior_content(normalized, budget_chars)
    return _BehaviorBudgetResult(
        content=effective,
        budget_chars=budget_chars,
        original_char_count=original_char_count,
        effective_char_count=len(effective),
        truncated=True,
        truncation_reason="char_budget_exceeded",
    )


class BehaviorBudgetResult(TypedDict):
    """check_behavior_file_budget 的返回类型。"""

    within_budget: bool
    current_chars: int
    budget_chars: int
    exceeded_by: int


def check_behavior_file_budget(file_path: str, content: str) -> BehaviorBudgetResult:
    """检查内容是否超出字符预算。

    从 file_path 末段提取 file_id，在 BEHAVIOR_FILE_BUDGETS 中查找预算上限。
    未知 file_id 默认不限制（within_budget=True）。
    """
    file_id = Path(file_path).name
    budget = BEHAVIOR_FILE_BUDGETS.get(file_id)
    current_chars = len(content)

    if budget is None:
        # 未知 file_id，不限制
        return {
            "within_budget": True,
            "current_chars": current_chars,
            "budget_chars": 0,
            "exceeded_by": 0,
        }

    exceeded_by = max(0, current_chars - budget)
    return {
        "within_budget": current_chars <= budget,
        "current_chars": current_chars,
        "budget_chars": budget,
        "exceeded_by": exceeded_by,
    }
