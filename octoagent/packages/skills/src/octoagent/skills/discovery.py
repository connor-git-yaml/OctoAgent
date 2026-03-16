"""SkillDiscovery -- SKILL.md 文件系统扫描、解析、缓存服务。

参考 Agent Zero 的 helpers/skills.py 模式，适配 OctoAgent 的 Pydantic 模型。
负责从三级目录（内置 > 用户全局 > 项目级）扫描 SKILL.md 文件，
解析 YAML frontmatter + Markdown body，按优先级去重，构建内存缓存。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog
import yaml

from .skill_models import SkillListItem, SkillMdEntry, SkillSource

logger = structlog.get_logger(__name__)


# ============================================================
# SKILL.md 解析工具函数
# ============================================================


def split_frontmatter(raw: str) -> tuple[str, str]:
    """分离 YAML frontmatter 和 Markdown body。

    SKILL.md 格式要求以 '---' 开头，第二个 '---' 分隔 frontmatter 和 body。

    Args:
        raw: SKILL.md 文件完整内容

    Returns:
        (frontmatter_str, body_str) 元组。
        如果没有有效的 frontmatter 分隔符，frontmatter_str 为空。
    """
    stripped = raw.strip()
    if not stripped.startswith("---"):
        return "", stripped

    # 按行匹配第二个 '---' 分隔符，避免 frontmatter 值中包含 '---' 时误切割
    lines = stripped.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", stripped

    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            frontmatter_str = "\n".join(lines[1:i]).strip()
            body_str = "\n".join(lines[i + 1:]).strip()
            return frontmatter_str, body_str

    return "", stripped


def parse_frontmatter(frontmatter_str: str) -> dict[str, Any]:
    """解析 YAML frontmatter 为 dict。

    Args:
        frontmatter_str: 纯 YAML 字符串（不含 '---' 分隔符）

    Returns:
        解析后的 dict

    Raises:
        yaml.YAMLError: YAML 语法错误
        ValueError: 解析结果不是 dict
    """
    if not frontmatter_str.strip():
        return {}

    result = yaml.safe_load(frontmatter_str)
    if result is None:
        return {}
    if not isinstance(result, dict):
        msg = f"Frontmatter 解析结果不是 dict，而是 {type(result).__name__}"
        raise ValueError(msg)
    return result


def validate_skill(data: dict[str, Any]) -> tuple[bool, str]:
    """验证 SKILL.md frontmatter 必填字段。

    必填字段：name, description

    Args:
        data: 解析后的 frontmatter dict

    Returns:
        (is_valid, error_message) 元组
    """
    if not data.get("name"):
        return False, "缺少必填字段: name"
    if not data.get("description"):
        return False, "缺少必填字段: description"
    return True, ""


# ============================================================
# SkillDiscovery 核心服务
# ============================================================


class SkillDiscovery:
    """SKILL.md 文件系统扫描与缓存服务。

    三级目录优先级（从低到高）：
    - builtin_dir: 代码仓库 skills/ 目录（内置）
    - user_dir: ~/.octoagent/skills/ 目录（用户全局）
    - project_dir: {project_root}/skills/ 目录（项目级）

    同名 Skill 按优先级去重，高优先级覆盖低优先级。
    """

    def __init__(
        self,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        """初始化 SkillDiscovery。

        Args:
            builtin_dir: 内置 Skill 目录路径
            user_dir: 用户全局 Skill 目录路径
            project_dir: 项目级 Skill 目录路径
        """
        self._builtin_dir = builtin_dir
        self._user_dir = user_dir
        self._project_dir = project_dir
        # 内存缓存：name -> SkillMdEntry
        self._cache: dict[str, SkillMdEntry] = {}

    @property
    def builtin_dir(self) -> Path | None:
        return self._builtin_dir

    @property
    def user_dir(self) -> Path | None:
        return self._user_dir

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    def scan(self) -> list[SkillMdEntry]:
        """扫描三级目录，解析 SKILL.md，按优先级去重，构建缓存。

        扫描顺序为优先级从低到高：内置 -> 用户 -> 项目。
        后扫描的同名 Skill 覆盖先扫描的。

        Returns:
            所有已发现的 SkillMdEntry 列表
        """
        start = time.monotonic()

        # 构建新缓存后原子替换，避免并发请求看到空缓存或不完整缓存
        new_cache: dict[str, SkillMdEntry] = {}
        old_cache = self._cache

        # 按优先级从低到高扫描，后扫描的覆盖先扫描的
        scan_sources = [
            (self._builtin_dir, SkillSource.BUILTIN),
            (self._user_dir, SkillSource.USER),
            (self._project_dir, SkillSource.PROJECT),
        ]

        total_found = 0
        total_skipped = 0

        # 临时将 _cache 指向新缓存以便 _scan_directory 写入
        self._cache = new_cache
        for dir_path, source in scan_sources:
            if dir_path is None or not dir_path.is_dir():
                continue

            found, skipped = self._scan_directory(dir_path, source)
            total_found += found
            total_skipped += skipped

        # 原子替换完成（self._cache 已指向 new_cache）

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "skill_discovery_scan_complete",
            total_cached=len(self._cache),
            total_found=total_found,
            total_skipped=total_skipped,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return list(self._cache.values())

    def get(self, name: str) -> SkillMdEntry | None:
        """按名称从缓存获取 SkillMdEntry。

        Args:
            name: Skill 名称

        Returns:
            匹配的 SkillMdEntry，不存在则返回 None
        """
        return self._cache.get(name)

    def list_items(self) -> list[SkillListItem]:
        """返回所有缓存 Skill 的摘要投影列表。

        Returns:
            SkillListItem 列表，按名称排序
        """
        items = [entry.to_list_item() for entry in self._cache.values()]
        items.sort(key=lambda x: x.name)
        return items

    def refresh(self) -> list[SkillMdEntry]:
        """重新扫描所有目录，更新缓存。

        Returns:
            最新的 SkillMdEntry 列表
        """
        return self.scan()

    # ============================================================
    # 内部方法
    # ============================================================

    def _scan_directory(self, dir_path: Path, source: SkillSource) -> tuple[int, int]:
        """扫描单个目录下的所有 SKILL.md 文件。

        遍历 dir_path 下的一级子目录，查找 SKILL.md 文件。

        Args:
            dir_path: 目录路径
            source: Skill 来源分类

        Returns:
            (found_count, skipped_count) 元组
        """
        found = 0
        skipped = 0

        try:
            subdirs = sorted(dir_path.iterdir())
        except OSError as exc:
            logger.warning(
                "skill_discovery_dir_error",
                dir_path=str(dir_path),
                error=str(exc),
            )
            return 0, 0

        for subdir in subdirs:
            if not subdir.is_dir():
                continue

            skill_md = subdir / "SKILL.md"
            if not skill_md.is_file():
                continue

            entry = self._parse_skill_file(skill_md, source)
            if entry is None:
                skipped += 1
                continue

            # 检查是否覆盖已有同名 Skill
            existing = self._cache.get(entry.name)
            if existing is not None:
                logger.info(
                    "skill_discovery_override",
                    name=entry.name,
                    old_source=existing.source,
                    new_source=entry.source,
                    old_path=existing.source_path,
                    new_path=entry.source_path,
                )

            self._cache[entry.name] = entry
            found += 1

        return found, skipped

    def _parse_skill_file(
        self, file_path: Path, source: SkillSource
    ) -> SkillMdEntry | None:
        """解析单个 SKILL.md 文件。

        Args:
            file_path: SKILL.md 文件路径
            source: Skill 来源分类

        Returns:
            解析成功返回 SkillMdEntry，失败返回 None
        """
        # 读取文件内容
        try:
            raw = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning(
                "skill_discovery_encoding_error",
                file_path=str(file_path),
                error="非 UTF-8 编码，已跳过",
            )
            return None
        except OSError as exc:
            logger.warning(
                "skill_discovery_read_error",
                file_path=str(file_path),
                error=str(exc),
            )
            return None

        # 分离 frontmatter 和 body
        frontmatter_str, body = split_frontmatter(raw)
        if not frontmatter_str:
            logger.warning(
                "skill_discovery_no_frontmatter",
                file_path=str(file_path),
            )
            return None

        # 解析 YAML frontmatter
        try:
            data = parse_frontmatter(frontmatter_str)
        except (yaml.YAMLError, ValueError) as exc:
            logger.warning(
                "skill_discovery_yaml_error",
                file_path=str(file_path),
                error=str(exc),
            )
            return None

        # 验证必填字段
        is_valid, error_msg = validate_skill(data)
        if not is_valid:
            logger.warning(
                "skill_discovery_validation_error",
                file_path=str(file_path),
                error=error_msg,
            )
            return None

        # 提取字段，构建 SkillMdEntry
        name = str(data["name"]).strip()
        description = str(data["description"]).strip()

        # 可选字段安全提取
        tags = data.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip() for t in tags if str(t).strip()]

        trigger_patterns = data.get("trigger_patterns", [])
        if not isinstance(trigger_patterns, list):
            trigger_patterns = []
        trigger_patterns = [str(t).strip() for t in trigger_patterns if str(t).strip()]

        tools_required = data.get("tools_required", [])
        if not isinstance(tools_required, list):
            tools_required = []
        tools_required = [str(t).strip() for t in tools_required if str(t).strip()]

        try:
            entry = SkillMdEntry(
                name=name,
                description=description,
                version=str(data.get("version", "")).strip(),
                author=str(data.get("author", "")).strip(),
                tags=tags,
                trigger_patterns=trigger_patterns,
                tools_required=tools_required,
                source=source,
                source_path=str(file_path.resolve()),
                content=body,
                raw_frontmatter=data,
                metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
            )
        except Exception as exc:
            logger.warning(
                "skill_discovery_model_error",
                file_path=str(file_path),
                error=str(exc),
            )
            return None

        return entry
