"""F111 Behavior Compactor — PROTECTED 区段占位符提取/插回（H2 护栏，Phase A）。

F063 Phase 3 设计过（`plan.md:212-219`）但从未实现的原语：behavior 文件中
``<!-- 🔒 PROTECTED -->...<!-- /🔒 PROTECTED -->`` 之间的内容在 LLM 智能合并时
必须**字节级原样保留**。

★ 占位符方案（spec §0.1.3 H2，强于"输出比对"方案）：

1. ``extract_protected_sections``：提取全部 PROTECTED 区段（含标记本身），替换成
   ``<<<PROTECTED_n>>>`` 占位符——**LLM 根本看不到受保护内容**，篡改在构造上不可能，
   且省 prompt 预算。
2. ``verify_and_reinsert``：LLM 输出中每个占位符必须 **exactly-once**（缺失/重复 →
   ``ProtectedSectionViolation``）；确定性替换回原文；终局断言无残留占位符 + 每个
   区段字节级包含（构造性保证之上的 belt-and-braces）。

保守语义（不猜测）：
- 标记不配对（有开无闭 / 闭在开前）→ ``ProtectedSectionMalformed``（半个区段泄给
  LLM 比拒绝处理更危险）。
- 原文本身含占位符字面量前缀 ``<<<PROTECTED_`` → ``PlaceholderCollision``（自查①：
  计数/替换会错乱）。

调用方（发现端）把三类异常映射为 SKIPPED reason，行为文件零触碰（C6 保守降级）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: PROTECTED 区段开/闭标记（与 F063 Phase 3 设计一致，emoji 锁是刻意的——
#: 人眼在编辑器里一眼可见"这段不许动"）。
PROTECTED_OPEN_MARKER = "<!-- 🔒 PROTECTED -->"
PROTECTED_CLOSE_MARKER = "<!-- /🔒 PROTECTED -->"

#: 占位符模板 / 识别正则。前缀碰撞守卫用 ``<<<PROTECTED_`` 字面量判定。
_PLACEHOLDER_TEMPLATE = "<<<PROTECTED_{n}>>>"
_PLACEHOLDER_PREFIX = "<<<PROTECTED_"
_PLACEHOLDER_RE = re.compile(r"<<<PROTECTED_(\d+)>>>")


class ProtectedSectionError(ValueError):
    """PROTECTED 处理错误基类（发现端映射为 SKIPPED reason）。"""


class ProtectedSectionMalformed(ProtectedSectionError):
    """标记不配对（有开无闭等）——格式损坏，整文件拒绝处理。"""


class PlaceholderCollision(ProtectedSectionError):
    """原文本身含占位符字面量——替换/计数会错乱，整文件拒绝处理。"""


class ProtectedSectionViolation(ProtectedSectionError):
    """LLM 输出违反占位符契约（缺失/重复/残留）——丢弃提议。"""


@dataclass(frozen=True, slots=True)
class ProtectedExtraction:
    """提取结果：占位后的文本 + 按序区段列表（含标记本身的完整字节）。"""

    masked_content: str
    sections: tuple[str, ...]


def extract_protected_sections(content: str) -> ProtectedExtraction:
    """提取全部 PROTECTED 区段并替换成占位符。

    Raises:
        PlaceholderCollision: 原文含 ``<<<PROTECTED_`` 字面量。
        ProtectedSectionMalformed: 开/闭标记不配对。
    """
    if _PLACEHOLDER_PREFIX in content:
        raise PlaceholderCollision(
            f"原文含占位符字面量 {_PLACEHOLDER_PREFIX!r}，无法安全占位"
        )

    sections: list[str] = []
    parts: list[str] = []
    cursor = 0
    while True:
        open_idx = content.find(PROTECTED_OPEN_MARKER, cursor)
        close_idx = content.find(PROTECTED_CLOSE_MARKER, cursor)
        if open_idx == -1 and close_idx == -1:
            break
        # 闭标记先于开标记出现（或只有闭标记）→ 不配对
        if open_idx == -1 or (close_idx != -1 and close_idx < open_idx):
            raise ProtectedSectionMalformed("PROTECTED 闭标记先于开标记出现")
        end_idx = content.find(
            PROTECTED_CLOSE_MARKER, open_idx + len(PROTECTED_OPEN_MARKER)
        )
        if end_idx == -1:
            raise ProtectedSectionMalformed("PROTECTED 开标记缺少配对的闭标记")
        section_end = end_idx + len(PROTECTED_CLOSE_MARKER)
        # 区段含标记本身——插回后连标记一起字节级不变
        parts.append(content[cursor:open_idx])
        parts.append(_PLACEHOLDER_TEMPLATE.format(n=len(sections)))
        sections.append(content[open_idx:section_end])
        cursor = section_end

    parts.append(content[cursor:])
    return ProtectedExtraction(
        masked_content="".join(parts), sections=tuple(sections)
    )


def verify_and_reinsert(compacted: str, sections: tuple[str, ...]) -> str:
    """校验 LLM 输出的占位符契约并确定性插回受保护区段。

    契约：``sections`` 中每个下标 n 的占位符 ``<<<PROTECTED_n>>>`` 必须在
    ``compacted`` 中出现 **exactly-once**，且不得出现任何未知/越界占位符。

    Returns:
        插回后的最终全文（终局断言：无残留占位符 + 每个区段字节级包含）。

    Raises:
        ProtectedSectionViolation: 占位符缺失/重复/未知，或终局断言失败。
    """
    found = _PLACEHOLDER_RE.findall(compacted)
    expected = {str(i) for i in range(len(sections))}
    counts: dict[str, int] = {}
    for n in found:
        counts[n] = counts.get(n, 0) + 1

    unknown = set(counts) - expected
    if unknown:
        raise ProtectedSectionViolation(
            f"LLM 输出含未知占位符下标：{sorted(unknown)}"
        )
    missing = sorted(n for n in expected if counts.get(n, 0) == 0)
    if missing:
        raise ProtectedSectionViolation(f"LLM 输出缺失占位符下标：{missing}")
    duplicated = sorted(n for n in expected if counts.get(n, 0) > 1)
    if duplicated:
        raise ProtectedSectionViolation(f"LLM 输出重复占位符下标：{duplicated}")
    # 非标准形态残留（如 <<<PROTECTED_x>>> 手写变体）——正则抓不到但前缀在，拒绝
    stripped = _PLACEHOLDER_RE.sub("", compacted)
    if _PLACEHOLDER_PREFIX in stripped:
        raise ProtectedSectionViolation("LLM 输出含非标准占位符残留")

    result = compacted
    for i, section in enumerate(sections):
        result = result.replace(_PLACEHOLDER_TEMPLATE.format(n=i), section, 1)

    # belt-and-braces 终局断言（构造上必真；防未来重构破坏）
    if _PLACEHOLDER_PREFIX in result:
        raise ProtectedSectionViolation("插回后仍有占位符残留")
    for section in sections:
        if section not in result:
            raise ProtectedSectionViolation("插回后 PROTECTED 区段字节级对账失败")
    return result


__all__ = [
    "PROTECTED_CLOSE_MARKER",
    "PROTECTED_OPEN_MARKER",
    "PlaceholderCollision",
    "ProtectedExtraction",
    "ProtectedSectionError",
    "ProtectedSectionMalformed",
    "ProtectedSectionViolation",
    "extract_protected_sections",
    "verify_and_reinsert",
]
