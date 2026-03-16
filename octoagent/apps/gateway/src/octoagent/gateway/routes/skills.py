"""Skills 管理 REST API -- Feature 057

对齐 contracts/skills-api.md §2。
提供 Skill 列表、详情、安装、卸载端点。

端点:
  GET    /api/skills          -- 列出所有已发现的 Skill
  GET    /api/skills/{name}   -- 获取指定 Skill 完整信息
  POST   /api/skills          -- 安装新 Skill（写入用户目录）
  DELETE /api/skills/{name}   -- 卸载用户安装的 Skill（内置不可卸载）
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException
from octoagent.skills import SkillDiscovery
from octoagent.skills.discovery import parse_frontmatter, split_frontmatter, validate_skill
from octoagent.skills.skill_models import SkillSource
from pydantic import BaseModel, Field

from ..deps import get_skill_discovery

log = structlog.get_logger(__name__)

# Skill 名称合法字符：小写字母、数字、连字符，长度 1-64
_SAFE_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")


def _validate_skill_name(name: str) -> None:
    """校验 Skill 名称安全性，防止路径遍历攻击。

    合法名称仅包含小写字母、数字、连字符（kebab-case），
    不允许出现路径分隔符、点号、空白等字符。
    """
    if not _SAFE_SKILL_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"非法 Skill 名称 '{name}'：仅允许小写字母、数字、连字符（kebab-case），"
                "长度 1-64 字符，且不能以连字符开头"
            ),
        )


def _ensure_path_within(child: Path, parent: Path) -> None:
    """确保 child 路径在 parent 目录内，防止路径遍历。"""
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="操作被拒绝：目标路径超出允许范围",
        ) from exc


router = APIRouter(prefix="/api/skills", tags=["skills"])


# ============================================================
# 请求/响应模型
# ============================================================


class SkillItemResponse(BaseModel):
    """单个 Skill 摘要（GET /api/skills 列表元素）。"""

    name: str
    description: str
    version: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str
    source_path: str = ""


class SkillListResponse(BaseModel):
    """GET /api/skills 响应体。"""

    items: list[SkillItemResponse]
    total: int


class SkillDetailResponse(SkillItemResponse):
    """GET /api/skills/{name} 响应体（含完整内容）。"""

    trigger_patterns: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    content: str = ""


class SkillInstallRequest(BaseModel):
    """POST /api/skills 请求体。"""

    name: str = Field(description="Skill 名称（kebab-case）")
    content: str = Field(description="完整的 SKILL.md 文件内容")


class SkillInstallResponse(BaseModel):
    """POST /api/skills 响应体。"""

    name: str
    source: str
    source_path: str
    message: str


class SkillDeleteResponse(BaseModel):
    """DELETE /api/skills/{name} 响应体。"""

    name: str
    message: str


# ============================================================
# 路由端点
# ============================================================


@router.get("", response_model=SkillListResponse)
async def list_skills(
    discovery: SkillDiscovery = Depends(get_skill_discovery),
) -> SkillListResponse:
    """列出所有已发现的 Skill。"""
    items = []
    for entry in discovery.list_items():
        items.append(
            SkillItemResponse(
                name=entry.name,
                description=entry.description,
                version=entry.version,
                tags=entry.tags,
                source=entry.source.value,
                source_path="",
            )
        )

    return SkillListResponse(items=items, total=len(items))


@router.get("/{name}", response_model=SkillDetailResponse)
async def get_skill(
    name: str,
    discovery: SkillDiscovery = Depends(get_skill_discovery),
) -> SkillDetailResponse:
    """获取指定 Skill 的完整信息（包含 content）。"""
    entry = discovery.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    return SkillDetailResponse(
        name=entry.name,
        description=entry.description,
        version=entry.version,
        author=entry.author,
        tags=entry.tags,
        trigger_patterns=entry.trigger_patterns,
        tools_required=entry.tools_required,
        source=entry.source.value,
        source_path=entry.source_path,
        content=entry.content,
    )


@router.post("", response_model=SkillInstallResponse, status_code=201)
async def install_skill(
    body: SkillInstallRequest,
    discovery: SkillDiscovery = Depends(get_skill_discovery),
) -> SkillInstallResponse:
    """安装新 Skill（上传 SKILL.md 内容到用户目录）。

    验证流程:
    1. 解析 content 中的 YAML frontmatter
    2. 验证必填字段（name, description）
    3. 验证 frontmatter 中的 name 与 URL 传入的 name 一致
    4. 写入 ~/.octoagent/skills/{name}/SKILL.md
    5. 调用 SkillDiscovery.refresh()
    """
    user_dir = discovery.user_dir
    if user_dir is None:
        raise HTTPException(
            status_code=500,
            detail="用户 Skill 目录未配置",
        )

    # 路径安全校验：防止路径遍历攻击
    _validate_skill_name(body.name)

    # 验证 SKILL.md 格式
    frontmatter_str, _body = split_frontmatter(body.content)
    if not frontmatter_str:
        raise HTTPException(
            status_code=400,
            detail="Invalid SKILL.md: 缺少 YAML frontmatter（需要 --- 分隔符）",
        )

    try:
        data = parse_frontmatter(frontmatter_str)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid SKILL.md: YAML 解析失败: {exc}",
        )

    is_valid, error_msg = validate_skill(data)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid SKILL.md: {error_msg}",
        )

    # 验证 frontmatter 中的 name 与请求中的 name 一致
    fm_name = str(data.get("name", "")).strip()
    if fm_name != body.name:
        raise HTTPException(
            status_code=400,
            detail=f"SKILL.md frontmatter 中的 name ('{fm_name}') 与请求中的 name ('{body.name}') 不一致",
        )

    # 写入用户 Skill 目录（二次防御：确保拼接后的路径仍在 user_dir 内）
    skill_dir = user_dir / body.name
    _ensure_path_within(skill_dir, user_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    try:
        skill_file.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        log.error(
            "skill_install_write_error",
            name=body.name,
            path=str(skill_file),
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"写入 SKILL.md 失败: {exc}",
        )

    # 刷新缓存
    discovery.refresh()

    log.info("skill_installed", name=body.name, path=str(skill_file))

    return SkillInstallResponse(
        name=body.name,
        source=SkillSource.USER.value,
        source_path=str(skill_file),
        message=f"Skill '{body.name}' installed successfully",
    )


@router.delete("/{name}", response_model=SkillDeleteResponse)
async def uninstall_skill(
    name: str,
    discovery: SkillDiscovery = Depends(get_skill_discovery),
) -> SkillDeleteResponse:
    """卸载用户安装的 Skill。

    内置 Skill 不可卸载（返回 403）。
    仅允许卸载 user 来源的 Skill。
    """
    # 路径安全校验
    _validate_skill_name(name)

    entry = discovery.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    if entry.source == SkillSource.BUILTIN:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot uninstall builtin skill '{name}'. Use a project-level override instead.",
        )

    # 删除 Skill 目录（仅允许删除 user_dir 内的目录）
    user_dir = discovery.user_dir
    if user_dir is None:
        raise HTTPException(
            status_code=500,
            detail="用户 Skill 目录未配置",
        )

    skill_path = Path(entry.source_path)
    skill_dir = skill_path.parent
    _ensure_path_within(skill_dir, user_dir)

    if skill_dir.is_dir():
        try:
            shutil.rmtree(skill_dir)
        except OSError as exc:
            log.error(
                "skill_uninstall_delete_error",
                name=name,
                path=str(skill_dir),
                error=str(exc),
            )
            raise HTTPException(
                status_code=500,
                detail=f"删除 Skill 目录失败: {exc}",
            )

    # 刷新缓存
    discovery.refresh()

    log.info("skill_uninstalled", name=name)

    return SkillDeleteResponse(
        name=name,
        message=f"Skill '{name}' uninstalled successfully",
    )
