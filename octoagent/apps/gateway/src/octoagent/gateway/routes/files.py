"""F104 文件工作台 v0.1 -- Files API 路由（FR-009）。

两级导航 + diff 内容查询。所有 endpoint 经 main.py 路由级 front-door 鉴权
（require_front_door_access，Constitution #10），handler 内不做权限拦截。

主响应（/tasks、/logical-files、/diff）**不含** artifact_id/storage_ref/hash 技术字段
（FR-017/SC-004，技术字段归 /versions Advanced endpoint）。

logical_file_id 可能含 '/'（如 progress-note:phase/1，step_id 未禁斜杠）→ diff/versions
endpoint 用 query 参数承载 logical_file_id，避免 path 段被 '/' 截断导致路由无法匹配
（Codex Phase2 medium）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from octoagent.core.store import StoreGroup
from pydantic import BaseModel

from ..deps import get_store_group

router = APIRouter()

# diff 文本超大降级阈值（字节）：超过则后端置 oversize flag，前端据此降级（FR-019/SC-005）。
DIFF_OVERSIZE_THRESHOLD = 256 * 1024


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------


class FileTaskItem(BaseModel):
    """两级导航第一级：有多版本逻辑文件的 task 条目。"""

    task_id: str
    title: str


class FileTasksResponse(BaseModel):
    tasks: list[FileTaskItem]


class LogicalFileItem(BaseModel):
    """两级导航第二级：version_count >= 2 的逻辑文件条目（主响应无技术字段）。"""

    logical_file_id: str
    display_name: str
    version_count: int


class LogicalFilesResponse(BaseModel):
    files: list[LogicalFileItem]


class DiffSide(BaseModel):
    """diff 一侧（当前版 / 上一版）内容（主响应 0 技术字段，SC-004）。

    version_no / hash / size / storage_kind 等技术字段一律不进主 diff 响应，
    只走 /versions Advanced endpoint（VersionMetaItem）。
    """

    content: str | None = None
    availability: str
    oversize: bool = False


class DiffResponse(BaseModel):
    """diff 主响应：当前版 + 上一版内容 + 降级 flag（FR-007/FR-013/FR-018/FR-019）。"""

    current: DiffSide | None = None
    previous: DiffSide | None = None
    binary: bool = False
    oversize: bool = False


class VersionMetaItem(BaseModel):
    """Advanced 区版本元信息（FR-017，技术字段集中在此 endpoint）。"""

    version_no: int
    ts: str
    size: int
    hash: str
    storage_kind: str


class VersionsResponse(BaseModel):
    versions: list[VersionMetaItem]


# ---------------------------------------------------------------------------
# 友好命名映射（SD-5 后端兜底，前端可覆盖）
# ---------------------------------------------------------------------------


def _friendly_display_name(logical_file_id: str) -> str:
    """SD-5 友好显示名兜底：progress-note:{step} → "进度笔记"，映射不到原样返回。"""
    if logical_file_id.startswith("progress-note:"):
        return "进度笔记"
    return logical_file_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/files/tasks", response_model=FileTasksResponse)
async def list_file_tasks(
    store_group: StoreGroup = Depends(get_store_group),
) -> FileTasksResponse:
    """两级导航第一级：列出有多版本逻辑文件的 task（FR-008/SD-7）。

    title 从 task_store 补；task 已删但版本表残留（理论上级联会清）时 title 兜底为 task_id。
    """
    task_ids = await store_group.artifact_store.list_tasks_with_versionable_files()
    items: list[FileTaskItem] = []
    for task_id in task_ids:
        task = await store_group.task_store.get_task(task_id)
        title = task.title if task is not None and task.title else task_id
        items.append(FileTaskItem(task_id=task_id, title=title))
    return FileTasksResponse(tasks=items)


@router.get(
    "/api/files/tasks/{task_id}/logical-files",
    response_model=LogicalFilesResponse,
)
async def list_logical_files(
    task_id: str,
    store_group: StoreGroup = Depends(get_store_group),
) -> LogicalFilesResponse:
    """两级导航第二级：列出该 task 下 version_count >= 2 的逻辑文件（FR-008/FR-012/SD-4）。

    单版本逻辑文件不返回（SC-006）。主响应不含技术字段（FR-017/SC-004）。
    """
    summaries = await store_group.artifact_store.list_versionable_files_for_task(task_id)
    return LogicalFilesResponse(
        files=[
            LogicalFileItem(
                logical_file_id=s.logical_file_id,
                display_name=s.display_name or _friendly_display_name(s.logical_file_id),
                version_count=s.version_count,
            )
            for s in summaries
        ]
    )


@router.get(
    "/api/files/tasks/{task_id}/diff",
    response_model=DiffResponse,
)
async def get_logical_file_diff(
    task_id: str,
    logical_file_id: str = Query(...),
    store_group: StoreGroup = Depends(get_store_group),
) -> DiffResponse:
    """当前版 vs 上一版内容（FR-007/FR-013）+ binary/oversize 后端预判（FR-018/FR-019）。

    logical_file_id 走 query 参数（可能含 '/'，path 段会被截断，Codex Phase2 medium）。
    主响应不含 artifact_id/storage_ref/hash（FR-017/SC-004）。
    内容不可用（storage_ref 文件被删/清理）→ availability='unavailable' 占位，不抛异常（FR-010）。
    oversize：store 层读前用 size 元数据拦截（FR-019/SC-005），超大内容不读取/序列化/返回。
    """
    current, previous = await store_group.artifact_store.get_current_and_previous(
        task_id, logical_file_id, max_content_bytes=DIFF_OVERSIZE_THRESHOLD
    )

    def _to_side(v) -> DiffSide | None:
        if v is None:
            return None
        # 主响应 0 技术字段（SC-004）：不透传 version_no/storage_kind，
        # 这些只走 /versions Advanced endpoint。
        return DiffSide(
            content=v.content,
            availability=v.availability,
            oversize=v.oversize,
        )

    current_side = _to_side(current)
    previous_side = _to_side(previous)

    # binary 预判：内容可用（文件在）但无文本（非 UTF-8）→ 二进制不支持行级 diff（FR-018）。
    # 仅在 storage_ref 文件存在但 decode 失败时成立
    # （store 层置 content=None + availability='available'）。
    # oversize 侧同样 content=None + availability='available'，故需排除 oversize 后再判 binary。
    binary = any(
        side is not None
        and side.availability == "available"
        and side.content is None
        and not side.oversize
        for side in (current_side, previous_side)
    )

    # oversize 预判：store 层读前拦截已置 side.oversize（FR-019/SC-005），此处仅透传聚合。
    oversize = any(
        side is not None and side.oversize
        for side in (current_side, previous_side)
    )

    return DiffResponse(
        current=current_side,
        previous=previous_side,
        binary=binary,
        oversize=oversize,
    )


@router.get(
    "/api/files/tasks/{task_id}/versions",
    response_model=VersionsResponse,
)
async def list_logical_file_versions(
    task_id: str,
    logical_file_id: str = Query(...),
    store_group: StoreGroup = Depends(get_store_group),
) -> VersionsResponse:
    """Advanced 区版本元信息（FR-017）：技术字段（hash/size/storage_kind）集中在此 endpoint。

    logical_file_id 走 query 参数（可能含 '/'，Codex Phase2 medium）。
    """
    metas = await store_group.artifact_store.list_versions(task_id, logical_file_id)
    return VersionsResponse(
        versions=[
            VersionMetaItem(
                version_no=m.version_no,
                ts=m.ts,
                size=m.size,
                hash=m.hash,
                storage_kind=m.storage_kind,
            )
            for m in metas
        ]
    )
