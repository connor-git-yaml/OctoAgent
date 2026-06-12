"""F108a W3：SetupDomainService 的 setup review 职责簇 mixin。

职责边界：setup review 汇总构建——五类风险项收集（provider runtime / channel
exposure / agent autonomy / tool & skill readiness / secret binding）、memory
alias 风险、active agent profile 解析与 patch 合并。新增"review / 风险汇总"
类方法放这里，防止职责堆回 setup_service.py。

依赖约定（由继承类 SetupDomainService 提供，经 MRO 解析）：
- ``self._resource_ref`` / ``self._policy_profile_by_id`` /
  ``self._tool_profile_allowed``（DomainServiceBase）
- ``self._deep_merge_dicts``（setup_service 主文件 merge helper）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from octoagent.core.models import (
    AgentProfilesDocument,
    BlockingReason,
    ControlPlaneResourceRef,
    DiagnosticsSummaryDocument,
    SetupReviewSummary,
    SetupRiskItem,
    SkillGovernanceDocument,
)
from octoagent.policy import DEFAULT_PROFILE


class SetupReviewMixin:
    """Setup review 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._resource_ref 等）由继承类
    SetupDomainService 提供。方法签名、返回值与副作用与拆分前完全等价
    （F108a 行为零变更）。
    """

    # ── review summary builder ───────────────────────────────────

    def _build_setup_review_summary(
        self,
        *,
        config: dict[str, Any],
        config_warnings: list[str],
        selected_project: Any | None,
        diagnostics: DiagnosticsSummaryDocument,
        active_agent_profile: dict[str, Any],
        policy_profile_id: str,
        skill_governance: SkillGovernanceDocument,
        secret_audit: Any | None,
        validation_errors: list[str],
    ) -> SetupReviewSummary:
        config_ref = self._resource_ref("config_schema", "config:octoagent")
        diagnostics_ref = self._resource_ref("diagnostics_summary", "diagnostics:runtime")
        agent_ref = self._resource_ref("agent_profiles", "agent-profiles:overview")
        policy_ref = self._resource_ref("policy_profiles", "policy:profiles")
        skill_ref = self._resource_ref("skill_governance", "skills:governance")
        provider_runtime_risks: list[SetupRiskItem] = []
        channel_exposure_risks: list[SetupRiskItem] = []
        agent_autonomy_risks: list[SetupRiskItem] = []
        tool_skill_readiness_risks: list[SetupRiskItem] = []
        secret_binding_risks: list[SetupRiskItem] = []

        providers = [
            item
            for item in config.get("providers", [])
            if isinstance(item, dict) and item.get("enabled", True)
        ]
        model_aliases = config.get("model_aliases", {})
        # F081 cleanup：runtime.llm_mode 已删除（曾经的 "echo" 模式专用于 LiteLLM Proxy）。
        # ProviderRouter 直连后所有用户都需要真实 model alias 才能跑——简化为始终 True。
        requires_real_model = True
        front_door = (
            config.get("front_door", {}) if isinstance(config.get("front_door"), dict) else {}
        )
        telegram_cfg = (
            config.get("channels", {}).get("telegram", {})
            if isinstance(config.get("channels"), dict)
            else {}
        )
        for message in validation_errors:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="config_validation_failed",
                    severity="high",
                    title="配置草稿未通过校验",
                    summary=message,
                    blocking=True,
                    recommended_action='先修正配置字段，再点击"检查配置"。',
                    source_ref=config_ref,
                )
            )
        if selected_project is None:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="project_unavailable",
                    severity="high",
                    title="当前没有可用 Project",
                    summary="setup 需要先解析到一个可用的 project / workspace。",
                    blocking=True,
                    recommended_action="先完成 project 选择或初始化默认项目。",
                    source_ref=self._resource_ref("project_selector", "project:selector"),
                )
            )
        if not providers:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="provider_missing",
                    severity="high" if requires_real_model else "warning",
                    title="还没有可用 Provider",
                    summary=(
                        "当前没有任何启用中的 provider，主 Agent 不能调用真实模型。"
                        if requires_real_model
                        else (
                            "当前处于体验模式，还没有接入真实模型；"
                            "你仍然可以先用 Web 跑通基础流程。"
                        )
                    ),
                    blocking=requires_real_model,
                    recommended_action=(
                        "至少配置 1 个 provider，并补齐对应 secret 引用。"
                        if requires_real_model
                        else (
                            "如果你只是先体验本地 Web，可暂时保留为空；"
                            "接 OpenRouter / OpenAI 时再补齐。"
                        )
                    ),
                    source_ref=config_ref,
                )
            )
        if "main" not in model_aliases:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="main_alias_missing",
                    severity="high" if requires_real_model else "warning",
                    title="缺少 main 模型别名",
                    summary=(
                        "主 Agent 依赖 main alias，当前 setup 还没有可用的默认模型。"
                        if requires_real_model
                        else "当前是体验模式，main alias 可以稍后再补；接入真实模型前需要配置好它。"
                    ),
                    blocking=requires_real_model,
                    recommended_action=(
                        "先为 main alias 指定 provider 和模型。"
                        if requires_real_model
                        else "准备接真实模型时，再为 main alias 指定 provider 和模型。"
                    ),
                    source_ref=config_ref,
                )
            )
        if "cheap" not in model_aliases:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="cheap_alias_missing",
                    severity="warning",
                    title="缺少 cheap 模型别名",
                    summary="当前系统仍可运行，但自动降级与低成本路径不可用。",
                    blocking=False,
                    recommended_action="建议补一个 cheap alias，便于 fallback 和后台任务使用。",
                    source_ref=config_ref,
                )
            )
        provider_runtime_risks.extend(
            self._collect_memory_alias_risks(
                config=config,
                config_ref=config_ref,
            )
        )
        for warning in config_warnings:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="config_warning",
                    severity="warning",
                    title="Provider / Runtime 仍有告警",
                    summary=warning,
                    blocking=False,
                    recommended_action="建议先处理 bridge 或 LiteLLM sync 告警。",
                    source_ref=diagnostics_ref,
                )
            )
        front_door_mode = str(front_door.get("mode", "loopback")).strip().lower() or "loopback"
        if front_door_mode == "trusted_proxy" and not front_door.get("trusted_proxy_cidrs"):
            channel_exposure_risks.append(
                SetupRiskItem(
                    risk_id="trusted_proxy_cidrs_missing",
                    severity="high",
                    title="Trusted Proxy 未限制来源",
                    summary="trusted_proxy 模式缺少受信代理来源 CIDR。",
                    blocking=True,
                    recommended_action="补齐 trusted_proxy_cidrs，避免非代理来源直接访问 Gateway。",
                    source_ref=config_ref,
                )
            )
        if telegram_cfg.get("enabled"):
            telegram_mode = str(telegram_cfg.get("mode", "webhook")).strip().lower()
            if telegram_mode == "webhook" and not telegram_cfg.get("webhook_url"):
                channel_exposure_risks.append(
                    SetupRiskItem(
                        risk_id="telegram_webhook_url_missing",
                        severity="high",
                        title="Telegram webhook 配置不完整",
                        summary="Telegram webhook 模式缺少 webhook_url。",
                        blocking=True,
                        recommended_action="补齐 webhook_url，或切换到 polling 模式。",
                        source_ref=config_ref,
                    )
                )
            if str(
                telegram_cfg.get("dm_policy", "")
            ).strip().lower() == "open" and not telegram_cfg.get("allow_users"):
                channel_exposure_risks.append(
                    SetupRiskItem(
                        risk_id="telegram_dm_open",
                        severity="warning",
                        title="Telegram 私聊对任意用户开放",
                        summary="当前 DM policy=open，陌生人也可以直接触发主 Agent。",
                        blocking=False,
                        recommended_action="小白默认建议使用 pairing 或 allowlist。",
                        source_ref=diagnostics_ref,
                    )
                )
            if str(
                telegram_cfg.get("group_policy", "")
            ).strip().lower() == "open" and not telegram_cfg.get("allowed_groups"):
                channel_exposure_risks.append(
                    SetupRiskItem(
                        risk_id="telegram_group_open",
                        severity="warning",
                        title="Telegram 群聊默认开放",
                        summary="当前 group policy=open，未限制 allowed_groups。",
                        blocking=False,
                        recommended_action="建议至少限制 allowed_groups 或改为 allowlist。",
                        source_ref=diagnostics_ref,
                    )
                )
        if not active_agent_profile:
            agent_autonomy_risks.append(
                SetupRiskItem(
                    risk_id="agent_profile_missing",
                    severity="high",
                    title="主 Agent 设置还没有保存",
                    summary="当前 project 还没有保存主 Agent 的名称、Persona 和默认能力。",
                    blocking=True,
                    recommended_action='先确认右侧主 Agent 名称和 Persona，再点击"保存配置"。',
                    source_ref=agent_ref,
                )
            )
        elif not str(active_agent_profile.get("name", "")).strip():
            agent_autonomy_risks.append(
                SetupRiskItem(
                    risk_id="agent_profile_name_missing",
                    severity="high",
                    title="主 Agent 名称不能为空",
                    summary="主 Agent 名称还是空的，当前设置还不能保存。",
                    blocking=True,
                    recommended_action='先填写主 Agent 名称，再点击"检查配置"。',
                    source_ref=agent_ref,
                )
            )
        policy_profile = self._policy_profile_by_id(policy_profile_id) or DEFAULT_PROFILE
        if policy_profile_id == "permissive":
            agent_autonomy_risks.append(
                SetupRiskItem(
                    risk_id="policy_profile_permissive",
                    severity="high",
                    title="当前安全等级为自主",
                    summary="自主模式会放宽审批和工具边界，只适用于完全受信环境。",
                    blocking=False,
                    recommended_action="普通用户默认建议使用谨慎或平衡。",
                    source_ref=policy_ref,
                )
            )
        if active_agent_profile:
            agent_tool_profile = str(active_agent_profile.get("tool_profile", "standard")).strip()
            if not self._tool_profile_allowed(
                agent_tool_profile,
                policy_profile.allowed_tool_profile,
            ):
                agent_autonomy_risks.append(
                    SetupRiskItem(
                        risk_id="agent_profile_exceeds_policy",
                        severity="warning",
                        title="主 Agent 工具级别高于当前安全等级",
                        summary=(
                            f"Agent 要求 {agent_tool_profile}，但当前安全等级只允许 "
                            f"{policy_profile.allowed_tool_profile}。"
                        ),
                        blocking=False,
                        recommended_action="降低 Agent tool_profile，或显式切换更高安全 preset。",
                        source_ref=policy_ref,
                    )
                )
        for item in skill_governance.items:
            if not item.selected:
                continue
            if item.availability == "available":
                continue
            is_blocking = item.blocking and requires_real_model
            tool_skill_readiness_risks.append(
                SetupRiskItem(
                    risk_id=f"{item.item_id}:not_ready",
                    severity="high" if is_blocking else "warning",
                    title=f"{item.label} 尚未就绪",
                    summary=(
                        "；".join(item.missing_requirements) or f"状态={item.availability}"
                        if requires_real_model
                        else "当前处于体验模式，这项扩展能力可以稍后再接入。"
                    ),
                    blocking=is_blocking,
                    recommended_action=(
                        item.install_hint or "先处理缺失依赖后再启用该能力。"
                        if requires_real_model
                        else "如果你只是先跑通 Web，可暂时忽略；需要真实模型或扩展能力时再处理。"
                    ),
                    source_ref=skill_ref,
                )
            )
        if secret_audit is not None:
            for target_key in secret_audit.missing_targets:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id=f"secret_missing:{target_key}",
                        severity="high",
                        title="缺少 Secret 绑定",
                        summary=f"{target_key} 还没有完成 canonical secret binding。",
                        blocking=True,
                        recommended_action=(
                            '先完成 Secret 绑定后，再点击"检查配置"。'
                        ),
                        source_ref=config_ref,
                    )
                )
            for unresolved in secret_audit.unresolved_refs:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id=f"secret_unresolved:{unresolved}",
                        severity="high",
                        title="Secret 引用无法解析",
                        summary=unresolved,
                        blocking=True,
                        recommended_action="修正 secret ref 或环境变量后重试。",
                        source_ref=config_ref,
                    )
                )
            for plaintext in secret_audit.plaintext_risks:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id="secret_plaintext_risk",
                        severity="high",
                        title="检测到明文 Secret 风险",
                        summary=plaintext,
                        blocking=True,
                        recommended_action="移除明文凭证，改用 refs-only secret binding。",
                        source_ref=config_ref,
                    )
                )
            if secret_audit.reload_required:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id="secret_reload_required",
                        severity="warning",
                        title="Secret 绑定已变更但尚未重载",
                        summary="当前 secret bindings 已更新，但 runtime 仍需要 reload / restart。",
                        blocking=False,
                        recommended_action="完成 reload 或重启后，再做健康检查或保存配置。",
                        source_ref=diagnostics_ref,
                    )
                )
            for warning in secret_audit.warnings:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id="secret_warning",
                        severity="warning",
                        title="Secret 配置仍有告警",
                        summary=warning,
                        blocking=False,
                        recommended_action=(
                            "建议把 legacy / provider bridge 迁移到 "
                            "canonical secret binding。"
                        ),
                        source_ref=config_ref,
                    )
                )
        else:
            secret_binding_risks.append(
                SetupRiskItem(
                    risk_id="secret_audit_unavailable",
                    severity="warning",
                    title="Secret audit 当前不可用",
                    summary="暂时无法确认 provider / runtime / channel 所需的 secret 是否完整。",
                    blocking=False,
                    recommended_action="稍后重试或检查 secret service 是否可用。",
                    source_ref=diagnostics_ref,
                )
            )
        all_risks = (
            provider_runtime_risks
            + channel_exposure_risks
            + agent_autonomy_risks
            + tool_skill_readiness_risks
            + secret_binding_risks
        )
        blocking_reasons = [item.risk_id for item in all_risks if item.blocking]
        # Feature 079 Phase 4：同一组 blocking 项对应的结构化描述。前端 modal
        # 用这条渲染可读标题/说明/建议操作，不再依赖解析 risk_id 字符串。
        blocking_reasons_detail = [
            BlockingReason(
                risk_id=item.risk_id,
                title=item.title,
                summary=item.summary,
                recommended_action=item.recommended_action,
                severity=item.severity,
            )
            for item in all_risks
            if item.blocking
        ]
        warnings = [item.summary for item in all_risks if item.severity != "info"]
        if any(item.severity == "high" for item in all_risks):
            risk_level = "high"
        elif all_risks:
            risk_level = "warning"
        else:
            risk_level = "info"
        next_actions: list[str] = []
        if any(item.blocking for item in secret_binding_risks):
            next_actions.append('先补齐 Secret 绑定，再点击"检查配置"。')
        if any(item.blocking for item in provider_runtime_risks):
            next_actions.append("先修正 Provider / model alias 配置，确保主 Agent 可调用模型。")
        if any(item.blocking for item in agent_autonomy_risks):
            next_actions.append('先确认右侧主 Agent 名称和 Persona，再点击"保存配置"。')
        if any(item.blocking for item in tool_skill_readiness_risks):
            next_actions.append("先处理 skills / MCP 缺失依赖，避免首用时能力不可用。")
        if not next_actions:
            if requires_real_model:
                next_actions.append('检查已通过，可以点击"保存配置"。')
            else:
                next_actions.append("当前是体验模式，可以先保存默认配置并直接开始使用。")
                next_actions.append("后续如需真实模型，再补齐 Provider 和 main alias。")
        return SetupReviewSummary(
            ready=not bool(blocking_reasons),
            risk_level=risk_level,
            warnings=warnings,
            blocking_reasons=blocking_reasons,
            blocking_reasons_detail=blocking_reasons_detail,
            next_actions=next_actions,
            provider_runtime_risks=provider_runtime_risks,
            channel_exposure_risks=channel_exposure_risks,
            agent_autonomy_risks=agent_autonomy_risks,
            tool_skill_readiness_risks=tool_skill_readiness_risks,
            secret_binding_risks=secret_binding_risks,
        )

    def _collect_memory_alias_risks(
        self,
        *,
        config: Mapping[str, Any],
        config_ref: ControlPlaneResourceRef,
    ) -> list[SetupRiskItem]:
        memory_cfg = config.get("memory", {}) if isinstance(config.get("memory"), dict) else {}
        model_aliases = (
            config.get("model_aliases", {}) if isinstance(config.get("model_aliases"), dict) else {}
        )
        providers_raw = config.get("providers", []) if isinstance(config.get("providers"), list) else []
        providers_by_id = {
            str(item.get("id", "")).strip(): item
            for item in providers_raw
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        memory_bindings = [
            ("reasoning_model_alias", "记忆加工", "main（默认）"),
            ("expand_model_alias", "查询扩写", "main（默认）"),
            ("embedding_model_alias", "语义检索", "内建 embedding"),
            ("rerank_model_alias", "结果重排", "heuristic（默认）"),
        ]
        risks: list[SetupRiskItem] = []
        for field_name, label, fallback_label in memory_bindings:
            alias_name = str(memory_cfg.get(field_name, "")).strip()
            if not alias_name:
                continue
            alias_payload = model_aliases.get(alias_name)
            field_path = f"memory.{field_name}"
            if not isinstance(alias_payload, dict):
                risks.append(
                    SetupRiskItem(
                        risk_id=f"memory_alias_missing:{field_path}",
                        severity="high",
                        title=f"{label} 模型别名不存在",
                        summary=(
                            f"{field_path} 当前填写为 {alias_name}，"
                            "但 model_aliases 中找不到这个 alias。"
                        ),
                        blocking=True,
                        recommended_action=(
                            f"把 {field_path} 改成已有 alias，"
                            f"或先补齐 {alias_name} 的 model_aliases 配置。"
                        ),
                        source_ref=config_ref,
                    )
                )
                continue
            provider_id = str(alias_payload.get("provider", "")).strip()
            provider_payload = providers_by_id.get(provider_id)
            if provider_payload is None or provider_payload.get("enabled", True) is False:
                risks.append(
                    SetupRiskItem(
                        risk_id=f"memory_alias_provider_unavailable:{field_path}",
                        severity="warning",
                        title=f"{label} 当前会回退",
                        summary=(
                            f"{field_path} 绑定的 alias {alias_name} 引用的 Provider "
                            f"{provider_id or '(未填写)'} 当前不可用，"
                            f"运行时会回退到 {fallback_label}。"
                        ),
                        blocking=False,
                        recommended_action=(
                            f"启用或修正 alias {alias_name} 对应的 Provider，"
                            f"否则 Memory 会继续回退到 {fallback_label}。"
                        ),
                        source_ref=config_ref,
                    )
                )
        return risks

    # ── agent profile helpers ──────────────────────────────────

    def _resolve_active_agent_profile_payload(
        self,
        *,
        agent_profiles: AgentProfilesDocument,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        if not agent_profiles.profiles:
            return {}
        if selected_project is not None and selected_project.default_agent_profile_id:
            matched = next(
                (
                    item
                    for item in agent_profiles.profiles
                    if item.profile_id == selected_project.default_agent_profile_id
                ),
                None,
            )
            if matched is not None:
                return matched.model_dump(mode="json")
        return agent_profiles.profiles[0].model_dump(mode="json")

    def _merge_agent_profile_payload(
        self,
        base: dict[str, Any],
        patch: dict[str, Any],
        *,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        merged = self._deep_merge_dicts(base, patch)
        if (
            str(merged.get("scope", "")).strip().lower() == "project"
            and selected_project is not None
        ):
            merged.setdefault("project_id", selected_project.project_id)
        return merged
