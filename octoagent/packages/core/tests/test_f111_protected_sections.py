"""F111 Phase A：PROTECTED 区段占位符提取/插回（H2 护栏 helper）测试。

AC-4 helper 层绑定（spec §6）：extract→占位→verify→插回 全链字节级不变；
碰撞/不配对/缺失/重复/未知/残留 全部保守拒绝。
"""

from __future__ import annotations

import pytest
from octoagent.core.behavior_workspace import (
    PROTECTED_CLOSE_MARKER,
    PROTECTED_OPEN_MARKER,
    PlaceholderCollision,
    ProtectedSectionMalformed,
    ProtectedSectionViolation,
    extract_protected_sections,
    verify_and_reinsert,
)


def _wrap(text: str) -> str:
    return f"{PROTECTED_OPEN_MARKER}\n{text}\n{PROTECTED_CLOSE_MARKER}"


class TestExtract:
    def test_no_sections_roundtrip(self):
        content = "# AGENTS\n\n- 规则 A\n- 规则 B\n"
        ex = extract_protected_sections(content)
        assert ex.sections == ()
        assert ex.masked_content == content
        # 空集 trivially pass（spec §7）
        assert verify_and_reinsert(ex.masked_content, ex.sections) == content

    def test_single_section_masked_and_recorded(self):
        section = _wrap("- 核心规则：绝不删库")
        content = f"# AGENTS\n\n{section}\n\n- 普通规则\n"
        ex = extract_protected_sections(content)
        assert ex.sections == (section,)
        assert "<<<PROTECTED_0>>>" in ex.masked_content
        # 受保护内容（含标记）绝不出现在送 LLM 的文本里
        assert "绝不删库" not in ex.masked_content
        assert PROTECTED_OPEN_MARKER not in ex.masked_content

    def test_multi_section_order_preserved(self):
        s0 = _wrap("第一段")
        s1 = _wrap("第二段")
        content = f"头\n{s0}\n中\n{s1}\n尾\n"
        ex = extract_protected_sections(content)
        assert ex.sections == (s0, s1)
        assert ex.masked_content.index("<<<PROTECTED_0>>>") < ex.masked_content.index(
            "<<<PROTECTED_1>>>"
        )

    def test_unclosed_marker_rejected(self):
        content = f"头\n{PROTECTED_OPEN_MARKER}\n没有闭标记\n"
        with pytest.raises(ProtectedSectionMalformed):
            extract_protected_sections(content)

    def test_close_before_open_rejected(self):
        content = f"头\n{PROTECTED_CLOSE_MARKER}\n{PROTECTED_OPEN_MARKER}\n"
        with pytest.raises(ProtectedSectionMalformed):
            extract_protected_sections(content)

    def test_placeholder_collision_rejected(self):
        """自查①：原文本身含占位符字面量 → 拒绝处理。"""
        content = "# 文档\n<<<PROTECTED_0>>> 这行是用户手写的\n"
        with pytest.raises(PlaceholderCollision):
            extract_protected_sections(content)


class TestVerifyAndReinsert:
    def test_byte_level_roundtrip(self):
        """H2 核心：extract → LLM 原样保留占位符 → 插回后区段字节级不变。"""
        section = _wrap("- 核心规则：绝不删库\n- 时区：Asia/Shanghai")
        content = f"# AGENTS\n\n{section}\n\n- 冗余规则 1\n- 冗余规则 2\n"
        ex = extract_protected_sections(content)
        # 模拟 LLM 精简了普通内容但保留占位符
        compacted = "# AGENTS\n\n<<<PROTECTED_0>>>\n\n- 合并后规则\n"
        final = verify_and_reinsert(compacted, ex.sections)
        assert section in final
        assert "<<<PROTECTED_" not in final

    def test_missing_placeholder_rejected(self):
        ex = extract_protected_sections(_wrap("内容"))
        with pytest.raises(ProtectedSectionViolation, match="缺失"):
            verify_and_reinsert("# 精简后没带占位符\n", ex.sections)

    def test_duplicated_placeholder_rejected(self):
        ex = extract_protected_sections(_wrap("内容"))
        with pytest.raises(ProtectedSectionViolation, match="重复"):
            verify_and_reinsert(
                "<<<PROTECTED_0>>>\n<<<PROTECTED_0>>>\n", ex.sections
            )

    def test_unknown_placeholder_rejected(self):
        ex = extract_protected_sections(_wrap("内容"))
        with pytest.raises(ProtectedSectionViolation, match="未知"):
            verify_and_reinsert(
                "<<<PROTECTED_0>>>\n<<<PROTECTED_7>>>\n", ex.sections
            )

    def test_nonstandard_placeholder_residue_rejected(self):
        ex = extract_protected_sections(_wrap("内容"))
        with pytest.raises(ProtectedSectionViolation, match="非标准"):
            verify_and_reinsert(
                "<<<PROTECTED_0>>>\n<<<PROTECTED_x>>>\n", ex.sections
            )

    def test_reordered_placeholders_allowed_content_intact(self):
        """spec §7：LLM 有权重排非保护内容顺序——占位符位置变但内容字节级不变。"""
        s0 = _wrap("A 段")
        s1 = _wrap("B 段")
        ex = extract_protected_sections(f"{s0}\n{s1}\n")
        final = verify_and_reinsert(
            "<<<PROTECTED_1>>>\n<<<PROTECTED_0>>>\n", ex.sections
        )
        assert s0 in final
        assert s1 in final
        assert final.index(s1) < final.index(s0)
