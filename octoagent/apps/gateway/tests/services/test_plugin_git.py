"""F106 Phase C git_ops 测试：repo_url 硬化 + tree-safe + 真实 local-remote pull（H8）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from octoagent.gateway.services.plugin_git import (
    GitError,
    derive_plugin_name,
    git_install,
    git_update,
    is_git_plugin,
    validate_repo_url,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, check=True, capture_output=True, text=True
    )


# ---------------------------------------------------------------- repo_url 硬化（H8）


@pytest.mark.parametrize(
    "bad",
    ["ext::sh -c whoami", "file:///etc/passwd", "fd::1/foo", "--upload-pack=evil", "-x", "", "  "],
)
def test_validate_repo_url_rejects_dangerous(bad: str) -> None:
    with pytest.raises(GitError):
        validate_repo_url(bad)


@pytest.mark.parametrize(
    "ok", ["https://github.com/owner/repo.git", "https://gitlab.com/o/r", "git@github.com:owner/repo.git"]
)
def test_validate_repo_url_accepts_valid(ok: str) -> None:
    assert validate_repo_url(ok) == ok


@pytest.mark.parametrize(
    "url,name",
    [
        ("https://github.com/owner/weather-helper.git", "weather-helper"),
        ("git@github.com:o/my-plugin.git", "my-plugin"),
        ("https://x.com/a/plug/", "plug"),
    ],
)
def test_derive_plugin_name(url: str, name: str) -> None:
    assert derive_plugin_name(url) == name


def test_derive_plugin_name_non_kebab_rejected() -> None:
    with pytest.raises(GitError):
        derive_plugin_name("https://x.com/o/Bad_Name.git")


# ---------------------------------------------------------------- install 非网络路径


async def test_git_install_rejects_banned_url(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        await git_install("file:///tmp/repo", tmp_path / "plugins")


async def test_git_install_clone_over_existing_rejected(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    (plugins / "weather-helper").mkdir(parents=True)  # 已存在
    with pytest.raises(GitError, match="已存在"):
        await git_install("https://github.com/o/weather-helper.git", plugins)


# ---------------------------------------------------------------- update 真实（local remote，无网络）


async def test_git_update_real_local_remote(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(["init", "--bare", "-b", "main", str(remote)])
    work = tmp_path / "work"
    _git(["clone", str(remote), str(work)])
    (work / "plugin.yaml").write_text("name: gp\nprovides:\n  skills: []\n")
    _git(["add", "-A"], cwd=work)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"], cwd=work)
    _git(["push", "origin", "main"], cwd=work)

    plugin_dir = tmp_path / "plugins" / "gp"
    plugin_dir.parent.mkdir(parents=True)
    _git(["clone", str(remote), str(plugin_dir)])
    assert is_git_plugin(plugin_dir)
    c1 = _git(["rev-parse", "HEAD"], cwd=plugin_dir).stdout.strip()

    # 远端新 commit
    (work / "extra.txt").write_text("2")
    _git(["add", "-A"], cwd=work)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "v2"], cwd=work)
    _git(["push", "origin", "main"], cwd=work)

    result = await git_update(plugin_dir)
    assert result.commit and result.commit != c1  # 拉到新 commit
    assert (plugin_dir / "extra.txt").exists()


async def test_git_update_non_git_rejected(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "notgit"
    plugin_dir.mkdir()
    with pytest.raises(GitError, match="非 git"):
        await git_update(plugin_dir)


async def test_git_update_symlink_dotgit_rejected(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "evil"
    plugin_dir.mkdir()
    real_git = tmp_path / "real_git"
    real_git.mkdir()
    (plugin_dir / ".git").symlink_to(real_git, target_is_directory=True)
    with pytest.raises(GitError, match="symlink"):
        await git_update(plugin_dir)
