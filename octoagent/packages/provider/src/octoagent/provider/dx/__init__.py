"""DX 工具 -- CLI 入口、octo init、octo doctor、dotenv 加载

Feature 003: Auth Adapter + DX 工具。
对齐 contracts/dx-cli-api.md。
"""

from .dotenv_loader import load_project_dotenv

__all__ = [
    "load_project_dotenv",
    # Feature 065 Phase 1
    "ConsolidationService",
    "ConsolidationScopeResult",
    "ConsolidationBatchResult",
    # Feature 065 Phase 2
    "CommittedSorInfo",
    "DerivedExtractionResult",
    "DerivedExtractionService",
    # Feature 067: FlushPromptInjector / FlushPromptResult 已废弃删除
    "ModelRerankerService",
    "RerankResult",
    # Feature 065 Phase 3
    "ToMExtractionService",
    "ToMExtractionResult",
    "ProfileGeneratorService",
    "ProfileGenerateResult",
]


def __getattr__(name: str):
    """延迟导入 Phase 1/Phase 2/Phase 3 服务类，避免启动时加载全部依赖。"""
    if name in (
        "ConsolidationService",
        "ConsolidationScopeResult",
        "ConsolidationBatchResult",
        "CommittedSorInfo",
        "DerivedExtractionResult",
    ):
        from .consolidation_service import (
            CommittedSorInfo,
            ConsolidationBatchResult,
            ConsolidationScopeResult,
            ConsolidationService,
            DerivedExtractionResult,
        )
        return locals()[name]
    if name == "DerivedExtractionService":
        from .derived_extraction_service import DerivedExtractionService
        return DerivedExtractionService
    # Feature 067: FlushPromptInjector / FlushPromptResult 已废弃删除
    if name in ("ModelRerankerService", "RerankResult"):
        from .model_reranker_service import ModelRerankerService, RerankResult
        return locals()[name]
    if name in ("ToMExtractionService", "ToMExtractionResult"):
        from .tom_extraction_service import ToMExtractionResult, ToMExtractionService
        return locals()[name]
    if name in ("ProfileGeneratorService", "ProfileGenerateResult"):
        from .profile_generator_service import ProfileGenerateResult, ProfileGeneratorService
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
