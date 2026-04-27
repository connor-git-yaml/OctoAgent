"""bootstrap_tools：Bootstrap 完成路径的 LLM 工具（Feature 082 P2）。

工具列表：
- bootstrap.complete

Agent 在判定首次引导对话已完成（用户已回答称呼/工作偏好/时区等基本问题）时，
调用 ``bootstrap.complete`` 工具，传入从用户回答中抽取的结构化字段。
工具内部委托 ``BootstrapSessionOrchestrator.complete_bootstrap()``：
- 应用字段冲突策略写回 OwnerProfile（用户显式 > LLM 推断 > 默认）
- 标记 ``.onboarding-state.json`` 完成
- BootstrapSession.status = COMPLETED

宪法原则 #9（Agent Autonomy）：本工具不试图自动从对话历史里抽取字段——
抽取由 Agent（LLM）自己负责，工具只接收结构化字段并落盘。
"""

from __future__ import annotations

import json

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ..bootstrap_orchestrator import BootstrapSessionOrchestrator
from ._deps import ToolDeps, resolve_runtime_project_context


async def register(broker, deps: ToolDeps) -> None:
    """注册 bootstrap.* 工具。"""

    @tool_contract(
        name="bootstrap.complete",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="bootstrap",
        tags=["bootstrap", "onboarding", "owner_profile"],
        manifest_ref="builtin://bootstrap.complete",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def bootstrap_complete(
        preferred_address: str = "",
        working_style: str = "",
        timezone: str = "",
        locale: str = "",
        display_name: str = "",
        interaction_preferences: list[str] | None = None,
        boundary_notes: list[str] | None = None,
        bootstrap_id: str = "",
    ) -> str:
        """触发当前 project 的 Bootstrap 完成。

        ## 何时调用
        - 用户已回答完称呼问题（preferred_address 已知）
        - 用户表达过工作风格偏好（working_style 可推断）
        - 用户给出过时区/语言信息（timezone / locale 可推断）
        - 一般在 ``BOOTSTRAP.md`` 的引导对话进入尾声时

        ## 参数
        - preferred_address: 用户希望被称呼的方式（如 "Connor" / "老板"）
        - working_style: 工作风格描述（如 "偏好直接结论，避免冗长背景"）
        - timezone: 用户时区（如 "Asia/Shanghai" / "UTC+8"）
        - locale: 主要语言（如 "zh-CN" / "en-US"）
        - display_name: 显示名称（如全名）
        - interaction_preferences: 沟通偏好列表（如 ["回答前先对齐 project 事实"]）
        - boundary_notes: 边界与禁忌（如 ["不要主动提敏感家庭话题"]）
        - bootstrap_id: 显式指定（一般不需要——自动从 project context 推断）

        ## 字段冲突策略
        - 用户在上次同步后改过的字段 → 严格保留（不被 LLM 推断覆盖）
        - 当前是历史伪默认（如 ``preferred_address: '你'``）→ 覆盖
        - 当前已是用户显式值 → 保留（除非提供了非空新值且属于伪默认）

        返回 JSON：``{success, bootstrap_id, owner_profile_updated,
        fields_updated, fields_skipped, onboarding_completed_at, warnings}``。
        """
        try:
            project, _workspace, _task = await resolve_runtime_project_context(deps)
        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "error": "PROJECT_RESOLVE_FAILED",
                    "message": f"无法解析当前 project context：{exc}",
                },
                ensure_ascii=False,
            )

        if not bootstrap_id.strip():
            if project is None or not project.project_id:
                return json.dumps(
                    {
                        "success": False,
                        "error": "BOOTSTRAP_ID_MISSING",
                        "message": "无活跃 project，无法定位 bootstrap session；请显式传 bootstrap_id",
                    },
                    ensure_ascii=False,
                )
            # 与 startup_bootstrap.py 中 _ensure_bootstrap_session 命名规则一致
            bootstrap_id = f"bootstrap-{project.project_id}"
        else:
            bootstrap_id = bootstrap_id.strip()

        profile_updates: dict = {}
        if preferred_address.strip():
            profile_updates["preferred_address"] = preferred_address.strip()
        if working_style.strip():
            profile_updates["working_style"] = working_style.strip()
        if timezone.strip():
            profile_updates["timezone"] = timezone.strip()
        if locale.strip():
            profile_updates["locale"] = locale.strip()
        if display_name.strip():
            profile_updates["display_name"] = display_name.strip()
        if interaction_preferences:
            cleaned = [s.strip() for s in interaction_preferences if s and s.strip()]
            if cleaned:
                profile_updates["interaction_preferences"] = cleaned
        if boundary_notes:
            cleaned = [s.strip() for s in boundary_notes if s and s.strip()]
            if cleaned:
                profile_updates["boundary_notes"] = cleaned

        try:
            orchestrator = BootstrapSessionOrchestrator(
                deps.stores.agent_context_store,
                deps.project_root,
            )
            result = await orchestrator.complete_bootstrap(
                bootstrap_id,
                profile_updates=profile_updates or None,
            )
            await deps.stores.conn.commit()

            payload = {"success": True, **result.to_payload()}
            return json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "error": "BOOTSTRAP_COMPLETE_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
            )

    await broker.try_register(reflect_tool_schema(bootstrap_complete), bootstrap_complete)
