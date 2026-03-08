"""Feature 025: SecretRef 解析与 keychain bridge。"""

from __future__ import annotations

import importlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from octoagent.core.models import SecretRefSourceType
from pydantic import SecretStr

from .secret_models import ResolvedSecretRef, SecretRef


class SecretResolutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_keyring_module() -> Any | None:
    try:
        return importlib.import_module("keyring")
    except Exception:
        return None


def is_keychain_available(keyring_module: Any | None = None) -> bool:
    module = keyring_module or load_keyring_module()
    if module is None:
        return False
    try:
        module.get_keyring()
    except Exception:
        return False
    return True


def store_keychain_secret(
    *,
    service: str,
    account: str,
    value: str,
    keyring_module: Any | None = None,
) -> None:
    module = keyring_module or load_keyring_module()
    if module is None:
        raise SecretResolutionError("SECRET_KEYCHAIN_UNAVAILABLE", "当前环境未安装 keyring。")
    try:
        module.set_password(service, account, value)
    except Exception as exc:
        raise SecretResolutionError(
            "SECRET_KEYCHAIN_WRITE_FAILED",
            f"写入 keychain 失败：{exc}",
        ) from exc


def remove_keychain_secret(
    *,
    service: str,
    account: str,
    keyring_module: Any | None = None,
) -> None:
    module = keyring_module or load_keyring_module()
    if module is None:
        return
    try:
        module.delete_password(service, account)
    except Exception:
        return


def inspect_secret_ref(ref: SecretRef, *, project_root: Path | None = None) -> list[str]:
    """返回不会泄露明文的静态风险提示。"""

    warnings: list[str] = []
    if ref.source_type != SecretRefSourceType.FILE:
        return warnings

    raw_path = str(ref.locator.get("path", "")).strip()
    if not raw_path:
        return warnings
    path = Path(raw_path)
    if not path.is_absolute() and project_root is not None:
        warnings.append(f"SecretRef(file) 使用相对路径：{raw_path}")
        path = (project_root / path).resolve()
    if not path.exists():
        return warnings
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        warnings.append(f"SecretRef(file) 权限过宽：{path}")
    return warnings


def resolve_secret_ref(
    ref: SecretRef,
    *,
    environ: dict[str, str] | None = None,
    cwd: Path | None = None,
    keyring_module: Any | None = None,
) -> ResolvedSecretRef:
    env = environ or os.environ
    summary = ""

    if ref.source_type == SecretRefSourceType.ENV:
        env_name = str(ref.locator.get("env_name", "")).strip()
        if not env_name:
            raise SecretResolutionError("SECRET_ENV_NAME_MISSING", "SecretRef(env) 缺少 env_name。")
        value = env.get(env_name, "")
        if not value:
            raise SecretResolutionError(
                "SECRET_ENV_NOT_FOUND",
                f"环境变量不存在或为空：{env_name}",
            )
        summary = f"env:{env_name}"
        return ResolvedSecretRef(ref=ref, value=SecretStr(value), resolution_summary=summary)

    if ref.source_type == SecretRefSourceType.FILE:
        raw_path = str(ref.locator.get("path", "")).strip()
        if not raw_path:
            raise SecretResolutionError("SECRET_FILE_PATH_MISSING", "SecretRef(file) 缺少 path。")
        path = Path(raw_path)
        if not path.is_absolute():
            base = cwd or Path.cwd()
            path = (base / path).resolve()
        if not path.exists():
            raise SecretResolutionError(
                "SECRET_FILE_NOT_FOUND",
                f"secret 文件不存在：{path}",
            )
        reader = str(ref.locator.get("reader", "text") or "text")
        if reader == "dotenv":
            key = str(ref.locator.get("key", "")).strip()
            if not key:
                raise SecretResolutionError(
                    "SECRET_DOTENV_KEY_MISSING",
                    "SecretRef(file:dotenv) 缺少 key。",
                )
            payload = dotenv_values(path)
            value = str(payload.get(key, "") or "").strip()
        else:
            value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise SecretResolutionError(
                "SECRET_FILE_EMPTY",
                f"secret 文件内容为空：{path}",
            )
        summary = f"file:{path.name}"
        return ResolvedSecretRef(ref=ref, value=SecretStr(value), resolution_summary=summary)

    if ref.source_type == SecretRefSourceType.EXEC:
        command = ref.locator.get("command")
        if not isinstance(command, list) or not command:
            raise SecretResolutionError(
                "SECRET_EXEC_COMMAND_INVALID",
                "SecretRef(exec) command 必须是非空数组。",
            )
        timeout_seconds = int(ref.locator.get("timeout_seconds", 10))
        result = subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd or Path.cwd()),
            env=env,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            raise SecretResolutionError(
                "SECRET_EXEC_FAILED",
                result.stderr.strip() or result.stdout.strip() or "exec 返回非零退出码。",
            )
        value = result.stdout.strip()
        if not value:
            raise SecretResolutionError("SECRET_EXEC_EMPTY", "exec 返回空 secret。")
        summary = f"exec:{' '.join(str(item) for item in command[:2])}"
        return ResolvedSecretRef(ref=ref, value=SecretStr(value), resolution_summary=summary)

    if ref.source_type == SecretRefSourceType.KEYCHAIN:
        service = str(ref.locator.get("service", "")).strip()
        account = str(ref.locator.get("account", "")).strip()
        if not service or not account:
            raise SecretResolutionError(
                "SECRET_KEYCHAIN_LOCATOR_INVALID",
                "SecretRef(keychain) 缺少 service/account。",
            )
        module = keyring_module or load_keyring_module()
        if module is None:
            raise SecretResolutionError(
                "SECRET_KEYCHAIN_UNAVAILABLE",
                "当前环境未安装 keyring。",
            )
        try:
            value = module.get_password(service, account)
        except Exception as exc:
            raise SecretResolutionError(
                "SECRET_KEYCHAIN_READ_FAILED",
                f"读取 keychain 失败：{exc}",
            ) from exc
        if not value:
            raise SecretResolutionError(
                "SECRET_KEYCHAIN_NOT_FOUND",
                f"keychain 中不存在 secret：service={service}, account={account}",
            )
        summary = f"keychain:{service}/{account}"
        return ResolvedSecretRef(ref=ref, value=SecretStr(value), resolution_summary=summary)

    raise SecretResolutionError(
        "SECRET_SOURCE_UNSUPPORTED",
        f"不支持的 SecretRef: {ref.source_type}",
    )
