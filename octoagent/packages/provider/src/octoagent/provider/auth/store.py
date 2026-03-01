"""Credential Store -- 对齐 contracts/auth-adapter-api.md SS2, FR-006

基于 JSON 文件的凭证持久化存储。
- 文件位置: ~/.octoagent/auth-profiles.json
- 文件权限: 0o600（仅当前用户可读写）
- 并发安全: filelock
- 原子写入: 先写临时文件再 rename
- 文件损坏恢复: 备份原文件并返回空 store
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import structlog
from filelock import FileLock
from pydantic import SecretStr

from ..exceptions import CredentialError
from .profile import CredentialStoreData, ProviderProfile

log = structlog.get_logger()

# 默认存储路径
_DEFAULT_STORE_DIR = Path.home() / ".octoagent"
_DEFAULT_STORE_FILE = "auth-profiles.json"
_LOCK_SUFFIX = ".lock"
_FILE_PERMISSION = 0o600


def _secret_json_encoder(obj: object) -> str:
    """JSON 编码器：将 SecretStr 和 datetime 转为可序列化类型

    model_dump(mode='python') 保留 SecretStr/datetime 原始对象，
    此编码器负责在 json.dumps 时正确转换。
    """
    if isinstance(obj, SecretStr):
        return obj.get_secret_value()
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class CredentialStore:
    """凭证存储管理器

    文件位置: ~/.octoagent/auth-profiles.json
    文件权限: 0o600（仅当前用户可读写）
    并发安全: filelock
    """

    def __init__(self, store_path: Path | None = None) -> None:
        """初始化

        Args:
            store_path: 存储文件路径，None 时使用默认路径
        """
        if store_path is None:
            self._path = _DEFAULT_STORE_DIR / _DEFAULT_STORE_FILE
        else:
            self._path = store_path
        self._lock_path = self._path.with_suffix(
            self._path.suffix + _LOCK_SUFFIX,
        )
        self._lock = FileLock(str(self._lock_path))

    @property
    def path(self) -> Path:
        """存储文件路径"""
        return self._path

    def load(self) -> CredentialStoreData:
        """加载 credential store

        Returns:
            CredentialStoreData 实例

        行为:
        - 文件不存在: 返回空 store
        - 文件损坏: 备份原文件，记录警告，返回空 store
        """
        if not self._path.exists():
            return CredentialStoreData()

        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                return CredentialStoreData.model_validate(data)
            except (json.JSONDecodeError, ValueError) as exc:
                # 文件损坏：备份并返回空 store (EC-2)
                backup_path = self._path.with_suffix(".json.corrupted")
                shutil.copy2(self._path, backup_path)
                log.warning(
                    "credential_store_corrupted",
                    path=str(self._path),
                    backup=str(backup_path),
                    error=str(exc),
                )
                return CredentialStoreData()
            except Exception as exc:
                raise CredentialError(
                    f"凭证存储加载失败: {exc}",
                ) from exc

    def save(self, data: CredentialStoreData) -> None:
        """持久化 credential store

        使用 filelock 保证并发安全。
        写入时先写临时文件再 rename（原子性）。
        自动设置文件权限为 0o600。
        """
        # 确保父目录存在
        self._path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            # 序列化（需要暴露 SecretStr 值以便持久化）
            # model_dump_json() 会将 SecretStr 掩码为 '**********'，
            # 因此使用 model_dump(mode='python') + json.dumps 自定义编码器
            json_str = json.dumps(
                data.model_dump(mode="python"),
                default=_secret_json_encoder,
                indent=2,
                ensure_ascii=False,
            )

            # 原子写入：先写临时文件再 rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent),
                suffix=".tmp",
            )
            try:
                os.write(fd, json_str.encode("utf-8"))
                os.close(fd)
                # 设置文件权限
                os.chmod(tmp_path, _FILE_PERMISSION)
                # 原子 rename
                os.replace(tmp_path, str(self._path))
            except Exception:
                # 清理临时文件
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise

    def get_profile(self, name: str) -> ProviderProfile | None:
        """按名称获取 profile"""
        store_data = self.load()
        return store_data.profiles.get(name)

    def set_profile(self, profile: ProviderProfile) -> None:
        """创建或更新 profile"""
        store_data = self.load()
        store_data.profiles[profile.name] = profile
        self.save(store_data)

    def remove_profile(self, name: str) -> bool:
        """删除 profile，返回是否成功"""
        store_data = self.load()
        if name not in store_data.profiles:
            return False
        del store_data.profiles[name]
        self.save(store_data)
        return True

    def get_default_profile(self) -> ProviderProfile | None:
        """获取默认 profile"""
        store_data = self.load()
        for profile in store_data.profiles.values():
            if profile.is_default:
                return profile
        return None

    def list_profiles(self) -> list[ProviderProfile]:
        """列出所有 profile"""
        store_data = self.load()
        return list(store_data.profiles.values())
