"""Feature 049: Butler persona / clarification behavior helpers。"""

from __future__ import annotations

import re
from typing import Any

from octoagent.core.models import (
    AgentProfile,
    BehaviorLayer,
    BehaviorLayerKind,
    BehaviorPack,
    BehaviorPackFile,
    BehaviorSliceEnvelope,
    BehaviorVisibility,
    ClarificationAction,
    ClarificationDecision,
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


def contains_explicit_location(user_text: str) -> bool:
    normalized = user_text.strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if _LOCATION_SUFFIX_PATTERN.search(normalized) or _EN_LOCATION_PATTERN.search(normalized):
        return True
    if any(token in normalized for token in _DIRECT_LOCATION_TOKENS):
        return True

    weather_anchor = min(
        (lowered.find(token) for token in _WEATHER_QUERY_TOKENS if lowered.find(token) >= 0),
        default=-1,
    )
    if weather_anchor < 0:
        return False

    prefix = normalized if weather_anchor < 0 else normalized[:weather_anchor]
    for token in _WEATHER_LOCATION_STOPWORDS:
        prefix = prefix.replace(token, "")
    prefix = re.sub(r"[，,。？！!?:：\s]", "", prefix)
    candidates = re.findall(r"[\u4e00-\u9fff]{2,8}", prefix)
    return any(candidate not in _WEATHER_LOCATION_STOPWORDS for candidate in candidates)


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
) -> BehaviorPack:
    metadata = dict(agent_profile.metadata)
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
    if agent_profile.scope.value == "project" and project_name:
        source_chain.append(f"project:{project_name}")
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
        metadata={"resolved_from": "default_templates"},
    )


def build_default_behavior_pack_files(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
) -> list[BehaviorPackFile]:
    is_worker_profile = is_worker_behavior_profile(agent_profile)
    project_label = project_name.strip() or agent_profile.name.strip() or "当前项目"
    return [
        BehaviorPackFile(
            file_id="AGENTS.md",
            title="Worker 总约束" if is_worker_profile else "Butler 总约束",
            path_hint="behavior/AGENTS.md",
            layer=BehaviorLayerKind.ROLE,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            content=(
                (
                    f"你是 OctoAgent 的 Root Agent「{agent_profile.name.strip() or 'Worker'}」。"
                    "优先在既定职责、工具边界和 project 约束内处理当前目标，"
                    "不承担 Butler 的总控职责。"
                )
                if is_worker_profile
                else (
                    "你是 OctoAgent 的 Butler。"
                    "先帮助用户理解现状、边界和下一步，再决定回答、补问或委派。"
                )
            ),
        ),
        BehaviorPackFile(
            file_id="SOUL.md",
            title="Butler 个性",
            path_hint="behavior/SOUL.md",
            layer=BehaviorLayerKind.COMMUNICATION,
            visibility=BehaviorVisibility.PRIVATE,
            content=(
                "语气要像长期协作助手，不装懂，不堆内部术语。先给结论，再给理由；"
                "当信息不足时，先承认缺口，再给下一步。"
            ),
        ),
        BehaviorPackFile(
            file_id="USER.md",
            title="Owner 基础偏好",
            path_hint="behavior/USER.md",
            layer=BehaviorLayerKind.COMMUNICATION,
            visibility=BehaviorVisibility.PRIVATE,
            content=(
                "用户优先关心：现在发生了什么、这对我有什么影响、我下一步该做什么。"
                "除非进入 Advanced/诊断区，否则不要默认展开系统内部实现。"
            ),
        ),
        BehaviorPackFile(
            file_id="PROJECT.md",
            title="Project 语境",
            path_hint="behavior/PROJECT.md",
            layer=BehaviorLayerKind.SOLVING,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            content=(
                f"当前 project：{project_label}。默认先围绕 project 目标、现状和交付节奏组织回答，"
                "不要把用户问题误降级成通用 demo。"
            ),
        ),
        BehaviorPackFile(
            file_id="TOOLS.md",
            title="工具与补问边界",
            path_hint="behavior/TOOLS.md",
            layer=BehaviorLayerKind.TOOL_BOUNDARY,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            content=(
                "回答前先区分已知事实、合理推断和缺失输入。"
                "遇到缺关键信息的问题，优先补最关键的 1-2 个条件；"
                "如果用户不补充，再明确标记 best-effort fallback。"
            ),
        ),
        BehaviorPackFile(
            file_id="MEMORY.md",
            title="长期记忆策略",
            path_hint="behavior/MEMORY.md",
            layer=BehaviorLayerKind.MEMORY_POLICY,
            visibility=BehaviorVisibility.PRIVATE,
            content=(
                "仅把已确认偏好和长期稳定事实当成可复用记忆，不把临时猜测、实时事实或未经确认的位置当成真相。"
            ),
        ),
        BehaviorPackFile(
            file_id="BOOTSTRAP.md",
            title="人格自举",
            path_hint="behavior/BOOTSTRAP.md",
            layer=BehaviorLayerKind.BOOTSTRAP,
            visibility=BehaviorVisibility.PRIVATE,
            content=(
                "首次回答或新 project 中，先建立帮助用户前进的协作节奏；"
                "若发现行为模式不理想，可以提出 behavior patch proposal，但默认不静默改写核心文件。"
            ),
        ),
    ]


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
            f"[{item.file_id}] {item.content.strip()}" for item in matching if item.content.strip()
        )
        layers.append(
            BehaviorLayer(
                layer=layer_kind,
                content=content,
                source_file_ids=[item.file_id for item in matching],
                metadata={"file_count": len(matching)},
            )
        )
    return layers


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
) -> dict[str, Any]:
    pack = resolve_behavior_pack(agent_profile=agent_profile, project_name=project_name)
    slice_envelope = build_behavior_slice_envelope(pack)
    return {
        "source_chain": list(pack.source_chain),
        "clarification_policy": dict(pack.clarification_policy),
        "files": [
            {
                "file_id": item.file_id,
                "title": item.title,
                "layer": item.layer.value,
                "visibility": item.visibility.value,
                "share_with_workers": item.share_with_workers,
                "source_kind": item.source_kind,
            }
            for item in pack.files
        ],
        "layers": [
            {
                "layer": item.layer.value,
                "source_file_ids": list(item.source_file_ids),
            }
            for item in pack.layers
        ],
        "worker_slice": {
            "shared_file_ids": list(slice_envelope.shared_file_ids),
            "layers": [item.layer.value for item in slice_envelope.layers],
        },
    }


def render_behavior_system_block(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    shared_only: bool = False,
) -> str:
    pack = resolve_behavior_pack(agent_profile=agent_profile, project_name=project_name)
    effective_layers = (
        build_behavior_slice_envelope(pack).layers if shared_only else pack.layers
    )
    rendered_layers = []
    for layer in effective_layers:
        rendered_layers.append(f"{layer.layer.value}: {layer.content}")
    return (
        "BehaviorSystem:\n"
        f"source_chain: {', '.join(pack.source_chain) or 'N/A'}\n"
        "clarification_policy: "
        f"{pack.clarification_policy}\n"
        f"{chr(10).join(rendered_layers)}"
    )


def decide_clarification(user_text: str) -> ClarificationDecision:
    normalized = user_text.strip()
    if not normalized:
        return ClarificationDecision()

    lowered = normalized.lower()
    technical_request = _looks_like_technical_request(normalized)
    if any(token in lowered for token in _WEATHER_QUERY_TOKENS) and not contains_explicit_location(
        normalized
    ):
        return ClarificationDecision(
            action=ClarificationAction.DELEGATE_AFTER_CLARIFICATION,
            category="weather_location",
            rationale="实时天气查询缺城市/区县，继续委派前需要先补关键位置。",
            missing_inputs=["城市或区县"],
            followup_prompt=build_clarification_reply(
                category="weather_location",
                user_text=normalized,
            ),
            delegate_after_clarification=True,
            metadata={"clarification_needed": "weather_location"},
        )

    if _looks_like_work_priority_request(normalized) and not _contains_explicit_task_inventory(
        normalized
    ):
        return ClarificationDecision(
            action=ClarificationAction.CLARIFY,
            category="work_priority_context",
            rationale="排优先级前缺真实待办或日程列表。",
            missing_inputs=["今天下午的待办列表或日程"],
            followup_prompt=build_clarification_reply(
                category="work_priority_context",
                user_text=normalized,
            ),
            fallback_hint="如果你现在只想先拿一个通用框架，也可以直接说“先给我通用版”。",
            metadata={"clarification_needed": "work_priority_context"},
        )

    if (
        _looks_like_recommendation_request(normalized)
        and not technical_request
        and not _contains_recommendation_context(normalized)
    ):
        return ClarificationDecision(
            action=ClarificationAction.CLARIFY,
            category="recommendation_context",
            rationale="推荐类请求缺少地点、预算或使用场景。",
            missing_inputs=["地点", "预算", "使用场景"],
            followup_prompt=build_clarification_reply(
                category="recommendation_context",
                user_text=normalized,
            ),
            fallback_hint=(
                "如果你不想补充条件，我也可以先给你一个通用 shortlist，"
                "但会明确标成通用建议。"
            ),
            metadata={"clarification_needed": "recommendation_context"},
        )

    if (
        _looks_like_comparison_request(normalized)
        and not technical_request
        and not _contains_comparison_criteria(normalized)
    ):
        return ClarificationDecision(
            action=ClarificationAction.CLARIFY,
            category="comparison_criteria",
            rationale="比较类问题缺少评判标准或使用场景。",
            missing_inputs=["你最在意的评判标准"],
            followup_prompt=build_clarification_reply(
                category="comparison_criteria",
                user_text=normalized,
            ),
            fallback_hint="如果你愿意，我也可以先按常见标准给一个通用对比框架。",
            metadata={"clarification_needed": "comparison_criteria"},
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
