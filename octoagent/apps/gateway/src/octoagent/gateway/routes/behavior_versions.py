"""F107 文件工作台 v0.2 W1-D -- behavior 文件版本历史 API（读侧）。

服务 Agent 中心的 behavior 文件版本时间线 + 任意两版 diff。所有 endpoint 经 main.py
路由级 front-door 鉴权（require_front_door_access，Constitution #10），handler 内不做权限拦截。

主响应（/diff）**不含** version_no/hash/size 技术字段（SC-004 范式，技术字段归 /versions
Advanced endpoint）。behavior 恒小 md inline → availability 恒 available、oversize 恒 False。

恢复（restore）走 control_plane action ``behavior.restore_version``（W1-C），不在本 REST 路由
（写操作经 ControlPlaneService Two-Phase + REVIEW_REQUIRED，SD-6），故本文件仅读侧。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from octoagent.core.behavior_workspace import behavior_version_key_for
from octoagent.core.models.behavior_version import BehaviorFileKey
from octoagent.core.store import StoreGroup
from pydantic import BaseModel

from ..deps import get_store_group

router = APIRouter()


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------


class BehaviorFileItem(BaseModel):
    """有版本历史的 behavior 文件条目（Agent 中心入口）。"""

    scope: str
    agent_slug: str = ""
    project_slug: str = ""
    file_id: str
    version_count: int


class BehaviorFilesResponse(BaseModel):
    files: list[BehaviorFileItem]


class BehaviorVersionMetaItem(BaseModel):
    """版本元信息（Advanced 区，技术字段集中于此）。"""

    version_no: int
    ts: str
    size: int
    hash: str


class BehaviorVersionsResponse(BaseModel):
    versions: list[BehaviorVersionMetaItem]


class DiffSide(BaseModel):
    """diff 一侧内容（主响应 0 技术字段，SC-004）。"""

    content: str | None = None
    availability: str
    oversize: bool = False


class BehaviorDiffResponse(BaseModel):
    """两版 diff 主响应：版本 A（较新）+ 版本 B（较旧）内容。"""

    current: DiffSide | None = None
    previous: DiffSide | None = None
    binary: bool = False
    oversize: bool = False


def _key(scope: str, agent_slug: str, project_slug: str, file_id: str) -> BehaviorFileKey:
    """构造 query 参数到 BehaviorFileKey；file_id 已知时优先按 file_id 路由归零无关字段。"""
    try:
        # 与写入端同 scope 路由 + 归零（保证读写 key 一致，MED-4）
        return behavior_version_key_for(
            file_id, agent_slug=agent_slug, project_slug=project_slug
        )
    except ValueError:
        # file_id 不在已知列表（防御）：按显式 query 参数构造
        return BehaviorFileKey(
            scope=scope,
            agent_slug=agent_slug,
            project_slug=project_slug,
            file_id=file_id,
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/behavior-versions/files", response_model=BehaviorFilesResponse)
async def list_behavior_files(
    scope: str | None = Query(default=None),
    stores: StoreGroup = Depends(get_store_group),
) -> BehaviorFilesResponse:
    """列有版本历史的 behavior 文件（可选 scope 过滤）。"""
    summaries = await stores.behavior_version_store.list_versioned_behavior_files(
        scope=scope
    )
    return BehaviorFilesResponse(
        files=[
            BehaviorFileItem(
                scope=s.scope,
                agent_slug=s.agent_slug,
                project_slug=s.project_slug,
                file_id=s.file_id,
                version_count=s.version_count,
            )
            for s in summaries
        ]
    )


@router.get("/api/behavior-versions/versions", response_model=BehaviorVersionsResponse)
async def list_behavior_versions(
    file_id: str = Query(...),
    scope: str = Query(default=""),
    agent_slug: str = Query(default=""),
    project_slug: str = Query(default=""),
    stores: StoreGroup = Depends(get_store_group),
) -> BehaviorVersionsResponse:
    """版本元信息列表（Advanced 区，时间线）。"""
    key = _key(scope, agent_slug, project_slug, file_id)
    metas = await stores.behavior_version_store.list_versions(key)
    return BehaviorVersionsResponse(
        versions=[
            BehaviorVersionMetaItem(
                version_no=m.version_no, ts=m.ts, size=m.size, hash=m.hash
            )
            for m in metas
        ]
    )


@router.get("/api/behavior-versions/diff", response_model=BehaviorDiffResponse)
async def behavior_version_diff(
    file_id: str = Query(...),
    scope: str = Query(default=""),
    agent_slug: str = Query(default=""),
    project_slug: str = Query(default=""),
    version_a: int | None = Query(default=None),
    version_b: int | None = Query(default=None),
    stores: StoreGroup = Depends(get_store_group),
) -> BehaviorDiffResponse:
    """两版 diff：显式 version_a/version_b → 任意两版；缺省 → 最新版 vs 上一版（FR-S-2）。"""
    key = _key(scope, agent_slug, project_slug, file_id)
    store = stores.behavior_version_store
    if version_a is not None and version_b is not None:
        # 约定 current = 较新版本号、previous = 较旧版本号
        hi, lo = (version_a, version_b) if version_a >= version_b else (version_b, version_a)
        current, previous = await store.get_two_versions(key, hi, lo)
    else:
        current, previous = await store.get_latest_two(key)

    def _side(v: object | None) -> DiffSide | None:
        if v is None:
            return None
        return DiffSide(
            content=v.content,  # type: ignore[attr-defined]
            availability=v.availability,  # type: ignore[attr-defined]
            oversize=False,
        )

    return BehaviorDiffResponse(
        current=_side(current),
        previous=_side(previous),
        binary=False,
        oversize=False,
    )
