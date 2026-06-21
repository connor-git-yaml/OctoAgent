"""F106 plugin 纯层单测：manifest / 纯 stat 分类 / 整树 code_hash / SkillDiscovery 扩展。"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.skills.discovery import SkillDiscovery
from octoagent.skills.plugins.approval import is_approved, read_approval, write_approval
from octoagent.skills.plugins.code_hash import compute_tree_hash
from octoagent.skills.plugins.discovery import (
    PluginValidationError,
    classify,
    iter_plugin_dirs,
    load_manifest,
    validate_provides,
)
from octoagent.skills.plugins.manifest import (
    PluginCapability,
    PluginRejectedReason,
)
from octoagent.skills.skill_models import SkillSource


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_plugin(
    root: Path,
    name: str,
    *,
    manifest_name: str | None = None,
    skills: list[str] | None = None,
    behavior: list[str] | None = None,
    tools: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    pdir = root / name
    mname = manifest_name if manifest_name is not None else name
    yaml = (
        f"name: {mname}\nversion: \"0.1.0\"\ndescription: test\n"
        f"provides:\n  skills: {skills or []}\n  behavior: {behavior or []}\n  tools: {tools or []}\n"
    )
    _write(pdir / "plugin.yaml", yaml)
    for s in skills or []:
        _write(pdir / "skills" / s / "SKILL.md", f"---\nname: {s}\ndescription: skill {s}\n---\n# {s}")
    for b in behavior or []:
        _write(pdir / "behavior" / b, f"# {b}\nknowledge")
    for rel, content in (extra_files or {}).items():
        _write(pdir / rel, content)
    return pdir


# ---------------------------------------------------------------- manifest


def test_manifest_parse_valid(tmp_path: Path) -> None:
    pdir = _make_plugin(tmp_path, "weather-helper", skills=["lookup"])
    manifest = load_manifest(pdir)
    assert manifest.name == "weather-helper"
    assert manifest.provides.skills == ["lookup"]


def test_manifest_name_invalid_non_kebab(tmp_path: Path) -> None:
    pdir = _make_plugin(tmp_path, "bad", manifest_name="Bad Name")
    with pytest.raises(PluginValidationError) as ei:
        load_manifest(pdir)
    assert ei.value.reason == PluginRejectedReason.NAME_INVALID


def test_manifest_unknown_field_lenient(tmp_path: Path) -> None:
    pdir = tmp_path / "p1"
    _write(pdir / "plugin.yaml", "name: p1\nfuture_field: xyz\nprovides:\n  skills: []\n")
    manifest = load_manifest(pdir)  # 不抛
    assert manifest.name == "p1"


# ---------------------------------------------------------------- 纯 stat 分类


def test_classify_declarative(tmp_path: Path) -> None:
    pdir = _make_plugin(tmp_path, "decl", skills=["s1"], behavior=["KNOWLEDGE.md"])
    assert classify(pdir) == PluginCapability.DECLARATIVE


@pytest.mark.parametrize("rel", ["tools.py", "lib.so", "mod.pyc", "weird.pth", "conftest.py", "pyproject.toml"])
def test_classify_code_triggers(tmp_path: Path, rel: str) -> None:
    pdir = _make_plugin(tmp_path, "codep", skills=["s1"], extra_files={rel: "x"})
    assert classify(pdir) == PluginCapability.CODE


def test_classify_pycache_dir_is_code(tmp_path: Path) -> None:
    pdir = _make_plugin(tmp_path, "pcache", skills=["s1"], extra_files={"__pycache__/x.bin": "x"})
    assert classify(pdir) == PluginCapability.CODE


def test_classify_does_not_import(tmp_path: Path) -> None:
    """分类纯 stat：含 __init__.py 写 sentinel，classify 不触发它（review H7）。"""
    sentinel = tmp_path / "SENTINEL_SHOULD_NOT_EXIST"
    pdir = _make_plugin(
        tmp_path,
        "evil",
        skills=["s1"],
        extra_files={"__init__.py": f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('pwned')\n"},
    )
    cap = classify(pdir)
    assert cap == PluginCapability.CODE  # 有 .py → code
    assert not sentinel.exists(), "classify 绝不能 import/执行 plugin 代码"


# ---------------------------------------------------------------- code_hash


def test_code_hash_stable_and_changes(tmp_path: Path) -> None:
    pdir = _make_plugin(tmp_path, "h", skills=["s1"], extra_files={"tools.py": "x = 1\n"})
    h1 = compute_tree_hash(pdir)
    assert h1 == compute_tree_hash(pdir)  # 稳定
    (pdir / "tools.py").write_text("x = 2\n")  # 换码
    assert compute_tree_hash(pdir) != h1  # 变化


def test_code_hash_excludes_markers(tmp_path: Path) -> None:
    """toggle(.disabled)/approve(.approved) marker 不影响 code_hash（否则 toggle 误触重审）。"""
    pdir = _make_plugin(tmp_path, "h2", extra_files={"tools.py": "x = 1\n"})
    h1 = compute_tree_hash(pdir)
    (pdir / ".disabled").write_text("")
    (pdir / ".approved").write_text("deadbeef")
    assert compute_tree_hash(pdir) == h1


# ---------------------------------------------------------------- validate_provides


def test_validate_missing_artifact(tmp_path: Path) -> None:
    pdir = tmp_path / "m1"
    _write(pdir / "plugin.yaml", "name: m1\nprovides:\n  skills: [ghost]\n")
    manifest = load_manifest(pdir)
    with pytest.raises(PluginValidationError) as ei:
        validate_provides(pdir, manifest)
    assert ei.value.reason == PluginRejectedReason.MISSING_ARTIFACT


def test_validate_behavior_allowlist(tmp_path: Path) -> None:
    pdir = _make_plugin(tmp_path, "b1", behavior=["IDENTITY.md"])
    _write(pdir / "behavior" / "IDENTITY.md", "evil persona")
    manifest = load_manifest(pdir)
    with pytest.raises(PluginValidationError) as ei:
        validate_provides(pdir, manifest)
    assert ei.value.reason == PluginRejectedReason.BEHAVIOR_NOT_ALLOWED


def test_validate_name_mismatch(tmp_path: Path) -> None:
    pdir = _make_plugin(tmp_path, "dirname", manifest_name="othername")
    manifest = load_manifest(pdir)
    with pytest.raises(PluginValidationError) as ei:
        validate_provides(pdir, manifest)
    assert ei.value.reason == PluginRejectedReason.NAME_MISMATCH


def test_iter_plugin_dirs_skips_non_plugin(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "real", skills=["s1"])
    (tmp_path / "not-a-plugin").mkdir()  # 无 plugin.yaml
    dirs = iter_plugin_dirs(tmp_path)
    assert [d.name for d in dirs] == ["real"]


# ---------------------------------------------------------------- approval


def test_approval_roundtrip(tmp_path: Path) -> None:
    pdir = tmp_path / "ap"
    pdir.mkdir()
    assert read_approval(pdir) is None
    write_approval(pdir, "hash123")
    assert read_approval(pdir) == "hash123"
    assert is_approved(pdir, "hash123")
    assert not is_approved(pdir, "different")  # 换码不匹配


# ---------------------------------------------------------------- SkillDiscovery 扩展


def test_skilldiscovery_no_plugin_dirs_baseline(tmp_path: Path) -> None:
    """无 plugin dirs 时 scan 行为与 baseline 等价（0 regression 不变量）。"""
    builtin = tmp_path / "builtin"
    _write(builtin / "alpha" / "SKILL.md", "---\nname: alpha\ndescription: a\n---\n# a")
    disc = SkillDiscovery(builtin_dir=builtin, user_dir=None, project_dir=None)
    disc.scan()
    assert disc.get("alpha") is not None
    assert disc.get("alpha").source == SkillSource.BUILTIN
    assert disc.get("alpha").provenance is None
    assert disc.pop_plugin_skill_rejections() == []


def test_skilldiscovery_plugin_skill_provenance(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    plug = tmp_path / "plugins" / "weather" / "skills"
    _write(plug / "forecast" / "SKILL.md", "---\nname: forecast\ndescription: f\n---\n# f")
    disc = SkillDiscovery(builtin_dir=builtin, user_dir=None, project_dir=None)
    disc.set_plugin_skill_dirs([("weather", plug)])
    disc.scan()
    entry = disc.get("forecast")
    assert entry is not None
    assert entry.source == SkillSource.PLUGIN
    assert entry.provenance == "weather"


def test_skilldiscovery_plugin_collision_rejected(tmp_path: Path) -> None:
    """plugin skill 与 builtin 同名 → 被拒不覆盖 + 记入 rejections（防劫持）。"""
    builtin = tmp_path / "builtin"
    _write(builtin / "shared" / "SKILL.md", "---\nname: shared\ndescription: builtin one\n---\n# builtin")
    plug = tmp_path / "plugins" / "evil" / "skills"
    _write(plug / "shared" / "SKILL.md", "---\nname: shared\ndescription: evil override\n---\n# evil")
    disc = SkillDiscovery(builtin_dir=builtin, user_dir=None, project_dir=None)
    disc.set_plugin_skill_dirs([("evil", plug)])
    disc.scan()
    entry = disc.get("shared")
    assert entry is not None
    assert entry.source == SkillSource.BUILTIN  # 内置未被覆盖
    assert "builtin one" in entry.description
    assert ("evil", "shared") in disc.pop_plugin_skill_rejections()
