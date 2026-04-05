"""SideEffectLevel 下沉到 core 后的验证测试。

验证：
1. core 中枚举可正常使用
2. tooling re-export 保持对象同一性（identity）
3. 枚举值与 FR-025a 锁定值一致
"""

from octoagent.core.models.enums import SideEffectLevel


def test_side_effect_level_values():
    """枚举值对齐 FR-025a 锁定规范。"""
    assert SideEffectLevel.NONE == "none"
    assert SideEffectLevel.REVERSIBLE == "reversible"
    assert SideEffectLevel.IRREVERSIBLE == "irreversible"
    assert len(SideEffectLevel) == 3


def test_side_effect_level_from_core_models():
    """可从 core.models 入口导入。"""
    from octoagent.core.models import SideEffectLevel as FromModels

    assert FromModels is SideEffectLevel


def test_side_effect_level_reexport_identity():
    """tooling re-export 与 core 定义是同一对象（isinstance/dict key 兼容）。"""
    from octoagent.tooling.models import SideEffectLevel as FromTooling

    assert FromTooling is SideEffectLevel
    # dict key 查找一致性
    d = {SideEffectLevel.NONE: "ok"}
    assert d[FromTooling.NONE] == "ok"
