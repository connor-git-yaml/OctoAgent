"""Feature 054: Memory 检索画像解析。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import MemoryRetrievalBindingItem, MemoryRetrievalProfile
from octoagent.memory import MemoryBackendStatus
from octoagent.memory.models.integration import MemoryRecallHookOptions

from .config_schema import OctoAgentConfig
from .config_wizard import load_config

_BACKEND_LABELS = {
    "memu": "内建增强检索",
    "sqlite-metadata": "本地元数据回退",
}

_TRANSPORT_LABELS = {
    "builtin": "内建",
    "compat": "兼容链路",
    "command": "本地命令",
    "http": "HTTP Bridge",
}


def load_memory_retrieval_profile(
    project_root: Path,
    *,
    backend_status: MemoryBackendStatus,
    active_embedding_target: str | None = None,
    requested_embedding_target: str | None = None,
) -> MemoryRetrievalProfile:
    """从 octoagent.yaml + backend status 解析当前生效的 retrieval 画像。"""

    return build_memory_retrieval_profile(
        config=load_config(project_root),
        backend_status=backend_status,
        active_embedding_target=active_embedding_target,
        requested_embedding_target=requested_embedding_target,
    )


def build_memory_retrieval_profile(
    *,
    config: OctoAgentConfig | None,
    backend_status: MemoryBackendStatus,
    active_embedding_target: str | None = None,
    requested_embedding_target: str | None = None,
) -> MemoryRetrievalProfile:
    memory = config.memory if config is not None else None
    inferred_backend_mode = (
        "memu"
        if backend_status.active_backend.strip() == "memu"
        else "local_only"
    )
    backend_mode = (
        memory.backend_mode if memory is not None else inferred_backend_mode
    ).strip().lower()
    transport = _resolve_transport(memory=memory, backend_mode=backend_mode)
    active_backend = backend_status.active_backend.strip() or "sqlite-metadata"
    bindings = [
        _resolve_alias_binding(
            config=config,
            binding_key="reasoning",
            label="记忆加工",
            configured_alias=memory.reasoning_model_alias if memory is not None else "",
            fallback_target="main",
            fallback_label="main（默认）",
            fallback_summary="未绑定记忆加工模型时，当前沿用 main 做总结、整理和候选归纳。",
            configured_summary_template="当前优先用 {target} 做记忆加工、总结和候选整理。",
        ),
        _resolve_alias_binding(
            config=config,
            binding_key="expand",
            label="查询扩写",
            configured_alias=memory.expand_model_alias if memory is not None else "",
            fallback_target="main",
            fallback_label="main（默认）",
            fallback_summary="未绑定查询扩写模型时，当前沿用 main 做 recall 扩写。",
            configured_summary_template="当前优先用 {target} 处理 recall 扩写。",
        ),
        _resolve_embedding_binding(
            config=config,
            configured_alias=memory.embedding_model_alias if memory is not None else "",
            active_backend=active_backend,
            active_embedding_target=active_embedding_target,
            requested_embedding_target=requested_embedding_target,
        ),
        _resolve_alias_binding(
            config=config,
            binding_key="rerank",
            label="结果重排",
            configured_alias=memory.rerank_model_alias if memory is not None else "",
            fallback_target="heuristic",
            fallback_label="heuristic（默认）",
            fallback_summary="未绑定 rerank 模型时，当前继续使用 heuristic 重排。",
            configured_summary_template="当前优先用 {target} 做结果重排。",
        ),
    ]
    warnings: list[str] = []
    for item in bindings:
        warnings.extend(item.warnings)

    engine_mode = "builtin" if backend_mode == "local_only" else "memu_compat"
    engine_label = "内建记忆引擎" if engine_mode == "builtin" else "MemU 兼容链路"
    active_backend_label = _BACKEND_LABELS.get(active_backend, active_backend or "未知路径")

    if engine_mode == "builtin":
        backend_summary = (
            "当前 Memory 以本地 canonical store 为主，默认优先使用内建 Qwen3-Embedding-0.6B；若本机运行时暂不可用，再诚实回退到双语 hash embedding 和本地元数据 recall。"
        )
    elif active_backend == "memu":
        backend_summary = (
            f"当前已经通过 { _TRANSPORT_LABELS.get(transport, transport) } 接上增强记忆链路，页面结果会优先使用增强检索。"
        )
    else:
        backend_summary = (
            "当前虽然保留了 MemU 兼容配置，但这次页面实际已经回退到本地路径；已有记忆仍然可读。"
        )

    return MemoryRetrievalProfile(
        engine_mode=engine_mode,
        engine_label=engine_label,
        transport=transport,
        transport_label=_TRANSPORT_LABELS.get(transport, transport or "内建"),
        active_backend=active_backend,
        active_backend_label=active_backend_label,
        backend_state=backend_status.state.value,
        backend_summary=backend_summary,
        uses_compat_bridge=engine_mode != "builtin",
        bindings=bindings,
        warnings=list(dict.fromkeys(warnings)),
    )


def resolve_memory_retrieval_targets(profile: MemoryRetrievalProfile) -> dict[str, str]:
    return {
        item.binding_key: item.effective_target
        for item in profile.bindings
        if item.binding_key and item.effective_target
    }


def apply_retrieval_profile_to_hook_options(
    hook_options: MemoryRecallHookOptions,
    profile: MemoryRetrievalProfile,
) -> MemoryRecallHookOptions:
    targets = resolve_memory_retrieval_targets(profile)
    return hook_options.model_copy(
        update={
            "reasoning_target": targets.get("reasoning", ""),
            "expand_target": targets.get("expand", ""),
            "embedding_target": targets.get("embedding", ""),
            "rerank_target": targets.get("rerank", ""),
        }
    )


def _resolve_transport(*, memory, backend_mode: str) -> str:
    if backend_mode == "local_only":
        return "builtin"
    if memory is None:
        return "compat"
    transport = str(memory.bridge_transport or "").strip().lower()
    if transport in {"command", "http"}:
        return transport
    if str(memory.bridge_command or "").strip():
        return "command"
    return "http"


def _resolve_alias_binding(
    *,
    config: OctoAgentConfig | None,
    binding_key: str,
    label: str,
    configured_alias: str,
    fallback_target: str,
    fallback_label: str,
    fallback_summary: str,
    configured_summary_template: str,
) -> MemoryRetrievalBindingItem:
    alias_name = configured_alias.strip()
    if not alias_name:
        return MemoryRetrievalBindingItem(
            binding_key=binding_key,
            label=label,
            effective_target=fallback_target,
            effective_label=fallback_label,
            fallback_target=fallback_target,
            fallback_label=fallback_label,
            status="fallback",
            summary=fallback_summary,
        )

    if config is None:
        return MemoryRetrievalBindingItem(
            binding_key=binding_key,
            label=label,
            configured_alias=alias_name,
            effective_target=fallback_target,
            effective_label=fallback_label,
            fallback_target=fallback_target,
            fallback_label=fallback_label,
            status="misconfigured",
            summary=f"当前读取不到配置文件，暂时回退到 {fallback_label}。",
            warnings=[f"{label} 已配置为 {alias_name}，但当前无法校验其可用性。"],
        )

    alias = config.model_aliases.get(alias_name)
    if alias is None:
        return MemoryRetrievalBindingItem(
            binding_key=binding_key,
            label=label,
            configured_alias=alias_name,
            effective_target=fallback_target,
            effective_label=fallback_label,
            fallback_target=fallback_target,
            fallback_label=fallback_label,
            status="misconfigured",
            summary=f"已配置 {alias_name}，但当前找不到这个别名，暂时回退到 {fallback_label}。",
            warnings=[f"{label} 别名 {alias_name} 不存在。"],
        )

    provider = config.get_provider(alias.provider)
    if provider is None or not provider.enabled:
        return MemoryRetrievalBindingItem(
            binding_key=binding_key,
            label=label,
            configured_alias=alias_name,
            effective_target=fallback_target,
            effective_label=fallback_label,
            fallback_target=fallback_target,
            fallback_label=fallback_label,
            status="misconfigured",
            summary=f"已配置 {alias_name}，但它引用的 Provider 当前不可用，暂时回退到 {fallback_label}。",
            warnings=[f"{label} 别名 {alias_name} 引用的 Provider 当前已停用或不存在。"],
        )

    return MemoryRetrievalBindingItem(
        binding_key=binding_key,
        label=label,
        configured_alias=alias_name,
        effective_target=alias_name,
        effective_label=alias_name,
        fallback_target=fallback_target,
        fallback_label=fallback_label,
        status="configured",
        summary=configured_summary_template.format(target=alias_name),
    )


def _resolve_embedding_binding(
    *,
    config: OctoAgentConfig | None,
    configured_alias: str,
    active_backend: str,
    active_embedding_target: str | None = None,
    requested_embedding_target: str | None = None,
) -> MemoryRetrievalBindingItem:
    fallback_target = "engine-default" if active_backend == "memu" else "sqlite-metadata"
    fallback_label = (
        "Qwen3-Embedding-0.6B（默认）" if active_backend == "memu" else "本地元数据回退"
    )
    fallback_summary = (
        "未绑定 embedding 模型时，当前优先由内建 Qwen3-Embedding-0.6B 接管；若本机运行时暂不可用，会自动回退到双语 hash embedding。"
        if active_backend == "memu"
        else "未绑定 embedding 模型时，当前主要依赖本地元数据和关键词召回。"
    )
    binding = _resolve_alias_binding(
        config=config,
        binding_key="embedding",
        label="语义检索",
        configured_alias=configured_alias,
        fallback_target=fallback_target,
        fallback_label=fallback_label,
        fallback_summary=fallback_summary,
        configured_summary_template="当前优先用 {target} 做语义检索。",
    )
    requested_target = (requested_embedding_target or binding.effective_target).strip()
    active_target = (active_embedding_target or requested_target).strip()
    if active_target and active_target != binding.effective_target:
        binding.effective_target = active_target
        binding.effective_label = active_target
        binding.status = "active_generation"
        binding.summary = (
            f"新的 embedding 已经配置为 {requested_target}，但切换完成前当前仍继续使用 {active_target}。"
            if requested_target and requested_target != active_target
            else f"当前语义检索继续使用 {active_target}。"
        )
        if requested_target and requested_target != active_target:
            binding.warnings = list(
                dict.fromkeys(
                    [
                        *binding.warnings,
                        f"embedding 迁移尚未 cutover；当前仍使用 {active_target}。",
                    ]
                )
            )
    return binding
