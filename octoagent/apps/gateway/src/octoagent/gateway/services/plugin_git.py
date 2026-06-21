"""F106 Phase C: git plugin 安装/更新（硬化，spec DP-7 / review H8）。

**H8 硬化（缺失即 RCE）**：
- repo_url scheme allowlist（`https://` / `git@host:path`），**禁 `ext::`/`fd::`/`file://`**
  （`ext::` transport = clone 即执行任意 shell）+ 禁 `-` 前缀值；git 命令用 `--` 终止符。
- git 跑 `-c protocol.ext.allow=never -c core.hooksPath=/dev/null -c core.fsmonitor=false`
  （禁 ext transport + 禁仓库 hooks + 禁 fsmonitor）+ scrub env + `GIT_TERMINAL_PROMPT=0`（防 auth 挂起）。
- clone 进 temp → 校验（无 symlink-`.git`、symlink 不逃逸树）→ move 进 `plugins_dir/<name>`
  （containment + kebab name，**不** clone-over-existing）。
- provenance commit 从实际 `.git` 读（非 manifest 自报，review L1）。

git 不可用 / 网络失败 / 非法 repo → 抛 GitError（registry 映射 4xx 降级，FR-7.4），现有 plugin 不受影响。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_GIT_SAFE_ENV_KEYS = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR")
_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_HTTPS_RE = re.compile(r"^https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")
# git@host:owner/repo(.git)  —— scp-like SSH（不含空格 / 不以 - 开头）
_SSH_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[A-Za-z0-9._~/+-]+$")
_BANNED_PREFIXES = ("ext::", "fd::", "file://", "-")
_GIT_HARDEN_FLAGS = [
    "-c", "protocol.ext.allow=never",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
]


class GitError(Exception):
    """git 操作失败（非法 repo / 网络 / git 不可用 / 树不安全）。"""


@dataclass
class GitResult:
    name: str
    path: Path
    commit: str
    repo_url: str


def _git_safe_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _GIT_SAFE_ENV_KEYS if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"  # 禁交互（防 credential 提示挂起）
    env["GIT_ASKPASS"] = "true"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def validate_repo_url(repo_url: str) -> str:
    u = (repo_url or "").strip()
    if not u:
        raise GitError("repo_url 为空")
    for p in _BANNED_PREFIXES:
        if u.startswith(p):
            raise GitError(f"repo_url 禁用 scheme/前缀 {p!r}（ext::/fd::/file:// 是 RCE/逃逸面）")
    if not (_HTTPS_RE.match(u) or _SSH_RE.match(u)):
        raise GitError(f"repo_url 须为 https:// 或 git@host:path，得到 {u!r}")
    return u


def derive_plugin_name(repo_url: str) -> str:
    seg = repo_url.rstrip("/").split("/")[-1].split(":")[-1]
    name = seg[:-4] if seg.endswith(".git") else seg
    if not _KEBAB.match(name):
        raise GitError(f"从 repo 推导的 plugin 名非 kebab: {name!r}")
    return name


def _ensure_within(path: Path, base: Path) -> None:
    if not path.resolve().is_relative_to(base.resolve()):
        raise GitError(f"路径 {path} 逃逸 {base}")


def _check_tree_safe(tree: Path) -> None:
    """拒 symlink-.git + 拒逃逸树的 symlink（review H8）。"""
    git_path = tree / ".git"
    if git_path.is_symlink():
        raise GitError(".git 是 symlink（拒，防 hooks 注入）")
    root = tree.resolve()
    for p in tree.rglob("*"):
        if p.is_symlink():
            target = p.resolve()
            if target != root and root not in target.parents:
                raise GitError(f"symlink 逃逸 plugin 树: {p}")


async def _run_git(args: list[str], *, cwd: Path | None = None, timeout: float = 120) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd) if cwd is not None else None,
            env=_git_safe_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise GitError("git 不可用（未安装 git 二进制）") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        proc.kill()
        raise GitError(f"git 超时（{timeout}s）") from exc
    if proc.returncode != 0:
        raise GitError(f"git 失败: {err.decode(errors='replace')[:300]}")
    return out.decode(errors="replace")


async def git_install(repo_url: str, plugins_dir: Path, *, timeout: float = 120) -> GitResult:
    """clone repo 进 plugins_dir/<name>（硬化 + temp-then-move + 校验）。"""
    url = validate_repo_url(repo_url)
    name = derive_plugin_name(url)
    dest = plugins_dir / name
    _ensure_within(dest, plugins_dir)
    if dest.exists():
        raise GitError(f"plugin {name!r} 已存在（不 clone-over-existing）")
    plugins_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(plugins_dir)) as tmp:
        tmp_clone = Path(tmp) / name
        await _run_git(
            [*_GIT_HARDEN_FLAGS, "clone", "--depth", "1", "--", url, str(tmp_clone)],
            timeout=timeout,
        )
        _check_tree_safe(tmp_clone)
        commit = (await _run_git(["rev-parse", "HEAD"], cwd=tmp_clone, timeout=30)).strip()
        shutil.move(str(tmp_clone), str(dest))
    log.info("plugin_git_installed", name=name, commit=commit[:12])
    return GitResult(name=name, path=dest, commit=commit, repo_url=url)


async def git_update(plugin_dir: Path, *, timeout: float = 120) -> GitResult:
    """git pull --ff-only 更新已有 git plugin（硬化）。"""
    git_dir = plugin_dir / ".git"
    if git_dir.is_symlink():
        raise GitError(".git 是 symlink（拒）")
    if not git_dir.is_dir():
        raise GitError("非 git plugin（无 .git 目录）")
    await _run_git([*_GIT_HARDEN_FLAGS, "pull", "--ff-only"], cwd=plugin_dir, timeout=timeout)
    _check_tree_safe(plugin_dir)
    commit = (await _run_git(["rev-parse", "HEAD"], cwd=plugin_dir, timeout=30)).strip()
    try:
        url = (await _run_git(["config", "--get", "remote.origin.url"], cwd=plugin_dir, timeout=30)).strip()
    except GitError:
        url = ""
    return GitResult(name=plugin_dir.name, path=plugin_dir, commit=commit, repo_url=url)


def is_git_plugin(plugin_dir: Path) -> bool:
    """provenance：plugin 是否 git 来源（.git 目录存在，非 manifest 自报）。"""
    return (plugin_dir / ".git").is_dir()
