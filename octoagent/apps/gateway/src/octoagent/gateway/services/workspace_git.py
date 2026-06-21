"""F107 文件工作台 v0.2 W2 -- workspace 真 git store（subprocess plumbing，外部 bare store）。

Hermes 蓝本：共享 bare store + 每 workspace 独立 `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`
重定向（**仅 per-subprocess env，绝不写 os.environ**，Codex MED-D）→ 用户 `projects/{slug}/`
目录无 `.git`。plumbing-only（add → write-tree → commit-tree → update-ref），绕 HEAD/用户分支。

- **降级（#6 构造性，SD-5）**：`shutil.which("git")` 探测；不可用 → available=False，
  方法返回空/None，快照静默跳过，绝不抛、绝不阻塞主流程。
- **deny-list（#5 / Codex HIGH-B，SD-3）**：从 path_policy `_BLACKLIST_*` 同源 + 结构性 behavior/
  artifacts/secret-bindings 写进 store `info/exclude`，`git add -A` 尊重 → secrets 永不进 index。
- **并发安全（Codex MED-E）**：per-workspace async 锁 + `update-ref <new> <old>` CAS + 冲突重试。
- **注入防御（FR-W2-7 / Codex LOW-F）**：commit hash hex 校验；path `.relative_to(worktree)`。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
from pathlib import Path

import structlog
from octoagent.core.models.workspace_git import (
    WorkspaceBlameLine,
    WorkspaceCommit,
    WorkspaceFileChange,
)

log = structlog.get_logger("workspace.git")

# 大文件踢出 index 阈值（Hermes 默认 10MB），避免把媒体/二进制塞进 git
_OVERSIZE_BYTES = 10 * 1024 * 1024

# deny-list：path_policy secrets/config 同源（Codex HIGH-B）+ 结构性另管兄弟（SD-3）
# 写进 store info/exclude（gitignore 语法），git add -A 自动排除。
_DENY_EXCLUDES = (
    # 结构性：另管/敏感兄弟
    "/behavior/",
    "/artifacts/",
    "project.secret-bindings.json",
    # path_policy _BLACKLIST_FILES（#5，单一事实源对齐）
    "octoagent.yaml",
    "litellm-config.yaml",
    "auth-profiles.json",
    # path_policy _BLACKLIST_FILE_PREFIXES
    ".env",
    ".env*",
    # infra（Hermes 式）
    ".venv/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
)

# commit hash：4-64 hex，且不以 '-' 开头（防被当 git flag，Hermes 注入防御）
_HASH_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")

_GIT_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "OctoAgent",
    "GIT_AUTHOR_EMAIL": "octo@localhost",
    "GIT_COMMITTER_NAME": "OctoAgent",
    "GIT_COMMITTER_EMAIL": "octo@localhost",
}


def is_valid_commit_hash(value: str) -> bool:
    """commit hash 注入防御：必须是 hex、不以 '-' 开头。"""
    return bool(value) and not value.startswith("-") and bool(_HASH_RE.match(value))


class WorkspaceGitStore:
    """workspace 真 git store（subprocess plumbing，外部 bare store + 每 workspace 重定向）。"""

    def __init__(self, store_dir: Path, *, git_path: str | None = None) -> None:
        self._store_dir = Path(store_dir)
        self._git = git_path or shutil.which("git")
        self._available = self._git is not None
        self._initialized = False
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def available(self) -> bool:
        """git 二进制可用（#6 降级判据）。"""
        return self._available

    # ---- 内部：路径/env/subprocess ----

    def _hash16(self, worktree: Path) -> str:
        return hashlib.sha256(str(worktree.resolve()).encode("utf-8")).hexdigest()[:16]

    def _ref(self, h: str) -> str:
        return f"refs/octo/{h}"

    def _index_path(self, h: str) -> Path:
        return self._store_dir / "indexes" / h

    def _env(self, worktree: Path, h: str) -> dict[str, str]:
        """per-subprocess env：重定向 git 到外部 store + 独立 index（绝不写 os.environ）。"""
        return {
            **os.environ,
            "GIT_DIR": str(self._store_dir),
            "GIT_WORK_TREE": str(worktree.resolve()),
            "GIT_INDEX_FILE": str(self._index_path(h)),
            **_GIT_AUTHOR_ENV,
        }

    def _lock_for(self, h: str) -> asyncio.Lock:
        lock = self._locks.get(h)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[h] = lock
        return lock

    async def _git_run(
        self, env: dict[str, str], *args: str, cwd: Path | None = None
    ) -> tuple[int, str, str]:
        """async subprocess git；返回 (rc, stdout, stderr)。git 缺失则 rc=-1。"""
        if not self._available or self._git is None:
            return (-1, "", "git unavailable")
        proc = await asyncio.create_subprocess_exec(
            self._git,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(cwd) if cwd else None,
        )
        out, err = await proc.communicate()
        return (
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    async def _ensure_store(self) -> None:
        """首次：git init --bare <store> + 写 info/exclude deny-list（幂等）。"""
        if self._initialized or not self._available:
            return
        self._store_dir.mkdir(parents=True, exist_ok=True)
        (self._store_dir / "indexes").mkdir(parents=True, exist_ok=True)
        if not (self._store_dir / "HEAD").exists():
            await self._git_run({**os.environ}, "init", "--bare", str(self._store_dir))
        # deny-list → info/exclude（gitignore 语法）；幂等覆写保证 deny-list 演进生效
        info_dir = self._store_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        (info_dir / "exclude").write_text(
            "\n".join(_DENY_EXCLUDES) + "\n", encoding="utf-8"
        )
        self._initialized = True

    async def _ref_tip(self, env: dict[str, str], h: str) -> str | None:
        rc, out, _ = await self._git_run(env, "rev-parse", "--verify", "-q", self._ref(h))
        tip = out.strip()
        return tip if rc == 0 and tip else None

    async def _commit_in_workspace(
        self, env: dict[str, str], h: str, commit: str
    ) -> bool:
        """commit 必须可从本 workspace 的 ref 到达（Codex W2-HIGH-1：防跨 workspace commit
        hash 泄露内容 / 误 checkout）。merge-base --is-ancestor <commit> <ref> rc=0 即可达。"""
        rc, _, _ = await self._git_run(
            env, "merge-base", "--is-ancestor", commit, self._ref(h)
        )
        return rc == 0

    async def _drop_oversize(self, env: dict[str, str], worktree: Path) -> None:
        """add 后把 > 阈值的大文件从 index 踢出（Hermes 式，避免 git 吞媒体/二进制）。"""
        rc, out, _ = await self._git_run(env, "ls-files", "--cached", "-z")
        if rc != 0 or not out:
            return
        for rel in out.split("\0"):
            if not rel:
                continue
            try:
                size = (worktree / rel).stat().st_size
            except OSError:
                continue
            if size > _OVERSIZE_BYTES:
                await self._git_run(env, "rm", "--cached", "--quiet", "--", rel)

    # ---- 快照（per-loop_step 触发，W2-B 调用） ----

    async def snapshot(self, worktree: Path, reason: str) -> str | None:
        """对 worktree 拍一次 git 快照；无变更则不产 commit（返回 None）。失败不抛（best-effort）。

        plumbing：add -A（尊重 deny-list）→ drop oversize → write-tree → 比对 parent 跳过无变更
        → commit-tree → update-ref CAS（per-workspace 锁 + 冲突重试）。
        """
        if not self._available:
            return None
        try:
            await self._ensure_store()
            worktree = Path(worktree)
            if not worktree.exists():
                return None
            h = self._hash16(worktree)
            env = self._env(worktree, h)
            async with self._lock_for(h):
                return await self._snapshot_locked(env, worktree, h, reason)
        except Exception as exc:  # best-effort：快照失败绝不阻断主流程
            log.warning("workspace_git_snapshot_failed", reason=reason, error=str(exc))
            return None

    async def _snapshot_locked(
        self, env: dict[str, str], worktree: Path, h: str, reason: str
    ) -> str | None:
        await self._git_run(env, "add", "-A")
        await self._drop_oversize(env, worktree)
        rc, tree, _ = await self._git_run(env, "write-tree")
        tree = tree.strip()
        if rc != 0 or not tree:
            return None
        for _ in range(3):  # CAS 冲突重试
            parent = await self._ref_tip(env, h)
            if parent is not None:
                # 与 parent tree 一致 → 无变更，跳过（git diff-index --cached --quiet）
                rc_d, _, _ = await self._git_run(
                    env, "diff-index", "--cached", "--quiet", parent
                )
                if rc_d == 0:
                    return None
            args = ["commit-tree", tree, "-m", reason]
            if parent:
                args += ["-p", parent]
            rc_c, commit, _ = await self._git_run(env, *args)
            commit = commit.strip()
            if rc_c != 0 or not commit:
                return None
            if parent:
                rc_u, _, _ = await self._git_run(
                    env, "update-ref", self._ref(h), commit, parent
                )
            else:
                rc_u, _, _ = await self._git_run(env, "update-ref", self._ref(h), commit)
            if rc_u == 0:
                return commit
            # CAS 失败（并发改了 ref）→ 重读 parent 重试
        return None

    # ---- 浏览 ----

    async def log(self, worktree: Path, *, limit: int = 50) -> list[WorkspaceCommit]:
        """提交历史（倒序）。无 git / 无 ref → 空列表。"""
        if not self._available:
            return []
        worktree = Path(worktree)
        h = self._hash16(worktree)
        env = self._env(worktree, h)
        if await self._ref_tip(env, h) is None:
            return []
        # 用 NUL 分隔的可解析格式
        fmt = "%H%x1f%h%x1f%cI%x1f%s%x1e"
        rc, out, _ = await self._git_run(
            env, "log", f"--max-count={int(limit)}", f"--format={fmt}", self._ref(h)
        )
        if rc != 0:
            return []
        commits: list[WorkspaceCommit] = []
        for rec in out.split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            parts = rec.split("\x1f")
            if len(parts) < 4:
                continue
            commits.append(
                WorkspaceCommit(
                    commit=parts[0], short=parts[1], ts=parts[2], summary=parts[3]
                )
            )
        return commits

    async def list_tracked(self, worktree: Path) -> list[str]:
        """当前快照 ref 下被 git 跟踪的文件清单（deny-list 校验 / SC-10 用）。"""
        if not self._available:
            return []
        worktree = Path(worktree)
        h = self._hash16(worktree)
        env = self._env(worktree, h)
        tip = await self._ref_tip(env, h)
        if tip is None:
            return []
        rc, out, _ = await self._git_run(env, "ls-tree", "-r", "--name-only", tip)
        if rc != 0:
            return []
        return [line for line in out.split("\n") if line]

    async def show_files(
        self, worktree: Path, commit: str
    ) -> list[WorkspaceFileChange]:
        """单提交涉及的文件清单 + 状态。"""
        if not self._available or not is_valid_commit_hash(commit):
            return []
        worktree = Path(worktree)
        h = self._hash16(worktree)
        env = self._env(worktree, h)
        if not await self._commit_in_workspace(env, h, commit):
            return []
        rc, out, _ = await self._git_run(
            env, "show", "--name-status", "--format=", "-z", commit
        )
        if rc != 0:
            return []
        changes: list[WorkspaceFileChange] = []
        tokens = [t for t in out.split("\0") if t]
        i = 0
        status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
        while i < len(tokens):
            code = tokens[i][:1]
            if code in ("A", "M", "D"):
                if i + 1 < len(tokens):
                    changes.append(
                        WorkspaceFileChange(
                            path=tokens[i + 1], status=status_map.get(code, "modified")
                        )
                    )
                i += 2
            elif code == "R":
                # rename: R<score>\0<old>\0<new>
                if i + 2 < len(tokens):
                    changes.append(
                        WorkspaceFileChange(path=tokens[i + 2], status="renamed")
                    )
                i += 3
            else:
                i += 1
        return changes

    async def blame(
        self, worktree: Path, commit: str, file_path: str
    ) -> list[WorkspaceBlameLine]:
        """逐行 blame（'谁改了这一行'）。注入防御：hash + path 越界。"""
        if not self._available or not is_valid_commit_hash(commit):
            return []
        worktree = Path(worktree)
        rel = self._safe_rel(worktree, file_path)
        if rel is None:
            return []
        h = self._hash16(worktree)
        env = self._env(worktree, h)
        if not await self._commit_in_workspace(env, h, commit):
            return []
        rc, out, _ = await self._git_run(
            env, "blame", "--line-porcelain", commit, "--", rel
        )
        if rc != 0:
            return []
        return self._parse_blame_porcelain(out)

    @staticmethod
    def _parse_blame_porcelain(out: str) -> list[WorkspaceBlameLine]:
        lines: list[WorkspaceBlameLine] = []
        cur: dict[str, str] = {}
        line_no = 0
        for raw in out.split("\n"):
            if not raw:
                continue
            if raw.startswith("\t"):
                line_no += 1
                lines.append(
                    WorkspaceBlameLine(
                        line_no=line_no,
                        content=raw[1:],
                        commit=cur.get("commit", ""),
                        short=cur.get("commit", "")[:7],
                        ts=cur.get("ts", ""),
                        summary=cur.get("summary", ""),
                    )
                )
                cur = {}
            elif raw.startswith("summary "):
                cur["summary"] = raw[len("summary ") :]
            elif raw.startswith("committer-time "):
                cur["ts"] = raw[len("committer-time ") :]
            elif _HASH_RE.match(raw.split(" ", 1)[0] or ""):
                cur["commit"] = raw.split(" ", 1)[0]
        return lines

    async def file_diff(
        self, worktree: Path, commit_a: str, commit_b: str | None, file_path: str
    ) -> tuple[str | None, str | None]:
        """两提交某文件内容（current=commit_a, previous=commit_b）；供 DiffBody 渲染。

        commit_b=None → previous=None（首版）。文件在某提交不存在 → 该侧 None。
        """
        if not self._available or not is_valid_commit_hash(commit_a):
            return (None, None)
        worktree = Path(worktree)
        rel = self._safe_rel(worktree, file_path)
        if rel is None:
            return (None, None)
        h = self._hash16(worktree)
        env = self._env(worktree, h)
        if not await self._commit_in_workspace(env, h, commit_a):
            return (None, None)
        current = await self._file_at(env, commit_a, rel)
        previous = None
        if (
            commit_b is not None
            and is_valid_commit_hash(commit_b)
            and await self._commit_in_workspace(env, h, commit_b)
        ):
            previous = await self._file_at(env, commit_b, rel)
        return (current, previous)

    async def _file_at(
        self, env: dict[str, str], commit: str, rel: str
    ) -> str | None:
        rc, out, _ = await self._git_run(env, "show", f"{commit}:{rel}")
        return out if rc == 0 else None

    # ---- 回滚执行（W2-C 调用） ----

    async def checkout_paths(
        self, worktree: Path, commit: str, paths: list[str] | None = None
    ) -> bool:
        """把 worktree 文件恢复到某提交（git checkout <commit> -- <paths>）。注入防御。

        paths=None → 整 worktree（`-- .`）。返回是否成功。不在此做审批（调用方 W2-C 保证）。
        """
        if not self._available or not is_valid_commit_hash(commit):
            return False
        worktree = Path(worktree)
        h = self._hash16(worktree)
        env = self._env(worktree, h)
        if not await self._commit_in_workspace(env, h, commit):
            return False
        rels: list[str] = []
        if paths:
            for p in paths:
                rel = self._safe_rel(worktree, p)
                if rel is None:
                    return False  # 越界 path → 拒绝整次回滚
                rels.append(rel)
        else:
            rels = ["."]
        async with self._lock_for(h):
            rc, _, err = await self._git_run(
                env, "checkout", commit, "--", *rels
            )
            if rc != 0:
                log.warning("workspace_git_checkout_failed", commit=commit, error=err)
                return False
            return True

    # ---- 注入防御 helper ----

    @staticmethod
    def _safe_rel(worktree: Path, file_path: str) -> str | None:
        """path 越界防御（FR-W2-7 / Codex LOW-F）：必须落在 worktree 内，返回相对路径。"""
        try:
            candidate = (worktree / file_path).resolve()
            rel = candidate.relative_to(worktree.resolve())
            return str(rel)
        except (ValueError, OSError):
            return None
