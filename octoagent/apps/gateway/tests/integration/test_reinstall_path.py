"""F084 Phase 4 T070：重装路径 E2E 验收测试。

模拟 spec 用户决策 6 的重装路径：
  rm -rf ~/.octoagent/data/ ~/.octoagent/behavior/   # 清 USER.md / data
  octo update                                         # 重启
  # 用户调 user_profile.update(add) → USER.md 落盘 → bootstrap 完成判定 True

覆盖：
- USER.md 不存在的 clean 状态 → bootstrap_completed = False
- user_profile.update(add) 真实写入 → USER.md 出现 + 内容 > 100 字符
- _user_md_substantively_filled 切换为 True → bootstrap 状态机退役后的"完成"信号
- sync_owner_profile_from_user_md 解析回填派生字段
- 不依赖任何旧 BootstrapSession / UserMdRenderer 路径（已 Phase 4 删除）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.core.models.agent_context import (
    _user_md_substantively_filled,
    sync_owner_profile_from_user_md,
)


# ---------------------------------------------------------------------------
# T070 验收
# ---------------------------------------------------------------------------


def test_reinstall_clean_state_user_md_missing(tmp_path: Path) -> None:
    """重装清空后 USER.md 不存在 → 实质填充判定 False（bootstrap_completed = False）。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    assert not user_md.exists(), "前置：USER.md 应该不存在"
    assert _user_md_substantively_filled(user_md) is False, \
        "重装后 USER.md 不存在时 bootstrap_completed 必须是 False（防 F35 真用户感知错误）"


def test_reinstall_user_md_too_short_below_threshold(tmp_path: Path) -> None:
    """USER.md 只有占位符（< 100 字符）→ 不算实质填充。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    # < 100 字符的占位内容
    user_md.write_text("# 待填写\n", encoding="utf-8")
    assert _user_md_substantively_filled(user_md) is False, \
        "USER.md 内容 < 100 字符时不算实质填充"


def test_reinstall_user_md_filled_substantively(tmp_path: Path) -> None:
    """USER.md 内容 > 100 字符 → 实质填充（bootstrap_completed = True 信号）。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    # 模拟用户通过 user_profile.update(add) 写入足够内容（必须 > 100 字符）
    content = (
        "§ 姓名: Connor\n"
        "§ 时区: Asia/Shanghai\n"
        "§ 语言: zh-CN\n"
        "§ 称呼: 你\n"
        "§ 工作风格: 技术深入、追求工程化、语言简洁；不喜欢过度抽象\n"
        "§ 偏好: 中文沟通；代码标识符英文；注释和文档全中文\n"
        "§ 项目背景: OctoAgent 个人 AI OS，Python 3.12 + FastAPI + SQLite WAL\n"
        "§ 关注点: prefix cache 保护、不可逆操作 Plan→Gate→Execute\n"
    )
    assert len(content) > 100, f"测试前提：内容必须 > 100 字符，实际 {len(content)}"
    user_md.write_text(content, encoding="utf-8")

    assert _user_md_substantively_filled(user_md) is True, \
        "USER.md 实质填充后 bootstrap_completed 信号必须切换为 True（F084 完成路径）"


@pytest.mark.asyncio
async def test_reinstall_sync_owner_profile_after_filled(tmp_path: Path) -> None:
    """USER.md 写入后 sync_owner_profile_from_user_md 解析派生字段（FR-9.2）。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    user_md.write_text(
        "§ 姓名: Connor\n"
        "§ 时区: Asia/Shanghai\n"
        "§ 语言: zh-CN\n"
        "§ 称呼: 你\n"
        "§ 工作风格: 技术深入、工程化优先、语言简洁；不喜欢过度抽象\n"
        "§ 项目背景: OctoAgent 个人 AI OS\n"
        "§ 沟通: 中文优先，技术术语保留英文\n",
        encoding="utf-8",
    )

    fields = await sync_owner_profile_from_user_md(user_md)
    assert fields is not None, "USER.md 实质填充时 sync hook 应返回 fields"
    assert fields.get("timezone") == "Asia/Shanghai"
    assert fields.get("locale") == "zh-CN"
    assert fields.get("display_name") == "Connor"
    assert fields.get("preferred_address") == "你"
    assert fields["bootstrap_completed"] is True
    assert "last_synced_from_user_md" in fields


@pytest.mark.asyncio
async def test_reinstall_sync_returns_none_when_user_md_missing(tmp_path: Path) -> None:
    """USER.md 不存在时 sync hook 返回 None 而不是抛异常（FR-9.2 graceful degrade）。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    assert not user_md.exists()

    result = await sync_owner_profile_from_user_md(user_md)
    assert result is None, "USER.md 不存在时 sync hook 应返回 None（不阻断启动）"


def test_reinstall_no_legacy_bootstrap_session_imports() -> None:
    """重装路径不依赖任何 F082 退役 import（防回归引入旧抽象）。

    F084 Phase 4 已退役 BootstrapSession / UserMdRenderer / BootstrapIntegrityChecker
    / bootstrap_orchestrator / bootstrap_commands。任何这些 import 引用都说明
    重装路径回归了旧抽象，必须修复。
    """
    # 显式断言这些模块不可 import（已删除）
    forbidden_modules = [
        "octoagent.gateway.services.bootstrap_orchestrator",
        "octoagent.gateway.services.bootstrap_integrity",
        "octoagent.gateway.services.user_md_renderer",
        "octoagent.gateway.services.builtin_tools.bootstrap_tools",
        "octoagent.provider.dx.bootstrap_commands",
    ]
    import importlib

    for module_name in forbidden_modules:
        with pytest.raises(ImportError):
            importlib.import_module(module_name)


def test_reinstall_no_legacy_bootstrap_session_class() -> None:
    """BootstrapSession 类应该已从 octoagent.core.models.agent_context 删除（F084 Phase 4）。"""
    from octoagent.core.models import agent_context

    assert not hasattr(agent_context, "BootstrapSession"), \
        "BootstrapSession 类应已被 F084 Phase 4 删除"
    assert not hasattr(agent_context, "BootstrapSessionStatus"), \
        "BootstrapSessionStatus 枚举应已被 F084 Phase 4 删除"
