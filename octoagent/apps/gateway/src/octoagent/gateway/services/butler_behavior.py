"""Feature 049: Butler persona / clarification behavior helpers。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from octoagent.core.behavior_workspace import (
    BEHAVIOR_FILE_BUDGETS,
    BEHAVIOR_OVERLAY_ORDER,
    resolve_behavior_workspace,
)
from octoagent.core.behavior_workspace import (
    build_default_behavior_pack_files as build_default_behavior_workspace_pack_files,
)
from octoagent.core.models import (
    AgentProfile,
    BehaviorLayer,
    BehaviorLayerKind,
    BehaviorPack,
    BehaviorPackFile,
    BehaviorSliceEnvelope,
    BehaviorWorkspace,
    ButlerDecision,
    ButlerDecisionMode,
    ButlerLoopPlan,
    ClarificationAction,
    ClarificationDecision,
    DynamicToolSelection,
    RecallPlan,
    RecallPlanMode,
    RuntimeHintBundle,
    ToolUniverseHints,
)

_WEATHER_QUERY_TOKENS = ("天气", "weather", "气温", "温度", "下雨", "降雨", "体感", "穿衣")
_WEATHER_LOCATION_STOPWORDS = (
    "今天",
    "今日",
    "现在",
    "此刻",
    "帮我",
    "请帮我",
    "请问",
    "查一下",
    "查查",
    "看一下",
    "看下",
    "问下",
    "问一问",
    "想知道",
    "想问",
    "一下",
    "会不会",
    "怎么样",
    "咋样",
    "如何",
    "这里",
    "这边",
    "本地",
    "当地",
    "我这里",
    "你",
    "你直接",
    "你直接去",
    "直接",
    "直接去",
    "websearch",
    "web search",
    "web",
    "search",
    "搜索",
    "联网",
    "实时",
)
_LOCATION_SUFFIX_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{1,10}(?:省|市|区|县|州|盟|旗|镇|乡|村|岛|湾|港)"
)
_EN_LOCATION_PATTERN = re.compile(
    r"\b(?:in|at|for)\s+[A-Za-z][A-Za-z .'-]{1,40}",
    re.IGNORECASE,
)
_DIRECT_LOCATION_TOKENS = {
    "北京",
    "上海",
    "天津",
    "重庆",
    "深圳",
    "广州",
    "杭州",
    "苏州",
    "南京",
    "武汉",
    "成都",
    "西安",
    "长沙",
    "青岛",
    "厦门",
    "宁波",
    "无锡",
    "香港",
    "澳门",
    "台北",
    "东京",
    "首尔",
    "伦敦",
    "巴黎",
    "纽约",
    "洛杉矶",
    "旧金山",
    "新加坡",
}
_WORK_PRIORITY_TOKENS = ("优先级", "先做什么", "后做什么", "顺序", "排序", "拆成")
_WORK_SCOPE_TOKENS = ("工作", "待办", "事情", "任务", "todo", "agenda", "下午", "今天", "今晚")
_RECOMMEND_TOKENS = ("推荐", "餐厅", "饭店", "酒店", "旅馆", "咖啡", "买", "选", "挑", "哪家")
_COMPARISON_TOKENS = ("哪个好", "哪个更好", "选哪个", "怎么选", "对比", "比较", "谁更适合")
_CRITERIA_HINT_TOKENS = (
    "因为",
    "主要看",
    "更在意",
    "预算",
    "场景",
    "通勤",
    "性能",
    "续航",
    "拍照",
    "价格",
)
_TECHNICAL_CONTEXT_TOKENS = (
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "sdk",
    "api",
    "pr",
    "mr",
    "commit",
    "repo",
    "分支",
    "代码",
    "实现",
    "架构",
    "框架",
    "日志库",
    "数据库",
    "仓库",
    "协议",
    "模型",
    "prompt",
    "worker",
    "agent",
    "部署",
    "测试",
)
_EXPLICIT_WEB_SEARCH_TOKENS = (
    "websearch",
    "web search",
    "web_search",
    "联网",
    "上网查",
    "搜一下",
    "搜索一下",
    "直接查",
    "直接去 websearch",
    "直接去 web search",
)


def contains_explicit_location(user_text: str) -> bool:
    return bool(extract_explicit_location(user_text))


def extract_explicit_location(user_text: str) -> str:
    normalized = user_text.strip()
    if not normalized:
        return ""

    for match in _LOCATION_SUFFIX_PATTERN.finditer(normalized):
        location = match.group(0).strip()
        if location:
            return location

    direct_matches = sorted(
        (token for token in _DIRECT_LOCATION_TOKENS if token in normalized),
        key=len,
        reverse=True,
    )
    if direct_matches:
        return direct_matches[0]

    english_match = _EN_LOCATION_PATTERN.search(normalized)
    if english_match:
        location = re.sub(r"^(?:in|at|for)\s+", "", english_match.group(0), flags=re.IGNORECASE)
        return location.strip()

    compact = re.sub(r"[，,。？！!?:：\s]", "", normalized)
    lowered = compact.lower()
    if not compact or len(compact) > 12:
        return ""
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,12}", compact):
        return ""
    if any(token in normalized for token in _WEATHER_QUERY_TOKENS):
        return ""
    if any(token in lowered for token in _EXPLICIT_WEB_SEARCH_TOKENS):
        return ""
    if any(token in compact for token in _WEATHER_LOCATION_STOPWORDS):
        return ""
    return compact


def is_worker_behavior_profile(agent_profile: AgentProfile) -> bool:
    metadata = agent_profile.metadata
    return (
        str(metadata.get("source_kind", "")).strip() == "worker_profile_mirror"
        or bool(str(metadata.get("source_worker_profile_id", "")).strip())
    )


def resolve_behavior_pack(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    project_root: Path | None = None,
) -> BehaviorPack:
    metadata = dict(agent_profile.metadata)
    filesystem_pack = _resolve_filesystem_behavior_pack(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        project_root=project_root,
    )
    if filesystem_pack is not None:
        return filesystem_pack

    raw_pack = metadata.get("behavior_pack")
    if isinstance(raw_pack, dict):
        try:
            pack = BehaviorPack.model_validate(raw_pack)
            if pack.layers:
                return pack
            return pack.model_copy(
                update={
                    "layers": build_behavior_layers(pack.files),
                    "source_chain": pack.source_chain
                    or ["agent_profile.metadata:behavior_pack"],
                }
            )
        except Exception:
            pass

    files = build_default_behavior_pack_files(
        agent_profile=agent_profile,
        project_name=project_name,
    )
    source_chain = ["default_behavior_templates"]
    if agent_profile.scope.value == "project" and project_slug:
        source_chain.append(f"project:{project_slug}")
    clarification_policy = {
        "max_clarification_turns": 2,
        "prefer_single_question": True,
        "fallback_requires_boundary_note": True,
        "delegate_after_clarification_for_realtime": True,
    }
    return BehaviorPack(
        pack_id=f"behavior-pack:{agent_profile.profile_id}",
        profile_id=agent_profile.profile_id,
        scope=agent_profile.scope.value,
        source_chain=source_chain,
        files=files,
        layers=build_behavior_layers(files),
        clarification_policy=clarification_policy,
        metadata={
            "resolved_from": "default_templates",
            "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
            "file_budgets": dict(BEHAVIOR_FILE_BUDGETS),
        },
    )


def build_default_behavior_pack_files(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
) -> list[BehaviorPackFile]:
    return build_default_behavior_workspace_pack_files(
        agent_profile=agent_profile,
        project_name=project_name,
        include_advanced=False,
    )


def build_behavior_layers(files: list[BehaviorPackFile]) -> list[BehaviorLayer]:
    ordered_layers = [
        BehaviorLayerKind.ROLE,
        BehaviorLayerKind.COMMUNICATION,
        BehaviorLayerKind.SOLVING,
        BehaviorLayerKind.TOOL_BOUNDARY,
        BehaviorLayerKind.MEMORY_POLICY,
        BehaviorLayerKind.BOOTSTRAP,
    ]
    layers: list[BehaviorLayer] = []
    for layer_kind in ordered_layers:
        matching = [item for item in files if item.layer is layer_kind and item.content.strip()]
        if not matching:
            continue
        content = "\n".join(
            (
                f"[{item.file_id}"
                + (
                    f"; truncated {item.effective_char_count}/{item.original_char_count} chars"
                    if item.truncated
                    else ""
                )
                + f"] {item.content.strip()}"
            )
            for item in matching
            if item.content.strip()
        )
        layers.append(
            BehaviorLayer(
                layer=layer_kind,
                content=content,
                source_file_ids=[item.file_id for item in matching],
                original_char_count=sum(item.original_char_count for item in matching),
                effective_char_count=sum(item.effective_char_count for item in matching),
                truncated_file_ids=[item.file_id for item in matching if item.truncated],
                metadata={
                    "file_count": len(matching),
                    "truncated_file_ids": [
                        item.file_id for item in matching if item.truncated
                    ],
                },
            )
        )
    return layers


def build_tool_universe_hints(
    selection: DynamicToolSelection | None,
    *,
    scope: str = "butler_main",
    note: str = "",
    tool_profile_fallback: str = "",
) -> ToolUniverseHints:
    if selection is None:
        return ToolUniverseHints(
            scope=scope,
            tool_profile=tool_profile_fallback.strip(),
            resolution_mode="unavailable",
            note=note,
        )
    effective = selection.effective_tool_universe
    return ToolUniverseHints(
        scope=scope,
        tool_profile=(
            effective.tool_profile
            if effective is not None and effective.tool_profile
            else tool_profile_fallback.strip()
        ),
        resolution_mode=selection.resolution_mode,
        selected_tools=(
            list(effective.selected_tools)
            if effective is not None and effective.selected_tools
            else list(selection.selected_tools)
        ),
        discovery_entrypoints=(
            list(effective.discovery_entrypoints)
            if effective is not None
            else []
        ),
        warnings=list(selection.warnings),
        mounted_tools=list(selection.mounted_tools),
        blocked_tools=list(selection.blocked_tools),
        note=note,
        metadata={
            "selection_id": selection.selection_id,
            "backend": selection.backend,
            "is_fallback": selection.is_fallback,
        },
    )


def build_behavior_slice_envelope(pack: BehaviorPack) -> BehaviorSliceEnvelope:
    shared_files = [item for item in pack.files if item.share_with_workers]
    shared_ids = [item.file_id for item in shared_files]
    shared_layers = build_behavior_layers(shared_files)
    return BehaviorSliceEnvelope(
        summary="Worker 仅继承可共享的行为切片，不继承 Butler 私有偏好全集。",
        shared_file_ids=shared_ids,
        layers=shared_layers,
        metadata={
            "shared_file_count": len(shared_files),
            "private_file_count": len(pack.files) - len(shared_files),
        },
    )


def build_behavior_system_summary(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    project_root: Path | None = None,
) -> dict[str, Any]:
    pack = resolve_behavior_pack(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        project_root=project_root,
    )
    slice_envelope = build_behavior_slice_envelope(pack)
    return {
        "source_chain": list(pack.source_chain),
        "clarification_policy": dict(pack.clarification_policy),
        "decision_modes": [item.value for item in ButlerDecisionMode],
        "runtime_hint_fields": [
            "explicit_web_search_requested",
            "can_delegate_research",
            "weather_query",
            "current_location_hint",
            "recent_location_hint",
            "effective_location_hint",
            "recent_worker_lane_worker_type",
            "recent_worker_lane_profile_id",
            "recent_worker_lane_topic",
            "recent_worker_lane_summary",
            "recent_clarification_category",
            "recent_clarification_source_text",
            "tool_universe",
        ],
        "files": [
            {
                "file_id": item.file_id,
                "title": item.title,
                "layer": item.layer.value,
                "visibility": item.visibility.value,
                "share_with_workers": item.share_with_workers,
                "source_kind": item.source_kind,
                "path_hint": item.path_hint,
                "is_advanced": bool(item.metadata.get("is_advanced", False)),
                "budget_chars": item.budget_chars,
                "original_char_count": item.original_char_count,
                "effective_char_count": item.effective_char_count,
                "truncated": item.truncated,
                "truncation_reason": item.truncation_reason,
            }
            for item in pack.files
        ],
        "layers": [
            {
                "layer": item.layer.value,
                "source_file_ids": list(item.source_file_ids),
                "truncated_file_ids": list(item.truncated_file_ids),
            }
            for item in pack.layers
        ],
        "worker_slice": {
            "shared_file_ids": list(slice_envelope.shared_file_ids),
            "layers": [item.layer.value for item in slice_envelope.layers],
        },
        "budget": {
            "overlay_order": list(pack.metadata.get("overlay_order", [])),
            "file_budgets": dict(pack.metadata.get("file_budgets", {})),
        },
    }


def render_behavior_system_block(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    project_root: Path | None = None,
    shared_only: bool = False,
) -> str:
    pack = resolve_behavior_pack(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        project_root=project_root,
    )
    effective_layers = (
        build_behavior_slice_envelope(pack).layers if shared_only else pack.layers
    )
    rendered_layers = []
    for layer in effective_layers:
        layer_header = layer.layer.value
        if layer.truncated_file_ids:
            layer_header = (
                f"{layer_header} [truncated files: {', '.join(layer.truncated_file_ids)}]"
            )
        rendered_layers.append(f"{layer_header}: {layer.content}")
    return (
        "BehaviorSystem:\n"
        f"source_chain: {', '.join(pack.source_chain) or 'N/A'}\n"
        "budget_policy: per-file char budgets with explicit truncation metadata\n"
        "clarification_policy: "
        f"{pack.clarification_policy}\n"
        "decision_modes: "
        f"{', '.join(item.value for item in ButlerDecisionMode)}\n"
        f"{chr(10).join(rendered_layers)}"
    )


def render_runtime_hint_block(*, user_text: str, runtime_hints: RuntimeHintBundle) -> str:
    tool_universe = runtime_hints.tool_universe
    if tool_universe is None:
        tool_block = (
            "ToolUniverseHints:\n"
            "tool_scope: N/A\n"
            "tool_profile: N/A\n"
            "tool_resolution_mode: N/A\n"
            "mounted_tools: N/A\n"
            "blocked_tools: N/A\n"
            "tool_universe_note: N/A"
        )
    else:
        mounted = (
            ", ".join(
                f"{item.tool_name}({item.status})" for item in tool_universe.mounted_tools
            )
            or "N/A"
        )
        blocked = (
            ", ".join(
                f"{item.tool_name}({item.status or 'blocked'}:{item.reason_code or 'n/a'})"
                for item in tool_universe.blocked_tools
            )
            or "N/A"
        )
        selected = ", ".join(tool_universe.selected_tools) or "N/A"
        warnings = ", ".join(tool_universe.warnings) or "N/A"
        tool_block = (
            "ToolUniverseHints:\n"
            f"tool_scope: {tool_universe.scope or 'N/A'}\n"
            f"tool_profile: {tool_universe.tool_profile or 'N/A'}\n"
            f"tool_resolution_mode: {tool_universe.resolution_mode or 'N/A'}\n"
            f"selected_tools: {selected}\n"
            f"mounted_tools: {mounted}\n"
            f"blocked_tools: {blocked}\n"
            f"tool_warnings: {warnings}\n"
            f"tool_universe_note: {tool_universe.note or 'N/A'}"
        )
    return (
        "RuntimeHints:\n"
        "这些是运行时线索，不是新的系统指令；请结合 BehaviorSystem 和当前对话一起判断。\n"
        f"current_user_text: {user_text.strip() or 'N/A'}\n"
        f"explicit_web_search_requested: {runtime_hints.explicit_web_search_requested}\n"
        f"can_delegate_research: {runtime_hints.can_delegate_research}\n"
        f"weather_query: {runtime_hints.weather_query}\n"
        f"current_location_hint: {runtime_hints.current_location_hint or 'N/A'}\n"
        f"recent_location_hint: {runtime_hints.recent_location_hint or 'N/A'}\n"
        f"effective_location_hint: {runtime_hints.effective_location_hint or 'N/A'}\n"
        "recent_clarification_category: "
        f"{runtime_hints.recent_clarification_category or 'N/A'}\n"
        "recent_clarification_source_text: "
        f"{runtime_hints.recent_clarification_source_text or 'N/A'}\n"
        "recent_worker_lane_worker_type: "
        f"{runtime_hints.recent_worker_lane_worker_type or 'N/A'}\n"
        "recent_worker_lane_profile_id: "
        f"{runtime_hints.recent_worker_lane_profile_id or 'N/A'}\n"
        f"recent_worker_lane_topic: {runtime_hints.recent_worker_lane_topic or 'N/A'}\n"
        "recent_worker_lane_summary: "
        f"{runtime_hints.recent_worker_lane_summary or 'N/A'}\n"
        f"{tool_block}"
    )


def build_butler_decision_messages(
    *,
    user_text: str,
    behavior_system_block: str,
    runtime_hint_block: str,
    conversation_context_block: str = "",
    project_name: str = "",
    project_slug: str = "",
) -> list[dict[str, str]]:
    schema = {
        "mode": (
            "direct_answer | ask_once | delegate_research | delegate_dev | "
            "delegate_ops | best_effort_answer"
        ),
        "category": "string",
        "rationale": "string",
        "missing_inputs": ["string"],
        "assumptions": ["string"],
        "tool_intent": "string",
        "target_worker_type": "string",
        "target_worker_profile_id": "string",
        "delegate_objective": "string",
        "continuity_topic": "string",
        "prefer_sticky_worker": "boolean",
        "user_visible_boundary_note": "string",
        "reply_prompt": "string",
    }
    return [
        {
            "role": "system",
            "content": (
                "你是 OctoAgent 的 ButlerDecision resolver。"
                "你的任务不是直接回答用户，而是先基于显式上下文判断下一步动作。"
                "你必须只返回一个 JSON object，不要输出 Markdown、解释或代码块。"
            ),
        },
        {
            "role": "system",
            "content": (
                "决策原则：优先信任 BehaviorSystem 和 RuntimeHints。"
                "除权限、审批、审计、loop guard 等硬边界外，不要退回僵硬硬编码。"
                "当当前挂载工具已经足够时，优先 direct_answer，不要为了形式上的多 Agent 结构强行委派。"
                "当问题会长期持续、跨多轮、跨权限、跨敏感边界或明显更适合 specialist worker 时，再 delegate。"
                "当 delegate 时，不要把用户原话直接转发给 Worker；应输出 delegate_objective 作为 Worker 任务目标。"
                "当存在近期同题材 specialist lane 时，优先设置 prefer_sticky_worker=true 或指定 target_worker_profile_id。"
                "当缺关键信息时最多 ask_once；"
                "如果用户显式要求联网但关键事实仍缺失，可以给 best_effort_answer，"
                "但必须明确边界，不能假装已经完成准确查询。"
            ),
        },
        {
            "role": "system",
            "content": (
                f"ProjectDecisionContext:\nproject_name: {project_name or 'N/A'}\n"
                f"project_slug: {project_slug or 'N/A'}"
            ),
        },
        {
            "role": "system",
            "content": behavior_system_block,
        },
        {
            "role": "system",
            "content": runtime_hint_block,
        },
        *(
            [
                {
                    "role": "system",
                    "content": conversation_context_block,
                }
            ]
            if conversation_context_block.strip()
            else []
        ),
        {
            "role": "user",
            "content": (
                f"当前用户消息：{user_text.strip() or 'N/A'}\n\n"
                "请优先输出一个 ButlerLoopPlan JSON："
                "{\"decision\": <ButlerDecision>, \"recall_plan\": <RecallPlan>}。"
                "如果你只能输出旧版 ButlerDecision JSON，也允许。"
                "RecallPlan schema: "
                "{\"mode\":\"skip|recall\",\"query\":\"...\",\"rationale\":\"...\","
                "\"subject_hint\":\"...\",\"focus_terms\":[\"...\"],"
                "\"allow_vault\":false,\"limit\":4}\n"
                f"ButlerDecision 字段模板：{json.dumps(schema, ensure_ascii=False)}"
            ),
        },
    ]


def _parse_recall_plan_payload(payload: dict[str, Any]) -> RecallPlan | None:
    try:
        plan = RecallPlan.model_validate(payload)
    except Exception:
        return None
    if plan.mode is RecallPlanMode.RECALL and not plan.query.strip():
        return plan.model_copy(update={"mode": RecallPlanMode.SKIP})
    return plan


def _parse_butler_decision_payload(payload: dict[str, Any]) -> ButlerDecision | None:
    normalized = dict(payload)
    if not normalized.get("target_worker_type") and normalized.get("mode") == "delegate_research":
        normalized["target_worker_type"] = "research"
    if not normalized.get("target_worker_type") and normalized.get("mode") == "delegate_dev":
        normalized["target_worker_type"] = "dev"
    if not normalized.get("target_worker_type") and normalized.get("mode") == "delegate_ops":
        normalized["target_worker_type"] = "ops"
    try:
        decision = ButlerDecision.model_validate(normalized)
    except Exception:
        return None
    if decision.mode is ButlerDecisionMode.ASK_ONCE and not decision.reply_prompt:
        decision = decision.model_copy(
            update={
                "reply_prompt": build_clarification_reply(
                    category=decision.category,
                    user_text="这条问题",
                )
            }
        )
    if decision.mode is ButlerDecisionMode.BEST_EFFORT_ANSWER and not decision.reply_prompt:
        decision = decision.model_copy(
            update={
                "reply_prompt": build_best_effort_reply(
                    category=decision.category,
                    user_text="这条问题",
                )
            }
        )
    return decision


def parse_butler_decision_response(content: str) -> ButlerDecision | None:
    loop_plan = parse_butler_loop_plan_response(content)
    if loop_plan is not None:
        return loop_plan.decision
    return None


def parse_butler_loop_plan_response(content: str) -> ButlerLoopPlan | None:
    raw = content.strip()
    if not raw:
        return None
    candidates = [raw]
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced if item.strip())
    brace_match = re.search(r"\{[\s\S]*\}", raw)
    if brace_match is not None:
        candidates.append(brace_match.group(0).strip())

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if "decision" in payload or "recall_plan" in payload:
            decision_payload = payload.get("decision", {})
            recall_payload = payload.get("recall_plan", {})
            if not isinstance(decision_payload, dict):
                decision_payload = {}
            if not isinstance(recall_payload, dict):
                recall_payload = {}
            decision = _parse_butler_decision_payload(decision_payload) or ButlerDecision()
            recall_plan = _parse_recall_plan_payload(recall_payload) or RecallPlan()
            return ButlerLoopPlan(
                decision=decision,
                recall_plan=recall_plan,
                metadata={
                    "loop_plan_source": "wrapped_json",
                },
            )
        decision = _parse_butler_decision_payload(payload)
        if decision is None:
            continue
        return ButlerLoopPlan(
            decision=decision,
            recall_plan=RecallPlan(),
            metadata={
                "loop_plan_source": "legacy_butler_decision",
            },
        )
    return None


def _resolve_filesystem_behavior_pack(
    *,
    agent_profile: AgentProfile,
    project_name: str,
    project_slug: str,
    project_root: Path | None,
) -> BehaviorPack | None:
    if project_root is None:
        return None

    workspace = resolve_behavior_workspace(
        project_root=project_root,
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
    )
    if not bool(workspace.metadata.get("has_filesystem_sources", False)):
        return None

    return _build_behavior_pack_from_workspace(
        agent_profile=agent_profile,
        workspace=workspace,
    )


def _build_behavior_pack_from_workspace(
    *,
    agent_profile: AgentProfile,
    workspace: BehaviorWorkspace,
) -> BehaviorPack:
    files = [
        BehaviorPackFile(
            file_id=item.file_id,
            title=item.title,
            path_hint=item.path,
            layer=item.layer,
            content=item.content,
            visibility=item.visibility,
            share_with_workers=item.share_with_workers,
            source_kind=item.source_kind,
            budget_chars=item.budget_chars,
            original_char_count=item.original_char_count,
            effective_char_count=item.effective_char_count,
            truncated=item.truncated,
            truncation_reason=item.truncation_reason,
            metadata=dict(item.metadata),
        )
        for item in workspace.files
    ]
    clarification_policy = {
        "max_clarification_turns": 2,
        "prefer_single_question": True,
        "fallback_requires_boundary_note": True,
        "delegate_after_clarification_for_realtime": True,
    }
    return BehaviorPack(
        pack_id=f"behavior-pack:{agent_profile.profile_id}",
        profile_id=agent_profile.profile_id,
        scope=agent_profile.scope.value,
        source_chain=list(workspace.source_chain),
        files=files,
        layers=build_behavior_layers(files),
        clarification_policy=clarification_policy,
        metadata={
            "resolved_from": "filesystem_behavior_workspace",
            "project_slug": workspace.project_slug,
            "overlay_order": list(workspace.metadata.get("overlay_order", [])),
            "file_budgets": dict(workspace.metadata.get("file_budgets", {})),
        },
    )


def build_runtime_hint_bundle(
    *,
    user_text: str,
    surface: str = "",
    can_delegate_research: bool = False,
    recent_clarification_category: str = "",
    recent_clarification_source_text: str = "",
    recent_location_hint: str = "",
    default_location_hint: str = "",
    recent_worker_lane_worker_type: str = "",
    recent_worker_lane_profile_id: str = "",
    recent_worker_lane_topic: str = "",
    recent_worker_lane_summary: str = "",
    tool_universe: ToolUniverseHints | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeHintBundle:
    normalized = user_text.strip()
    lowered = normalized.lower()
    current_location = extract_explicit_location(normalized)
    explicit_web_search_requested = any(token in lowered for token in _EXPLICIT_WEB_SEARCH_TOKENS)
    effective_location = (
        current_location
        or recent_location_hint.strip()
        or default_location_hint.strip()
    )
    return RuntimeHintBundle(
        surface=surface.strip(),
        explicit_web_search_requested=explicit_web_search_requested,
        can_delegate_research=can_delegate_research,
        weather_query=any(token in lowered for token in _WEATHER_QUERY_TOKENS),
        current_location_hint=current_location,
        recent_location_hint=recent_location_hint.strip(),
        effective_location_hint=effective_location,
        recent_clarification_category=recent_clarification_category.strip(),
        recent_clarification_source_text=recent_clarification_source_text.strip(),
        recent_worker_lane_worker_type=recent_worker_lane_worker_type.strip(),
        recent_worker_lane_profile_id=recent_worker_lane_profile_id.strip(),
        recent_worker_lane_topic=recent_worker_lane_topic.strip(),
        recent_worker_lane_summary=recent_worker_lane_summary.strip(),
        tool_universe=tool_universe,
        metadata=dict(metadata or {}),
    )


def decide_butler_decision(
    user_text: str,
    *,
    runtime_hints: RuntimeHintBundle | None = None,
) -> ButlerDecision:
    normalized = user_text.strip()
    if not normalized:
        return ButlerDecision()

    hints = runtime_hints or build_runtime_hint_bundle(user_text=normalized)

    if (
        hints.recent_clarification_category == "weather_location"
        and hints.current_location_hint
        and not hints.weather_query
    ):
        rewritten = build_weather_followup_query(
            location_text=hints.current_location_hint,
            original_user_text=hints.recent_clarification_source_text or "今天天气怎么样？",
        )
        return _compatibility_fallback_decision(
            ButlerDecision(
            mode=ButlerDecisionMode.DELEGATE_RESEARCH,
            category="weather_location_followup",
            rationale="检测到上一轮天气补问后的地点补充，恢复 research 链路。",
            assumptions=[f"用户补充的位置是 {hints.current_location_hint}。"],
            tool_intent="web.search",
            target_worker_type="research",
            metadata={
                "delegation_strategy": "butler_owned_freshness",
                "followup_mode": "weather_location",
                "rewritten_user_text": rewritten,
                "resolved_location": hints.current_location_hint,
            },
            ),
            fallback_reason="weather_followup_resume",
        )

    if hints.weather_query:
        if not hints.effective_location_hint:
            if (
                hints.explicit_web_search_requested
                and hints.recent_clarification_category == "weather_location"
            ):
                return _compatibility_fallback_decision(
                    ButlerDecision(
                    mode=ButlerDecisionMode.BEST_EFFORT_ANSWER,
                    category="weather_location_missing",
                    rationale=(
                        "用户再次显式要求 WebSearch，"
                        "但仍缺位置，不能假装已完成准确实时查询。"
                    ),
                    missing_inputs=["城市或区县"],
                    tool_intent="web.search",
                    target_worker_type="research",
                    user_visible_boundary_note="缺少城市 / 区县，无法给出准确的实时天气结果。",
                    reply_prompt=build_best_effort_reply(
                        category="weather_location_missing",
                        user_text=normalized,
                    ),
                    metadata={"clarification_needed": "weather_location"},
                    ),
                    fallback_reason="weather_location_missing_best_effort",
                )
            return _compatibility_fallback_decision(
                ButlerDecision(
                mode=ButlerDecisionMode.ASK_ONCE,
                category="weather_location",
                rationale="实时天气查询缺城市/区县，继续委派前需要先补关键位置。",
                missing_inputs=["城市或区县"],
                tool_intent="web.search",
                target_worker_type="research",
                reply_prompt=build_clarification_reply(
                    category="weather_location",
                    user_text=normalized,
                ),
                metadata={
                    "clarification_needed": "weather_location",
                    "followup_mode": "weather_location",
                },
                ),
                fallback_reason="weather_location_missing_clarify",
            )

        assumptions: list[str] = []
        if hints.current_location_hint != hints.effective_location_hint:
            assumptions.append(f"沿用最近确认的位置：{hints.effective_location_hint}。")
        return _compatibility_fallback_decision(
            ButlerDecision(
            mode=ButlerDecisionMode.DELEGATE_RESEARCH,
            category="weather_location_resolved",
            rationale="天气查询的位置线索已齐，可以直接进入 research。",
            assumptions=assumptions,
            tool_intent="web.search",
            target_worker_type="research",
            metadata={
                "delegation_strategy": "butler_owned_freshness",
                "resolved_location": hints.effective_location_hint,
            },
            ),
            fallback_reason="weather_location_resolved",
        )

    return ButlerDecision()


def _compatibility_fallback_decision(
    decision: ButlerDecision,
    *,
    fallback_reason: str,
) -> ButlerDecision:
    return decision.model_copy(
        update={
            "metadata": {
                **dict(decision.metadata),
                "decision_source": "compatibility_fallback",
                "decision_fallback_reason": fallback_reason,
            }
        }
    )


def decide_clarification(user_text: str) -> ClarificationDecision:
    decision = decide_butler_decision(user_text)
    if decision.mode is ButlerDecisionMode.ASK_ONCE:
        action = ClarificationAction.CLARIFY
        if decision.category == "weather_location" and decision.target_worker_type == "research":
            action = ClarificationAction.DELEGATE_AFTER_CLARIFICATION
        return ClarificationDecision(
            action=action,
            category=decision.category,
            rationale=decision.rationale,
            missing_inputs=list(decision.missing_inputs),
            followup_prompt=decision.reply_prompt,
            delegate_after_clarification=(
                action is ClarificationAction.DELEGATE_AFTER_CLARIFICATION
            ),
            metadata=dict(decision.metadata),
        )
    if decision.mode is ButlerDecisionMode.BEST_EFFORT_ANSWER:
        return ClarificationDecision(
            action=ClarificationAction.BEST_EFFORT_FALLBACK,
            category=decision.category,
            rationale=decision.rationale,
            missing_inputs=list(decision.missing_inputs),
            followup_prompt=decision.reply_prompt,
            fallback_hint=decision.user_visible_boundary_note,
            metadata=dict(decision.metadata),
        )
    return ClarificationDecision()


def build_clarification_reply(*, category: str, user_text: str) -> str:
    question = user_text.strip() or "这条问题"
    if category == "weather_location":
        return (
            f"我可以继续帮你查实时天气，但这条问题还缺少**城市 / 区县**信息：{question}\n\n"
            "你可以直接回我一个城市 / 区县，例如：`深圳`、`北京朝阳区`；"
            "也可以一次性把完整问题发成 `深圳今天天气怎么样`。\n"
            "你一补充位置，我就继续按受治理的实时查询链路往下走。"
        )
    if category == "work_priority_context":
        return (
            "我可以帮你把今天下午的工作拆成优先级，"
            "但我现在还没拿到你的**真实待办 / 日程列表**。\n\n"
            "你可以直接把今天下午要做的事贴给我；"
            "如果你暂时只想先拿一个通用框架，也可以直接回我：`先给我通用版`。\n"
            "我会把“基于真实待办的排序”和“通用建议”明确区分开。"
        )
    if category == "recommendation_context":
        return (
            f"我可以继续帮你推荐，但这条问题还差 1-2 个关键条件：{question}\n\n"
            "先告诉我 **地点 / 预算 / 使用场景** 里最关键的 1-2 项就行；"
            "如果你不想补充，我也可以先给一个通用建议清单，并明确标注它不是基于你的真实上下文。"
        )
    if category == "comparison_criteria":
        return (
            f"我可以继续帮你比较，但还缺一个最关键的判断标准：{question}\n\n"
            "你可以直接告诉我你更在意什么，比如 **价格 / 性能 / 体积 / 续航 / 风格 / 风险**；"
            "如果你不确定，我也可以先按常见标准给你一个通用对比框架。"
        )
    return ""


def build_best_effort_reply(*, category: str, user_text: str) -> str:
    question = user_text.strip() or "这条问题"
    if category == "weather_location_missing":
        return (
            f"我可以继续去做 WebSearch，但这条问题目前仍然缺少**城市 / 区县**信息：{question}\n\n"
            "没有地点时，我没法给你准确的实时天气结果，也不能假装已经查到了正确城市。"
            "你直接回我一个位置，比如 `深圳` 或 `北京朝阳区`，我就继续往下查。"
        )
    return ""


def build_weather_followup_query(*, location_text: str, original_user_text: str) -> str:
    location = location_text.strip()
    original = original_user_text.strip() or "今天天气怎么样？"
    if not location:
        return original
    if contains_explicit_location(original):
        return original
    normalized_original = re.sub(r"^[，,。？！!?:：\s]+", "", original)
    return f"{location}，{normalized_original}"


def _looks_like_work_priority_request(user_text: str) -> bool:
    lowered = user_text.lower()
    return any(token in lowered for token in _WORK_PRIORITY_TOKENS) and any(
        token in lowered for token in _WORK_SCOPE_TOKENS
    )


def _contains_explicit_task_inventory(user_text: str) -> bool:
    normalized = user_text.strip()
    if "\n" in normalized:
        return True
    if re.search(r"(?:^|[\n：:])\s*(?:\d+[).、]|[-*])", normalized):
        return True
    if re.search(r"[：:](.+[，,；;、].+)", normalized):
        return True
    separators = normalized.count("，") + normalized.count(",") + normalized.count("、")
    return separators >= 2


def _looks_like_recommendation_request(user_text: str) -> bool:
    lowered = user_text.lower()
    return any(token in lowered for token in _RECOMMEND_TOKENS)


def _contains_recommendation_context(user_text: str) -> bool:
    if contains_explicit_location(user_text):
        return True
    lowered = user_text.lower()
    if re.search(r"\d+\s*(?:元|块|rmb|¥|\$)", lowered):
        return True
    return any(
        token in lowered
        for token in ("预算", "通勤", "约会", "聚餐", "独自", "两个人", "带孩子", "商务", "送礼")
    )


def _looks_like_comparison_request(user_text: str) -> bool:
    lowered = user_text.lower()
    return any(token in lowered for token in _COMPARISON_TOKENS)


def _looks_like_technical_request(user_text: str) -> bool:
    lowered = user_text.lower()
    return any(token in lowered for token in _TECHNICAL_CONTEXT_TOKENS)


def _contains_comparison_criteria(user_text: str) -> bool:
    lowered = user_text.lower()
    if any(token in lowered for token in _CRITERIA_HINT_TOKENS):
        return True
    return bool(re.search(r"(?:预算|适合|场景|性能|价格|续航|拍照|通勤|办公|游戏)", lowered))
