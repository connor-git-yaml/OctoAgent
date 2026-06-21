"""plugin 发现 + 纯 stat 能力分类 + manifest 校验（F106 Phase A，review H7）。

发现/分类/校验**纯文件系统 stat + 文本读，绝不 import / __import__ / 加 sys.path**——
防 __init__.py / .pth / conftest.py 的 import 副作用旁路审批 gate（spec FR-1.4）。
代码加载只发生在 loader.py，且仅对 ENABLED + code_hash 匹配的 plugin（Phase B）。
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from .manifest import (
    _CODE_FILE_NAMES,
    _CODE_FILE_SUFFIXES,
    PLUGIN_BEHAVIOR_ALLOWLIST,
    PLUGIN_MANIFEST_FILE,
    PluginCapability,
    PluginManifest,
    PluginRejectedReason,
)

log = structlog.get_logger(__name__)


class PluginValidationError(Exception):
    """plugin 校验失败，携带 PluginRejectedReason（registry 据此降级隔离）。"""

    def __init__(self, reason: PluginRejectedReason, detail: str = "") -> None:
        super().__init__(detail or reason.value)
        self.reason = reason
        self.detail = detail


def iter_plugin_dirs(plugins_dir: Path) -> list[Path]:
    """列出 plugins_dir 下含 plugin.yaml 的一级子目录（无 yaml 静默跳过）。"""
    if not plugins_dir.is_dir():
        return []
    out: list[Path] = []
    try:
        children = sorted(plugins_dir.iterdir())
    except OSError as exc:
        log.warning("plugin_discovery_dir_error", plugins_dir=str(plugins_dir), error=str(exc))
        return []
    for d in children:
        # 跳过 symlink 目录（防 plugin 目录指向树外，review H-1）
        if not d.is_dir() or d.is_symlink() or d.name.startswith("."):
            continue
        if (d / PLUGIN_MANIFEST_FILE).is_file():
            out.append(d)
    return out


def validate_no_symlinks(plugin_dir: Path) -> None:
    """拒绝含任何 symlink 的 plugin（review H-1）。

    plugin 无合法 symlink 需求；symlink 会在 code_hash（跳过 symlink）与 loader（resolve 跟随）
    之间制造"审批 hash ≠ 执行字节"的缝 → 审批后换 symlink 目标 = 静默 RCE。v0.1 一律拒。

    Raises:
        PluginValidationError(PATH_ESCAPE): 树内含 symlink。
    """
    try:
        for f in plugin_dir.rglob("*"):
            if f.is_symlink():
                raise PluginValidationError(
                    PluginRejectedReason.PATH_ESCAPE, f"plugin 含 symlink（拒）: {f.name}"
                )
    except OSError as exc:
        raise PluginValidationError(PluginRejectedReason.PATH_ESCAPE, str(exc)) from exc


def load_manifest(plugin_dir: Path) -> PluginManifest:
    """解析 + 基础校验 plugin.yaml（yaml.safe_load，非 unsafe）。

    Raises:
        PluginValidationError: manifest 非法 / name 非法。
    """
    try:
        raw = (plugin_dir / PLUGIN_MANIFEST_FILE).read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginValidationError(PluginRejectedReason.MANIFEST_INVALID, str(exc)) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise PluginValidationError(PluginRejectedReason.MANIFEST_INVALID, f"YAML 错误: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginValidationError(
            PluginRejectedReason.MANIFEST_INVALID, "plugin.yaml 顶层非 mapping"
        )
    try:
        return PluginManifest.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        # name 字段校验失败（kebab/长度）→ NAME_INVALID；其余 → MANIFEST_INVALID
        reason = (
            PluginRejectedReason.NAME_INVALID
            if "name" in str(exc).lower()
            else PluginRejectedReason.MANIFEST_INVALID
        )
        raise PluginValidationError(reason, str(exc)) from exc


def classify(plugin_dir: Path) -> PluginCapability:
    """纯 stat 能力分类：含任一可执行触发文件 → CODE，否则 DECLARATIVE。

    **绝不 import**。触发器覆盖 .py 之外载体（.so/.pyc/.pth）+ __pycache__ 存在 +
    构建 hook（setup.py/pyproject.toml）——防 manifest 谎报（review H7）。
    """
    try:
        for f in plugin_dir.rglob("*"):
            rel_parts = f.relative_to(plugin_dir).parts
            if "__pycache__" in rel_parts:
                return PluginCapability.CODE
            if not f.is_file():
                continue
            if f.suffix in _CODE_FILE_SUFFIXES or f.name in _CODE_FILE_NAMES:
                return PluginCapability.CODE
    except OSError:
        # 读目录失败：保守判 CODE（按高风险处理，宁可要求审批）
        return PluginCapability.CODE
    return PluginCapability.DECLARATIVE


def validate_provides(plugin_dir: Path, manifest: PluginManifest) -> None:
    """校验 manifest.provides 引用的制品存在 + behavior allowlist + 路径不逃逸。

    Raises:
        PluginValidationError: name 不匹配目录 / 制品缺失 / behavior 越界 / 路径逃逸。
    """
    if manifest.name != plugin_dir.name:
        raise PluginValidationError(
            PluginRejectedReason.NAME_MISMATCH,
            f"manifest name {manifest.name!r} != 目录名 {plugin_dir.name!r}",
        )

    resolved_root = plugin_dir.resolve()

    def _ensure_within(p: Path) -> Path:
        rp = p.resolve()
        if rp != resolved_root and resolved_root not in rp.parents:
            raise PluginValidationError(
                PluginRejectedReason.PATH_ESCAPE, f"路径逃逸 plugin 目录: {p}"
            )
        return rp

    for skill_name in manifest.provides.skills:
        skill_md = _ensure_within(plugin_dir / "skills" / skill_name / "SKILL.md")
        if not skill_md.is_file():
            raise PluginValidationError(
                PluginRejectedReason.MISSING_ARTIFACT, f"skill 缺 SKILL.md: {skill_name}"
            )

    for behavior_file in manifest.provides.behavior:
        if behavior_file not in PLUGIN_BEHAVIOR_ALLOWLIST:
            raise PluginValidationError(
                PluginRejectedReason.BEHAVIOR_NOT_ALLOWED,
                f"behavior {behavior_file!r} 不在 allowlist {sorted(PLUGIN_BEHAVIOR_ALLOWLIST)}",
            )
        bf = _ensure_within(plugin_dir / "behavior" / behavior_file)
        if not bf.is_file():
            raise PluginValidationError(
                PluginRejectedReason.MISSING_ARTIFACT, f"behavior 文件缺失: {behavior_file}"
            )
