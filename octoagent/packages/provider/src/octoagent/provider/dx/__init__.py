"""DX 工具 -- CLI 入口、octo init、octo doctor、dotenv 加载"""

from octoagent.gateway.services.config.dotenv_loader import load_project_dotenv

__all__ = [
    "load_project_dotenv",
]


def __getattr__(name: str):
    """延迟导入已迁移到 gateway 的服务类（兼容层）。"""
    # Phase 2 配置读写已迁移到 gateway/services/config/
    if name in (
        "OctoAgentConfig",
        "ProviderEntry",
        "ModelAlias",
        "RuntimeConfig",
        "FrontDoorConfig",
        "ConfigParseError",
        "CredentialLeakError",
        "ProviderNotFoundError",
        "THINKING_BUDGET_TOKENS",
        "normalize_provider_model_string",
    ):
        import importlib
        mod = importlib.import_module("octoagent.gateway.services.config.config_schema")
        return getattr(mod, name)
    if name in (
        "load_config",
        "save_config",
        "_atomic_write",
        "OCTOAGENT_YAML_NAME",
    ):
        import importlib
        mod = importlib.import_module("octoagent.gateway.services.config.config_wizard")
        return getattr(mod, name)
    # Feature 081 P4：litellm_generator / litellm_runtime 已 git rm；
    # 不再支持 lazy reexport，直接抛 AttributeError。
    # Phase 1 推理服务已迁移到 gateway/services/inference/
    if name in (
        "ConsolidationService",
        "ConsolidationScopeResult",
        "ConsolidationBatchResult",
        "CommittedSorInfo",
        "DerivedExtractionResult",
    ):
        from octoagent.gateway.services.inference.consolidation_service import (
            CommittedSorInfo,
            ConsolidationBatchResult,
            ConsolidationScopeResult,
            ConsolidationService,
            DerivedExtractionResult,
        )
        return locals()[name]
    if name == "DerivedExtractionService":
        from octoagent.gateway.services.inference.derived_extraction_service import DerivedExtractionService
        return DerivedExtractionService
    if name in ("ModelRerankerService", "RerankResult"):
        from octoagent.gateway.services.inference.model_reranker_service import ModelRerankerService, RerankResult
        return locals()[name]
    if name in ("ToMExtractionService", "ToMExtractionResult"):
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionResult, ToMExtractionService
        return locals()[name]
    if name in ("ProfileGeneratorService", "ProfileGenerateResult"):
        from octoagent.gateway.services.inference.profile_generator_service import ProfileGenerateResult, ProfileGeneratorService
        return locals()[name]
    # Phase 3 Memory 运行时已迁移到 gateway/services/memory/
    _memory_modules = {
        "MemoryConsoleService": "octoagent.gateway.services.memory.memory_console_service",
        "MemoryConsoleView": "octoagent.gateway.services.memory.memory_console_view",
        "MemoryRuntimeService": "octoagent.gateway.services.memory.memory_runtime_service",
        "MemoryBackendResolver": "octoagent.gateway.services.memory.memory_backend_resolver",
        "MemoryExportService": "octoagent.gateway.services.memory.memory_export_service",
        "MemoryVaultBridge": "octoagent.gateway.services.memory.memory_vault_bridge",
        "MemoryMaintenanceBridge": "octoagent.gateway.services.memory.memory_maintenance_bridge",
        "MemoryConsoleBase": "octoagent.gateway.services.memory._memory_console_base",
        "MemoryConsoleError": "octoagent.gateway.services.memory._memory_console_base",
        "RetrievalPlatformService": "octoagent.gateway.services.memory.retrieval_platform_service",
        "RetrievalPlatformStore": "octoagent.gateway.services.memory.retrieval_platform_store",
        "BuiltinMemUBridge": "octoagent.gateway.services.memory.builtin_memu_bridge",
        "load_memory_retrieval_profile": "octoagent.gateway.services.memory.memory_retrieval_profile",
        "build_memory_retrieval_profile": "octoagent.gateway.services.memory.memory_retrieval_profile",
        "resolve_memory_retrieval_targets": "octoagent.gateway.services.memory.memory_retrieval_profile",
        "apply_retrieval_profile_to_hook_options": "octoagent.gateway.services.memory.memory_retrieval_profile",
        "MemoryRetrievalProfile": "octoagent.gateway.services.memory.memory_retrieval_profile",
    }
    if name in _memory_modules:
        import importlib
        mod = importlib.import_module(_memory_modules[name])
        return getattr(mod, name)
    # Phase 4 CP 状态 + 其他已迁移到 gateway/services/
    # Feature 081 P4：ProxyProcessManager 已 git rm（不再支持 lazy reexport）。
    _phase4_modules = {
        "ControlPlaneStateStore": "octoagent.gateway.services.control_plane.control_plane_state",
        "AutomationStore": "octoagent.gateway.services.control_plane.automation_store",
        "AutomationStoreSnapshot": "octoagent.gateway.services.control_plane.automation_store",
        "TelegramBotClient": "octoagent.gateway.services.telegram_client",
        "TelegramBotClientError": "octoagent.gateway.services.telegram_client",
        "InlineKeyboardButton": "octoagent.gateway.services.telegram_client",
        "InlineKeyboardMarkup": "octoagent.gateway.services.telegram_client",
    }
    if name in _phase4_modules:
        import importlib
        mod = importlib.import_module(_phase4_modules[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
