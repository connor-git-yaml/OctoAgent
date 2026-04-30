"""F087 P2 T-P2-9：real_codex_credential_store fixture（OAuth profile 隔离）。

只读复制宿主 ``~/.octoagent/auth-profiles.json`` → ``tmp/auth-profiles.json``，
chmod 0o600，构造一个全新的 ``CredentialStore`` 指向 tmp 副本。

设计要点：
1. **不动宿主原文件**（包括不动 mtime / atime）
2. tmp 副本权限严格 0o600（保护测试 quota）
3. 宿主缺 auth-profiles.json 时 ``pytest.skip(reason=...)`` 不 FAIL
4. **不重定向 HOME**（子进程 Codex CLI 仍读宿主 HOME 路径以获取 OAuth refresh
   token；这不是漏洞——CredentialStore 是 OctoAgent 进程内对 OAuth 的封装；子
   进程 OAuth 来自 ChatGPT Pro 订阅本身）

返回值：``CredentialStore`` 实例，可直接传给 ``OctoHarness(credential_store=...)``。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def real_codex_credential_store(tmp_path: Path) -> Any:
    """e2e fixture：tmp 副本 CredentialStore，宿主缺文件 → SKIP。

    Returns:
        ``octoagent.provider.auth.store.CredentialStore`` 实例
    """
    home = Path(os.environ.get("HOME", str(Path.home())))
    src = home / ".octoagent" / "auth-profiles.json"

    if not src.exists():
        pytest.skip(
            f"real_codex_credential_store: 宿主 {src} 不存在；"
            "F087 e2e 需要 ChatGPT Pro OAuth profile 才能跑真实 LLM。"
        )

    # tmp 副本 + 严格权限
    dst_dir = tmp_path / "octoagent_tmp_creds"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "auth-profiles.json"
    shutil.copy2(src, dst)
    os.chmod(dst, 0o600)

    # 延迟 import，避免 conftest 收集时 provider 包不可用
    from octoagent.provider.auth.store import CredentialStore

    return CredentialStore(store_path=dst)


__all__ = ["real_codex_credential_store"]
