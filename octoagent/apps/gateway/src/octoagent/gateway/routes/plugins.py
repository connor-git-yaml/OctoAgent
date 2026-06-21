"""F106 User Plugin Loader REST API（/api/plugins）。

list/get/toggle/approve/delete/refresh。front-door protected（main.py 装配）。
code plugin approve 是**用户独立 human-initiated 请求**（非 LLM 同轮自填，review M9）；
approve 响应含风险披露，**不**显示"已扫描/安全"（review M1）——v0.1 无沙箱，启用=运行任意代码。
Phase C 追加 install（git）/ update。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from octoagent.gateway.services.plugin_git import GitError
from octoagent.skills.plugins.manifest import PluginRecord

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# code plugin 审批风险披露（review M1/M2/§0.3）——审批面必须呈现，禁"已扫描/安全"措辞。
_APPROVE_RISK_DISCLOSURE = (
    "启用此插件将运行其代码，拥有对你 Agent 的完整访问权（可读取凭证、访问网络、"
    "甚至更改安全策略），且未做代码安全扫描——仅启用你信任的插件。"
)


class PluginListResponse(BaseModel):
    items: list[PluginRecord]
    total: int


class PluginToggleRequest(BaseModel):
    enabled: bool


class PluginApproveResponse(BaseModel):
    plugin: PluginRecord
    risk_disclosure: str = _APPROVE_RISK_DISCLOSURE


class PluginRefreshResponse(BaseModel):
    loaded: int
    rejected: int
    pending: int
    total: int


class PluginInstallRequest(BaseModel):
    repo_url: str


def _registry(request: Request) -> Any:
    reg = getattr(request.app.state, "plugin_registry", None)
    if reg is None:
        raise HTTPException(status_code=503, detail="plugin 子系统不可用（降级）")
    return reg


@router.get("", response_model=PluginListResponse)
async def list_plugins(request: Request) -> PluginListResponse:
    reg = _registry(request)
    items = reg.list_records()
    return PluginListResponse(items=items, total=len(items))


@router.get("/{name}", response_model=PluginRecord)
async def get_plugin(name: str, request: Request) -> PluginRecord:
    reg = _registry(request)
    record = reg.get_record(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"plugin 不存在: {name}")
    return record


@router.post("/{name}/toggle", response_model=PluginRecord)
async def toggle_plugin(name: str, body: PluginToggleRequest, request: Request) -> PluginRecord:
    reg = _registry(request)
    record = await reg.toggle(name, body.enabled)
    if record is None:
        raise HTTPException(status_code=404, detail=f"plugin 不存在: {name}")
    return record


@router.post("/{name}/approve", response_model=PluginApproveResponse)
async def approve_plugin(name: str, request: Request) -> PluginApproveResponse:
    """审批 code plugin（运行其代码）。human-initiated；记 code_hash。"""
    reg = _registry(request)
    record = await reg.approve(name)
    if record is None:
        raise HTTPException(
            status_code=400,
            detail=f"plugin 不存在或非 code-capable（declarative 无须审批）: {name}",
        )
    return PluginApproveResponse(plugin=record)


@router.delete("/{name}", status_code=204)
async def delete_plugin(name: str, request: Request) -> None:
    reg = _registry(request)
    try:
        removed = await reg.remove(name)
    except ValueError as exc:  # path_escape
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"plugin 不存在: {name}")


@router.post("/refresh", response_model=PluginRefreshResponse)
async def refresh_plugins(request: Request) -> PluginRefreshResponse:
    reg = _registry(request)
    counts = await reg.refresh()
    return PluginRefreshResponse(**counts)


@router.post("/install", status_code=201, response_model=PluginRecord)
async def install_plugin(body: PluginInstallRequest, request: Request) -> PluginRecord:
    """git clone 安装 plugin（硬化）。code plugin 默认 pending_approval。"""
    reg = _registry(request)
    try:
        record = await reg.install(body.repo_url)
    except GitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=500, detail="安装后未找到 plugin 记录")
    return record


@router.post("/{name}/update", response_model=PluginRecord)
async def update_plugin(name: str, request: Request) -> PluginRecord:
    """git pull 更新 git plugin。改 code → 自动转 pending_approval（re-approval）。"""
    reg = _registry(request)
    try:
        record = await reg.update(name)
    except GitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"plugin 不存在: {name}")
    return record
