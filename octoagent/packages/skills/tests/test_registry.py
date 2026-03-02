"""SkillRegistry 单元测试。"""

import pytest
from octoagent.skills.exceptions import SkillNotFoundError, SkillRegistrationError
from octoagent.skills.registry import SkillRegistry


def test_registry_register_and_get(echo_manifest) -> None:
    registry = SkillRegistry()
    registry.register(echo_manifest, "echo prompt")
    item = registry.get("demo.echo")
    assert item.manifest.skill_id == "demo.echo"
    assert item.prompt_template == "echo prompt"


def test_registry_duplicate_rejected(echo_manifest) -> None:
    registry = SkillRegistry()
    registry.register(echo_manifest, "echo prompt")
    with pytest.raises(SkillRegistrationError):
        registry.register(echo_manifest, "echo prompt")


def test_registry_not_found() -> None:
    registry = SkillRegistry()
    with pytest.raises(SkillNotFoundError):
        registry.get("missing")


def test_registry_unregister(echo_manifest) -> None:
    registry = SkillRegistry()
    registry.register(echo_manifest, "echo prompt")
    assert registry.unregister("demo.echo") is True
    assert registry.unregister("demo.echo") is False
