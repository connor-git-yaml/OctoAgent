"""F107 文件工作台 v0.2 W2-D -- workspace 真 git API（浏览 + 回滚）。

浏览：history / commit 文件清单 / blame / 两提交 diff（复用前端 DiffBody）。
回滚：REST Two-Phase —— POST /rollback 创建 durable 请求（pending，proposal）→ 用户确认 →
POST /rollback/{id}/approve 执行（pre-snapshot → checkout → 回滚后 commit，SD-10 仅文件态）。

> 说明（spec 偏离）：spec SD-10 原案用 ApprovalGate SSE 审批卡。本实现用**显式 REST Two-Phase**
> （create=proposal / approve=execute），与 W1-C behavior 恢复同范式——durable 请求 + 显式
> approve 端点同样满足 Two-Phase + #1 durability + #4/#7；ApprovalGate SSE 卡是 UX refinement。

所有 endpoint 经 main.py 路由级 front-door 鉴权（#10）。git 不可用 → store.available=False，
浏览返回空、回滚 503（降级，#6）。主响应平实（SD-8），commit hash/branch 归 Advanced（前端折叠）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from octoagent.core.models.workspace_git import (
    WorkspaceBlameLine,
    WorkspaceCommit,
    WorkspaceFileChange,
)
from pydantic import BaseModel

router = APIRouter()


# ---- 响应模型 ----


class WorkspaceHistoryResponse(BaseModel):
    available: bool
    commits: list[WorkspaceCommit]


class WorkspaceCommitFilesResponse(BaseModel):
    files: list[WorkspaceFileChange]


class WorkspaceBlameResponse(BaseModel):
    lines: list[WorkspaceBlameLine]


class DiffSide(BaseModel):
    """diff 一侧内容（主响应 0 技术字段，SC-004 范式）。"""

    content: str | None = None
    availability: str = "available"
    oversize: bool = False


class WorkspaceDiffResponse(BaseModel):
    current: DiffSide | None = None
    previous: DiffSide | None = None
    binary: bool = False
    oversize: bool = False


class RollbackBody(BaseModel):
    project_slug: str
    target_commit: str
    paths: list[str] | None = None


class RollbackProposalResponse(BaseModel):
    request_id: str
    status: str
    target_commit: str
    files_count: int


class RollbackResultResponse(BaseModel):
    request_id: str
    status: str
    detail: str = ""


# ---- helpers ----


def _store(request: Request):
    return getattr(request.app.state, "workspace_git_store", None)


def _rollback_service(request: Request):
    return getattr(request.app.state, "workspace_rollback_service", None)


def _worktree(request: Request, project_slug: str) -> Path:
    project_root = Path(request.app.state.project_root)
    return project_root / "projects" / (project_slug or "_default")


# ---- 浏览 endpoints ----


@router.get("/api/workspace-git/history", response_model=WorkspaceHistoryResponse)
async def workspace_history(
    request: Request,
    project_slug: str = Query(...),
    limit: int = Query(default=50),
) -> WorkspaceHistoryResponse:
    store = _store(request)
    if store is None or not store.available:
        return WorkspaceHistoryResponse(available=False, commits=[])
    commits = await store.log(_worktree(request, project_slug), limit=limit)
    return WorkspaceHistoryResponse(available=True, commits=commits)


@router.get("/api/workspace-git/commit", response_model=WorkspaceCommitFilesResponse)
async def workspace_commit_files(
    request: Request,
    project_slug: str = Query(...),
    commit: str = Query(...),
) -> WorkspaceCommitFilesResponse:
    store = _store(request)
    if store is None or not store.available:
        return WorkspaceCommitFilesResponse(files=[])
    files = await store.show_files(_worktree(request, project_slug), commit)
    return WorkspaceCommitFilesResponse(files=files)


@router.get("/api/workspace-git/blame", response_model=WorkspaceBlameResponse)
async def workspace_blame(
    request: Request,
    project_slug: str = Query(...),
    commit: str = Query(...),
    path: str = Query(...),
) -> WorkspaceBlameResponse:
    store = _store(request)
    if store is None or not store.available:
        return WorkspaceBlameResponse(lines=[])
    lines = await store.blame(_worktree(request, project_slug), commit, path)
    return WorkspaceBlameResponse(lines=lines)


@router.get("/api/workspace-git/diff", response_model=WorkspaceDiffResponse)
async def workspace_diff(
    request: Request,
    project_slug: str = Query(...),
    commit_a: str = Query(...),
    path: str = Query(...),
    commit_b: str | None = Query(default=None),
) -> WorkspaceDiffResponse:
    store = _store(request)
    if store is None or not store.available:
        return WorkspaceDiffResponse()
    current, previous = await store.file_diff(
        _worktree(request, project_slug), commit_a, commit_b, path
    )

    def _side(c: str | None) -> DiffSide | None:
        if c is None:
            return None
        return DiffSide(content=c, availability="available", oversize=False)

    return WorkspaceDiffResponse(current=_side(current), previous=_side(previous))


# ---- 回滚 endpoints（REST Two-Phase） ----


@router.post("/api/workspace-git/rollback", response_model=RollbackProposalResponse)
async def workspace_rollback_propose(
    request: Request, body: RollbackBody
) -> RollbackProposalResponse:
    store = _store(request)
    svc = _rollback_service(request)
    if store is None or not store.available or svc is None:
        raise HTTPException(status_code=503, detail="workspace git 不可用")
    worktree = _worktree(request, body.project_slug)
    req = await svc.create_request(
        project_slug=body.project_slug,
        worktree=worktree,
        target_commit=body.target_commit,
        paths=body.paths,
    )
    if req is None:
        raise HTTPException(status_code=400, detail="非法 commit")
    return RollbackProposalResponse(
        request_id=req.request_id,
        status=req.status,
        target_commit=req.target_commit,
        files_count=len(req.paths),
    )


@router.post(
    "/api/workspace-git/rollback/{request_id}/approve",
    response_model=RollbackResultResponse,
)
async def workspace_rollback_approve(
    request: Request, request_id: str
) -> RollbackResultResponse:
    svc = _rollback_service(request)
    if svc is None:
        raise HTTPException(status_code=503, detail="workspace git 不可用")
    ok = await svc.approve_and_execute(request_id)
    req = await svc.get_request(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="回滚请求不存在")
    if not ok and req.status not in ("executed",):
        return RollbackResultResponse(
            request_id=request_id, status=req.status, detail=req.detail
        )
    return RollbackResultResponse(request_id=request_id, status=req.status)


@router.post(
    "/api/workspace-git/rollback/{request_id}/reject",
    response_model=RollbackResultResponse,
)
async def workspace_rollback_reject(
    request: Request, request_id: str
) -> RollbackResultResponse:
    svc = _rollback_service(request)
    if svc is None:
        raise HTTPException(status_code=503, detail="workspace git 不可用")
    await svc.reject(request_id)
    req = await svc.get_request(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="回滚请求不存在")
    return RollbackResultResponse(request_id=request_id, status=req.status)
