"""F107 文件工作台 v0.2 W2 -- workspace 真 git 浏览返回契约模型。

workspace（`projects/{slug}/` 工作树 − deny-list）的 git 历史视图。主响应平实（SD-8），
原始 git 术语（commit hash / ref）归 Advanced。所有内容由 WorkspaceGitStore 经 subprocess
git plumbing 派生（外部 bare store，用户目录无 `.git`）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceCommit(BaseModel):
    """一次 workspace 快照（提交）的平实视图。"""

    commit: str = Field(description="完整 commit hash（Advanced 区）")
    short: str = Field(description="短 hash（Advanced 区）")
    ts: str = Field(description="提交时间（ISO 8601）")
    summary: str = Field(description="平实说明（触发原因，如 'before filesystem.write_text'）")
    files_changed: int = Field(default=0, description="本次改动文件数")
    insertions: int = Field(default=0, description="新增行数")
    deletions: int = Field(default=0, description="删除行数")


class WorkspaceFileChange(BaseModel):
    """单提交内一个文件的改动状态。"""

    path: str = Field(description="工作树内相对路径")
    status: str = Field(description="改动类型：added/modified/deleted/renamed")


class WorkspaceBlameLine(BaseModel):
    """blame 逐行归属（'谁改了这一行'）。"""

    line_no: int = Field(description="行号（从 1 起）")
    content: str = Field(description="该行文本")
    commit: str = Field(description="最近改动此行的 commit（Advanced）")
    short: str = Field(description="短 hash")
    ts: str = Field(description="该 commit 时间")
    summary: str = Field(default="", description="该 commit 平实说明")
