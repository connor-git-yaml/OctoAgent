"""Feature 067: BaseFilesystemRegistry — 三级目录扫描泛型基类。

将 PipelineRegistry 和 SkillDiscovery 共享的目录扫描、优先级覆盖、
缓存管理逻辑抽取为泛型基类。子类只需实现：
  - _marker_filename: 定义文件名（如 "PIPELINE.md" 或 "SKILL.md"）
  - _parse_file(): 将单个文件解析为 T 或 None
  - _entry_key(): 从 T 中提取唯一键（用于缓存去重）
  - _log_prefix: structlog 事件名前缀
"""

from __future__ import annotations

import abc
import time
from enum import StrEnum
from pathlib import Path
from typing import Generic, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class AssetSource(StrEnum):
    """三级目录来源枚举（通用）。优先级：PROJECT > USER > BUILTIN。"""

    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"


class BaseFilesystemRegistry(abc.ABC, Generic[T]):
    """三级目录文件系统扫描泛型基类。

    扫描顺序按优先级从低到高：builtin -> user -> project。
    同名条目后扫描覆盖先扫描。

    子类需实现：
    - _marker_filename: 要扫描的文件名
    - _parse_file(file_path, source): 解析单个文件
    - _entry_key(entry): 从解析结果提取缓存键
    - _log_prefix: structlog 事件名前缀
    """

    def __init__(
        self,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._builtin_dir = builtin_dir
        self._user_dir = user_dir
        self._project_dir = project_dir
        self._cache: dict[str, T] = {}

    @property
    def builtin_dir(self) -> Path | None:
        return self._builtin_dir

    @property
    def user_dir(self) -> Path | None:
        return self._user_dir

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    # ------------------------------------------------------------------
    # 子类必须实现的抽象属性/方法
    # ------------------------------------------------------------------

    @property
    @abc.abstractmethod
    def _marker_filename(self) -> str:
        """定义文件名，如 "PIPELINE.md" 或 "SKILL.md"。"""
        ...

    @abc.abstractmethod
    def _parse_file(self, file_path: Path, source: AssetSource) -> T | None:
        """解析单个定义文件。成功返回 T，失败返回 None（子类自行 log）。"""
        ...

    @abc.abstractmethod
    def _entry_key(self, entry: T) -> str:
        """从解析结果提取缓存键（如 pipeline_id 或 skill name）。"""
        ...

    @property
    @abc.abstractmethod
    def _log_prefix(self) -> str:
        """structlog 事件名前缀，如 "pipeline_registry" 或 "skill_discovery"。"""
        ...

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def scan(self) -> list[T]:
        """扫描三级目录，按优先级去重，构建缓存。

        Returns:
            所有已发现条目的列表。
        """
        start = time.monotonic()

        new_cache: dict[str, T] = {}
        self._cache = new_cache

        scan_sources = [
            (self._builtin_dir, AssetSource.BUILTIN),
            (self._user_dir, AssetSource.USER),
            (self._project_dir, AssetSource.PROJECT),
        ]

        total_found = 0
        total_skipped = 0

        for dir_path, source in scan_sources:
            if dir_path is None or not dir_path.is_dir():
                continue
            found, skipped = self._scan_directory(dir_path, source)
            total_found += found
            total_skipped += skipped

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            f"{self._log_prefix}_scan_complete",
            total_cached=len(self._cache),
            total_found=total_found,
            total_skipped=total_skipped,
            elapsed_ms=round(elapsed_ms, 2),
        )

        return list(self._cache.values())

    def get(self, key: str) -> T | None:
        """按键从缓存获取。"""
        return self._cache.get(key)

    def refresh(self) -> list[T]:
        """重新扫描所有目录，更新缓存。"""
        return self.scan()

    def all_items(self) -> list[T]:
        """返回缓存中所有条目。"""
        return list(self._cache.values())

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _scan_directory(
        self,
        dir_path: Path,
        source: AssetSource,
    ) -> tuple[int, int]:
        """扫描单个目录下所有子目录中的定义文件。"""
        found = 0
        skipped = 0

        try:
            subdirs = sorted(dir_path.iterdir())
        except OSError as exc:
            logger.warning(
                f"{self._log_prefix}_dir_error",
                dir_path=str(dir_path),
                error=str(exc),
            )
            return 0, 0

        for subdir in subdirs:
            if not subdir.is_dir():
                continue

            marker_file = subdir / self._marker_filename
            if not marker_file.is_file():
                continue

            entry = self._parse_file(marker_file, source)
            if entry is None:
                skipped += 1
                continue

            key = self._entry_key(entry)
            existing = self._cache.get(key)
            if existing is not None:
                logger.info(
                    f"{self._log_prefix}_override",
                    key=key,
                    new_source=str(source),
                )

            self._cache[key] = entry
            found += 1

        return found, skipped
