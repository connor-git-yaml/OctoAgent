"""media_tools：媒体处理、行为文件写入与 skills 工具（6 个）。

工具列表：
- pdf.inspect
- image.inspect
- tts.speak
- canvas.write
- behavior.write_file
- skills
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import structlog

from octoagent.core.behavior_workspace import (
    BOOTSTRAP_COMPLETED_MARKER,
    check_behavior_file_budget,
    get_behavior_file_review_modes,
    mark_onboarding_completed,
)
from octoagent.core.models.behavior import BehaviorReviewMode
from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ..execution_context import get_current_execution_context
from ._deps import ToolDeps, current_parent

_log = structlog.get_logger()


async def register(broker, deps: ToolDeps) -> None:
    """注册所有媒体与行为工具。"""
    from octoagent.skills.tools import SkillsTool as _SkillsTool
    from ..task_service import TaskService

    task_service = TaskService(deps.stores, project_root=deps.project_root)

    # 预构建 review_mode 查找表（file_id -> BehaviorReviewMode）
    _behavior_review_modes = get_behavior_file_review_modes(include_advanced=True)

    # Feature 057: skills tool 依赖 SkillDiscovery
    _skills_tool = _SkillsTool(deps.skill_discovery)

    def _extract_agent_slug(ctx: Any) -> str:
        """从执行上下文的 agent_runtime_id 中提取 agent slug。"""
        runtime_id = getattr(ctx, "agent_runtime_id", "") or ""
        for part in runtime_id.split("|"):
            if part.startswith("worker_profile:"):
                return part.split(":", 1)[1].strip() or "main"
        return "main"

    def _extract_project_slug(ctx: Any) -> str:
        """从执行上下文的 agent_runtime_id 中提取 project slug。"""
        runtime_id = getattr(ctx, "agent_runtime_id", "") or ""
        for part in runtime_id.split("|"):
            if part.startswith("project:"):
                raw = part.split(":", 1)[1].strip()
                # project-default -> default
                if raw.startswith("project-"):
                    return raw[len("project-"):]
                return raw or "default"
        return "default"

    @tool_contract(
        name="pdf.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="document",
        tags=["pdf", "document", "inspect"],
        manifest_ref="builtin://pdf.inspect",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def pdf_inspect(path: str) -> str:
        """检查 PDF 文件摘要。"""

        payload = deps._pack_service._inspect_pdf_file(Path(path))
        return json.dumps(payload, ensure_ascii=False)

    @tool_contract(
        name="image.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="media",
        tags=["image", "media", "inspect"],
        manifest_ref="builtin://image.inspect",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def image_inspect(path: str) -> str:
        """检查图片文件尺寸与格式。"""

        payload = deps._pack_service._inspect_image_file(Path(path))
        return json.dumps(payload, ensure_ascii=False)

    @tool_contract(
        name="tts.speak",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="media",
        tags=["tts", "speech", "audio"],
        manifest_ref="builtin://tts.speak",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def tts_speak(text: str, voice: str = "") -> str:
        """通过系统 TTS 朗读文本。"""

        command = deps._pack_service._tts_command(text=text, voice=voice)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return json.dumps(
            {
                "command": command,
                "returncode": completed.returncode,
                "stderr": completed.stderr.strip(),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="canvas.write",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="canvas",
        tags=["canvas", "artifact", "write"],
        manifest_ref="builtin://canvas.write",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def canvas_write(name: str, content: str, description: str = "") -> str:
        """在当前 task 下创建文本 artifact。"""

        _, context, parent_task = await current_parent(deps)
        artifact = await task_service.create_text_artifact(
            task_id=parent_task.task_id,
            name=name,
            description=description or f"Canvas output for {parent_task.task_id}",
            content=content,
            trace_id=context.trace_id,
            session_id=context.session_id,
            source="builtin:canvas.write",
        )
        return json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "task_id": parent_task.task_id,
                "name": artifact.name,
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="behavior.write_file",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="behavior",
        tags=["behavior", "file", "write", "context"],
        manifest_ref="builtin://behavior.write_file",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def behavior_write_file(
        file_id: str,
        content: str,
        confirmed: bool = False,
    ) -> str:
        """修改行为文件内容。file_id 为短名（如 USER.md），系统自动解析路径。"""
        from octoagent.core.behavior_workspace import resolve_write_path_by_file_id

        file_id = file_id.strip()
        if not file_id:
            return json.dumps(
                {"error": "MISSING_PARAM", "message": "file_id 不能为空"},
                ensure_ascii=False,
            )

        # 从执行上下文获取 agent_slug 和 project_slug
        ctx = get_current_execution_context()
        agent_slug = _extract_agent_slug(ctx)
        project_slug = _extract_project_slug(ctx)

        # 根据 file_id 自动解析磁盘路径
        try:
            resolved = resolve_write_path_by_file_id(
                deps.project_root,
                file_id,
                agent_slug=agent_slug,
                project_slug=project_slug,
            )
        except ValueError as exc:
            return json.dumps(
                {"error": "INVALID_FILE_ID", "message": str(exc)},
                ensure_ascii=False,
            )

        # 字符预算检查
        budget_result = check_behavior_file_budget(file_id, content)
        if not budget_result["within_budget"]:
            return json.dumps(
                {
                    "file_id": file_id,
                    "written": False,
                    "error": "BUDGET_EXCEEDED",
                    "current_chars": budget_result["current_chars"],
                    "budget_chars": budget_result["budget_chars"],
                    "exceeded_by": budget_result["exceeded_by"],
                    "message": (
                        f"内容超出字符预算 {budget_result['exceeded_by']} 字符，请精简后重试"
                    ),
                },
                ensure_ascii=False,
            )

        # 查找 review_mode
        review_mode = _behavior_review_modes.get(
            file_id, BehaviorReviewMode.REVIEW_REQUIRED,
        )

        # proposal 模式：review_required 且未确认时返回 proposal
        if review_mode == BehaviorReviewMode.REVIEW_REQUIRED and not confirmed:
            # 读取当前内容用于对比
            try:
                if resolved.exists():
                    current_content = resolved.read_text(encoding="utf-8")
                    exists = True
                else:
                    current_content = ""
                    exists = False
            except Exception:
                current_content = ""
                exists = False
            return json.dumps(
                {
                    "file_id": file_id,
                    "proposal": True,
                    "review_mode": review_mode.value if hasattr(review_mode, "value") else str(review_mode),
                    "current_content": current_content,
                    "proposed_content": content,
                    "current_chars": len(current_content),
                    "proposed_chars": len(content),
                    "budget_chars": budget_result["budget_chars"],
                    "message": "请向用户展示修改摘要并请求确认，确认后再次调用并设置 confirmed=true",
                },
                ensure_ascii=False,
            )

        # 实际写入磁盘（confirmed=true 时直接信任 Agent 传入的 content）
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except Exception as exc:
            return json.dumps(
                {"error": "FILE_WRITE_ERROR", "message": str(exc)},
                ensure_ascii=False,
            )

        # 记录 structlog 事件（FR-018）
        _bwf_log = structlog.get_logger("behavior.write_file")
        _bwf_log.info(
            "behavior_file_written",
            source="llm_tool",
            file_id=file_id,
            chars_written=len(content),
            resolved_path=str(resolved),
        )

        # Feature 063 T1.4: 路径 A — 检测 BOOTSTRAP.md 的 <!-- COMPLETED --> 标记
        onboarding_completed = False
        if file_id == "BOOTSTRAP.md" and BOOTSTRAP_COMPLETED_MARKER in content:
            try:
                mark_onboarding_completed(deps.project_root)
                onboarding_completed = True
                _bwf_log.info(
                    "onboarding_completed_via_marker",
                    file_id=file_id,
                )
            except Exception:
                _bwf_log.warning(
                    "onboarding_completion_mark_failed",
                    file_id=file_id,
                )

        # Feature 063 T2.4: 所有副作用完成后 invalidate 缓存
        from octoagent.gateway.services.agent_decision import (
            invalidate_behavior_pack_cache,
        )

        invalidate_behavior_pack_cache(project_root=deps.project_root)

        result_payload: dict[str, Any] = {
            "file_id": file_id,
            "written": True,
            "chars_written": len(content),
            "budget_chars": budget_result["budget_chars"],
        }
        if onboarding_completed:
            result_payload["onboarding_completed"] = True
        return json.dumps(result_payload, ensure_ascii=False)

    @tool_contract(
        name="skills",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="skills",
        tags=["skills", "discovery", "knowledge"],
        manifest_ref="builtin://skills",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def skills(action: str, name: str = "") -> str:
        """管理和使用 SKILL.md 定义的技能。支持列出所有可用技能的摘要，或加载指定技能的完整指令到当前会话。"""
        # 读取 AgentSession.metadata 作为 loaded_skill_names 的持久化层
        session_metadata: dict[str, Any] | None = None
        agent_session_id = ""
        try:
            context = get_current_execution_context()
            agent_session_id = getattr(context, "agent_session_id", "") or ""
        except Exception:
            pass

        if agent_session_id and action in ("load", "unload", "list"):
            try:
                agent_session = await deps.stores.agent_context_store.get_agent_session(
                    agent_session_id
                )
                if agent_session is not None:
                    session_metadata = agent_session.metadata
            except Exception as _skill_sess_exc:
                _log.warning(
                    "skills_session_lookup_failed",
                    agent_session_id=agent_session_id[:80],
                    error=str(_skill_sess_exc),
                )

        if session_metadata is None and action in ("load", "unload"):
            # 降级：使用空 metadata 而非拒绝执行。
            # skill load 只需要读写 loaded_skill_names，空 dict 足以让首次 load 工作。
            _log.warning(
                "skills_session_metadata_fallback",
                agent_session_id=agent_session_id[:80],
                action=action,
            )
            session_metadata = {}

        result = await _skills_tool.execute(
            action=action,
            name=name,
            session_metadata=session_metadata,
        )

        # 写回 AgentSession.metadata，持久化 loaded_skill_names
        if (
            action in ("load", "unload")
            and agent_session_id
            and session_metadata is not None
        ):
            try:
                agent_session = await deps.stores.agent_context_store.get_agent_session(
                    agent_session_id
                )
                if agent_session is not None:
                    agent_session.metadata["loaded_skill_names"] = session_metadata.get(
                        "loaded_skill_names", []
                    )
                    from datetime import datetime, UTC
                    agent_session.updated_at = datetime.now(UTC)
                    await deps.stores.agent_context_store.save_agent_session(agent_session)
            except Exception as exc:
                _log.warning("skill_session_metadata_writeback_failed", error=str(exc))

        return result

    for handler in (
        pdf_inspect,
        image_inspect,
        tts_speak,
        canvas_write,
        behavior_write_file,
        skills,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
